from uuid import uuid4

from conftest import QIF
from playwright.sync_api import expect, sync_playwright
from test_desktop_operations import FILE, exported, start
from test_desktop_operations import device as account_device
from test_register import cell, edit, login
from test_workflow import action, fields, ready

device = account_device


def request_account(auth, name="2028 Example St.", **changes):
    return auth.post(
        "/api/account-requests",
        json={
            "request_id": str(uuid4()),
            "name": name,
            "account_type": "Bank",
            "confirmed": True,
            **changes,
        },
    )


def fresh(auth, device, content=QIF):
    run, owner = start(auth, device, "refresh")
    event, _ = exported(auth, device, run, owner, content=content, purpose="refresh")
    response = auth.post(
        f"/api/device/operations/{run['id']}/reconcile",
        headers=device,
        json={"owner": owner, "event_id": event["id"]},
    )
    assert response.status_code == 200, response.text
    return event["id"]


def test_latest_account_without_date_and_with_older_date(auth, db, photo):
    row = ready(auth, db, photo)
    assert row["data"]["date"] is None
    assert row["data"]["account"] == "2027 Example St."
    row = action(auth, row, "save", {**fields(row), "date": "2020-01-01"}).json()[0]
    assert row["data"]["account"] == "2027 Example St."
    row = action(
        auth, row, "save", {**fields(row), "account": "2026 Example St.", "account_override": True}
    ).json()[0]
    row = action(auth, row, "save", {**fields(row), "date": None}).json()[0]
    assert row["data"]["account"] == "2026 Example St."


def test_new_account_import_reroutes_automatic_approval_with_new_revision(auth, db, photo):
    from quicker.catalog import import_catalog

    row = ready(auth, db, photo)
    approved = action(auth, row, "approve", {**fields(row), "date": "2026-08-10"}).json()[0]
    with db.write() as session:
        import_catalog(session, QIF + "!Account\nN2028 Example St.\nTBank\n^\n")
    rerouted = auth.get("/api/transactions").json()[0]
    assert rerouted["data"]["account"] == "2028 Example St."
    assert rerouted["status"] == "review"
    assert rerouted["revision"] == approved["revision"] + 1
    assert action(auth, approved, "approve").status_code == 409


def test_default_migration_preserves_overrides_and_entered_rows(db):
    from quicker.db import Audit, Candidate
    from sqlalchemy import select, text
    from test_desktop_operations import candidate

    auto = candidate(db)
    override = candidate(db, account_override=True)
    entered = candidate(db)
    with db.write() as session:
        session.get(Candidate, entered).status = "entered"
        session.execute(text("DROP TABLE account_requests"))
        session.execute(text("UPDATE alembic_version SET version_num='d9345c12b233'"))
    db.migrate()
    with db.session() as session:
        auto_row = session.get(Candidate, auto)
        assert auto_row.data["account"] == "2027 Example St."
        assert auto_row.status == "review" and auto_row.revision == 2
        for row_id in (override, entered):
            row = session.get(Candidate, row_id)
            assert row.data["account"] == "2026 Example St."
            assert row.revision == 1
        assert session.get(Candidate, entered).status == "entered"
        assert session.get(Candidate, override).status == "approved"
        assert len(list(session.scalars(select(Audit).where(Audit.action == "latest_account_default")))) == 1


def test_account_request_requires_confirmation_target_and_valid_name(auth, device):
    assert request_account(auth, confirmed=False).status_code == 422
    assert request_account(auth, "2026 Example st.").status_code == 409
    for name in (" ", "Bad\nAccount", "[Transfer]", "Not QIF 😀", "x" * 40):
        assert request_account(auth, name).status_code == 422
    request_id = str(uuid4())
    response = request_account(auth, " New Account ", request_id=request_id)
    assert response.status_code == 200, response.text
    request = response.json()
    assert request["name"] == "New Account"
    assert request["status"] == "queued"
    assert "support" in request["message"]
    assert request_account(auth, "New Account", request_id=request_id).json()["id"] == request_id
    assert request_account(auth, "new account").json()["id"] == request_id
    for kind in ("Cash", "CCard", "Bill"):
        assert request_account(auth, "New Account", account_type=kind).status_code == 422
    assert request_account(auth, "Different", request_id=request_id).status_code == 409
    assert auth.post("/api/account-requests", headers=device, json={}).status_code == 403
    ref = auth.get("/api/catalog").json()
    assert request["name"] not in [a["name"] for a in ref["accounts"]]
    assert ref["account_requests"][0]["name"] == request["name"]


