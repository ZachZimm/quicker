import json
from contextlib import contextmanager
from threading import Event
from time import time
from uuid import uuid4

import pytest
from conftest import QIF
from quicker.catalog import import_catalog
from quicker.db import Candidate, Document, ExportEvent
from quicker_client.operations import Operations
from quicker_client.qif import render_entry
from sqlalchemy import select

FILE = "a" * 64


@pytest.fixture
def device(auth):
    code = auth.post("/api/pair-code").json()["code"]
    pair = auth.post("/api/pair", json={"name": "Test", "code": code}).json()
    headers = {"Authorization": "Bearer " + pair["token"]}
    assert (
        auth.post(
            "/api/device/heartbeat",
            headers=headers,
            json={"protocol": 1, "file_identity": FILE, "file_name": "test.QDF"},
        ).status_code
        == 200
    )
    return headers


def candidate(db, **changes):
    with db.write() as s:
        doc_id, row_id = str(uuid4()), str(uuid4())
        s.add(Document(id=doc_id, name="Synthetic", status="complete", created=int(time()), ignored=[]))
        s.flush()
        s.add(
            Candidate(
                id=row_id,
                document_id=doc_id,
                revision=1,
                status="approved",
                warnings=[],
                data={
                    "payee": "Desktop Test",
                    "amount_minor": -123,
                    "date": "2026-09-17",
                    "currency": "USD",
                    "account": "2026 Bell St.",
                    "category": "Utilities",
                    "tag": "Water",
                    "property": "Bell St.",
                    "unit": "whole_property",
                    "memo": "Test",
                    **changes,
                },
            )
        )
        return row_id


def start(auth, device, kind="entry"):
    r = auth.post("/api/entry", json={"request_id": str(uuid4()), "kind": kind})
    assert r.status_code == 200, r.text
    run = r.json()
    owner = str(uuid4())
    assert (
        auth.post(
            f"/api/device/operations/{run['id']}/take", headers=device, json={"owner": owner}
        ).status_code
        == 200
    )
    return run, owner


def exported(auth, device, run, owner, content=QIF, purpose="pre", generation=None, event_id=None):
    if generation is None:
        generation = auth.get("/api/device/reference", headers=device).json()["generation"]
    meta = {
        "id": event_id or str(uuid4()),
        "run_id": run["id"],
        "owner": owner,
        "purpose": purpose,
        "file_identity": FILE,
        "expected_generation": generation,
        "started": time(),
        "completed": time(),
    }
    body = {"metadata": json.dumps(meta)}
    files = {"file": ("fresh.qif", content.encode() if isinstance(content, str) else content)}
    result = auth.post("/api/device/export-events", headers=device, data=body, files=files)
    assert result.status_code == 200, result.text
    return result.json(), (body, files)


