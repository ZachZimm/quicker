import pytest
from quicker import units
from quicker.contracts import Extraction
from quicker.db import Audit, Candidate
from quicker.documents import ingest
from quicker.review import create_candidates
from sqlalchemy import select, text
from test_workflow import action, fields


@pytest.fixture
def unit_catalog(db):
    from quicker.db import Setting

    with db.write() as session:
        setting = session.get(Setting, "catalog")
        setting.value = {
            **setting.value,
            "tags": setting.value["tags"]
            + [
                {"name": "1008 Bell"},
                {"name": "2 Bell"},
                {"name": "Utilities"},
            ],
        }


def invoice(auth, db, photo, **changes):
    doc_id = ingest(db, [("bill.png", photo)], "unit-bill")[0]
    with db.write() as session:
        create_candidates(
            session,
            doc_id,
            Extraction.model_validate(
                {
                    "document_type": "invoice",
                    "transactions": [
                        {
                            "kind": "invoice",
                            "payee": "Example Energy",
                            "amount": "214.55",
                            "date": "2026-08-10",
                            "date_basis": "payment",
                            "category": "Utilities",
                            "tag": "Utilities",
                            "utility_account": "001-234",
                            "property_address": {
                                "street": "1008 Bell St",
                                "city": "Reno",
                                "state": "NV",
                                "role": "service",
                            },
                            **changes,
                        }
                    ],
                }
            ),
        )
    return auth.get("/api/transactions").json()[0]


def test_base_address_unresolved_manual_unit_preserves_account_and_expense_tag(auth, db, photo, unit_catalog):
    row = invoice(auth, db, photo)
    assert row["data"]["unit"] == "unresolved"
    assert row["data"]["utility_account"] == "001-234"
    assert row["data"]["property_address"]["street"] == "1008 Bell St"
    selected = action(auth, row, "approve", {**fields(row), "unit": "2 Bell"}).json()[0]
    assert selected["status"] == "approved"
    assert selected["data"]["account"] == "2026 Bell St."
    assert selected["quicken_tags"] == ["Utilities", "2 Bell"]
    assert selected["data"]["amount_minor"] == -21455
    # Changing rental clears the earlier approval and updates only the unit tag.
    changed = action(auth, selected, "save", {**fields(selected), "unit": "1008 Bell"}).json()[0]
    assert changed["status"] == "review"
    assert changed["quicken_tags"] == ["Utilities", "1008 Bell"]
    whole = action(auth, changed, "save", {**fields(changed), "unit": "whole_property"}).json()[0]
    assert whole["quicken_tags"] == ["Utilities"]
    assert whole["data"]["amount_minor"] == -21455


def test_invalid_unit_rejected_and_property_change_clears_unit(auth, db, photo, unit_catalog):
    row = invoice(auth, db, photo)
    assert action(auth, row, "save", {**fields(row), "unit": "Holman 07"}).status_code == 422
    assert action(auth, row, "save", {**fields(row), "tag": "2 Bell"}).status_code == 422
    selected = action(auth, row, "save", {**fields(row), "unit": "2 Bell"}).json()[0]
    moved = action(
        auth,
        selected,
        "save",
        {
            **fields(selected),
            "property": "R&K Properties",
            "unit": "2 Bell",
        },
    ).json()[0]
    assert moved["data"]["unit"] == "unresolved"
    assert moved["data"]["unit_evidence"] is None
    assert moved["data"]["account"] == "R&K Properties 2026"


def test_unresolved_can_be_approved_and_older_clients_preserve_unit(auth, db, photo, unit_catalog):
    row = invoice(auth, db, photo)
    approved = action(auth, row, "approve").json()[0]
    assert approved["data"]["unit"] == "unresolved"
    selected = action(auth, approved, "save", {**fields(approved), "unit": "2 Bell"}).json()[0]
    payload = {**fields(selected), "memo": "Reviewed bill"}
    payload.pop("unit", None)
    saved = action(auth, selected, "save", payload).json()[0]
    assert saved["data"]["unit"] == "2 Bell"


def test_verified_account_suggests_unit_and_manual_review_takes_precedence(
    auth, db, photo, unit_catalog, monkeypatch
):
    monkeypatch.setattr(
        units,
        "VERIFIED_UNIT_MAPPINGS",
        [
            {
                "property": "Bell St.",
                "unit": "2 Bell",
                "merchant": "Example Energy",
                "utility_account": "001234",
                "evidence": "Synthetic fixture: two matched bills.",
            }
        ],
    )
    row = invoice(auth, db, photo)
    assert row["data"]["unit"] == "2 Bell"
    assert row["data"]["unit_evidence"] == "Synthetic fixture: two matched bills."
    changed = action(auth, row, "save", {**fields(row), "unit": "whole_property"}).json()[0]
    again = action(auth, changed, "save", {**fields(changed), "unit": "whole_property"}).json()[0]
    assert again["data"]["unit"] == "whole_property"


@pytest.mark.parametrize(
    "changes",
    [
        {"utility_account": "1234"},
        {"utility_account": "XX1234"},
        {"utility_account": None},
        {"payee": "Other Energy"},
        {"document_type": "credit_card"},
        {"document_type": "tax"},
        {"property": "Holman Way"},
    ],
)
def test_unit_account_match_does_not_guess(changes, monkeypatch):
    monkeypatch.setattr(
        units,
        "VERIFIED_UNIT_MAPPINGS",
        [
            {
                "property": "Bell St.",
                "unit": "2 Bell",
                "merchant": "Example Energy",
                "utility_account": "001234",
                "evidence": "Synthetic verified account.",
            }
        ],
    )
    result = units.initial_unit_assignment(
        {
            "property": "Bell St.",
            "document_type": "invoice",
            "payee": "Example Energy",
            "utility_account": "001234",
            **changes,
        }
    )
    assert result["unit"] == "unresolved"


