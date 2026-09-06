from uuid import uuid4

from quicker.contracts import Extraction
from quicker.db import Job
from quicker.worker import process_one
from sqlalchemy import select


def upload(client, photo, request_id=None):
    result = client.post(
        "/api/upload",
        data={"request_id": request_id or str(uuid4())},
        files=[("files", ("bill.png", photo, "image/png"))],
    )
    assert result.status_code == 200, result.text
    return result.json()


class InvoiceAdapter:
    def __init__(self, config):
        self.config = config

    def extract(self, images, ref):
        assert images[0].startswith(b"\xff\xd8")
        return Extraction.model_validate(
            {
                "document_type": "invoice",
                "transactions": [
                    {
                        "kind": "invoice",
                        "payee": "Example Energy",
                        "amount": "214.55",
                        "date": "2026-07-19",
                        "date_basis": "invoice",
                        "source": "Printed total",
                        "property": "Bell St.",
                    }
                ],
            }
        )


def ready(auth, db, photo):
    upload(auth, photo)
    assert process_one(db, InvoiceAdapter)
    return auth.get("/api/transactions").json()[0]


def fields(row):
    return {
        k: row["data"][k]
        for k in (
            "payee",
            "amount_minor",
            "date",
            "currency",
            "category",
            "tag",
            "property",
            "account",
            "account_override",
            "memo",
            "duplicate_acknowledged",
        )
    }


def action(auth, row, verb, data=None):
    body = {"id": row["id"], "revision": row["revision"]}
    if data is not None:
        body["fields"] = data
    return auth.post("/api/review", json={"action": verb, "rows": [body]})


