"""Real browser acceptance checks against an isolated local application."""

from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from quicker.worker import process_one
from test_workflow import InvoiceAdapter


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
        page.get_by_role("button", name="Upload documents", exact=True).first.click()
        page.locator("input[type=file]").set_input_files(
            {"name": "test-invoice.png", "mimeType": "image/png", "buffer": photo}
        )
        page.get_by_role("button", name="Upload 1 photo", exact=True).click()
        expect(page.get_by_role("heading", name="Source documents")).to_be_visible()
        assert process_one(db, InvoiceAdapter)
        page.get_by_role("button", name="Review", exact=False).first.click()
        page.reload()
        page.get_by_role("button", name="Example Energy", exact=True).click()
        page.keyboard.press("Escape")
        expect(page.get_by_role("dialog")).to_have_count(0)
        page.get_by_role("button", name="Example Energy", exact=True).click()
        page.get_by_role("button", name="Save & approve").click()
        expect(page.get_by_role("dialog").get_by_role("alert")).to_contain_text("date")
        page.get_by_label("Payment / transaction date", exact=True).fill("2026-08-10")
        page.get_by_role("button", name="Save & approve").click()
        expect(page.get_by_role("dialog")).to_have_count(0)
        page.get_by_role("tab", name="Approved").click()
        expect(page.get_by_role("button", name="Example Energy", exact=True)).to_be_visible()
        page.get_by_label("Select Example Energy", exact=True).check()
        page.get_by_role("button", name="Remove", exact=True).click()
        page.get_by_role("tab", name="Removed").click()
        page.get_by_label("Select Example Energy", exact=True).check()
        page.get_by_role("button", name="Restore", exact=True).click()
        page.get_by_role("tab", name="Needs review").click()
        expect(page.get_by_role("button", name="Example Energy", exact=True)).to_be_visible()
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
        page.get_by_role("button", name="Spectrum 855-707-7328 MO", exact=True).click()
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
        expect(page.locator(".property-directory")).to_contain_text("1008 Bell")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        browser.close()