def test_exact_service_unit_and_conflicting_identifiers(monkeypatch):
    address = {"street": "1008 Bell St Apt 2", "city": "Reno", "state": "NV"}
    mapping = {
        "property": "Bell St.",
        "unit": "2 Bell",
        "merchant": "Example Energy",
        "address": address,
        "evidence": "Synthetic verified service location.",
    }
    monkeypatch.setattr(units, "VERIFIED_UNIT_MAPPINGS", [mapping])
    data = {
        "property": "Bell St.",
        "document_type": "invoice",
        "payee": "Example Energy",
        "property_address": {**address, "role": "service"},
    }
    assert units.initial_unit_assignment(data)["unit"] == "2 Bell"
    for change in [
        {"street": "1008 Bell St"},
        {"street": "1008 Bell St Apt 3"},
        {"role": "mailing"},
        {"role": "utility_customer"},
        {"city": "Sparks"},
    ]:
        assert (
            units.initial_unit_assignment(
                {
                    **data,
                    "property_address": {**data["property_address"], **change},
                }
            )["unit"]
            == "unresolved"
        )
    monkeypatch.setattr(units, "VERIFIED_UNIT_MAPPINGS", [mapping, {**mapping, "unit": "1008 Bell"}])
    result = units.initial_unit_assignment(data)
    assert result["unit"] == "unresolved" and "conflict" in result["unit_evidence"]


def test_legacy_units_and_tax_scope_migrate_with_audit(db, photo):
    doc = ingest(db, [("legacy.png", photo)], "legacy")[0]
    with db.write() as session:
        session.add_all(
            [
                Candidate(
                    id="legacy-unit",
                    document_id=doc,
                    revision=4,
                    status="approved",
                    warnings=[],
                    data={"property": "Bell St.", "tag": "2 Bell", "amount_minor": -6210},
                ),
                Candidate(
                    id="legacy-tax",
                    document_id=doc,
                    revision=1,
                    status="review",
                    warnings=[],
                    data={
                        "property": "Bell St.",
                        "document_type": "tax",
                        "amount_minor": -40311,
                        "property_assignment": "verified_parcel",
                    },
                ),
            ]
        )
        session.execute(text("UPDATE alembic_version SET version_num='9b9eb6571ac7'"))
    db.migrate()
    with db.session() as session:
        unit = session.get(Candidate, "legacy-unit")
        assert unit.data["unit"] == "2 Bell" and unit.data["tag"] is None
        assert unit.status == "approved" and unit.revision == 5
        assert units.quicken_tags(unit.data) == ["2 Bell"]
        tax = session.get(Candidate, "legacy-tax")
        assert tax.data["unit"] == "whole_property" and tax.data["amount_minor"] == -40311
        audits = list(session.scalars(select(Audit).where(Audit.action == "unit_fields_migrated")))
        assert len(audits) == 2
    db.migrate()
    with db.session() as session:
        assert session.get(Candidate, "legacy-unit").revision == 5


def test_verified_utility_account_can_identify_property_without_address(monkeypatch):
    monkeypatch.setattr(
        units,
        "VERIFIED_UNIT_MAPPINGS",
        [
            {
                "property": "Bell St.",
                "unit": "2 Bell",
                "merchant": "Example Energy",
                "utility_account": "001234",
                "evidence": "Synthetic verified account.",
            }
        ],
    )
    result = units.initial_unit_assignment(
        {"document_type": "invoice", "payee": "Example Energy", "utility_account": "001234"}
    )
    assert result["property"] == "Bell St." and result["unit"] == "2 Bell"


def test_verification_requires_evidence_and_all_configured_identifiers(monkeypatch):
    mapping = {
        "property": "Bell St.",
        "unit": "2 Bell",
        "merchant": "Example Energy",
        "utility_account": "001234",
    }
    data = {
        "property": "Bell St.",
        "document_type": "invoice",
        "payee": "Example Energy",
        "utility_account": "001234",
    }
    monkeypatch.setattr(units, "VERIFIED_UNIT_MAPPINGS", [mapping])
    assert units.initial_unit_assignment(data)["unit"] == "unresolved"
    monkeypatch.setattr(
        units,
        "VERIFIED_UNIT_MAPPINGS",
        [
            {
                **mapping,
                "evidence": "Synthetic account and location.",
                "address": {"street": "1008 Bell St Apt 2", "city": "Reno", "state": "NV"},
            }
        ],
    )
    assert units.initial_unit_assignment(data)["unit"] == "unresolved"


def test_assign_property_and_unit_together_and_accept_property_alias(auth, db, photo, unit_catalog):
    row = invoice(auth, db, photo, property_address=None, property=None)
    selected = action(
        auth,
        row,
        "save",
        {
            **fields(row),
            "property": "Bell;One Half",
            "unit": "2 Bell",
        },
    ).json()[0]
    assert selected["data"]["property"] == "Bell St."
    assert selected["data"]["unit"] == "2 Bell"
    assert selected["data"]["account"] == "2026 Bell St."


def test_unit_tag_must_exist_in_catalog_before_approval(auth, db, photo):
    row = invoice(auth, db, photo, tag=None)
    response = action(auth, row, "approve", {**fields(row), "unit": "2 Bell"})
    assert response.status_code == 422
    assert "Quicken tags" in response.text
    unchanged = auth.get("/api/transactions").json()[0]
    assert unchanged["revision"] == row["revision"]
    assert unchanged["data"]["unit"] == "unresolved"
