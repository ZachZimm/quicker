"""Register editing against the real review API, including refresh and conflict races."""

from playwright.sync_api import expect, sync_playwright
from test_workflow import action, fields, ready


def login(page, url):
    page.goto(url)
    page.get_by_label("Username", exact=True).fill("admin")
    page.get_by_label("Password", exact=True).fill("test-password-12345")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.get_by_role("heading", name="Review transactions")).to_be_visible()


def cell(row, field):
    return row.locator(f'td[data-field="{field}"]')


def edit(row, field, value):
    cell(row, field).get_by_role("button").click()
    cell(row, field).locator("input").fill(value)


def test_register_edit_route_approve_remove_restore_and_source(browser_url, auth, db, photo):
    candidate = ready(auth, db, photo)
    from quicker.db import Setting
    with db.write() as session:
        setting = session.get(Setting, "catalog")
        setting.value = {**setting.value, "tags": setting.value["tags"] + [{"name": "2 Example"}]}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        login(page, browser_url)
        row = page.locator(f'tr[data-transaction-id="{candidate["id"]}"]')
        edit(row, "date", "2027-02-15")
        page.keyboard.press("Tab")
        expect(cell(row, "payee").get_by_role("button")).to_be_focused()
        expect(cell(row, "account")).to_contain_text("2027 Example St.")
        edit(row, "unit", "2 Example")
        page.keyboard.press("Tab")
        edit(row, "category", "Utilities")
        page.keyboard.press("Enter")
        expect(row.get_by_role("status")).to_be_empty()
        saved = auth.get("/api/transactions").json()[0]
        assert saved["data"]["date"] == "2027-02-15"
        assert saved["data"]["unit"] == "2 Example"
        assert saved["data"]["account"] == "2027 Example St."
        assert saved["status"] == "review"
        # Cancelling a property edit also restores its derived unit and account.
        edit(row, "property", "R&K Properties")
        expect(cell(row, "unit")).to_contain_text("Unit unresolved")
        page.keyboard.press("Escape")
        expect(cell(row, "unit")).to_contain_text("2 Example")
        expect(cell(row, "account")).to_contain_text("2027 Example St.")
        edit(row, "account", "2026 Example St.")
        page.keyboard.press("Enter")
        expect(row.get_by_role("status")).to_be_empty()
        expect(cell(row, "account")).to_contain_text("Override")
        edit(row, "account", "Use automatic account")
        page.keyboard.press("Enter")
        expect(row.get_by_role("status")).to_be_empty()
        expect(cell(row, "account")).to_contain_text("2027 Example St.")
        # Detail view includes the saved grid draft and the original document.
        row.get_by_role("button", name="Details and source", exact=False).click()
        expect(page.get_by_label("Payment / transaction date", exact=True)).to_have_value("2027-02-15")
        expect(page.get_by_label("Unit", exact=True)).to_have_value("2 Example")
        expect(page.locator(".source-viewer img")).to_be_visible()
        page.get_by_role("button", name="Close transaction").click()
        expect(row.get_by_role("button", name="Details and source", exact=False)).to_be_focused()
        # Approval flushes a draft before submitting the new revision.
        row.get_by_role("checkbox").check()
        edit(row, "amount_minor", "-123.45")
        page.get_by_role("button", name="Approve", exact=True).click()
        page.get_by_role("tab", name="Approved", exact=False).click()
        expect(row).to_contain_text("-$123.45")
        # Editing an approved row sends it back to review.
        edit(row, "payee", "Revised Energy")
        page.keyboard.press("Enter")
        expect(row.get_by_role("status")).to_be_empty()
        expect(row).to_contain_text("Ready to approve")
        page.get_by_role("tab", name="Needs review", exact=False).click()
        # No checkbox or detail drawer is required to remove or restore.
        row.get_by_role("button", name="Remove Revised Energy", exact=True).click()
        expect(row).to_have_count(0)
        page.get_by_role("tab", name="Removed", exact=False).click()
        expect(row).to_be_visible()
        row.get_by_role("button", name="Restore Revised Energy", exact=True).click()
        page.get_by_role("tab", name="Needs review", exact=False).click()
        expect(row).to_be_visible()
        expect(row.get_by_role("checkbox")).to_be_enabled()
        page.screenshot(path=".local/register-desktop.png", full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        expect(row.get_by_role("button", name="Details and source", exact=False)).to_be_visible()
        browser.close()


def test_register_preserves_drafts_across_poll_navigation_and_conflict(browser_url, auth, db, photo):
    candidate = ready(auth, db, photo)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        login(page, browser_url)
        row = page.locator(f'tr[data-transaction-id="{candidate["id"]}"]')
        edit(row, "payee", "My pending correction")
        # Another browser changes a different field; the poll must not overwrite this draft.
        response = action(auth, candidate, "save", {**fields(candidate), "memo": "Other browser"})
        assert response.status_code == 200
        with page.expect_response("**/api/transactions"):
            page.wait_for_timeout(6100)
        expect(cell(row, "payee").get_by_role("textbox")).to_have_value("My pending correction")
        expect(cell(row, "payee").get_by_role("textbox")).to_be_focused()
        page.keyboard.press("Enter")
        expect(row).to_contain_text("Save failed")
        expect(row).to_contain_text("another tab")
        page.get_by_role("navigation").get_by_role("button", name="Settings", exact=True).click()
        page.get_by_role("navigation").get_by_role("button", name="Review", exact=False).click()
        expect(row).to_contain_text("My pending correction")
        assert auth.get("/api/transactions").json()[0]["data"]["payee"] == "Example Energy"
        row.get_by_role("button", name="Discard edits / reload", exact=True).click()
        expect(row).not_to_contain_text("Save failed")
        expect(cell(row, "payee")).to_contain_text("Example Energy")
        row.get_by_role("button", name="Details and source", exact=False).click()
        expect(page.get_by_role("dialog").get_by_role("textbox", name="Memo", exact=True)).to_have_value("Other browser")
        browser.close()


def test_register_invalid_values_escape_and_failed_save_retry(browser_url, auth, db, photo):
    candidate = ready(auth, db, photo)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        login(page, browser_url)
        row = page.locator(f'tr[data-transaction-id="{candidate["id"]}"]')
        edit(row, "amount_minor", "-12.345")
        page.keyboard.press("Enter")
        expect(cell(row, "amount_minor").get_by_role("textbox")).to_have_attribute("aria-invalid", "true")
        assert auth.get("/api/transactions").json()[0]["revision"] == candidate["revision"]
        page.keyboard.press("Escape")
        expect(cell(row, "amount_minor")).to_contain_text("-$214.55")
        edit(row, "date", "2026-02-30")
        page.keyboard.press("Enter")
        expect(cell(row, "date").get_by_role("textbox")).to_have_attribute("aria-invalid", "true")
        page.keyboard.press("Escape")
        # Transport failure retains edits for a deliberate retry.
        page.route("**/api/review", lambda route: route.fulfill(status=503, content_type="application/json", body='{"detail":"Temporarily unavailable"}'))
        edit(row, "payee", "Retry Energy")
        page.keyboard.press("Enter")
        expect(row).to_contain_text("Save failed")
        expect(row).to_contain_text("Retry Energy")
        page.unroute("**/api/review")
        row.get_by_role("button", name="Save row", exact=True).click()
        expect(row).not_to_contain_text("Save failed")
        expect(row.get_by_role("status")).to_be_empty()
        assert auth.get("/api/transactions").json()[0]["data"]["payee"] == "Retry Energy"
        browser.close()


def test_register_serializes_edits_during_save_and_removes_invalid_draft(browser_url, auth, db, photo):
    candidate = ready(auth, db, photo)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        login(page, browser_url)
        row = page.locator(f'tr[data-transaction-id="{candidate["id"]}"]')
        held = []
        released = False

        def intercept(route):
            if released:
                route.continue_()
            else:
                held.append(route)

        page.route("**/api/review", intercept)
        # Typing on a selected cell starts editing without dropping the first character.
        cell(row, "payee").get_by_role("button").focus()
        page.keyboard.type("Pending Energy")
        expect(cell(row, "payee").get_by_role("textbox")).to_have_value("Pending Energy")
        page.keyboard.press("Enter")
        expect(row).to_contain_text("Saving")
        edit(row, "amount_minor", "-88.19")
        page.keyboard.press("Enter")
        assert len(held) == 1
        # Let the first save complete; the second edit must use its returned revision.
        released = True
        held[0].continue_()
        expect(row.get_by_role("status")).to_be_empty()
        saved = auth.get("/api/transactions").json()[0]
        assert saved["data"]["payee"] == "Pending Energy"
        assert saved["data"]["amount_minor"] == -8819
        assert saved["revision"] == candidate["revision"] + 2
        edit(row, "amount_minor", "invalid")
        row.get_by_role("button", name="Remove Pending Energy", exact=True).click()
        expect(row).to_have_count(0)
        removed = auth.get("/api/transactions").json()[0]
        assert removed["status"] == "removed"
        assert removed["data"]["amount_minor"] == -8819
        page.get_by_role("tab", name="Removed", exact=False).click()
        expect(cell(row, "payee").get_by_role("button")).to_have_attribute("aria-disabled", "true")
        row.get_by_role("button", name="Details and source", exact=False).click()
        expect(page.get_by_role("button", name="Restore to review")).to_be_visible()
        expect(page.get_by_role("button", name="Save & approve")).to_have_count(0)
        browser.close()
