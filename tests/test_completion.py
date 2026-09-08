import hashlib
from uuid import uuid4

import httpx
from quicker.catalog import catalog, parse_qif
from quicker.contracts import Extraction
from quicker.db import Candidate, ExtractionAttempt
from quicker.matching import historical_matches
from quicker.reference_exports import store_export
from quicker.worker import process_one
from quicker_client.sync import Companion
from sqlalchemy import select
from test_reference import HISTORY
from test_workflow import InvoiceAdapter, action, fields, ready


def export(account="2026 Holman Way", amount="251.86", day="2/1'26", tag="", payee="City of Sparks"):
    return f"!Account\nN{account}\nTBank\n^\n!Type:Bank\nD{day}\nT-{amount}\nP{payee}\nLSewer{('/' + tag) if tag else ''}\n^\n!Type:Cat\nNSewer\nE\n^\n"


def test_history_match_scopes_property_unit_and_service_period():
    ref = parse_qif(export() + export(account="2026 Grose Lane"))
    data = {
        "payee": "City of Sparks",
        "amount_minor": -25186,
        "date": None,
        "property": "Holman Way",
        "service_period": "01/01/2026 - 03/31/2026",
    }
    matches = historical_matches(ref, data)
    assert len(matches) == 1 and matches[0]["match_confidence"] == "service_period"
    assert data["date"] is None
    assert not historical_matches(ref, {**data, "service_period": "unknown"})
    ref = parse_qif(
        export(account="2026 Bell St.", tag="1008 Bell") + export(account="2026 Bell St.", tag="2 Bell")
    )
    data.update(property="Bell St.", unit="2 Bell", date="2026-02-03")
    assert [r["tag"] for r in historical_matches(ref, data)] == ["2 Bell"]
    assert historical_matches(ref, data)[0]["match_confidence"] == "near_date"
    assert not historical_matches(ref, {**data, "amount_minor": 25186})


def test_qif_ids_survive_new_rows_order_and_cleared_status():
    original = parse_qif(HISTORY)["history"]
    changed = HISTORY.replace("D5/ 7'26", "D5/ 1'26\nT-2\nPNew\n^\nD5/ 7'26\nCX")
    current = parse_qif(changed)["history"]
    assert {r["id"] for r in original} <= {r["id"] for r in current}


def test_exports_keep_exact_backups_hold_partial_and_stale_versions(auth, db):
    raw = export().replace("\n", "\r\n").encode()
    first = auth.post("/api/catalog", files={"file": ("full.QIF", raw)}).json()
    assert first["status"] == "active"
    assert auth.get(f"/api/reference-exports/{first['sha256']}/original").content == raw
    new = export() + export(day="3/1'26")
    second = store_export(db, new.encode(), "new.QIF", expected_digest=parse_qif(raw.decode())["digest"])
    assert second["status"] == "active"
    assert store_export(db, raw, "old.QIF")["status"] == "archived"
    partial = store_export(db, export(day="3/1'26").encode(), "partial.QIF")
    assert partial["status"] == "needs_review"
    assert "less" in partial["note"]
    stale = store_export(db, (new + export(day="4/1'26")).encode(), "stale.QIF", expected_digest="stale")
    assert stale["status"] == "needs_review"
    status = auth.get("/api/reference-exports").json()
    assert status["digest"] == parse_qif(new)["digest"]
    assert len(status["exports"]) == 4
    path = f"/api/reference-exports/{partial['sha256']}/activate"
    assert auth.post(path, json={"expected_digest": "stale"}).status_code == 409
    assert auth.post(path, json={"expected_digest": status["digest"]}).status_code == 200
    assert (db.blobs / hashlib.sha256(raw).hexdigest()).read_bytes() == raw


