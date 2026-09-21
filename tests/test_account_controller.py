from contextlib import contextmanager
from pathlib import Path

import pytest
from quicker_client.desktop import DesktopUnavailable
from quicker_client.operations import Operations
from quicker_client.qif import render_account
from test_accounts import request_account
from test_desktop_operations import FILE, ApiCompanion, FakeQuicken
from test_desktop_operations import device as account_device

device = account_device


class AccountQuicken(FakeQuicken):
    path = Path("test.QDF")

    def __init__(self):
        super().__init__()
        self.failure = None
        self.inside = False

    @contextmanager
    def session(self, background=False):
        assert not self.inside
        self.inside = True
        try:
            yield self
        finally:
            self.inside = False

    def create_account(self, request, directory, before_submit):
        assert self.inside
        if self.failure == "dialog":
            raise DesktopUnavailable("Unexpected dialog")
        before_submit()
        if self.failure == "before_submit":
            raise DesktopUnavailable("Interrupted before submit")
        self.submissions += 1
        self.content += render_account(request)
        if self.failure == "after_submit":
            raise DesktopUnavailable("Crash after submit")


class AccountApi(ApiCompanion):
    def __init__(self, auth, device):
        super().__init__(auth, device)
        self.lose_response = None
        self.disconnect_after_attempt = False

    def request(self, method, path, **kwargs):
        result = super().request(method, path, **kwargs)
        if path.endswith("/attempt") and self.disconnect_after_attempt:
            self.stop.set()
        if self.lose_response and path.endswith(self.lose_response):
            self.lose_response = None
            raise RuntimeError("Lost response")
        return result


def setup(auth, device, tmp_path):
    auth.post(
        "/api/device/heartbeat",
        headers=device,
        json={
            "protocol": 1,
            "file_identity": FILE,
            "account_creation": 1,
        },
    ).raise_for_status()
    request = request_account(auth).json()
    api = AccountApi(auth, device)
    adapter = AccountQuicken()
    ops = Operations(api, adapter, tmp_path)
    return request, api, adapter, ops


def pending(api):
    return api.request("GET", "/api/device/account-requests")[0]


def test_create_bank_is_verified_and_idempotent(auth, device, tmp_path):
    request, api, adapter, ops = setup(auth, device, tmp_path)
    assert ops.process_account(request)["status"] == "complete"
    assert adapter.submissions == 1
    assert api.request("GET", "/api/device/account-requests") == []
    assert ops.process_account(request)["status"] == "complete"
    assert adapter.submissions == 1


@pytest.mark.parametrize("failure", ["before_submit", "after_submit", "lost_attempt", "disconnect"])
def test_uncertain_attempt_never_replays(auth, device, tmp_path, failure):
    request, api, adapter, ops = setup(auth, device, tmp_path)
    adapter.failure = failure
    api.lose_response = "/attempt" if failure == "lost_attempt" else None
    api.disconnect_after_attempt = failure == "disconnect"
    with pytest.raises(RuntimeError):
        ops.process_account(request)
    api.stop.clear()
    adapter.failure = None
    ops = Operations(api, adapter, tmp_path)
    if failure == "after_submit":
        assert ops.process_account(pending(api))["status"] == "complete"
    else:
        for _ in range(2):
            with pytest.raises(RuntimeError, match="creation is unverified"):
                ops.process_account(pending(api))
        assert adapter.submissions == 0
        # Operator completes it manually; the next fresh export resolves uncertainty.
        adapter.content += render_account(request)
        assert ops.process_account(pending(api))["status"] == "complete"
    assert adapter.submissions == (1 if failure == "after_submit" else 0)


@pytest.mark.parametrize("failure", ["/claim", "/complete", "/reconcile", "/api/entry"])
def test_lost_responses_recover_without_duplicate_creation(auth, device, tmp_path, failure):
    request, api, adapter, ops = setup(auth, device, tmp_path)
    api.lose_response = failure
    with pytest.raises(RuntimeError, match="Lost response"):
        ops.process_account(request)
    ops = Operations(api, adapter, tmp_path)
    assert ops.process_account(request)["status"] == "complete"
    assert adapter.submissions == 1


def test_existing_account_and_conflicts(auth, device, tmp_path):
    request, _api, adapter, ops = setup(auth, device, tmp_path)
    adapter.content += render_account(request)
    assert ops.process_account(request)["status"] == "complete"
    assert adapter.submissions == 0