def test_account_request_requires_configured_file(auth):
    assert request_account(auth).status_code == 409


def test_creation_proof_capability_owner_and_no_repeat_attempt(auth, device, db, photo):
    request = request_account(auth).json()
    base = f"/api/device/account-requests/{request['id']}"
    owner = str(uuid4())
    event_id = fresh(auth, device)
    assert (
        auth.post(base + "/claim", headers=device, json={"owner": owner, "event_id": event_id}).status_code
        == 409
    )
    auth.post(
        "/api/device/heartbeat",
        headers=device,
        json={"protocol": 1, "file_identity": FILE, "account_creation": 1},
    )
    claim = auth.post(base + "/claim", headers=device, json={"owner": owner, "event_id": event_id})
    assert claim.status_code == 200, claim.text
    assert claim.json()["status"] == "creating"
    assert auth.post("/api/entry", json={"request_id": str(uuid4()), "kind": "entry"}).status_code == 409
    assert auth.post(base + "/attempt", headers=device, json={"owner": str(uuid4())}).status_code == 409
    assert auth.post(base + "/attempt", headers=device, json={"owner": owner}).json()["may_create"] is True
    assert auth.post(base + "/attempt", headers=device, json={"owner": owner}).json()["may_create"] is False
    assert (
        auth.post(base + "/complete", headers=device, json={"owner": owner, "event_id": event_id}).status_code
        == 409
    )
    missing = fresh(auth, device)
    assert (
        auth.post(base + "/complete", headers=device, json={"owner": owner, "event_id": missing}).status_code
        == 409
    )
    wrong_type = fresh(auth, device, QIF + "!Account\nN2028 Example St.\nTCash\n^\n")
    assert (
        auth.post(
            base + "/complete", headers=device, json={"owner": owner, "event_id": wrong_type}
        ).status_code
        == 409
    )
    proof = fresh(auth, device, QIF + "!Account\nN2028 Example St.\nTBank\n^\n")
    result = auth.post(base + "/complete", headers=device, json={"owner": owner, "event_id": proof})
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "complete"
    assert (
        auth.post(base + "/complete", headers=device, json={"owner": owner, "event_id": proof}).status_code
        == 200
    )
    assert auth.get("/api/device/account-requests", headers=device).json() == []


def test_request_already_present_in_fresh_export_is_not_created(auth, device):
    request = request_account(auth).json()
    auth.post(
        "/api/device/heartbeat",
        headers=device,
        json={"protocol": 1, "file_identity": FILE, "account_creation": 1},
    )
    proof = fresh(auth, device, QIF + "!Account\nN2028 Example St.\nTBank\n^\n")
    base = f"/api/device/account-requests/{request['id']}"
    owner = str(uuid4())
    assert (
        auth.post(base + "/claim", headers=device, json={"owner": owner, "event_id": proof}).json()["status"]
        == "complete"
    )
    assert auth.post(base + "/attempt", headers=device, json={"owner": owner}).status_code == 409


def test_claim_can_refresh_before_attempt_and_rejects_changed_file(auth, device):
    request = request_account(auth).json()
    owner = str(uuid4())
    base = f"/api/device/account-requests/{request['id']}"
    auth.post(
        "/api/device/heartbeat",
        headers=device,
        json={"protocol": 1, "file_identity": FILE, "account_creation": 1},
    )
    pre = fresh(auth, device)
    assert (
        auth.post(base + "/claim", headers=device, json={"owner": owner, "event_id": pre}).json()["status"]
        == "creating"
    )
    # Another fresh export supersedes the pre-event before the UI has submitted.
    newer = fresh(auth, device)
    assert auth.post(base + "/attempt", headers=device, json={"owner": owner}).status_code == 409
    assert (
        auth.post(base + "/claim", headers=device, json={"owner": owner, "event_id": newer}).json()["status"]
        == "creating"
    )
    auth.post(
        "/api/device/heartbeat",
        headers=device,
        json={"protocol": 1, "file_identity": "b" * 64, "account_creation": 1},
    )
    assert auth.post(base + "/attempt", headers=device, json={"owner": owner}).status_code == 409
    auth.post(
        "/api/device/heartbeat",
        headers=device,
        json={"protocol": 1, "file_identity": FILE, "account_creation": 1},
    )
    assert auth.post(base + "/attempt", headers=device, json={"owner": owner}).json()["may_create"] is True


