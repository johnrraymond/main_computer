"""Backend browser is isolated from legacy launcher and URL route authority."""
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from main_computer.mcel_backend_browser import BackendBrowser, BrowserError, resolve_browser_address
from main_computer.mcel_backend_browser_route import _command, browser_request_authorized
from main_computer.mcel_applications_shell_host import (
    compile_applications_shell,
    legacy_desktop_reference,
    render_applications_shell_candidate,
)
from main_computer.mcel_browser_host import render_browser_page


BASE = "http://127.0.0.1:8765"


@pytest.mark.parametrize("value,expected", [
    ("", BASE + "/"),
    ("/", BASE + "/"),
    ("/applications", BASE + "/applications"),
    ("applications/browser", BASE + "/applications/browser"),
    ("/api/example?one=1", BASE + "/api/example?one=1"),
    ("?one=1", BASE + "/dir/page?one=1"),
    ("../other", BASE + "/other"),
    ("localhost:8765/docs", "http://localhost:8765/docs"),
    ("127.0.0.1:5000/path", "http://127.0.0.1:5000/path"),
    ("https://example.org/entry", "https://example.org/entry"),
    ("example.org/entry", "https://example.org/entry"),
])
def test_address_resolution(value, expected):
    assert resolve_browser_address(BASE, value, current_url=BASE + "/dir/page") == expected


@pytest.mark.parametrize("value", [
    "file:///etc/passwd", "javascript:alert(1)", "data:text/html,Hi", "ftp://example.com",
    "//example.org", "http://user:pw@example.com", "http://127.0.0.1:70000",
    "something\\bad", "/test\nHeader:bad", "a" * 2049,
])
def test_disallow_non_http_or_dangerous_addresses(value):
    with pytest.raises(BrowserError):
        resolve_browser_address(BASE, value)


def fake_handler(host="127.0.0.1:8765", origin="http://127.0.0.1:8765", client="127.0.0.1", **headers):
    browser = Mock()
    browser.call.return_value = {"ok":True,"image":"ZmFrZQ==","url":BASE + "/applications"}
    return SimpleNamespace(server=SimpleNamespace(server_port=8765,backend_browser=browser),
        client_address=(client,9999),headers={"Host":host,"Origin":origin,
        "Content-Type":"application/json","X-MCEL-Browser-Operation":"browser-control",**headers})


def test_same_origin_and_locality_required():
    assert browser_request_authorized(fake_handler(), write=True)
    for overrides in (
        {"origin":"http://other.invalid"},
        {"host":"evil.invalid:8765"},
        {"host":"127.0.0.1:5555"},
        {"client":"192.168.1.1"},
        {"Sec-Fetch-Site":"cross-site"},
        {"X-MCEL-Browser-Operation":"wrong"},
        {"Content-Type":"text/plain"},
    ):
        assert not browser_request_authorized(fake_handler(**overrides), write=True)


def test_bridge_resolves_on_backend_and_does_not_proxy_raw_html():
    handler=fake_handler()
    result=_command(handler,{"operation":"start","address":"/applications", "width":720,"height":480})
    assert result["image"] == "ZmFrZQ=="
    handler.server.backend_browser.call.assert_called_once_with(
        "start",url=BASE + "/applications",width=720,height=480)


def test_unverified_session_and_key_rejected_before_playwright():
    handler=fake_handler()
    with pytest.raises(BrowserError):
        _command(handler,{"operation":"click","session":"x", "x":12,"y":34})
    with pytest.raises(BrowserError):
        _command(handler,{"operation":"key","session":"X"*32, "key":"Control+Shift+J"})
    handler.server.backend_browser.call.assert_not_called()


def test_browser_addition_preserves_original_desktop_oracle():
    reference=legacy_desktop_reference()
    assert len(reference["icons"]) == 21
    _, compiled = compile_applications_shell()
    assert len(compiled["apps"]) == len(reference["launcher"])
    original=render_applications_shell_candidate()
    promoted=render_applications_shell_candidate(canonical_promoted=True)
    assert 'data-shell-launch="browser"' not in original
    assert 'data-shell-launch="browser"' in promoted
    assert 'href="/applications/browser"' in promoted
    assert '"mount":"legacy","launcher":true' in promoted