@pytest.mark.parametrize("record", [b"N2028 EXAMPLE ST.\nTBank", b"N2028 Example St.\nTCash"])
def test_conflict_stops_before_import(auth, device, tmp_path, record):
    request, _api, adapter, ops = setup(auth, device, tmp_path)
    adapter.content += b"!Account\n" + record + b"\n^\n"
    with pytest.raises(RuntimeError, match="conflicting"):
        ops.process_account(request)
    assert adapter.submissions == 0


def test_wrong_file_owner_dialog_and_revoked_device(auth, device, tmp_path, db):
    request, api, adapter, ops = setup(auth, device, tmp_path)
    with pytest.raises(DesktopUnavailable, match="different configured"):
        ops.process_account({**request, "file_identity": "b" * 64})
    adapter.failure = "dialog"
    with pytest.raises(DesktopUnavailable, match="Unexpected dialog"):
        ops.process_account(request)
    assert not pending(api)["attempted"]
    another = Operations(api, adapter, tmp_path / "other")
    with pytest.raises(RuntimeError, match="Another companion"):
        another.process_account(pending(api))
    adapter.failure = None
    from quicker.db import Device
    from sqlalchemy import select

    with db.write() as session:
        session.scalar(select(Device)).revoked = True
    with pytest.raises(RuntimeError):
        ops.process_account(request)
    assert adapter.submissions == 0


@pytest.mark.parametrize("name", ["", " x", "x\nT100", "[name]", "bad^", "x" * 40, "😀", "bad\x7f"])
def test_account_format_rejects_unsafe_names(name):
    with pytest.raises((ValueError, UnicodeError)):
        render_account({"name": name, "account_type": "Bank"})


def test_account_format_contains_no_transactions():
    assert render_account({"name": "2028 Example;One half & café", "account_type": "Bank"}) == (
        b"!Account\r\nN2028 Example;One half & caf\xe9\r\nTBank\r\n^\r\n"
    )
    with pytest.raises(ValueError, match="Only Bank"):
        render_account({"name": "New", "account_type": "Cash"})


def test_active_entry_is_not_started_by_account_creation(auth, device, tmp_path):
    request, _api, adapter, ops = setup(auth, device, tmp_path)
    ops.start("entry")
    with pytest.raises(DesktopUnavailable, match="Finish the active transaction"):
        ops.process_account(request)
    assert adapter.submissions == 0


def test_companion_polls_offline_queue_and_advertises_bank_creation(auth, device, tmp_path):
    import httpx
    from quicker_client.sync import Companion

    request = request_account(auth).json()
    assert "support" in request["message"]

    def transport(req):
        response = auth.request(req.method, req.url.path, content=req.content, headers=dict(req.headers))
        return httpx.Response(response.status_code, json=response.json())

    engine = Companion(
        "http://testserver",
        device["Authorization"].removeprefix("Bearer "),
        tmp_path / "in",
        tmp_path / "out",
        tmp_path / "state",
        transport=httpx.MockTransport(transport),
    )
    adapter = AccountQuicken()
    engine.operations = Operations(engine, adapter, tmp_path)
    try:
        engine.heartbeat()
        assert engine.request("GET", "/api/device/account-requests")[0]["message"] == "Waiting for Windows"
        engine.desktop_cycle()
        engine.desktop_cycle()
        assert adapter.submissions == 1
        assert engine.request("GET", "/api/device/account-requests") == []
    finally:
        engine.http.close()


def test_reduced_post_export_stops_and_recovers_without_creation(auth, device, tmp_path):
    request, api, adapter, ops = setup(auth, device, tmp_path)
    original_create = adapter.create_account
    complete_content = None

    def truncated(request, directory, before_submit):
        nonlocal complete_content
        original_create(request, directory, before_submit)
        complete_content = adapter.content
        adapter.content = render_account(request)

    adapter.create_account = truncated
    with pytest.raises(RuntimeError, match="coverage|lists"):
        ops.process_account(request)
    assert pending(api)["attempted"]
    adapter.content = complete_content
    ops = Operations(api, adapter, tmp_path)
    assert ops.process_account(pending(api))["status"] == "complete"
    assert adapter.submissions == 1
