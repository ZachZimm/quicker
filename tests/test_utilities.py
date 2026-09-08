import json

from quicker.contracts import Extraction
from quicker.extraction import VisionAdapter
from quicker.profile import property_from_address
from quicker.worker import process_one
from test_workflow import upload


def test_utility_type_detected_after_upload_and_location_totals_stay_separate(auth, db, photo, monkeypatch):
    common = {
        "kind": "invoice",
        "payee": "WM",
        "billing_customer_id": "6-37085-65004",
        "invoice_date": "2026-04-03",
        "invoice_number": "fixture-invoice",
        "service_period": "04/01/26-06/30/26",
        "date": "2026-04-03",
        "date_basis": "invoice",
    }
    rows = []
    for street, customer, amount in [
        ("1008 Bell St", "6-36357-95001", "62.10"),
        ("1008 Bell St #1/2", "6-36462-05002", "69.45"),
    ]:
        rows.append(
            {
                **common,
                "amount": amount,
                "amount_basis": "service_location_total",
                "service_customer_id": customer,
                "source": street,
                "property_address": {"street": street, "city": "Reno", "state": "NV", "role": "service"},
            }
        )
    for basis, amount in [("statement_total", "424.29"), ("component", "62.10"), ("illustration", "123.45")]:
        rows.append({**common, "amount": amount, "amount_basis": basis, "source": basis})
    replies = iter(
        [
            json.dumps(
                {
                    "document_type": "utility",
                    "transactions": rows,
                    "warnings": ["Page 3 of 3 only; other locations may be missing."],
                }
            ),
            '{"crossed_out": [], "uncertain": []}',
        ]
    )

    def complete(self, prompt, images, *args):
        return next(replies)

    monkeypatch.setattr(VisionAdapter, "complete", complete)
    # Only photos and grouping are uploaded. Detection occurs in extraction.
    upload(auth, photo)
    assert process_one(db)
    transactions = auth.get("/api/transactions").json()
    assert len(transactions) == 2
    assert sum(r["data"]["amount_minor"] for r in transactions) == -13155
    assert {r["data"]["service_customer_id"] for r in transactions} == {"6-36357-95001", "6-36462-05002"}
    for r in transactions:
        assert r["data"]["document_type"] == "utility"
        assert r["data"]["property"] == "Bell St."
        assert r["data"]["unit"] == "unresolved"
        assert r["data"]["utility_account"] is None
        assert r["data"]["billing_customer_id"] == "6-37085-65004"
        assert r["data"]["invoice_date"] == "2026-04-03"
        assert r["data"]["invoice_number"] == "fixture-invoice"
        assert r["data"]["date"] is None and r["data"]["account"] is None
        assert r["data"]["category"] == "Garbage"
    assert len(auth.get("/api/documents").json()[0]["ignored"]) == 3


def test_sewer_current_charge_excludes_previous_payment_and_breakdown(auth, db, photo):
    class SewerAdapter:
        def __init__(self, config):
            pass

        def extract(self, images, ref):
            return Extraction.model_validate(
                {
                    "document_type": "utility",
                    "transactions": [
                        {
                            "kind": "invoice",
                            "payee": "City of Sparks",
                            "amount": "251.86",
                            "amount_basis": "current_charges",
                            "utility_account": "013035-000",
                            "date": "2026-02-13",
                            "date_basis": "due",
                            "property_address": {"street": "983 HOLMAN WY", "role": "service"},
                        },
                        {"kind": "payment", "amount": "251.86", "source": "Previous payment"},
                        {"kind": "invoice", "amount": "158.66", "amount_basis": "component"},
                        {"kind": "invoice", "amount": "60.74", "amount_basis": "component"},
                        {"kind": "invoice", "amount": "32.46", "amount_basis": "component"},
                    ],
                }
            )

    upload(auth, photo)
    assert process_one(db, SewerAdapter)
    rows = auth.get("/api/transactions").json()
    assert len(rows) == 1
    assert rows[0]["data"]["amount_minor"] == -25186
    assert rows[0]["data"]["property"] == "Holman Way"
    assert rows[0]["data"]["category"] == "Sewer"
    assert rows[0]["data"]["date"] is None
    assert rows[0]["data"]["utility_account"] == "013035-000"


def test_fractional_service_address_resolves_property_without_losing_evidence():
    address = {"street": "1008 Bell St #1/2", "city": "Reno", "state": "NV", "role": "service"}
    assert property_from_address(address) == "Bell St."
    assert address["street"] == "1008 Bell St #1/2"
    assert property_from_address({**address, "street": "1009 Bell St #1/2"}) is None
