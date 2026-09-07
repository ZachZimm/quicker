import pytest
from quicker.contracts import Extraction
from quicker.profile import property_from_address
from quicker.worker import process_one
from test_workflow import InvoiceAdapter, action, fields, upload


@pytest.mark.parametrize(
    "street", ["1008 BELL ST", "1008 Bell Street", "1008 Bell St.", "1008 Bell St Unit 2"]
)
def test_verified_address_normalization(street):
    assert (
        property_from_address({"street": street, "city": "Reno", "state": "Nevada", "role": "service"})
        == "Bell St."
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"street": "1009 Bell St"},
        {"street": "Bell St"},
        {"street": "1008 Bell St Extra"},
        {"city": "Las Vegas"},
        {"state": "CA"},
        {"role": "mailing"},
        {"role": "unknown"},
    ],
)
def test_unverified_or_nonservice_address_does_not_assign(changes):
    assert (
        property_from_address(
            {"street": "1008 Bell St", "city": "Reno", "state": "NV", "role": "service", **changes}
        )
        is None
    )


@pytest.mark.parametrize("role", ["service", "job", "utility_customer"])
def test_address_assignment_keeps_payment_date_required_and_review_editable(auth, db, photo, role):
    class AddressAdapter(InvoiceAdapter):
        def extract(self, images, ref):
            return Extraction.model_validate(
                {
                    "document_type": "invoice",
                    "transactions": [
                        {
                            "kind": "invoice",
                            "payee": "Example Energy",
                            "amount": "214.55",
                            "date": "2025-07-19",
                            "date_basis": "invoice",
                            "property_address": {
                                "street": "1008 BELL ST",
                                "city": "Reno",
                                "state": "NV",
                                "role": role,
                            },
                        }
                    ],
                }
            )

    upload(auth, photo)
    assert process_one(db, AddressAdapter)
    row = auth.get("/api/transactions").json()[0]
    assert row["data"]["property"] == "Bell St."
    assert row["data"]["property_assignment"] == "document_address"
    assert row["data"]["property_address"]["street"] == "1008 BELL ST"
    assert row["data"]["tag"] is None
    assert row["data"]["date"] is None and row["data"]["account"] is None
    assert action(auth, row, "approve").status_code == 422
    saved = action(auth, row, "save", {**fields(row), "date": "2026-08-10"}).json()[0]
    assert saved["data"]["account"] == "2026 Bell St."
    changed = action(auth, saved, "save", {**fields(saved), "property": "R&K Properties"}).json()[0]
    assert changed["data"]["property"] == "R&K Properties"
    assert changed["data"]["account"] == "R&K Properties 2026"


def test_statement_address_never_assigns_all_card_purchases(auth, db, photo):
    class CardAdapter(InvoiceAdapter):
        def extract(self, images, ref):
            return Extraction.model_validate(
                {
                    "document_type": "credit_card",
                    "transactions": [
                        {
                            "kind": "purchase",
                            "payee": "Example Store",
                            "amount": "12.34",
                            "property": "Bell St.",
                            "property_address": {"street": "1008 Bell St", "role": "service"},
                        }
                    ],
                }
            )

    upload(auth, photo)
    assert process_one(db, CardAdapter)
    assert auth.get("/api/transactions").json()[0]["data"]["property"] is None


def test_mailing_address_does_not_validate_a_model_property_guess(auth, db, photo):
    class MailingAdapter(InvoiceAdapter):
        def extract(self, images, ref):
            return Extraction.model_validate(
                {
                    "document_type": "invoice",
                    "transactions": [
                        {
                            "kind": "invoice",
                            "payee": "Example Store",
                            "amount": "12.34",
                            "property": "Bell St.",
                            "property_address": {"street": "1008 Bell St", "role": "mailing"},
                        }
                    ],
                }
            )

    upload(auth, photo)
    assert process_one(db, MailingAdapter)
    row = auth.get("/api/transactions").json()[0]
    assert row["data"]["property"] is None
    assert "could not be matched" in row["warnings"][0]


def test_ambiguous_verified_address_stays_unassigned(monkeypatch):
    from quicker import profile

    monkeypatch.setattr(
        profile,
        "PROPERTIES",
        [
            {"name": name, "addresses": [{"street": "1008 Bell St", "city": "Reno", "state": "NV"}]}
            for name in ["Bell St.", "Another property"]
        ],
    )
    assert property_from_address({"street": "1008 Bell St", "role": "service"}) is None


def test_business_rule_has_priority_over_a_conflicting_service_address(auth, db, photo):
    class BusinessAddressAdapter(InvoiceAdapter):
        def extract(self, images, ref):
            return Extraction.model_validate(
                {
                    "document_type": "invoice",
                    "transactions": [
                        {
                            "kind": "invoice",
                            "payee": "STATE FARM MUTUAL AUTOMO",
                            "amount": "422.00",
                            "property_address": {"street": "1008 Bell St", "role": "service"},
                        }
                    ],
                }
            )

    upload(auth, photo)
    assert process_one(db, BusinessAddressAdapter)
    row = auth.get("/api/transactions").json()[0]
    assert row["data"]["property"] == row["data"]["account"] == "R&K Properties"
    assert any("different properties" in warning for warning in row["warnings"])
