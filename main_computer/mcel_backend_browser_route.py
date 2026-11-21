"""Strictly localhost, same-origin HTTP bridge for backend Chromium."""
from __future__ import annotations

from http import HTTPStatus
import ipaddress
import json
import re
from urllib.parse import parse_qs, urlsplit

from main_computer.mcel_backend_browser import BrowserError, resolve_browser_address

_BROWSER_API = "/api/applications/browser"
_SESSION_RE = re.compile(r"[A-Za-z0-9_-]{24,64}\Z")
_KEY_NAMES = frozenset({"Enter", "Escape", "Backspace", "Tab", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown", "Delete", "Space"})
_OPERATIONS = frozenset({"start", "navigate", "back", "forward", "reload", "click", "scroll", "key", "type", "resize", "close"})


def browser_request_authorized(handler, *, write: bool) -> bool:
    """Deny remote clients, hostile Host, cross-origin scripts and forms."""
    try:
        client = handler.client_address[0]
        if not ipaddress.ip_address(client).is_loopback:
            return False
    except (ValueError, IndexError, TypeError):
        return False
    host = (handler.headers.get("Host") or "").lower().strip()
    if not re.fullmatch(r"(?:localhost|127\.0\.0\.1):[0-9]{1,5}", host):
        return False
    try:
        if int(host.rsplit(":", 1)[-1]) != handler.server.server_port:
            return False
    except (AttributeError, ValueError):
        return False
    origin = (handler.headers.get("Origin") or "").lower().strip()
    if write and origin != "http://" + host:
        return False
    if origin and origin != "http://" + host:
        return False
    if (handler.headers.get("Sec-Fetch-Site") or "").lower() not in {"", "same-origin", "none"}:
        return False
    if write and (handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() != "application/json":
        return False
    if write and handler.headers.get("X-MCEL-Browser-Operation") != "browser-control":
        return False
    return True


def _port_base(handler) -> str:
    return f"http://127.0.0.1:{handler.server.server_port}"


def _session_id(value: object) -> str:
    if not isinstance(value, str) or _SESSION_RE.fullmatch(value) is None:
        raise BrowserError("Invalid browser session")
    return value


def _coordinate(value: object, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise BrowserError("Invalid browser coordinate")
    return value


def _command(handler, payload: dict) -> dict:
    operation = payload.get("operation")
    if operation not in _OPERATIONS:
        raise BrowserError("Unknown browser command")
    args: dict[str, object] = {}
    if operation != "start":
        args["session"] = _session_id(payload.get("session"))
    if operation in {"navigate", "start"}:
        address = payload.get("address", "")
        # A new tab is blank unless the caller supplies a real destination.
        # This prevents Browser launching a recursive copy of the MCEL shell.
        if operation == "start" and address == "":
            args["url"] = "about:blank"
        else:
            args["url"] = resolve_browser_address(
                _port_base(handler), address,
                current_url=payload.get("current_url", "") if isinstance(payload.get("current_url"), str) else "",
            )
    if operation in {"start", "resize"}:
        args["width"] = _coordinate(payload.get("width", 1080), minimum=320, maximum=1920)
        args["height"] = _coordinate(payload.get("height", 690), minimum=240, maximum=1080)
    if operation == "click":
        args["x"] = _coordinate(payload.get("x"), minimum=0, maximum=1920)
        args["y"] = _coordinate(payload.get("y"), minimum=0, maximum=1080)
    if operation == "scroll":
        args["dx"] = _coordinate(payload.get("dx"), minimum=-1200, maximum=1200)
        args["dy"] = _coordinate(payload.get("dy"), minimum=-1200, maximum=1200)
        if "x" in payload and "y" in payload:
            args["x"] = _coordinate(payload["x"], minimum=0, maximum=1920)
            args["y"] = _coordinate(payload["y"], minimum=0, maximum=1080)
    if operation == "type":
        text = payload.get("text")
        if not isinstance(text, str) or not 1 <= len(text) <= 512 or any(ord(c) < 32 and c not in "\n\t" for c in text):
            raise BrowserError("Invalid browser input text")
        args["text"] = text
    if operation == "key":
        key = payload.get("key")
        if not isinstance(key, str) or not (len(key) == 1 and key.isprintable() or key in _KEY_NAMES):
            raise BrowserError("Invalid browser key")
        args["key"] = key
    return handler.server.backend_browser.call(operation, **args)


def handle_browser_post(handler) -> None:
    if not browser_request_authorized(handler, write=True):
        handler._send_json({"ok": False, "error": "Browser requires localhost and same-origin access"}, HTTPStatus.FORBIDDEN)
        return
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if not 1 <= length <= 4096:
            raise BrowserError("Browser request size invalid")
        payload = json.loads(handler.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise BrowserError("Expected a JSON browser command")
        if payload.get("operation") in {"surface-start", "surface-touch", "surface-close"}:
            from main_computer.mcel_scramjet_docker import ScramjetError
            try:
                manager = handler.server.scramjet_docker
                action = payload["operation"]
                if action == "surface-start":
                    result = manager.open()
                    result["base_url"] = _port_base(handler) + "/"
                else:
                    token = _session_id(payload.get("session"))
                    result = manager.touch(token) if action == "surface-touch" else manager.release(token)
            except ScramjetError as exc:
                handler._send_json({"ok": False, "error": str(exc)[:360]}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            handler._send_json(result)
            return
        result = _command(handler, payload)
        if payload.get("operation") == "start":
            result["base_url"] = _port_base(handler) + "/"
        handler._send_json(result)
    except (BrowserError, ValueError, TypeError, UnicodeError) as exc:
        handler._send_json({"ok": False, "error": str(exc)[:360]}, HTTPStatus.BAD_REQUEST)


def handle_browser_get(handler) -> None:
    if not browser_request_authorized(handler, write=False):
        handler._send_json({"ok": False, "error": "Browser requires localhost and same-origin access"}, HTTPStatus.FORBIDDEN)
        return
    try:
        params = parse_qs(urlsplit(handler.path).query)
        token = _session_id(params.get("session", [None])[0])
        result = handler.server.backend_browser.call("frame", session=token)
        handler._send_json(result)
    except BrowserError as exc:
        handler._send_json({"ok": False, "error": str(exc)[:360]}, HTTPStatus.BAD_REQUEST)
