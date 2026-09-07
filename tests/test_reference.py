import pytest
from quicker.catalog import import_catalog, parse_qif, route_list
from quicker.contracts import Extraction
from quicker.profile import canonical_property, merchant_identity, property_directory
from quicker.rules import initial_assignments
from quicker.worker import process_one
from test_workflow import InvoiceAdapter, action, fields, upload

HISTORY = """!Account
NR&K Properties 2026
TBank
^
!Type:Bank 
D5/ 7'26
T-50.00
PSpectrum
LInternet-Reno
^
D5/ 8'26
T1,900.00
LRent Received/Unit A
^
D5/ 9'26
T0.99
PApple
LICloud
^
!Account
NR&K Properties
TBank
^
!Type:Bank
D5/ 7'26
T-50.00
PSpectrum
LInternet-Reno
^
!Type:Cat
NInternet-Reno
E
^
NICloud
E
^
!Type:Memorized
PRemembered
LInternet-Reno
^
"""


def test_full_records_coverage_and_memorized_payees_have_no_inherited_account():
    ref = parse_qif(HISTORY)
    assert len(ref["history"]) == 4
    assert ref["coverage"]["blank_payees"] == 1
    assert ref["coverage"]["accounts"][0] == {
        "account": "R&K Properties 2026",
        "count": 3,
        "first_date": "2026-05-07",
        "last_date": "2026-05-09",
    }
    blank = ref["history"][1]
    assert blank["payee"] == "" and blank["amount_minor"] == 190000
    assert blank["tag"] == "Unit A" and "T1,900.00" in blank["raw"]
    assert ref["history"][2]["amount_minor"] == 99
    assert "account" not in ref["payees"][0]
    assert [r["id"] for r in ref["history"]] == [r["id"] for r in parse_qif(HISTORY)["history"]]
    incomplete = parse_qif(HISTORY.replace("D5/ 8'26", "Dinvalid").replace("T1,900.00", "TNaN"))
    assert incomplete["coverage"]["invalid_dates"] == incomplete["coverage"]["invalid_amounts"] == 1
    with pytest.raises(ValueError, match="Unterminated"):
        parse_qif(HISTORY.rstrip()[:-1])


def test_property_aliases_preserve_exact_accounts_and_manual_override(db):
    qif = """!Account
N2025 G Street
TBank
^
N2026 1810 G Street
TBank
^
N2024 - West Sixth Street
TBank
^
N2026 West 6th Street
TBank
^
N1008 Bell St.
TBank
^
"""
    with db.write() as s:
        import_catalog(s, qif)
        routes = route_list(s)
        g = [r for r in routes if r["property"] == "1810 G Street"]
        assert {r["account"] for r in g} == {"2025 G Street", "2026 1810 G Street"}
        west = [r for r in routes if r["property"] == "West 6th Street"]
        assert len(west) == 2
        assert not any(r["account"] == "1008 Bell St." for r in routes)
        registry = property_directory(routes)
        assert next(p for p in registry if p["name"] == "Bell St.")["units"] == ["1008 Bell", "2 Bell"]
        assert canonical_property("Holman Way") != canonical_property("Holman Circle")
        assert canonical_property("Bell;one half") == "Bell St."
        from quicker.db import Route

        s.get(Route, ("1810 G Street", 2026)).account = "2025 G Street"
        import_catalog(s, qif)
        assert s.get(Route, ("1810 G Street", 2026)).account == "2025 G Street"


@pytest.mark.parametrize(
    "payee,amount,source,category",
    [
        ("APPLE.COM/BILL 866-712-7753 CA", -99, "", "ICloud"),
        ("HP *ALL-IN PLAN 888-447-0148 CA", -1406, "", "All In Plan"),
        ("HP Instant Ink", -2283, "", "Printer Plan"),
        ("SAMSCLUB #4768 RENO NV", -7627, "", "Truck Gas"),
        ("The Wash Shop", -1000, "", "Truck Wash"),
        ("USPS", -10800, "PO Box renewal", "P.O. Box-6 Months"),
        ("Spectrum Mobile 855-707-7328", -4530, "", "Cell Phones"),
    ],
)
def test_business_defaults_use_2026_categories_without_year_override(payee, amount, source, category):
    row = initial_assignments({"payee": payee, "amount_minor": amount, "source": source})
    assert row["category"] == category
    assert row["property"] == "R&K Properties"
    assert row["account_override"] is False and row["account"] is None
    assert row["payee"] == payee and row["source"] == source


