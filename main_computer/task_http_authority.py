"""Bounded same-machine Task Manager HTTP/CSRF capability boundary.

This is a loopback-browser session check, not authentication against other
programs running under the same OS user. No token is present in application IR.
"""
from __future__ import annotations

from http import HTTPStatus
import hmac
import ipaddress
import re
import secrets
import threading
import time
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

_LOCK = threading.RLock()
_SESSIONS: dict[str, tuple[str, float]] = {}
_TTL = 900.0
_COOKIE = "mcel_task_session"


def _local_authority(handler) -> bool:
    peer = handler.client_address[0] if handler.client_address else ""
    try:
        if not ipaddress.ip_address(peer).is_loopback:
            return False
    except ValueError:
        return False
    host = (handler.headers.get("Host") or "").lower().strip()
    allowed = {"localhost", "127.0.0.1", "[::1]"}
    try:
        port = int(handler.server.server_address[1])
    except (ValueError, TypeError, IndexError, AttributeError):
        return False
    if host not in {f"{h}:{port}" for h in allowed}:
        return False
    origin = handler.headers.get("Origin")
    if origin is not None:
        try:
            parts = urlsplit(origin)
        except ValueError:
            return False
        if parts.scheme not in {"http", "https"} or parts.netloc.lower() != host or parts.path or parts.query or parts.fragment:
            return False
    fetch_site = handler.headers.get("Sec-Fetch-Site", "").lower()
    if fetch_site not in {"", "same-origin", "none"}:
        return False
    return True


def issue(handler) -> None:
    if not _local_authority(handler):
        handler._send_json({"ok": False, "error": "caller-not-authorized"}, HTTPStatus.FORBIDDEN)
        return
    session = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    with _LOCK:
        now = time.monotonic()
        for ident, (_, expires) in list(_SESSIONS.items()):
            if expires <= now:
                del _SESSIONS[ident]
        _SESSIONS[session] = (csrf, now + _TTL)
    # HttpOnly: cookie not readable by JS; CSRF appears only in same-origin JSON.
    # Do not put either value in IR, URLs, logs, or parity traces.
    from json import dumps
    payload = dumps({"ok": True, "csrf": csrf, "expires_in": int(_TTL)}).encode()
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Set-Cookie", f"{_COOKIE}={session}; Path=/api/applications/task; HttpOnly; SameSite=Strict" +
                        ("; Secure" if handler.headers.get("X-Forwarded-Proto", "").lower() == "https" else ""))
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def require(handler) -> bool:
    if not _local_authority(handler):
        handler._send_json({"ok": False, "error": "caller-not-authorized"}, HTTPStatus.FORBIDDEN)
        return False
    if handler.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
        handler._send_json({"ok": False, "error": "json-required"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        return False
    try:
        size = int(handler.headers.get("Content-Length", ""))
    except ValueError:
        size = 0
    if not (0 < size <= 16384):
        handler._send_json({"ok": False, "error": "invalid-body-length"}, HTTPStatus.BAD_REQUEST)
        return False
    cookies = SimpleCookie()
    try:
        cookies.load(handler.headers.get("Cookie", ""))
        session = cookies[_COOKIE].value if _COOKIE in cookies else ""
    except Exception:
        session = ""
    csrf = handler.headers.get("X-MCEL-Task-CSRF", "")
    with _LOCK:
        entry = _SESSIONS.get(session)
        valid = bool(entry and entry[1] > time.monotonic() and hmac.compare_digest(entry[0], csrf))
    if not valid:
        handler._send_json({"ok": False, "error": "task-session-required"}, HTTPStatus.FORBIDDEN)
    return valid