def test_existing_link_blocks_approval_and_reopens_when_export_loses_match(auth, db, photo):
    row = ready(auth, db, photo)
    ref = export(account="2026 Bell St.", amount="214.55", payee="Example Energy")
    store_export(db, ref.encode(), "reference.QIF")
    with db.write() as session:
        candidate = session.get(Candidate, row["id"])
        candidate.data = {**candidate.data, "service_period": "2026-01-01 to 2026-03-31"}
    row = auth.get("/api/transactions").json()[0]
    assert len(row["historical_duplicates"]) == 1
    linked = auth.post(
        "/api/review",
        json={
            "action": "existing",
            "rows": [
                {
                    "id": row["id"],
                    "revision": row["revision"],
                    "existing_id": row["historical_duplicates"][0]["id"],
                }
            ],
        },
    ).json()[0]
    assert linked["status"] == "existing" and not linked["entry_eligible"]
    assert linked["data"]["date"] is None
    assert action(auth, linked, "approve").status_code == 422
    # Cleared flags and newly inserted history don't break the existing link.
    newer = ref.replace("T-214.55", "CX\nT-214.55") + export(day="3/1'26")
    store_export(db, newer.encode(), "new.QIF")
    assert auth.get("/api/transactions").json()[0]["status"] == "existing"
    corrected = newer.replace("T-214.55", "T-214.54")
    store_export(db, corrected.encode(), "corrected.QIF")
    reopened = auth.get("/api/transactions").json()[0]
    assert reopened["status"] == "review" and reopened["revision"] > linked["revision"]


def test_manual_rows_validate_source_and_retry_without_duplication(auth, db, photo):
    row = ready(auth, db, photo)
    path = f"/api/documents/{row['document_id']}/transactions"
    body = {"request_id": str(uuid4()), "page": 2, "source": "Missed receipt"}
    assert auth.post(path, json=body).status_code == 422
    body["page"] = 1
    first = auth.post(path, json=body).json()
    second = auth.post(path, json=body).json()
    assert first["id"] == second["id"]
    assert first["status"] == "review" and not first["entry_eligible"]
    assert len(auth.get("/api/transactions").json()) == 2
    assert auth.get(f"/api/transactions/{first['id']}/history").json()[0]["action"] == "manual_created"


class ExtraRowAdapter(InvoiceAdapter):
    def extract(self, images, ref):
        result = super().extract(images, ref)
        second = result.transactions[0].model_copy(
            update={"payee": "Missed Merchant", "amount": "12.00", "source": "Second receipt"}
        )
        return Extraction(document_type=result.document_type, transactions=[*result.transactions, second])


def test_reextract_compares_original_evidence_preserves_edits_and_adds_once(auth, db, photo):
    row = ready(auth, db, photo)
    edited = action(
        auth, row, "save", {**fields(row), "payee": "Corrected merchant", "amount_minor": -21400}
    ).json()[0]
    removed = action(auth, edited, "remove").json()[0]
    doc = row["document_id"]
    assert auth.post(f"/api/documents/{doc}/retry").status_code == 200
    assert process_one(db, ExtraRowAdapter)
    rows = auth.get("/api/transactions").json()
    assert rows == [removed]
    attempts = auth.get(f"/api/documents/{doc}/attempts").json()
    attempt = next(a for a in attempts if "comparison" in a["result"])
    path = f"/api/documents/{doc}/attempts/{attempt['id']}/comparison"
    comparison = auth.get(path).json()
    assert comparison["proposals"][0]["existing"]["id"] == row["id"]
    assert comparison["proposals"][1]["existing"] is None
    assert auth.post(path, json={"revision": comparison["revision"], "indices": [0]}).status_code == 409
    assert auth.post(path, json={"revision": "stale", "indices": [1]}).status_code == 409
    body = {"revision": comparison["revision"], "indices": [1]}
    assert auth.post(path, json=body).status_code == 200
    assert auth.post(path, json=body).status_code == 200
    assert len(auth.get("/api/transactions").json()) == 2
    with db.session() as session:
        assert session.scalar(select(ExtractionAttempt).where(ExtractionAttempt.id == attempt["id"])).result[
            "applied"
        ] == [1]


