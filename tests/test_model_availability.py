import threading
import time
from concurrent.futures import ThreadPoolExecutor
from email.utils import formatdate

import httpx
import pytest
from quicker import model_availability
from quicker.analysis_status import ModelConnection
from quicker.contracts import ModelConfig
from quicker.db import Database, ExtractionAttempt, Job
from quicker.extraction import VisionAdapter
from quicker.model_endpoint import ModelUnavailable, PermanentModelError
from quicker.worker import process_one
from sqlalchemy import select, text
from test_workflow import InvoiceAdapter, upload


@pytest.fixture
def clock(monkeypatch):
    now = [int(time.time())]
    monkeypatch.setattr(time, "time", lambda: now[0])
    return now


class Offline(InvoiceAdapter):
    def extract(self, images, ref):
        raise ModelUnavailable("Model server is offline.")


def test_outage_keeps_documents_and_retry_budget_across_restart(auth, db, photo, clock):
    upload(auth, photo)
    upload(auth, photo)
    for delay in (30, 60, 120, 240, 300):
        assert process_one(db, Offline)
        assert not process_one(db, Offline)
        with db.session() as session:
            jobs = list(session.scalars(select(Job)))
            assert len(jobs) == 2
            assert all(job.status == "queued" and job.attempts == 0 for job in jobs)
            assert not list(session.scalars(select(ExtractionAttempt)))
        rows = auth.get("/api/documents").json()
        assert all(row["status"] == "queued" for row in rows)
        assert all(row["analysis"]["retry_at"] == clock[0] + delay for row in rows)
        assert all("offline" in row["analysis"]["waiting_reason"] for row in rows)
        clock[0] += delay
    # A new database/worker instance sees the same cooldown and resumes without user action.
    restarted = Database(db.directory)
    try:
        assert process_one(restarted, InvoiceAdapter)
        assert process_one(restarted, InvoiceAdapter)
    finally:
        restarted.engine.dispose()
    assert all(row["status"] == "ready" for row in auth.get("/api/documents").json())
    with db.session() as session:
        state = model_availability.status(session, ModelConfig())
        assert state["reason"] is None and state["last_success"] == clock[0]


def test_one_recovery_probe_and_stale_success_does_not_clear_outage(auth, db, photo, clock):
    upload(auth, photo)
    upload(auth, photo)
    process_one(db, Offline)
    clock[0] += 30
    started, finish = threading.Event(), threading.Event()

    class Slow(InvoiceAdapter):
        def extract(self, images, ref):
            started.set()
            assert finish.wait(5)
            return super().extract(images, ref)

    with ThreadPoolExecutor() as pool:
        future = pool.submit(process_one, db, Slow)
        try:
            assert started.wait(5)
            assert not process_one(db, InvoiceAdapter)
            # A manual retry cannot release a live recovery probe.
            assert auth.post("/api/analysis/retry").status_code == 200
            assert not process_one(db, InvoiceAdapter)
            # A concurrent, older request reports a new outage while this probe runs.
            with db.write() as session:
                model_availability.unavailable(session, ModelConfig(), ModelUnavailable("Still offline"))
        finally:
            finish.set()
        assert future.result()
    assert not process_one(db, InvoiceAdapter)
    clock[0] += 60
    assert process_one(db, InvoiceAdapter)


def test_expired_recovery_probe_is_reclaimed_without_stale_publication(auth, db, photo, clock):
    upload(auth, photo)
    process_one(db, Offline)
    clock[0] += 30
    started, finish = threading.Event(), threading.Event()

    class Slow(InvoiceAdapter):
        def extract(self, images, ref):
            started.set()
            assert finish.wait(5)
            return super().extract(images, ref)

    with ThreadPoolExecutor() as pool:
        future = pool.submit(process_one, db, Slow)
        try:
            assert started.wait(5)
            with db.session() as session:
                clock[0] = session.scalar(select(Job)).lease_until + 1
            assert process_one(db, InvoiceAdapter)
        finally:
            finish.set()
        assert future.result()
    with db.session() as session:
        assert len(list(session.scalars(select(ExtractionAttempt)))) == 1