def claim(auth, device, run, owner, event):
    r = auth.post(
        f"/api/device/operations/{run['id']}/claim",
        headers=device,
        json={"owner": owner, "event_id": event["id"]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_identical_fresh_exports_and_retry_never_reactivate(auth, db, device):
    run, owner = start(auth, device, "refresh")
    first, request = exported(auth, device, run, owner, purpose="refresh")
    second, _ = exported(auth, device, run, owner, purpose="refresh")
    assert first["sha256"] == second["sha256"]
    assert first["id"] != second["id"] and second["generation"] == first["generation"] + 1
    retry = auth.post("/api/device/export-events", headers=device, data=request[0], files=request[1])
    assert retry.json() == first
    assert auth.get("/api/device/reference", headers=device).json()["generation"] == second["generation"]
    with db.session() as s:
        assert len(list(s.scalars(select(ExportEvent)))) == 2


def test_stale_claim_and_aba_reference_rejected(auth, db, device):
    candidate(db)
    run, owner = start(auth, device)
    event, _ = exported(auth, device, run, owner)
    with db.write() as s:
        import_catalog(s, QIF + "!Type:Cat\nNAnother\n^\n")
        import_catalog(s, QIF)
    r = auth.post(
        f"/api/device/operations/{run['id']}/claim",
        headers=device,
        json={"owner": owner, "event_id": event["id"]},
    )
    assert r.status_code == 409
    held, _ = exported(auth, device, run, owner, generation=event["generation"])
    assert held["status"] == "needs_review"


def test_claim_locks_revision_and_attempt_is_once(auth, db, device):
    row = candidate(db)
    run, owner = start(auth, device)
    duplicate = auth.post("/api/entry", headers=device, json={"request_id": str(uuid4())}).json()
    assert duplicate["id"] == run["id"]
    event, _ = exported(auth, device, run, owner)
    run = claim(auth, device, run, owner, event)
    item = run["items"][0]
    assert (
        auth.post("/api/review", json={"action": "save", "rows": [{"id": row, "revision": 1}]}).status_code
        == 409
    )
    path = f"/api/device/operations/{run['id']}/items/{item['id']}/attempt"
    assert auth.post(path, headers=device, json={"owner": owner}).status_code == 200
    assert auth.post(path, headers=device, json={"owner": owner}).status_code == 409
    assert (
        auth.post(
            f"/api/device/operations/{run['id']}/claim",
            headers=device,
            json={"owner": owner, "event_id": event["id"]},
        ).status_code
        == 409
    )


@pytest.mark.parametrize("change", [None, "wrong_tag", "missing", "duplicate", "wrong_account"])
def test_post_export_requires_one_exact_new_marker(auth, db, device, change):
    row_id = candidate(db)
    run, owner = start(auth, device)
    event, _ = exported(auth, device, run, owner)
    run = claim(auth, device, run, owner, event)
    item = run["items"][0]
    assert (
        auth.post(
            f"/api/device/operations/{run['id']}/items/{item['id']}/attempt",
            headers=device,
            json={"owner": owner},
        ).status_code
        == 200
    )
    qif = render_entry(item).decode("cp1252")
    if change == "wrong_tag":
        qif = qif.replace("/Water", "/Property")
    if change == "missing":
        qif = ""
    if change == "duplicate":
        qif *= 2
    if change == "wrong_account":
        qif = qif.replace("2026 Bell St.", "2027 Bell St.")
    post, _ = exported(auth, device, run, owner, QIF + qif, "post")
    result = auth.post(
        f"/api/device/operations/{run['id']}/reconcile",
        headers=device,
        json={"owner": owner, "event_id": post["id"]},
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == ("complete" if change is None else "needs_review")
    with db.session() as s:
        assert s.get(Candidate, row_id).status == ("entered" if change is None else "entering")
    if change == "missing":
        resolved = auth.post(
            f"/api/entry/{run['id']}/resolve/{item['id']}", json={"confirmed_not_entered": True}
        )
        assert resolved.status_code == 200, resolved.text
        with db.session() as s:
            assert s.get(Candidate, row_id).status == "review"
    elif change:
        assert (
            auth.post(
                f"/api/entry/{run['id']}/resolve/{item['id']}", json={"confirmed_not_entered": True}
            ).status_code
            == 409
        )


def test_reference_manual_addition_revokes_approval_and_no_new_approvals_sneak_in(auth, db, device):
    row = candidate(db)
    run, owner = start(auth, device)
    candidate(db, payee="Later approval")
    qif = (
        QIF
        + "!Account\nN2026 Bell St.\nTBank\n^\n!Type:Bank\nD9/17'26\nT-1.23\nPDesktop Test\nLUtilities/Water\n^\n"
    )
    event, _ = exported(auth, device, run, owner, qif)
    run = claim(auth, device, run, owner, event)
    assert run["items"] == []
    with db.session() as s:
        assert s.get(Candidate, row).status == "review"


def test_reduced_or_partial_export_cannot_claim(auth, db, device):
    candidate(db)
    run, owner = start(auth, device)
    exported(
        auth,
        device,
        run,
        owner,
        QIF + "!Account\nN2026 Bell St.\nTBank\n^\n!Type:Bank\nD1/1'26\nT-2\nPOther\n^\n",
    )
    held, _ = exported(auth, device, run, owner)
    assert held["status"] == "needs_review"
    assert (
        auth.post(
            f"/api/device/operations/{run['id']}/claim",
            headers=device,
            json={"owner": owner, "event_id": held["id"]},
        ).status_code
        == 409
    )


class ApiCompanion:
    server_scope = "test"

    def __init__(self, auth, device):
        self.auth, self.device, self.stop = auth, device, Event()
        self.report = lambda message: None
        self.lose_claim = False

    def request(self, method, path, **kwargs):
        r = self.auth.request(method, path, headers=self.device, **kwargs)
        if r.is_error:
            raise RuntimeError(r.text)
        if self.lose_claim and path.endswith("/claim"):
            self.lose_claim = False
            raise RuntimeError("Lost claim response")
        return r.json()


class FakeQuicken:
    identity = FILE

    def __init__(self):
        self.content = QIF.encode()
        self.submissions = 0
        self.crash = False

    def ready(self, background=False):
        pass

    @contextmanager
    def session(self, background=False):
        yield self

    def export(self, path):
        assert not path.exists()
        path.write_bytes(self.content)
        return self.content

    def enter(self, item, directory, before_submit):
        before_submit()
        self.submissions += 1
        self.content += render_entry(item)
        if self.crash:
            raise RuntimeError("Crash after Quicken accepted entry")


@pytest.mark.parametrize("lost_claim", [False, True])
def test_controller_restart_reconciles_without_replaying(tmp_path, auth, db, device, lost_claim):
    row = candidate(db)
    api = ApiCompanion(auth, device)
    adapter = FakeQuicken()
    adapter.crash = not lost_claim
    api.lose_claim = lost_claim
    ops = Operations(api, adapter, tmp_path)
    run = ops.start("entry")
    with pytest.raises(RuntimeError):
        ops.process(run)
    ops = Operations(api, adapter, tmp_path)
    run = ops.start("refresh")
    result = ops.process(run)
    assert result["status"] == "complete"
    assert adapter.submissions == (0 if lost_claim else 1)
    with db.session() as s:
        assert s.get(Candidate, row).status == ("review" if lost_claim else "entered")


def test_qif_input_validation():
    item = {
        "id": str(uuid4()),
        "account_type": "Bank",
        "tags": [],
        "data": {
            "account": "2026 Bell St.",
            "payee": "Test",
            "date": "2026-09-17",
            "amount_minor": -1,
            "category": "Utilities",
            "memo": "x" * 36,
        },
    }
    assert render_entry(item)
    item["data"]["memo"] += "x"
    with pytest.raises(ValueError):
        render_entry(item)
    item["data"]["memo"] = "injected\nT100"
    with pytest.raises(ValueError):
        render_entry(item)
