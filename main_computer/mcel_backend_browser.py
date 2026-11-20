"""Local-only backend Chromium session bridge for the MCEL Browser app.

Chromium and all visited-page JS execute in a separate Playwright context on a
single backend worker thread. The frontend receives pixels and metadata, never
third-party HTML or a same-origin proxy document.
"""
from __future__ import annotations

from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import dataclass
import base64
import os
import shutil
import queue
import re
import secrets
import threading
import time
from urllib.parse import urljoin, urlsplit


MAX_SESSIONS = 4
SESSION_IDLE_SECONDS = 20 * 60
MAX_ADDRESS_LENGTH = 2048
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 690
HTTP_PORT = re.compile(r"^(?:localhost|127\.0\.0\.1)(?::[0-9]{1,5})?(?:[/?#]|$)", re.I)
HOSTNAME = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}(?::[0-9]{1,5})?(?:[/?#]|$)", re.I)


class BrowserError(Exception):
    pass


def resolve_browser_address(base_url: str, entry: str, *, current_url: str = "") -> str:
    """Resolve relative user addresses against the actual localhost service origin.

    A leading slash and a plain path both resolve at the service root. Only
    explicit ./ or ../ and fragment/query-only entries resolve against the
    current document. A full http(s) address is never rewritten to localhost.
    """
    if not isinstance(entry, str) or len(entry) > MAX_ADDRESS_LENGTH:
        raise BrowserError("Invalid browser address length")
    value = entry.strip()
    if not value:
        return base_url.rstrip("/") + "/"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value) or "\\" in value:
        raise BrowserError("Invalid browser address")
    if value.startswith("//"):
        raise BrowserError("Protocol-relative addresses are not accepted")
    if HTTP_PORT.match(value):
        result = "http://" + value
    elif HOSTNAME.match(value):
        result = "https://" + value
    elif value.lower().startswith(("http://", "https://")):
        result = value
    elif ":" in value.split("/", 1)[0]:
        raise BrowserError("Only HTTP and HTTPS addresses are allowed")
    elif value.startswith(("./", "../", "?", "#")):
        result = urljoin(current_url or base_url.rstrip("/") + "/", value)
    else:
        result = urljoin(base_url.rstrip("/") + "/", value.lstrip("/"))
    parsed = urlsplit(result)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise BrowserError("Only HTTP(S) browser URLs without embedded credentials are allowed")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise BrowserError("Invalid browser port") from exc
    return result


def _size(value: object, fallback: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return fallback
    return max(low, min(high, value))


@dataclass
class _Session:
    context: object
    page: object
    used_at: float
    navigation_error: str = ""


class BackendBrowser:
    """Serializes every Playwright operation onto its owning thread."""

    def __init__(self) -> None:
        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False

    def call(self, action: str, **kwargs: object) -> dict:
        with self._lock:
            if self._closed:
                raise BrowserError("Backend browser is closed")
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="mcel-backend-browser", daemon=True)
                self._thread.start()
        result: Future = Future()
        self._jobs.put((action, kwargs, result))
        try:
            return result.result(timeout=35)
        except FutureTimeout as exc:
            raise BrowserError(
                f"Backend Chromium {action} timed out after 35 seconds; "
                "the browser worker may be stalled. Restart the Main Computer server if it persists."
            ) from exc

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            worker = self._thread
            if worker is not None:
                self._jobs.put(None)
        if worker is not None and threading.current_thread() is not worker:
            worker.join(timeout=8)

    @staticmethod
    def _snapshot(session: _Session) -> dict:
        page = session.page
        # A stalled remote document must not consume the entire 35-second
        # bridge deadline before the screenshot is attempted.
        try:
            title = page.title()[:256]
        except Exception:
            title = ""
        try:
            can_go_back = bool(page.evaluate("() => history.length > 1"))
        except Exception:
            can_go_back = False
        return {
            "ok": True,
            "navigation_error": session.navigation_error,
            "url": page.url,
            "title": title,
            "can_go_back": can_go_back,
            "image": base64.b64encode(page.screenshot(type="jpeg", quality=68,
                animations="disabled", timeout=6000)).decode("ascii"),
            "width": page.viewport_size["width"],
            "height": page.viewport_size["height"],
        }

    def _run(self) -> None:
        playwright = None
        browser = None
        sessions: dict[str, _Session] = {}
        try:
            while True:
                job = self._jobs.get()
                if job is None:
                    break
                action, args, future = job
                try:
                    if playwright is None:
                        from playwright.sync_api import sync_playwright
                        playwright = sync_playwright().start()
                    if browser is None:
                        launch_error = None
                        # Prefer Playwright's matched Chromium; a system Chromium
                        # is a supported fallback when its managed bundle is absent.
                        executable_candidates = [None]
                        configured = os.environ.get("MCEL_BROWSER_CHROMIUM_EXECUTABLE", "").strip()
                        if configured:
                            executable_candidates.append(configured)
                        for name in ("chromium", "chromium-browser", "google-chrome", "chrome", "msedge"):
                            path = shutil.which(name)
                            if path and path not in executable_candidates:
                                executable_candidates.append(path)
                        if os.name == "nt":
                            for root in (os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", "")):
                                for suffix in ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe"):
                                    path = os.path.join(root, suffix) if root else ""
                                    if path and os.path.isfile(path) and path not in executable_candidates:
                                        executable_candidates.append(path)
                        for executable in executable_candidates:
                            try:
                                options = {"headless": True, "args": ["--disable-dev-shm-usage"]}
                                if executable is not None:
                                    options["executable_path"] = executable
                                browser = playwright.chromium.launch(**options)
                                break
                            except Exception as exc:
                                launch_error = exc
                        if browser is None:
                            raise BrowserError(
                                "Chromium unavailable. Install with "
                                "python -m playwright install chromium, or configure "
                                "MCEL_BROWSER_CHROMIUM_EXECUTABLE. " + str(launch_error)[:110]
                            )
                    now = time.monotonic()
                    for token, expired in list(sessions.items()):
                        if now - expired.used_at > SESSION_IDLE_SECONDS:
                            expired.context.close()
                            del sessions[token]
                    token = args.get("session")
                    if action == "start":
                        if len(sessions) >= MAX_SESSIONS:
                            raise BrowserError("Backend browser session limit reached")
                        token = secrets.token_urlsafe(24)
                        context = browser.new_context(
                            viewport={"width": _size(args.get("width"), DEFAULT_WIDTH, 320, 1920),
                                      "height": _size(args.get("height"), DEFAULT_HEIGHT, 240, 1080)},
                            accept_downloads=False,
                            permissions=[],
                        )
                        page = context.new_page()
                        page.set_default_timeout(4000)
                        page.on("popup", lambda popup: popup.close())
                        session = _Session(context, page, now)
                        sessions[token] = session
                        # Start at about:blank rather than recursively opening
                        # the full MCEL desktop inside its own Browser. A deep
                        # link may request a specific page on first launch.
                        try:
                            if args["url"] != "about:blank":
                                try:
                                    page.goto(str(args["url"]), wait_until="domcontentloaded", timeout=12000)
                                except Exception as exc:
                                    session.navigation_error = "Navigation failed: " + str(exc).splitlines()[0][:180]
                            payload = self._snapshot(session)
                        except Exception:
                            context.close()
                            del sessions[token]
                            raise
                        payload["session"] = token
                    else:
                        if not isinstance(token, str) or token not in sessions:
                            raise BrowserError("Browser session unavailable; reopen Browser")
                        session = sessions[token]
                        session.used_at = now
                        page = session.page
                        if action in {"navigate", "back", "forward", "reload"}:
                            session.navigation_error = ""
                        if action == "navigate":
                            try:
                                page.goto(str(args["url"]), wait_until="domcontentloaded", timeout=12000)
                            except Exception as exc:
                                session.navigation_error = "Navigation failed: " + str(exc).splitlines()[0][:180]
                        elif action == "back":
                            page.go_back(wait_until="domcontentloaded", timeout=12000)
                        elif action == "forward":
                            page.go_forward(wait_until="domcontentloaded", timeout=12000)
                        elif action == "reload":
                            page.reload(wait_until="domcontentloaded", timeout=12000)
                        elif action == "click":
                            page.mouse.click(int(args["x"]), int(args["y"]))
                        elif action == "scroll":
                            if "x" in args and "y" in args:
                                page.mouse.move(int(args["x"]), int(args["y"]))
                            page.mouse.wheel(int(args["dx"]), int(args["dy"]))
                        elif action == "type":
                            page.keyboard.insert_text(str(args["text"]))
                        elif action == "key":
                            key = args["key"]
                            if len(key) == 1:
                                page.keyboard.insert_text(key)
                            else:
                                page.keyboard.press(key)
                        elif action == "resize":
                            page.set_viewport_size({
                                "width": _size(args.get("width"), DEFAULT_WIDTH, 320, 1920),
                                "height": _size(args.get("height"), DEFAULT_HEIGHT, 240, 1080),
                            })
                        elif action == "frame":
                            pass
                        elif action == "close":
                            session.context.close()
                            del sessions[token]
                            future.set_result({"ok": True, "closed": True})
                            continue
                        else:
                            raise BrowserError("Unrecognized browser operation")
                        payload = self._snapshot(session)
                    future.set_result(payload)
                except Exception as exc:
                    # A failed Chromium launch remains recoverable. Never
                    # return a traceback or a remote document to the client.
                    if not future.done():
                        future.set_exception(BrowserError(str(exc)[:360]))
        finally:
            for session in sessions.values():
                try:
                    session.context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            if playwright is not None:
                try:
                    playwright.stop()
                except Exception:
                    pass