def test_manual_retry_respects_provider_delay(auth, db, photo, clock):
    upload(auth, photo)

    class Busy(InvoiceAdapter):
        def extract(self, images, ref):
            raise ModelUnavailable("Busy", "busy", retry_after=120)

    process_one(db, Busy)
    assert auth.post("/api/analysis/retry").status_code == 200
    assert not process_one(db, InvoiceAdapter)
    clock[0] += 120
    assert process_one(db, InvoiceAdapter)


def test_retry_now_and_queued_settings_change(auth, db, photo):
    upload(auth, photo)
    process_one(db, Offline)
    assert auth.post("/api/analysis/retry").status_code == 200
    assert process_one(db, InvoiceAdapter)
    upload(auth, photo)
    process_one(db, Offline)
    config = auth.get("/api/settings").json()
    config.pop("has_api_key")
    config["model"] = "replacement-model"
    assert auth.put("/api/settings", json=config).status_code == 200
    queued = next(row for row in auth.get("/api/documents").json() if row["status"] == "queued")
    assert not queued["analysis"]["uses_current_settings"]
    assert auth.post(f"/api/documents/{queued['id']}/retry").status_code == 200
    assert process_one(db, InvoiceAdapter)


def test_other_endpoint_is_not_blocked(auth, db, photo):
    upload(auth, photo)
    process_one(db, Offline)
    upload(auth, photo)
    with db.write() as session:
        jobs = list(session.scalars(select(Job).order_by(Job.created, Job.id)))
        jobs[-1].config = ModelConfig(model="another-model").model_dump()
    assert process_one(db, InvoiceAdapter)
    assert not process_one(db, InvoiceAdapter)


def test_repeated_memory_failures_require_intervention(auth, db, photo, clock):
    upload(auth, photo)

    class NoMemory(InvoiceAdapter):
        def extract(self, images, ref):
            raise ModelUnavailable("GPU memory unavailable", "memory")

    for delay in (30, 60, 120):
        process_one(db, NoMemory)
        clock[0] += delay
    row = auth.get("/api/documents").json()[0]
    assert row["status"] == "failed" and "repeatedly" in row["error"]
    with db.session() as session:
        assert session.scalar(select(Job)).resource_failures == 3
        assert len(list(session.scalars(select(ExtractionAttempt)))) == 1
    assert auth.post(f"/api/documents/{row['id']}/retry").status_code == 200
    assert process_one(db, InvoiceAdapter)


@pytest.mark.parametrize(
    "status,payload,expected,reason",
    [
        (401, {"message": "private credential"}, PermanentModelError, None),
        (400, {"message": "context length exceeded private text"}, PermanentModelError, None),
        (400, {"message": "unsupported model private text"}, PermanentModelError, None),
        (500, {"message": "CUDA out of memory private text"}, ModelUnavailable, "memory"),
        (503, {"message": "Loading model private text"}, ModelUnavailable, "loading"),
        (429, {"message": "private text"}, ModelUnavailable, "busy"),
        (502, {"message": "private text"}, ModelUnavailable, "busy"),
    ],
)
def test_provider_errors_are_typed_and_sanitized(monkeypatch, status, payload, expected, reason):
    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(
            **kwargs,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    status,
                    json={"error": payload},
                    headers={"retry-after": "90"},
                )
            ),
        ),
    )
    with pytest.raises(expected) as caught:
        VisionAdapter(ModelConfig()).complete("Read", [b"photo"])
    assert "private" not in str(caught.value)
    if reason:
        assert caught.value.reason == reason and caught.value.retry_after == 90


@pytest.mark.parametrize(
    "exception,reason", [(httpx.ConnectError, "offline"), (httpx.ReadTimeout, "timeout")]
)
def test_transport_errors_wait_without_provider_text(monkeypatch, exception, reason):
    original = httpx.Client

    def handler(request):
        raise exception("private provider details")

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(
            **kwargs,
            transport=httpx.MockTransport(handler),
        ),
    )
    with pytest.raises(ModelUnavailable) as caught:
        VisionAdapter(ModelConfig()).complete("Read", [])
    assert caught.value.reason == reason and "private" not in str(caught.value)


