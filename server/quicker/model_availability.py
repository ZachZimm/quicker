"""Shared persistent cooldowns; database write transactions serialize recovery probes."""

import hashlib
import json
import time

from .contracts import ModelConfig
from .db import Setting


def key(config):
    identity = [config.url, config.model, config.api_key]
    return "model_availability:" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def read(session, config):
    row = session.get(Setting, key(config))
    return dict(row.value) if row else {}


def save(session, config, value):
    row = session.get(Setting, key(config))
    if row:
        row.value = value
    else:
        session.add(Setting(key=key(config), value=value))


def acquire(session, config, claim, lease_until, now):
    state = read(session, config)
    if state.get("failures", 0):
        if max(state.get("retry_at", 0), state.get("probe_until", 0)) > now:
            return None
        state.update(probe_claim=claim, probe_until=lease_until)
        save(session, config, state)
    return state.get("generation", 0)


def unavailable(session, config, error, claim=None):
    state = read(session, config)
    failures = state.get("failures", 0) + 1
    now = int(time.time())
    provider_until = max(state.get("provider_until", 0), now + error.retry_after)
    state.update(
        failures=failures,
        generation=state.get("generation", 0) + 1,
        reason=error.reason,
        message=str(error),
        retry_at=max(now + min(300, 30 * 2 ** min(failures - 1, 4)), provider_until),
        provider_until=provider_until,
    )
    if state.get("probe_claim") == claim:
        state.update(probe_claim=None, probe_until=0)
    save(session, config, state)


def completed(session, config, generation, claim, success):
    state = read(session, config)
    if success:
        state["last_success"] = int(time.time())
    # An older request must not clear a newer outage or someone else's probe.
    if state.get("generation", 0) == generation and state.get("probe_claim") in (None, claim):
        state.update(failures=0, retry_at=0, provider_until=0, probe_claim=None, probe_until=0)
        state.pop("reason", None)
        state.pop("message", None)
    elif state.get("probe_claim") == claim:
        state.update(probe_claim=None, probe_until=0)
    save(session, config, state)


def retry_now(session, config):
    state = read(session, config)
    # A manual retry cannot release a live recovery probe or bypass Retry-After.
    state["retry_at"] = state.get("provider_until", 0)
    save(session, config, state)


def status(session, config):
    state = read(session, config)
    return {
        "reason": state.get("reason"),
        "message": state.get("message"),
        "retry_at": max(state.get("retry_at", 0), state.get("probe_until", 0)) or None,
        "recovering": state.get("probe_until", 0) > time.time(),
        "last_success": state.get("last_success"),
    }


def job_status(session, job, config_revision):
    if job is None:
        return None
    availability = status(session, ModelConfig.model_validate(job.config))
    queued = job.status == "queued"
    return {
        "attempts": job.attempts,
        "retry_at": max(job.available, availability["retry_at"] or 0) if queued else None,
        "waiting_reason": availability["message"] if queued else None,
        "recovering": availability["recovering"] if queued else False,
        "uses_current_settings": job.config.get("revision") == config_revision,
    }