def test_authentication_csrf_and_rate_limit(client, photo):
    assert client.get("/api/documents").status_code == 401
    assert client.get("/api/settings").status_code == 401
    assert (
        client.post(
            "/api/login",
            json={"username": "admin", "password": "test-password-12345"},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    for _ in range(10):
        assert client.post("/api/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    assert (
        client.post("/api/login", json={"username": "admin", "password": "test-password-12345"}).status_code
        == 429
    )


def test_csrf_and_device_scope(auth, photo):
    assert auth.post("/api/pair-code", headers={"X-CSRF-Token": ""}).status_code == 403
    code = auth.post("/api/pair-code").json()["code"]
    result = auth.post("/api/pair", json={"name": "Windows", "code": code}).json()
    header = {"Authorization": "Bearer " + result["token"]}
    assert auth.get("/api/settings", headers=header).status_code == 403
    assert auth.post("/api/device/heartbeat", headers=header).status_code == 200
    assert auth.post("/api/pair", json={"name": "Other", "code": code}).status_code == 401
    assert auth.delete("/api/devices/" + result["device_id"]).status_code == 200
    assert auth.post("/api/device/heartbeat", headers=header).status_code == 401


def test_upload_idempotency_storage_and_grouping(auth, db, photo):
    first = upload(auth, photo, "repeat")
    second = upload(auth, photo, "repeat")
    assert first == second
    assert len(auth.get("/api/documents").json()) == 1
    original = auth.get("/api/pages/" + first["pages"][0]["id"] + "/original")
    assert original.content == photo
    partial = auth.get("/api/pages/" + first["pages"][0]["id"] + "/original", headers={"Range": "bytes=100-"})
    assert partial.status_code == 206
    assert partial.content == photo[100:]
    result = auth.post(
        "/api/upload",
        data={"request_id": "group", "grouped": "true"},
        files=[("files", ("page1.png", photo, "image/png")), ("files", ("page2.png", photo, "image/png"))],
    )
    assert len(result.json()["document_ids"]) == 1
    assert [p["ordinal"] for p in result.json()["pages"]] == [0, 1]
    auth.post("/api/logout")
    assert auth.get("/api/pages/" + first["pages"][0]["id"] + "/original").status_code == 401


def test_review_required_date_override_and_stale_edit(auth, db, photo):
    row = ready(auth, db, photo)
    assert row["data"]["date"] is None  # Invoice date must never become payment date.
    assert row["data"]["category"] == "Utilities"
    assert row["data"]["amount_minor"] == -21455
    assert action(auth, row, "approve").status_code == 422
    edit = {**fields(row), "date": "2026-08-10"}
    response = action(auth, row, "save", edit)
    assert response.status_code == 200, response.text
    saved = response.json()[0]
    assert saved["data"]["account"] == "2026 Bell St."
    assert action(auth, row, "save", edit).status_code == 409
    approved = action(auth, saved, "approve").json()[0]
    assert approved["status"] == "approved"
    edited = action(auth, approved, "save", {**fields(approved), "date": "2027-01-02"}).json()[0]
    assert edited["status"] == "review"
    assert edited["data"]["account"] == "2027 Bell St."
    override = action(
        auth, edited, "save", {**fields(edited), "account": "2026 Bell St.", "account_override": True}
    ).json()[0]
    assert override["data"]["account"] == "2026 Bell St."
    assert len(auth.get("/api/transactions/" + row["id"] + "/history").json()) == 5


def test_remove_restore_and_no_reprocessing_over_edits(auth, db, photo):
    row = ready(auth, db, photo)
    removed = action(auth, row, "remove").json()[0]
    assert removed["status"] == "removed"
    assert action(auth, removed, "approve").status_code == 422
    assert auth.post("/api/documents/" + row["document_id"] + "/retry").status_code == 409
    restored = action(auth, removed, "restore").json()[0]
    assert restored["status"] == "review"
    assert restored["data"] == removed["data"]
    assert auth.post("/api/entry").status_code == 501
    assert auth.get("/api/transactions").json()[0]["status"] == "review"


def test_card_and_tax_business_rules(auth, db, photo):
    class RulesAdapter(InvoiceAdapter):
        def extract(self, images, ref):
            return Extraction.model_validate(
                {
                    "document_type": "credit_card",
                    "transactions": [
                        {
                            "kind": kind,
                            "payee": kind,
                            "amount": "12.34",
                            "date": "2026-08-10",
                            "date_basis": "purchase",
                            "property": "Bell St.",
                            "tag": "Property",
                        }
                        for kind in ["purchase", "refund", "payment", "fee", "interest"]
                    ],
                }
            )

    upload(auth, photo)
    process_one(db, RulesAdapter)
    rows = auth.get("/api/transactions").json()
    assert len(rows) == 2
    assert {r["data"]["amount_minor"] for r in rows} == {-1234, 1234}
    assert all(r["data"]["property"] is None for r in rows)
    assert len(auth.get("/api/documents").json()[0]["ignored"]) == 3

    class TaxAdapter(InvoiceAdapter):
        def extract(self, images, ref):
            return Extraction.model_validate(
                {
                    "document_type": "tax",
                    "transactions": [
                        {
                            "kind": "tax",
                            "payee": "County",
                            "amount": "137.66",
                            "date": "2026-08-10",
                            "date_basis": "payment",
                            "paid": True,
                            "parcel": "03127109",
                        },
                        {
                            "kind": "tax",
                            "amount": "137.66",
                            "date": "2027-01-04",
                            "date_basis": "due",
                            "paid": False,
                        },
                    ],
                }
            )

    upload(auth, photo)
    process_one(db, TaxAdapter)
    tax = [r for r in auth.get("/api/transactions").json() if r["data"]["kind"] == "tax"]
    assert len(tax) == 1
    assert tax[0]["data"]["parcel"] == "03127109"
    assert tax[0]["data"]["date"] == "2026-08-10"


def test_duplicate_requires_acknowledgement_and_batch_is_atomic(auth, db, photo):
    ready(auth, db, photo)
    ready(auth, db, photo)
    rows = auth.get("/api/transactions").json()
    response = auth.post(
        "/api/review",
        json={
            "action": "approve",
            "rows": [
                {"id": r["id"], "revision": r["revision"], "fields": {**fields(r), "date": "2026-08-10"}}
                for r in rows
            ],
        },
    )
    assert response.status_code == 422
    rows = auth.get("/api/transactions").json()
    assert all(r["revision"] == 1 and r["data"]["date"] is None for r in rows)
    response = auth.post(
        "/api/review",
        json={
            "action": "approve",
            "rows": [
                {
                    "id": r["id"],
                    "revision": r["revision"],
                    "fields": {**fields(r), "date": "2026-08-10", "duplicate_acknowledged": True},
                }
                for r in rows
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert all(r["status"] == "approved" for r in response.json())


def test_worker_recovers_expired_claim_and_records_failures(auth, db, photo):
    from quicker.extraction import ModelError

    class Failing(InvoiceAdapter):
        def extract(self, images, ref):
            raise ModelError("Model endpoint unavailable")

    uploaded = upload(auth, photo)
    for attempt in range(3):
        with db.write() as session:
            job = session.scalar(select(Job))
            job.available = 0
        process_one(db, Failing)
    assert auth.get("/api/documents").json()[0]["status"] == "failed"
    assert len(auth.get("/api/documents/" + uploaded["document_ids"][0] + "/attempts").json()) == 3
    assert auth.post("/api/documents/" + uploaded["document_ids"][0] + "/retry").status_code == 200
    with db.write() as session:
        job = session.scalar(select(Job).where(Job.status == "queued"))
        job.status = "running"
        job.lease_until = 0
        job.claim = "expired"
    process_one(db, InvoiceAdapter)
    assert auth.get("/api/documents").json()[0]["status"] == "ready"


def test_settings_key_is_optional_masked_and_revision_checked(auth, db):
    config = auth.get("/api/settings").json()
    assert not config.pop("has_api_key")
    config.update(api_key="private-test-key", host="127.0.0.1", port=9999)
    response = auth.put("/api/settings", json=config)
    assert response.status_code == 200
    assert "private-test-key" not in auth.get("/api/settings").text
    assert auth.put("/api/settings", json=config).status_code == 409
    config["revision"] = response.json()["revision"]
    config["api_key"] = ""
    assert auth.put("/api/settings", json=config).status_code == 200
    assert not auth.get("/api/settings").json()["has_api_key"]


def test_crossed_out_items_are_ignored_but_other_rows_remain(auth, db, photo):
    class CrossedOutAdapter(InvoiceAdapter):
        def extract(self, images, ref):
            return Extraction.model_validate(
                {
                    "document_type": "credit_card",
                    "transactions": [
                        {
                            "kind": "purchase",
                            "payee": "MHS INCLINE VILLAGE",
                            "amount": "22.06",
                            "date": "2026-07-16",
                            "date_basis": "purchase",
                            "crossed_out": True,
                            "source": "Crossed-out MHS row",
                        },
                        {
                            "kind": "purchase",
                            "payee": "Other merchant",
                            "amount": "73.71",
                            "date": "2026-07-16",
                            "date_basis": "purchase",
                        },
                        {
                            "kind": "refund",
                            "payee": "Returned purchase",
                            "amount": "10.00",
                            "date": "2026-07-17",
                            "date_basis": "refund",
                            "crossed_out": True,
                        },
                    ],
                }
            )

    upload(auth, photo)
    assert process_one(db, CrossedOutAdapter)
    rows = auth.get("/api/transactions").json()
    assert len(rows) == 1
    assert rows[0]["data"]["payee"] == "Other merchant"
    assert rows[0]["data"]["property"] is None
    ignored = auth.get("/api/documents").json()[0]["ignored"]
    assert len(ignored) == 2
    assert all(item["reason"] == "Crossed-out item excluded" for item in ignored)
    assert ignored[0]["source"] == "Crossed-out MHS row"
    assert ignored[1]["source"] == "Returned purchase"


class InsuranceAdapter(InvoiceAdapter):
    def extract(self, images, ref):
        return Extraction.model_validate(
            {
                "document_type": "credit_card",
                "transactions": [
                    {
                        "kind": "purchase",
                        "payee": payee,
                        "category": category,
                        "amount": "422.00",
                        "date": "2026-07-08",
                        "date_basis": "purchase",
                    }
                    for payee, category in [
                        ("STATE FARM MUTUAL AUTOMO 800-956-6310 IL", None),
                        ("Example insurer", "Auto & Transport:Auto Insurance"),
                        ("STATE FARM INSURANCE 800-956-6310 IL", None),
                        ("Auto body shop", "Repairs"),
                    ]
                ],
            }
        )


def test_auto_insurance_defaults_use_exact_account_and_allow_review_edits(auth, db, photo):
    from quicker.db import Setting

    with db.write() as session:
        setting = session.get(Setting, "catalog")
        setting.value = {
            **setting.value,
            "accounts": setting.value["accounts"] + [{"name": "R&K Properties", "type": "Bank"}],
            "categories": setting.value["categories"]
            + [
                {"name": name, "type": "expense"}
                for name in ["Insurance (Business):Truck", "Auto & Transport:Auto Insurance"]
            ],
        }
    upload(auth, photo)
    assert process_one(db, InsuranceAdapter)
    rows = {r["data"]["payee"]: r for r in auth.get("/api/transactions").json()}
    for name in ["STATE FARM MUTUAL AUTOMO 800-956-6310 IL", "Example insurer"]:
        data = rows[name]["data"]
        assert data["property"] == data["account"] == "R&K Properties"
        assert data["category"] == "Insurance (Business):Truck"
        assert data["account_override"] is True
    for name in ["STATE FARM INSURANCE 800-956-6310 IL", "Auto body shop"]:
        assert rows[name]["data"]["property"] is None
        assert rows[name]["data"]["account"] is None
    row = rows["STATE FARM MUTUAL AUTOMO 800-956-6310 IL"]
    saved = action(auth, row, "save", {**fields(row), "date": "2027-01-02"}).json()[0]
    assert saved["data"]["account"] == "R&K Properties"
    approved = action(auth, saved, "approve")
    assert approved.status_code == 200, approved.text
    edited = action(
        auth,
        approved.json()[0],
        "save",
        {
            **fields(saved),
            "property": "Bell St.",
            "account": "2027 Bell St.",
            "category": "Repairs",
        },
    ).json()[0]
    assert edited["status"] == "review"
    assert edited["data"]["property"] == "Bell St."
    assert edited["data"]["account"] == "2027 Bell St."
    assert edited["data"]["category"] == "Repairs"
    assert action(auth, edited, "approve").status_code == 200


def test_insurance_rule_does_not_substitute_year_account_if_exact_catalog_entries_missing(auth, db, photo):
    upload(auth, photo)
    assert process_one(db, InsuranceAdapter)
    row = next(r for r in auth.get("/api/transactions").json() if "MUTUAL AUTOMO" in r["data"]["payee"])
    assert row["data"]["account"] == "R&K Properties"
    assert "Choose an existing account from the QIF catalog" in row["issues"]
    assert "Choose an existing category from the QIF catalog" in row["issues"]
    assert action(auth, row, "approve").status_code == 422