def test_bonsai_health_loading_prevents_inference_and_shows_status(auth, db, photo, monkeypatch, clock):
    upload(auth, photo)
    original = httpx.Client
    requests = []

    def handler(request):
        requests.append(request.url.path)
        assert request.method == "GET"
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(
            503,
            json={"error": {"message": "Loading model"}},
            headers={
                "Retry-After": formatdate(clock[0] + 90, usegmt=True),
            },
        )

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(
            **kwargs,
            transport=httpx.MockTransport(handler),
        ),
    )
    assert process_one(db)
    assert requests == ["/v1/health"]
    result = auth.get("/api/analysis-status").json()
    assert result["model"]["state"] == "loading"
    assert result["model"]["retry_at"] == clock[0] + 90
    assert result["queue"]["retrying"] == 1


def test_health_falls_back_if_proxy_does_not_expose_it(monkeypatch):
    original = httpx.Client

    def handler(request):
        return (
            httpx.Response(404)
            if request.url.path.endswith("/health")
            else httpx.Response(200, json={"data": []})
        )

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(
            **kwargs,
            transport=httpx.MockTransport(handler),
        ),
    )
    assert ModelConnection().status(ModelConfig())["state"] == "connected"


def test_retry_requires_authentication_and_csrf(client, auth):
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/analysis/retry").status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    auth.post("/api/logout")
    assert client.post("/api/analysis/retry").status_code == 401


def test_health_memory_shortage_does_not_blame_document(auth, db, photo, monkeypatch, clock):
    upload(auth, photo)
    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(
            **kwargs,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    503,
                    json={"error": {"message": "CUDA out of memory"}},
                )
            ),
        ),
    )
    for _ in range(4):
        assert process_one(db)
        clock[0] += 300
    row = auth.get("/api/documents").json()[0]
    assert row["status"] == "queued" and row["analysis"]["attempts"] == 0
    with db.session() as session:
        assert session.scalar(select(Job)).resource_failures == 0


def test_permanent_request_errors_fail_once(auth, db, photo):
    upload(auth, photo)

    class BadRequest(InvoiceAdapter):
        def extract(self, images, ref):
            raise PermanentModelError("Request exceeds context capacity")

    assert process_one(db, BadRequest)
    assert not process_one(db, BadRequest)
    assert auth.get("/api/documents").json()[0]["status"] == "failed"
    with db.session() as session:
        assert session.scalar(select(Job)).attempts == 1


def test_outage_reanalysis_preserves_removed_rows_and_comparison(auth, db, photo):
    upload(auth, photo)
    process_one(db, InvoiceAdapter)
    row = auth.get("/api/transactions").json()[0]
    assert (
        auth.post(
            "/api/review",
            json={
                "action": "remove",
                "rows": [{"id": row["id"], "revision": row["revision"]}],
            },
        ).status_code
        == 200
    )
    removed = auth.get("/api/transactions").json()[0]
    assert auth.post(f"/api/documents/{row['document_id']}/retry").status_code == 200
    assert process_one(db, Offline)
    assert auth.post("/api/analysis/retry").status_code == 200
    assert process_one(db, InvoiceAdapter)
    assert auth.get("/api/transactions").json()[0] == removed
    attempts = auth.get(f"/api/documents/{row['document_id']}/attempts").json()
    assert len(attempts) == 2
    assert any("comparison" in attempt["result"] for attempt in attempts)


def test_resource_failure_migration_preserves_existing_queue(auth, db, photo):
    upload(auth, photo)
    with db.write() as session:
        job_id = session.scalar(select(Job.id))
        session.execute(text("ALTER TABLE jobs DROP COLUMN resource_failures"))
        session.execute(text("UPDATE alembic_version SET version_num='e0456d23c344'"))
    db.migrate()
    db.migrate()
    with db.session() as session:
        job = session.get(Job, job_id)
        assert job.status == "queued" and job.resource_failures == 0
    assert process_one(db, InvoiceAdapter)


def test_generic_memory_error_does_not_claim_gpu_shortage():
    from quicker.model_endpoint import check_response

    with pytest.raises(ModelUnavailable) as caught:
        check_response(httpx.Response(500, json={"error": {"message": "out of memory"}}))
    assert "GPU" not in str(caught.value)
