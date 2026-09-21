"""Real browser acceptance checks against an isolated local application."""

from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from quicker.worker import process_one
from test_workflow import InvoiceAdapter


def test_popup_backdrops_dismiss_only_outside_and_when_not_submitting(browser_url, auth, db, photo):
    from test_workflow import upload

    upload(auth, photo)
    assert process_one(db, InvoiceAdapter)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(browser_url)
        page.get_by_label("Username", exact=True).fill("admin")
        page.get_by_label("Password", exact=True).fill("test-password-12345")
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.get_by_role("button", name="Upload & analyze", exact=True).first.click()
        dialog = page.get_by_role("dialog", name="Upload & analyze")
        dialog.get_by_role("heading").click()
        expect(dialog).to_be_visible()
        # Dragging from inside to outside must not be mistaken for a backdrop click.
        heading = dialog.get_by_role("heading").bounding_box()
        page.mouse.move(heading["x"] + 5, heading["y"] + 5)
        page.mouse.down()
        page.mouse.move(5, 5)
        page.mouse.up()
        expect(dialog).to_be_visible()
        page.locator(".modal-overlay").click(position={"x": 5, "y": 5})
        expect(dialog).to_have_count(0)
        page.get_by_role("button", name="Upload & analyze", exact=True).first.click()
        held = []
        page.route("**/api/upload", lambda route: held.append(route))
        page.locator("input[type=file]").set_input_files(
            {"name": "waiting.png", "mimeType": "image/png", "buffer": photo}
        )
        dialog.get_by_role("button", name="Upload & analyze", exact=True).click()
        expect(dialog.get_by_role("button", name="Close upload")).to_be_disabled()
        page.locator(".modal-overlay").click(position={"x": 5, "y": 5})
        expect(dialog).to_be_visible()
        assert len(held) == 1
        held[0].fulfill(status=503, json={"detail": "Test upload interruption"})
        expect(dialog.get_by_role("button", name="Close upload")).to_be_enabled()
        page.locator(".modal-overlay").click(position={"x": 5, "y": 5})
        expect(dialog).to_have_count(0)
        page.get_by_role("button", name="Details and source for Example Energy", exact=True).click()
        page.get_by_role("dialog").get_by_role("heading").first.click()
        expect(page.get_by_role("dialog")).to_be_visible()
        page.locator(".drawer-overlay").click(position={"x": 5, "y": 5})
        expect(page.get_by_role("dialog")).to_have_count(0)
        page.get_by_role("navigation").get_by_role("button", name="Documents", exact=True).click()
        page.get_by_role("button", name="Open document", exact=False).click()
        page.locator(".modal-overlay").click(position={"x": 5, "y": 5})
        expect(page.get_by_role("dialog")).to_have_count(0)
        browser.close()


def test_browser_model_outage_retry_and_recovery(browser_url, auth, db, photo):
    from quicker.analysis_status import heartbeat
    from test_model_availability import Offline
    from test_workflow import upload

    upload(auth, photo)
    assert process_one(db, Offline)
    heartbeat(db, "test-worker", 0, 1)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(browser_url)
        page.get_by_label("Username", exact=True).fill("admin")
        page.get_by_label("Password", exact=True).fill("test-password-12345")
        page.get_by_role("button", name="Sign in", exact=True).click()
        panel = page.get_by_role("region", name="Document analysis status")
        expect(panel).to_contain_text("Model connection: Offline")
        expect(panel).to_contain_text("Next recovery check after")
        page.get_by_role("navigation").get_by_role("button", name="Documents", exact=True).click()
        expect(page.locator(".document-card")).to_contain_text("Model server is offline")
        panel.get_by_role("button", name="Retry now", exact=True).click()
        expect(panel).to_contain_text("Retry requested")
        assert process_one(db, InvoiceAdapter)
        expect(page.locator(".document-card .badge")).to_have_text("Analysis complete", timeout=10000)
        expect(panel).not_to_contain_text("None recorded yet", timeout=10000)
        browser.close()