def test_companion_qif_sync_real_server_retains_source_and_retries_idempotently(
    browser_url, auth, db, tmp_path
):
    code = auth.post("/api/pair-code").json()["code"]
    paired = httpx.post(browser_url + "/api/pair", json={"name": "QIF sync test", "code": code}).json()
    source = tmp_path / "reference.QIF"
    source.write_text(export())
    engine = Companion(
        browser_url,
        paired["token"],
        tmp_path / "input",
        tmp_path / "archive",
        tmp_path / "state",
        qif_path=source,
    )
    engine.cycle()
    assert auth.get("/api/reference-exports").json()["exports"] == []
    engine.cycle()
    engine.cycle()
    assert source.exists()
    versions = auth.get("/api/reference-exports").json()["exports"]
    assert len(versions) == 1 and versions[0]["status"] == "active"
    assert engine.request("GET", "/api/device/reference")["digest"] == parse_qif(export())["digest"]
    assert (
        engine.http.post(
            "/api/device/reference", files={"file": ("file.QIF", source.read_bytes())}
        ).status_code
        == 422
    )
    assert (
        engine.http.post(
            f"/api/reference-exports/{versions[0]['sha256']}/activate", json={"expected_digest": ""}
        ).status_code
        == 403
    )
    source.write_text(export() + export(day="3/1'26"))
    engine.cycle()
    engine.cycle()
    with db.session() as session:
        assert len(catalog(session)["history"]) == 2
    assert len(auth.get("/api/reference-exports").json()["exports"]) == 2
    engine.http.close()


def test_new_document_duplicate_invalidates_prior_approval(auth, db, photo):
    row = ready(auth, db, photo)
    approved = action(auth, row, "approve", {**fields(row), "date": "2026-07-19"}).json()[0]
    assert approved["entry_eligible"]
    doc = row["document_id"]
    added = auth.post(
        f"/api/documents/{doc}/transactions",
        json={
            "request_id": str(uuid4()),
            "page": 1,
            "source": "A second copy of the same payment",
            "fields": {**fields(approved)},
        },
    ).json()
    rows = {r["id"]: r for r in auth.get("/api/transactions").json()}
    assert rows[row["id"]]["status"] == "review" and not rows[row["id"]]["entry_eligible"]
    assert action(auth, approved, "approve").status_code == 409
    assert action(auth, added, "approve").status_code == 422
    confirmed = action(
        auth, rows[row["id"]], "approve", {**fields(rows[row["id"]]), "duplicate_acknowledged": True}
    ).json()[0]
    assert confirmed["entry_eligible"]
    # A further copy invalidates the earlier separate-transaction acknowledgement.
    auth.post(
        f"/api/documents/{doc}/transactions",
        json={"request_id": str(uuid4()), "page": 1, "source": "A third copy", "fields": fields(confirmed)},
    )
    rows = {r["id"]: r for r in auth.get("/api/transactions").json()}
    assert rows[row["id"]]["status"] == "review"
    assert rows[row["id"]]["data"]["duplicate_acknowledged"] is False


def test_older_device_export_is_backed_up_without_activation(db):
    first = store_export(db, export().encode(), "first.QIF", source="device:test", source_modified="200")
    second = store_export(
        db, (export() + export(day="3/1'26")).encode(), "old.QIF", source="device:test", source_modified="100"
    )
    assert first["status"] == "active" and second["status"] == "needs_review"
    with db.session() as session:
        assert len(catalog(session)["history"]) == 1


def test_first_device_export_can_initialize_empty_reference(auth, db):
    from quicker.db import Setting

    with db.write() as session:
        session.delete(session.get(Setting, "catalog"))
    code = auth.post("/api/pair-code").json()["code"]
    token = auth.post("/api/pair", json={"name": "Fresh sync", "code": code}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    reference = auth.get("/api/device/reference", headers=headers).json()
    assert reference["digest"] == "empty"
    response = auth.post(
        "/api/device/reference",
        headers=headers,
        data={"expected_digest": reference["digest"]},
        files={"file": ("first.QIF", export().encode())},
    )
    assert response.status_code == 200 and response.json()["status"] == "active"
