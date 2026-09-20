import time

import httpx
import pytest
from quicker.analysis_status import HEARTBEAT_TTL, ModelConnection, heartbeat, worker_status
from quicker.contracts import ModelConfig
from quicker.db import Job, Setting
from quicker.worker import process_one
from sqlalchemy import select
from test_workflow import InvoiceAdapter, upload


def test_worker_heartbeat_expiry_multiple_workers_and_queue(auth, db, photo, monkeypatch):
    upload(auth, photo)
    with db.session() as session:
        before = worker_status(session)
    assert not before["worker"]["online"]
    assert before["queue"] == {"waiting": 1, "analyzing": 0, "retrying": 0}
    heartbeat(db, "first", 0, 1)
    heartbeat(db, "second", 0, 2)
    with db.session() as session:
        assert worker_status(session)["worker"]["capacity"] == 3
    heartbeat(db, "first", 0, 0)
    with db.session() as session:
        status = worker_status(session)
        assert status["worker"]["capacity"] == 2
    now = time.time()
    monkeypatch.setattr("quicker.analysis_status.time.time", lambda: now + HEARTBEAT_TTL + 1)
    with db.session() as session:
        assert not worker_status(session)["worker"]["online"]


def test_analysis_status_authenticated_cached_and_redacts_provider_errors(auth, client, monkeypatch):
    requests = []
    original = httpx.Client

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "local-model"}]})

    monkeypatch.setattr("quicker.analysis_status.httpx.Client", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handle),
    ))
    first = auth.get("/api/analysis-status")
    assert first.status_code == 200
    assert first.json()["model"]["state"] == "connected"
    assert auth.get("/api/analysis-status").json() == first.json()
    assert len(requests) == 1
    config = auth.get("/api/settings").json()
    config.pop("has_api_key")
    config["api_key"] = "private-test-key"
    assert auth.put("/api/settings", json=config).status_code == 200
    assert auth.get("/api/analysis-status").status_code == 200
    assert len(requests) == 2
    assert requests[-1].headers["Authorization"] == "Bearer private-test-key"
    assert "private-test-key" not in auth.get("/api/analysis-status").text
    auth.post("/api/logout")
    assert client.get("/api/analysis-status").status_code == 401


@pytest.mark.parametrize("response,expected", [
    (httpx.Response(401, text="secret-key echoed by provider"), "unreachable"),
    (httpx.Response(404), "unknown"),
    (httpx.Response(200, text="not JSON"), "unknown"),
    (httpx.Response(200, json={"unexpected": True}), "unknown"),
    (httpx.Response(200, json={"models": []}), "connected"),
])
def test_model_probe_distinguishes_connection_from_inference(monkeypatch, response, expected):
    original = httpx.Client
    monkeypatch.setattr("quicker.analysis_status.httpx.Client", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(lambda request: response),
    ))
    result = ModelConnection().status(ModelConfig(api_key="secret-key"))
    assert result["state"] == expected
    assert "secret-key" not in str(result)


def test_model_probe_timeout_is_sanitized(monkeypatch):
    original = httpx.Client

    def fail(request):
        raise httpx.ReadTimeout("secret-provider-details")

    monkeypatch.setattr("quicker.analysis_status.httpx.Client", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(fail),
    ))
    result = ModelConnection().status(ModelConfig())
    assert result["state"] == "unreachable"
    assert "secret-provider-details" not in str(result)


def test_document_waiting_metadata_and_automatic_retry(auth, db, photo):
    upload(auth, photo)
    initial = auth.get("/api/documents").json()[0]
    assert initial["status"] == "queued"
    assert initial["analysis"]["attempts"] == 0
    assert initial["analysis"]["uses_current_settings"]
    with db.write() as session:
        session.add(Setting(key="model", value=ModelConfig(revision=2).model_dump()))
        job = session.scalar(select(Job))
        job.attempts = 1
        job.available = int(time.time()) + 30
    waiting = auth.get("/api/documents").json()[0]
    assert waiting["analysis"]["retry_at"] > time.time()
    assert not waiting["analysis"]["uses_current_settings"]
    with db.session() as session:
        assert worker_status(session)["queue"]["retrying"] == 1
    # Backoff ends and the original automatic job can complete without another upload.
    with db.write() as session:
        session.scalar(select(Job)).available = 0
    assert process_one(db, InvoiceAdapter)
    completed = auth.get("/api/documents").json()[0]
    assert completed["status"] == "ready"
    assert completed["analysis"] is None


def test_worker_main_reports_availability_and_cleans_up(db, monkeypatch):
    from quicker import worker

    class StopWorker(Exception):
        pass

    def stop_after_heartbeat(seconds):
        with db.session() as session:
            assert worker_status(session)["worker"]["online"]
        raise StopWorker()

    monkeypatch.setattr("sys.argv", ["quicker.worker"])
    monkeypatch.setattr(worker, "Database", lambda: db)
    monkeypatch.setattr(worker.time, "sleep", stop_after_heartbeat)
    with pytest.raises(StopWorker):
        worker.main()
    with db.session() as session:
        assert not worker_status(session)["worker"]["online"]