def test_browser_requests_entry_and_displays_verified_result(browser_url, auth, db, tmp_path):
    from quicker_client.operations import Operations
    from test_desktop_operations import FILE, ApiCompanion, FakeQuicken, candidate

    candidate(db)
    code = auth.post("/api/pair-code").json()["code"]
    pair = auth.post("/api/pair", json={"name": "Windows test", "code": code}).json()
    header = {"Authorization": "Bearer " + pair["token"]}
    auth.post("/api/device/heartbeat", headers=header, json={"protocol": 1, "file_identity": FILE, "file_name": "test.QDF", "ready": True})
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(browser_url)
        page.get_by_label("Username", exact=True).fill("admin")
        page.get_by_label("Password", exact=True).fill("test-password-12345")
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.get_by_role("button", name="Windows companion", exact=True).click()
        button = page.get_by_role("button", name="Enter approved transactions", exact=False)
        expect(button).to_be_enabled()
        button.click()
        expect(page.get_by_text("Waiting for Quicken", exact=True)).to_be_visible()
        expect(button).to_be_disabled()
        pending = auth.get("/api/device/operations", headers=header).json()
        result = Operations(ApiCompanion(auth, header), FakeQuicken(), tmp_path).process(pending[0])
        assert result["status"] == "complete"
        page.reload()
        page.get_by_role("button", name="Windows companion", exact=True).click()
        expect(page.get_by_text("Fresh Quicken export verified", exact=True)).to_be_visible()
        page.screenshot(path=".local/windows-entry-browser.png", full_page=True)
        page.get_by_role("navigation", name="Main navigation").get_by_role("button", name="Review", exact=False).click()
        page.get_by_role("tab", name="Entered", exact=False).click()
        page.get_by_role("button", name="Details and source for Desktop Test", exact=True).click()
        expect(page.get_by_text("Verified in a fresh Quicken export.", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Save & approve")).to_have_count(0)
        browser.close()


def test_browser_upload_review_remove_restore_and_settings(browser_url, db, photo):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(browser_url)
        page.get_by_label("Username", exact=True).fill("admin")
        page.get_by_label("Password", exact=True).fill("test-password-12345")
        page.get_by_role("button", name="Sign in", exact=True).click()
        expect(page.get_by_role("heading", name="Review transactions")).to_be_visible()
        page.get_by_role("button", name="Upload & analyze", exact=True).first.click()
        page.locator("input[type=file]").set_input_files(
            {"name": "test-invoice.png", "mimeType": "image/png", "buffer": photo}
        )
        page.get_by_role("dialog").get_by_role("button", name="Upload & analyze", exact=True).click()
        expect(page.get_by_role("heading", name="Source documents")).to_be_visible()
        assert process_one(db, InvoiceAdapter)
        page.get_by_role("button", name="Review", exact=False).first.click()
        page.reload()
        page.get_by_role("button", name="Details and source for Example Energy", exact=True).click()
        page.keyboard.press("Escape")
        expect(page.get_by_role("dialog")).to_have_count(0)
        page.get_by_role("button", name="Details and source for Example Energy", exact=True).click()
        page.get_by_role("button", name="Save & approve").click()
        expect(page.get_by_role("dialog").get_by_role("alert")).to_contain_text("date")
        page.get_by_label("Payment / transaction date", exact=True).fill("2026-08-10")
        page.get_by_role("button", name="Save & approve").click()
        expect(page.get_by_role("dialog")).to_have_count(0)
        page.get_by_role("tab", name="Approved").click()
        expect(page.get_by_role("button", name="Details and source for Example Energy", exact=True)).to_be_visible()
        page.get_by_label("Select Example Energy", exact=True).check()
        page.get_by_role("button", name="Remove", exact=True).click()
        page.get_by_role("tab", name="Removed").click()
        page.get_by_label("Select Example Energy", exact=True).check()
        page.get_by_role("button", name="Restore", exact=True).click()
        page.get_by_role("tab", name="Needs review").click()
        expect(page.get_by_role("button", name="Details and source for Example Energy", exact=True)).to_be_visible()
        Path(".local").mkdir(exist_ok=True)
        page.screenshot(path=".local/qa-desktop.png", full_page=True)
        page.get_by_role("button", name="Settings", exact=True).click()
        expect(page.get_by_label("Host", exact=True)).to_have_value("localhost")
        page.get_by_label("Port", exact=True).fill("1235")
        page.get_by_role("button", name="Save model settings").click()
        expect(page.get_by_role("status")).to_contain_text("Model settings saved")
        page.reload()
        page.get_by_role("button", name="Settings", exact=True).click()
        expect(page.get_by_label("Port", exact=True)).to_have_value("1235")
        page.get_by_role("button", name="Windows companion", exact=True).click()
        page.get_by_role("button", name="Create pairing code").click()
        expect(page.locator(".pair-code code")).not_to_be_empty()
        expect(page.get_by_role("button", name="Enter approved transactions", exact=True)).to_be_disabled()
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="Review", exact=False).first.click()
        expect(page.get_by_role("heading", name="Review transactions")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=".local/qa-phone.png", full_page=True)
        assert not errors
        browser.close()


def test_browser_reference_coverage_and_historical_duplicate_review(browser_url, auth, db, photo):
    from quicker.catalog import import_catalog
    from test_reference import HISTORY, SpectrumAdapter
    from test_workflow import upload

    with db.write() as session:
        import_catalog(session, HISTORY)
    upload(auth, photo)
    assert process_one(db, SpectrumAdapter)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(browser_url)
        page.get_by_label("Username", exact=True).fill("admin")
        page.get_by_label("Password", exact=True).fill("test-password-12345")
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.get_by_role("button", name="Details and source for Spectrum 855-707-7328 MO", exact=True).click()
        expect(page.get_by_role("heading", name="Matching Quicken transactions")).to_be_visible()
        expect(page.locator(".history-match")).to_have_count(2)
        expect(page.locator(".historical-matches")).to_contain_text("R&K Properties 2026")
        page.get_by_label("Property or business", exact=True).select_option("R&K Properties")
        page.get_by_label("Category", exact=True).select_option("Internet-Reno")
        page.get_by_role("button", name="Save & approve").click()
        expect(page.get_by_role("dialog").get_by_role("alert")).to_contain_text("duplicate")
        page.get_by_label("I checked the possible duplicate", exact=False).check()
        page.get_by_role("button", name="Save & approve").click()
        expect(page.get_by_role("dialog")).to_have_count(0)
        page.get_by_role("button", name="Settings", exact=True).click()
        expect(page.get_by_role("heading", name="Imported transaction coverage")).to_be_visible()
        expect(page.locator(".reference-coverage")).to_contain_text("4 transactions")
        expect(page.locator(".reference-coverage")).to_contain_text("1 with blank payees retained")
        expect(page.locator(".reference-coverage")).to_contain_text("2026-05-07")
        expect(page.locator(".property-directory")).to_contain_text("4100 Example")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        browser.close()


def test_browser_rental_selection_preserves_expense_tag(browser_url, auth, db, photo):
    from quicker.db import Setting
    from test_units import invoice

    with db.write() as session:
        setting = session.get(Setting, 'catalog')
        setting.value = {**setting.value, 'tags': setting.value['tags'] + [
            {'name': '4100 Example'}, {'name': '2 Example'}, {'name': 'Utilities'},
        ]}
    invoice(auth, db, photo)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        page.goto(browser_url)
        page.get_by_label('Username', exact=True).fill('admin')
        page.get_by_label('Password', exact=True).fill('test-password-12345')
        page.get_by_role('button', name='Sign in', exact=True).click()
        expect(page.get_by_text('Unit unresolved', exact=True)).to_be_visible()
        page.get_by_role('button', name='Details and source for Example Energy', exact=True).click()
        expect(page.get_by_label('Unit', exact=True)).to_have_value('unresolved')
        expect(page.get_by_text('Printed utility account: 001-234', exact=True)).to_be_visible()
        page.get_by_label('Unit', exact=True).select_option('2 Example')
        expect(page.get_by_text('Quicken tags: Utilities, 2 Example', exact=True)).to_be_visible()
        page.get_by_role('button', name='Save for review', exact=True).click()
        expect(page.get_by_role('dialog')).to_have_count(0)
        page.get_by_role('button', name='Details and source for Example Energy', exact=True).click()
        expect(page.get_by_label('Unit', exact=True)).to_have_value('2 Example')
        expect(page.get_by_role('combobox', name='Destination account', exact=True)).to_have_value('2027 Example St.')
        page.get_by_label('Unit', exact=True).select_option('whole_property')
        expect(page.get_by_text('Quicken tags: Utilities', exact=True)).to_be_visible()
        page.get_by_label('Property or business', exact=True).select_option('R&K Properties')
        expect(page.get_by_label('Unit', exact=True)).to_have_value('unresolved')
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.screenshot(path='.local/unit-review-phone.png', full_page=True, animations='disabled')
        browser.close()


def test_browser_corrections_and_existing_link(browser_url, auth, db, photo):
    from quicker.reference_exports import store_export
    from test_completion import ExtraRowAdapter, export
    from test_workflow import ready

    ready(auth, db, photo)
    store_export(db, export(account="2026 Example St.", amount="214.55", day="7/19'26", payee="Example Energy").encode(), "test.QIF")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(browser_url)
        page.get_by_label("Username", exact=True).fill("admin")
        page.get_by_label("Password", exact=True).fill("test-password-12345")
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.get_by_role("button", name="Details and source for Example Energy", exact=True).click()
        page.get_by_label("Payment / transaction date", exact=True).fill("2026-07-19")
        page.get_by_role("button", name="Save for review", exact=True).click()
        page.get_by_role("button", name="Details and source for Example Energy", exact=True).click()
        page.get_by_role("button", name="Already in Quicken", exact=True).click()
        page.get_by_role("tab", name="Already in Quicken").click()
        expect(page.get_by_role("button", name="Details and source for Example Energy", exact=True)).to_be_visible()
        page.get_by_role("button", name="Details and source for Example Energy", exact=True).click()
        expect(page.get_by_role("button", name="Restore to review")).to_be_visible()
        page.keyboard.press("Escape")
        page.get_by_role("button", name="Documents", exact=False).first.click()
        page.get_by_role("button", name="Open document", exact=False).first.click()
        page.get_by_label("Source description", exact=True).fill("Missed paper receipt")
        page.get_by_role("button", name="Add missed transaction", exact=True).click()
        expect(page.get_by_label("Source description", exact=True)).to_have_value("")
        page.get_by_role("button", name="Analyze again", exact=True).click()
        expect(page.get_by_role("dialog")).to_have_count(0)
        assert process_one(db, ExtraRowAdapter)
        page.reload()
        page.get_by_role("button", name="Documents", exact=False).first.click()
        page.get_by_role("button", name="Open document", exact=False).first.click()
        page.get_by_role("button", name="Compare analysis", exact=True).click()
        expect(page.get_by_role("heading", name="Analysis comparison")).to_be_visible()
        page.get_by_label("Page 1: Missed Merchant", exact=False).check()
        page.get_by_role("button", name="Add selected missing rows").click()
        expect(page.get_by_role("button", name="Add selected missing rows")).to_be_disabled()
        expect(page.get_by_text("Saved: Missed Merchant", exact=False)).to_be_visible()
        assert len(auth.get("/api/transactions").json()) == 3
        page.get_by_role("button", name="Close document", exact=True).click()
        page.get_by_role("button", name="Settings", exact=True).click()
        expect(page.get_by_role("link", name="Download backup", exact=True)).to_be_visible()
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors
        browser.close()


def test_http_lan_upload_and_repeated_manual_corrections(browser_url, db, photo):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--host-resolver-rules=MAP quicker.test 127.0.0.1", "--no-proxy-server",
        ])
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(browser_url.replace("127.0.0.1", "quicker.test"))
        assert page.evaluate("isSecureContext") is False
        assert page.evaluate("typeof crypto.randomUUID") == "undefined"
        page.get_by_label("Username", exact=True).fill("admin")
        page.get_by_label("Password", exact=True).fill("test-password-12345")
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.get_by_role("button", name="Upload & analyze", exact=True).first.click()
        page.locator("input[type=file]").set_input_files(
            {"name": "lan-invoice.png", "mimeType": "image/png", "buffer": photo}
        )
        page.get_by_role("dialog").get_by_role("button", name="Upload & analyze", exact=True).click()
        expect(page.get_by_role("heading", name="Source documents")).to_be_visible()
        assert process_one(db, InvoiceAdapter)
        page.get_by_role("button", name="Open document", exact=False).first.click()
        for source in ("First missed receipt", "Second missed receipt"):
            page.get_by_label("Source description", exact=True).fill(source)
            page.get_by_role("button", name="Add missed transaction", exact=True).click()
            expect(page.get_by_label("Source description", exact=True)).to_have_value("")
        from quicker.db import Candidate
        from sqlalchemy import select
        with db.session() as session:
            rows = list(session.scalars(select(Candidate)))
            assert len(rows) == 3
            assert {r.data["source"] for r in rows if r.data.get("manual")} == {
                "First missed receipt", "Second missed receipt",
            }
        assert not errors
        browser.close()