def test_browser_child_page_uses_isolated_frontend_execution():
    html=render_browser_page()
    assert 'id="browser-frame"' in html
    assert "/api/applications/browser/control" in html
    assert "surface-start" in html
    assert "/api/applications/browser/frame" not in html
    assert 'srcdoc=' not in html and 'sandbox="allow-scripts allow-same-origin' in html


def test_new_browser_tab_starts_blank_instead_of_loading_recursive_mcel_shell():
    handler = fake_handler()
    _command(handler, {"operation": "start", "address": "", "width": 720, "height": 480})
    handler.server.backend_browser.call.assert_called_once_with(
        "start", url="about:blank", width=720, height=480,
    )


def test_browser_deep_link_route_preserves_host_and_child_navigation():
    from pathlib import Path
    source = Path("main_computer/viewport_state.py").read_text(encoding="utf-8")
    route_dispatch = Path("main_computer/viewport_route_dispatch.py").read_text(encoding="utf-8")
    shell = Path("main_computer/web/applications/scripts/mcel-applications-shell.js").read_text(encoding="utf-8")
    page = render_browser_page()
    assert 'if candidate == "browser":' in source
    assert 'if len(parts) > 4:' in source  # Checked only after the Browser case.
    assert 'route_path.startswith("/applications/browser/")' in route_dispatch
    assert 'target.search = location.search' in shell
    assert 'target.hash = location.hash' in shell
    assert 'function requestedAddress()' in page
    assert 'function syncAddressRoute(remoteUrl)' in page
    assert "host.history.replaceState(host.history.state, '', canonical)" in page
    assert 'const initial = requestedAddress()' in page
    assert 'setStatus((error.message || String(error)) + ". Install Chromium' not in page


def test_browser_timeout_error_names_operation_and_does_not_misdiagnose_missing_binary():
    from concurrent.futures import Future
    from unittest.mock import patch
    from queue import Queue
    browser = BackendBrowser()
    browser._thread = Mock()
    browser._jobs = Queue()
    with patch.object(Future, "result", side_effect=TimeoutError):
        with pytest.raises(BrowserError, match="Chromium frame timed out.*worker") as failure:
            browser.call("frame", session="X" * 32)
    assert "install" not in str(failure.value).lower()
    browser._closed = True



def test_canonical_application_router_resolves_unbounded_browser_destination():
    # viewport_state imports the full graphical page bundle (including game
    # manifests absent from export snapshots). Execute only its real routing
    # functions with an isolated table, without mocking the function bodies.
    import ast
    from urllib.parse import urlsplit

    source = Path("main_computer/viewport_state.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {"_application_route_parts", "_application_route_target"}
    routing = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in routing} == names
    scope = {
        "urlsplit": urlsplit,
        "APPLICATION_ROUTE_PREFIXES": {"/applications", "/apps", "/app"},
        "APPLICATION_ROUTE_DEFAULT": "desktop",
        "APPLICATION_ROUTE_ALIASES": {},
        "APPLICATION_ROUTE_NAMES": {"browser", "task-manager", "desktop"},
        "APPLICATION_TASK_MANAGER_TABS": {"connections"},
        "APPLICATION_WEBSITE_BUILDER_ROUTE": "website-builder",
    }
    exec(compile(ast.Module(body=routing, type_ignores=[]), "viewport_state.py", "exec"), scope)
    route = scope["_application_route_target"]
    for destination in (
        "/applications/browser",
        "/applications/browser/https://youtube.com",
        "/applications/browser/https://youtube.com/watch?v=123&t=3",
        "/applications/browser/https://youtube.com/one/two/three/four/five",
    ):
        assert route(destination) == "browser", destination
    assert route("/applications/browserish/https://youtube.com") is None
    assert route("/applications/task-manager/not-a-tab") is None
