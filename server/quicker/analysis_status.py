"""Analysis availability without sending documents or inference requests to the model."""

import threading
import time

import httpx
from sqlalchemy import select

from . import model_availability
from .contracts import ModelConfig
from .db import Job, Setting
from .model_endpoint import ModelError, ModelUnavailable, check_bonsai_ready, check_response

HEARTBEAT_TTL = 15


def heartbeat(db, worker_id, active, capacity):
    now = int(time.time())
    with db.write() as session:
        stored = session.get(Setting, "analysis_workers")
        workers = {
            key: value for key, value in (stored.value if stored else {}).items()
            if now - value["seen"] <= HEARTBEAT_TTL
        }
        if capacity:
            workers[worker_id] = {"seen": now, "active": active, "capacity": capacity}
        else:
            workers.pop(worker_id, None)
        if stored:
            stored.value = workers
        else:
            session.add(Setting(key="analysis_workers", value=workers))


def worker_status(session):
    now = int(time.time())
    stored = session.get(Setting, "analysis_workers")
    workers = list((stored.value if stored else {}).values())
    online = [w for w in workers if now - w["seen"] <= HEARTBEAT_TTL]
    jobs = list(session.scalars(select(Job).where(Job.status.in_(["queued", "running"]))))
    return {
        "worker": {
            "online": bool(online),
            "last_seen": max((w["seen"] for w in workers), default=None),
            "capacity": sum(w["capacity"] for w in online),
        },
        "queue": {
            "waiting": sum(j.status == "queued" for j in jobs),
            "analyzing": sum(j.status == "running" for j in jobs),
            "retrying": sum(
                j.status == "queued" and (
                    j.available > now or bool(model_availability.status(session, ModelConfig.model_validate(j.config))["reason"])
                ) for j in jobs
            ),
        },
    }


class ModelConnection:
    """Share a short connection probe across polling browsers, keyed by saved settings."""

    def __init__(self):
        self.lock = threading.Lock()
        self.key = None
        self.checked = 0
        self.result = None

    def status(self, config):
        key = config.model_dump_json()
        with self.lock:
            if key == self.key and time.monotonic() - self.checked < 30:
                return self.result
            headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
            try:
                # /models is supported by the configured OpenAI-compatible and
                # LM Studio APIs. It checks reachability, not vision capability.
                with httpx.Client(timeout=3, follow_redirects=False) as client:
                    response = client.get(config.url + "/models", headers=headers)
                if response.is_success:
                    payload = response.json()
                    if not isinstance(payload, dict) or not any(
                        isinstance(payload.get(k), list) for k in ("data", "models")
                    ):
                        raise ValueError("Unexpected models response")
                    state, message = "connected", "Model server is reachable."
                    if check_bonsai_ready(config):
                        message = "Model is loaded. Inference capacity is checked when analysis runs."
                elif response.status_code in (401, 403):
                    state, message = "unreachable", "Model server rejected authentication. Check the saved API key."
                elif response.status_code == 404:
                    state, message = "unknown", "This endpoint does not support the connection check. Analysis may still work."
                else:
                    check_response(response)
                    state, message = "unknown", "Model server returned an unsupported response."
            except ModelUnavailable as exc:
                state, message = exc.reason, str(exc)
            except ModelError as exc:
                state, message = "configuration_error", str(exc)
            except httpx.HTTPError:
                state, message = "unreachable", "Cannot reach the model server. Check that it is running and the saved connection settings are correct."
            except (ValueError, TypeError):
                state, message = "unknown", "The connection check returned an unexpected response. Check the saved endpoint settings."
            self.key, self.checked = key, time.monotonic()
            self.result = {
                "state": state, "message": message, "checked_at": int(time.time()), "name": config.model,
            }
            return self.result