def test_browser_keeps_login_after_closing_and_reopening(browser_url, tmp_path):
    import time

    with sync_playwright() as p:
        profile = tmp_path / "browser-profile"
        context = p.chromium.launch_persistent_context(str(profile), headless=True)
        page = context.new_page()
        page.goto(browser_url)
        page.get_by_label("Username", exact=True).fill("admin")
        page.get_by_label("Password", exact=True).fill("test-password-12345")
        page.get_by_role("button", name="Sign in", exact=True).click()
        expect(page.get_by_role("heading", name="Review transactions")).to_be_visible()
        cookie = next(c for c in context.cookies() if c["name"] == "quicker_session")
        assert cookie["expires"] > time.time() + 399 * 86400
        context.close()
        reopened = p.chromium.launch_persistent_context(str(profile), headless=True)
        page = reopened.new_page()
        page.goto(browser_url)
        expect(page.get_by_role("heading", name="Review transactions")).to_be_visible()
        expect(page.get_by_role("button", name="Sign in", exact=True)).to_have_count(0)
        reopened.close()


def test_browser_analysis_waiting_status_and_live_document_progress(browser_url, auth, db, photo, monkeypatch):
    import time

    from quicker.analysis_status import ModelConnection, heartbeat
    from quicker.db import Document, Job
    from sqlalchemy import select
    from test_workflow import upload

    uploaded = upload(auth, photo)
    doc_id = uploaded["document_ids"][0]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(browser_url)
        page.get_by_label("Username", exact=True).fill("admin")
        page.get_by_label("Password", exact=True).fill("test-password-12345")
        page.get_by_role("button", name="Sign in", exact=True).click()
        panel = page.get_by_role("region", name="Document analysis status")
        expect(panel).to_contain_text("Worker: No recent heartbeat")
        expect(panel).to_contain_text("Model connection: Unreachable")
        page.get_by_role("navigation").get_by_role("button", name="Documents", exact=True).click()
        expect(page.locator(".document-card .badge")).to_have_text("Waiting for analysis")
        expect(page.locator(".document-card")).to_contain_text("start automatically")
        page.get_by_role("button", name="Open document", exact=False).click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_contain_text("Waiting for analysis")
        expect(dialog.get_by_role("button", name="Analyze again")).to_have_count(0)
        heartbeat(db, "test-worker", 1, 1)
        monkeypatch.setattr(ModelConnection, "status", lambda self, cfg: {
            "state": "connected", "message": "Model server is reachable.",
            "checked_at": int(time.time()), "name": cfg.model,
        })
        with db.write() as session:
            session.get(Document, doc_id).status = "extracting"
            job = session.scalar(select(Job))
            job.status = "running"
            job.lease_until = 0
        # The open document follows polling updates instead of keeping a stale snapshot.
        expect(dialog.locator(".badge")).to_have_text("Analyzing", timeout=10000)
        expect(panel).to_contain_text("Model connection: Connected", timeout=10000)
        assert process_one(db, InvoiceAdapter)
        expect(dialog.locator(".badge")).to_have_text("Analysis complete", timeout=10000)
        expect(dialog.get_by_role("button", name="Analyze again")).to_be_visible()
        dialog.get_by_role("button", name="Analyze again").click()
        expect(dialog).to_have_count(0)
        expect(page.locator(".document-card .badge")).to_have_text("Waiting for analysis")
        assert len(auth.get("/api/transactions").json()) == 1
        page.screenshot(path=".local/document-analysis-status.png", full_page=True)
        browser.close()
