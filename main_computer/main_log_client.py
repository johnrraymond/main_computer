from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_MAIN_LOG_HOST = "127.0.0.1"
DEFAULT_MAIN_LOG_PORT = 8767
DEFAULT_MAIN_LOG_TIMEOUT_S = 0.25
ENV_MAIN_LOG_URL = "MAIN_COMPUTER_MAIN_LOG_URL"
ENV_MAIN_LOG_HOST = "MAIN_COMPUTER_MAIN_LOG_HOST"
ENV_MAIN_LOG_PORT = "MAIN_COMPUTER_MAIN_LOG_PORT"
ENV_MAIN_LOG_DISABLED = "MAIN_COMPUTER_MAIN_LOG_DISABLED"


OutputFunc = Callable[[str], None]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_main_log_url() -> str:
    explicit = str(os.environ.get(ENV_MAIN_LOG_URL) or "").strip()
    if explicit:
        return explicit.rstrip("/")
    host = str(os.environ.get(ENV_MAIN_LOG_HOST) or DEFAULT_MAIN_LOG_HOST).strip() or DEFAULT_MAIN_LOG_HOST
    port_text = str(os.environ.get(ENV_MAIN_LOG_PORT) or DEFAULT_MAIN_LOG_PORT).strip()
    try:
        port = int(port_text)
    except ValueError:
        port = DEFAULT_MAIN_LOG_PORT
    return f"http://{host}:{port}"


def main_log_is_disabled() -> bool:
    return str(os.environ.get(ENV_MAIN_LOG_DISABLED) or "").strip().lower() in {"1", "true", "yes", "on"}


def fallback_event_line(event: dict[str, Any]) -> str:
    payload = dict(event)
    payload.setdefault("at", now_iso())
    payload.setdefault("main_log_fallback", True)
    return json.dumps(payload, sort_keys=True, default=str)


def emit_main_log_event(
    event: dict[str, Any] | None = None,
    *,
    url: str | None = None,
    timeout_s: float = DEFAULT_MAIN_LOG_TIMEOUT_S,
    fallback_output: OutputFunc | None = None,
    fallback_on_error: bool = False,
    **fields: Any,
) -> dict[str, Any]:
    """Best-effort append to the main log service.

    This helper is intentionally fail-open.  Callers that are performing process
    orchestration should pass a small ``timeout_s`` and a local stdout/stderr
    fallback.  Any network, protocol, or JSON failure is returned to the caller
    and never raised.
    """

    if main_log_is_disabled():
        return {"ok": False, "state": "disabled", "message": "main log client is disabled"}

    payload = dict(event or {})
    payload.update(fields)
    payload.setdefault("schema_version", 1)
    payload.setdefault("at", now_iso())
    payload.setdefault("pid", os.getpid())
    payload.setdefault("process_name", Path(sys.argv[0] or "").name or "python")
    target = (url or default_main_log_url()).rstrip("/")
    if not target:
        return {"ok": False, "state": "missing-url", "message": "main log URL is not configured"}

    request_body = json.dumps({"events": [payload]}, sort_keys=True, default=str).encode("utf-8")
    request = Request(
        f"{target}/v1/log/events",
        data=request_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=max(0.001, float(timeout_s))) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except (OSError, HTTPError, URLError, TimeoutError, ValueError) as exc:
        if fallback_on_error and fallback_output is not None:
            try:
                fallback_output(fallback_event_line({**payload, "main_log_emit_failed": True, "error": str(exc)}))
            except Exception:
                pass
        return {"ok": False, "state": "emit-failed", "error": str(exc), "url": target}

    try:
        decoded = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        return {"ok": False, "state": "bad-response", "error": str(exc), "url": target, "response": raw[:500]}
    if isinstance(decoded, dict):
        decoded.setdefault("ok", True)
        decoded.setdefault("url", target)
        return decoded
    return {"ok": False, "state": "bad-response", "message": "main log service returned a non-object response", "url": target}


def healthcheck_main_log(
    *,
    url: str | None = None,
    timeout_s: float = DEFAULT_MAIN_LOG_TIMEOUT_S,
) -> dict[str, Any]:
    target = (url or default_main_log_url()).rstrip("/")
    request = Request(f"{target}/health", method="GET")
    try:
        with urlopen(request, timeout=max(0.001, float(timeout_s))) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except (OSError, HTTPError, URLError, TimeoutError, ValueError) as exc:
        return {"ok": False, "state": "healthcheck-failed", "error": str(exc), "url": target}
    try:
        decoded = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        return {"ok": False, "state": "bad-response", "error": str(exc), "url": target, "response": raw[:500]}
    if isinstance(decoded, dict):
        decoded.setdefault("url", target)
        return decoded
    return {"ok": False, "state": "bad-response", "message": "health response was not an object", "url": target}