@pytest.mark.parametrize(
    "payee,source,amount",
    [
        ("Sam's Club", "Membership renewal", -6500),
        ("Sam's Club", "Groceries", -7000),
        ("Apple", "iPad", -99900),
        ("HP", "Laptop", -140600),
        ("USPS", "", -1200),
        ("State Farm Insurance", "", -53200),
        ("Spectrum", "", -5000),
    ],
)
def test_ambiguous_merchants_do_not_get_a_business_assignment(payee, source, amount):
    row = initial_assignments({"payee": payee, "source": source, "amount_minor": amount})
    assert not row.get("assignment_rule") and row["property"] is None


@pytest.mark.parametrize(
    "payee,category",
    [
        ("TMWA", "Water"),
        ("City 0f Reno", "Sewer"),
        ("Waste Management", "Garbage"),
        ("Washoe County Treasurer", "Property Tax"),
    ],
)
def test_utility_category_does_not_infer_property(payee, category):
    row = initial_assignments({"payee": payee})
    assert row["category"] == category and row["property"] is None


def test_service_identities_remain_distinct():
    assert merchant_identity("City 0f Sparks") == merchant_identity("City Of Sparks")
    assert merchant_identity("Spectrum Mobile 855") != merchant_identity("Spectrum 855")
    assert merchant_identity("HP Instant Ink") != merchant_identity("HP *ALL-IN PLAN")


class SpectrumAdapter(InvoiceAdapter):
    def extract(self, images, ref):
        return Extraction.model_validate(
            {
                "document_type": "credit_card",
                "transactions": [
                    {
                        "kind": "purchase",
                        "payee": "Spectrum 855-707-7328 MO",
                        "amount": "50.00",
                        "date": "2026-05-07",
                        "date_basis": "purchase",
                    },
                ],
            }
        )


def test_historical_duplicates_across_accounts_require_acknowledgement(auth, db, photo):
    with db.write() as s:
        import_catalog(s, HISTORY)
    upload(auth, photo)
    assert process_one(db, SpectrumAdapter)
    row = auth.get("/api/transactions").json()[0]
    matches = row["historical_duplicates"]
    assert len(matches) == 2
    assert {r["account"] for r in matches} == {"R&K Properties", "R&K Properties 2026"}
    edit = {**fields(row), "property": "R&K Properties", "category": "Internet-Reno"}
    assert action(auth, row, "approve", edit).status_code == 422
    saved = action(auth, row, "save", edit).json()[0]
    assert saved["data"]["account"] == "R&K Properties 2026"
    assert (
        action(auth, saved, "approve", {**fields(saved), "duplicate_acknowledged": True}).status_code == 200
    )
    approved = auth.get("/api/transactions").json()[0]
    # Re-importing unchanged history must preserve the acknowledgement and approved state.
    with db.write() as s:
        import_catalog(s, HISTORY)
    assert auth.get("/api/transactions").json()[0]["revision"] == approved["revision"]
    # An additional historical match invalidates approval and any open stale edit.
    with db.write() as s:
        import_catalog(
            s, HISTORY.replace("!Type:Cat", "D5/ 7'26\nT-50.00\nPSpectrum\nLInternet-Reno\n^\n!Type:Cat")
        )
    revised = auth.get("/api/transactions").json()[0]
    assert revised["status"] == "review" and not revised["data"]["duplicate_acknowledged"]
    assert action(auth, approved, "save").status_code == 409
    # A changed amount does not match simply because payee/date match.
    updated = action(auth, revised, "save", {**fields(revised), "amount_minor": -5100}).json()[0]
    assert not updated["historical_duplicates"]


def test_import_preview_api_includes_coverage_and_property_directory(auth):
    response = auth.post("/api/catalog", files={"file": ("reference.qif", HISTORY, "text/plain")})
    assert response.status_code == 200
    assert response.json()["coverage"]["total"] == 4
    catalog = auth.get("/api/catalog").json()
    assert catalog["coverage"]["total"] == 4
    assert any(p["name"] == "R&K Properties" for p in catalog["properties"])
    assert catalog["preferred_categories"]["printer_ink"] == "Printer Plan"


def test_explicit_umbrella_policy_is_not_overridden_by_conflicting_old_category():
    data = initial_assignments(
        {
            "payee": "State Farm Insurance",
            "category": "Truck Insurance",
            "source": "Umbrella Rental Policy",
            "amount_minor": -49400,
        }
    )
    assert not data.get("assignment_rule") and data["property"] is None
