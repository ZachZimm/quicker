"""Sanitized model failures and lightweight readiness checks."""

import time
from email.utils import parsedate_to_datetime

import httpx


class ModelError(RuntimeError):
    pass


class PermanentModelError(ModelError):
    """A request or configuration needs intervention rather than another attempt."""


class ModelUnavailable(ModelError):
    def __init__(self, message, reason="offline", retry_after=0):
        super().__init__(message)
        self.reason = reason
        self.retry_after = retry_after
        self.document_request = True


def retry_delay(response):
    value = response.headers.get("retry-after", "")
    try:
        return max(0, int(value))
    except ValueError:
        try:
            return max(0, int(parsedate_to_datetime(value).timestamp() - time.time()))
        except (ValueError, TypeError, OverflowError):
            return 0


def check_response(response):
    if not response.is_error:
        return
    # Inspect only to classify; never persist/return provider text or credentials.
    try:
        error = response.json().get("error", {})
        description = str(error).lower()[:8192]
    except (ValueError, AttributeError):
        description = ""
    delay = retry_delay(response)
    code = response.status_code
    if code in (401, 403):
        raise PermanentModelError("Model server rejected authentication. Check the saved API key.")
    if any(
        s in description for s in ("out of memory", "out_of_memory", "insufficient vram", "cuda error: out")
    ):
        resource = "GPU memory" if any(s in description for s in ("cuda", "gpu", "vram")) else "memory"
        raise ModelUnavailable(f"Model server reported insufficient {resource}.", "memory", delay)
    if "loading model" in description or "model is loading" in description:
        raise ModelUnavailable("Model is loading. Analysis will resume automatically.", "loading", delay)
    if any(s in description for s in ("context length", "context size", "context window", "too many tokens")):
        raise PermanentModelError(
            "This request exceeds model capacity. Check context/output limits or use fewer pages."
        )
    if code in (408, 429) or code >= 500:
        raise ModelUnavailable("Model server is busy or temporarily unavailable.", "busy", delay)
    raise PermanentModelError(
        f"Model endpoint returned HTTP {code}. Check model, vision support and request settings."
    )


def connection_error(exc):
    if isinstance(exc, httpx.TimeoutException):
        return ModelUnavailable("Model request timed out. Analysis will retry automatically.", "timeout")
    return ModelUnavailable("Model server is offline or unreachable. Analysis will resume automatically.")


def check_bonsai_ready(config):
    """Bonsai uses llama.cpp health; other providers retain inference-based checks."""
    if not config.model.lower().startswith("bonsai"):
        return None
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    try:
        with httpx.Client(timeout=3, follow_redirects=False) as client:
            response = client.get(config.url + "/health", headers=headers)
        if response.status_code in (404, 405):
            return None  # Compatible proxies may expose only the inference API.
        check_response(response)
        data = response.json()
        if data.get("status") in ("loading", "busy"):
            raise ModelUnavailable("Model is loading or busy. Analysis will resume automatically.", "loading")
        return data.get("status") == "ok"
    except ModelUnavailable as exc:
        # A health check has no document payload. Memory failures here cannot
        # establish that a particular document is too large.
        exc.document_request = False
        raise
    except httpx.HTTPError as exc:
        raise connection_error(exc) from exc
    except (ValueError, AttributeError):
        return None  # An unsupported health response is not evidence of readiness.