def test_pending_account_cannot_be_approved(auth, device, db, photo):
    row = ready(auth, db, photo)
    request_account(auth)
    response = action(
        auth, row, "save", {**fields(row), "account": "2028 Example St.", "account_override": True}
    )
    assert response.status_code == 200
    row = response.json()[0]
    assert "Account creation is pending Windows verification" in row["issues"]
    assert action(auth, row, "approve").status_code == 422


def test_register_searchable_choices_and_confirmed_account_creation(browser_url, auth, device, db, photo):
    candidate = ready(auth, db, photo)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        login(page, browser_url)
        row = page.locator(f'tr[data-transaction-id="{candidate["id"]}"]')
        expect(cell(row, "account")).to_contain_text("2027 Example St.")
        cell(row, "account").get_by_role("button").click()
        options = page.get_by_role("listbox", name="Edit Account options")
        expect(options.get_by_role("option", name="2026 Example St.", exact=True)).to_be_visible()
        page.get_by_role("combobox", name="Edit Account").fill("Example")
        expect(options.get_by_role("option", name="2026 Example St.", exact=True)).to_be_visible()
        expect(options.get_by_role("option", name="R&K Properties 2026", exact=True)).to_have_count(0)
        options.get_by_role("option", name="2026 Example St.", exact=True).click()
        expect(cell(row, "account")).to_contain_text("2026 Example St.")
        edit(row, "account", "x" * 40)
        page.keyboard.press("Enter")
        too_long = page.get_by_role("dialog", name="Create Quicken account")
        expect(too_long.get_by_role("alert")).to_contain_text("39 characters")
        expect(too_long.get_by_role("button", name="Confirm account creation")).to_be_disabled()
        too_long.get_by_role("button", name="Close account creation").click()
        edit(row, "date", "2024-01-01")
        page.keyboard.press("Enter")
        expect(cell(row, "account")).to_contain_text("2026 Example St.")
        edit(row, "account", "2028 New Rental")
        page.keyboard.press("Enter")
        dialog = page.get_by_role("dialog", name="Create Quicken account")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_role("combobox")).to_have_count(0)
        expect(dialog).to_contain_text("Bank account")
        assert auth.get("/api/catalog").json()["account_requests"] == []
        dialog.get_by_role("button", name="Close account creation", exact=True).click()
        expect(dialog).to_have_count(0)
        expect(cell(row, "account")).to_contain_text("2026 Example St.")
        edit(row, "account", "2028 New Rental")
        page.keyboard.press("Enter")
        dialog.get_by_role("button", name="Confirm account creation", exact=True).click()
        expect(dialog).to_have_count(0)
        expect(cell(row, "account")).to_contain_text("Waiting for a Windows client")
        assert auth.get("/api/catalog").json()["account_requests"][0]["account_type"] == "Bank"
        expect(row.locator(".register-status")).to_contain_text("pending Windows verification")
        page.reload()
        expect(cell(row, "account")).to_contain_text("2028 New Rental")
        expect(cell(row, "account")).to_contain_text("Waiting for a Windows client")
        request = auth.get("/api/catalog").json()["account_requests"][0]
        auth.post(
            "/api/device/heartbeat",
            headers=device,
            json={
                "protocol": 1,
                "file_identity": FILE,
                "account_creation": 1,
            },
        )
        owner = str(uuid4())
        base = f"/api/device/account-requests/{request['id']}"
        proof = fresh(auth, device)
        auth.post(
            base + "/claim", headers=device, json={"owner": owner, "event_id": proof}
        ).raise_for_status()
        auth.post(base + "/attempt", headers=device, json={"owner": owner}).raise_for_status()
        proof = fresh(auth, device, QIF + "!Account\nN2028 New Rental\nTBank\n^\n")
        auth.post(
            base + "/complete", headers=device, json={"owner": owner, "event_id": proof}
        ).raise_for_status()
        expect(cell(row, "account")).not_to_contain_text("Waiting", timeout=10000)
        expect(row.locator(".register-status")).not_to_contain_text("pending Windows verification")
        assert auth.get("/api/transactions").json()[0]["status"] == "review"
        page.screenshot(path=".local/account-creation-register.png", full_page=True)
        browser.close()
