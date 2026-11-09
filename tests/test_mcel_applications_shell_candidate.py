"""Isolated, non-promoting acceptance for the MCEL applications parent shell.

This test never invokes AI providers, filesystem writes or process actions.
The browser fixture supplies inert child pages under canonical route names.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from main_computer.mcel_applications_shell_host import (
    APP_ID, ROUTE, SEMANTIC_APPS, compile_applications_shell,
    render_applications_shell_candidate,
)


def test_compiled_shell_owns_region_hierarchy_without_child_effects():
    ir, registry = compile_applications_shell()
    assert ir["application"]["appId"] == APP_ID
    assert not ir["capabilities"] and not ir["effects"] and not ir["intents"]
    assert len(ir["layouts"]) == 1
    assert ir["layouts"][0]["layoutTree"]["schema"] == "mcel.layout-tree.v1"
    assert ir["layouts"][0]["layoutTree"]["root"]["selector"] == ".mcel-applications-shell"
    regions = ir["surfaces"][0]["interfaceIntent"]["regions"]
    assert {r["id"].split(".")[-1] for r in regions} == {
        "application", "masthead", "shell", "launcher", "workspace",
        "workspace-header", "application-mount", "status",
    }
    ids = {r["id"] for r in regions}
    assert all(r.get("parentId") is None or r["parentId"] in ids for r in regions)
    assert {x["id"] for x in registry["apps"] if x["mount"] == "semantic"} == SEMANTIC_APPS
    assert all(x["route"].startswith("/applications") for x in registry["apps"])


def test_rendered_shell_binds_compiled_ir_and_preserves_legacy_routes():
    page = render_applications_shell_candidate()
    assert 'id="mcel-applications-shell-ir"' in page
    assert 'id="mcel-shell-launcher-panel"' in page
    assert 'id="mcel-shell-frame"' in page
    assert 'data-shell-launch="chat-console"' in page
    assert 'href="/applications"' in page
    assert 'mcel.layout-tree.v1' in page
    assert 'MainComputerMCELChatConsole' not in page  # no duplicate child runtime
    assert '<!-- @include' not in page
    assert page.split('<script id="mcel-applications-shell-ir"')[0].count('data-mcel-region-id="interface-region:semantic-applications-shell.') == 8


def test_compiler_refuses_invalid_canonical_tree(tmp_path):
    import main_computer.mcel_applications_shell_host as host
    from main_computer.mcel_phase2c_candidate_host import CandidateHostError

    # Refuse a source that no longer declares the expected canonical tree.
    original = (host.ROOT / host.SOURCE).read_text(encoding="utf-8")
    source = tmp_path / "application.js"
    source.write_text(original.replace('schema: "mcel.layout-tree.v1"', 'schema: "invalid-layout"'), encoding="utf-8")
    (tmp_path / "application.html").write_text((host.ROOT / host.SOURCE.with_suffix(".html")).read_text(encoding="utf-8"), encoding="utf-8")
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(host, "SOURCE", Path("application.js"))
        with pytest.raises(CandidateHostError):
            host.compile_applications_shell(tmp_path)
    finally:
        monkey.undo()


@pytest.fixture(scope="module")
def inert_host():
    preview = render_applications_shell_candidate().encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?", 1)[0] == ROUTE:
                data = preview
            elif self.path.split("?", 1)[0] in {"/applications/" + app for app in SEMANTIC_APPS}:
                data = (f'<!doctype html><html><body data-child="{self.path.strip("/")}">'
                        f'CANONICAL CHILD {self.path}</body></html>').encode("utf-8")
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_local_candidate_navigation_does_not_replace_canonical_route(inert_host):
    with urlopen(inert_host + ROUTE, timeout=10) as response:
        assert response.status == 200
        html = response.read().decode("utf-8")
        assert "semantic-applications-shell" in html
    with pytest.raises(HTTPError) as exc:
        urlopen(inert_host + "/applications", timeout=10)
    assert exc.value.code == 404  # the fixture does NOT mount any legacy desktop


@pytest.mark.parametrize("width", [390, 500, 1440])
def test_browser_semantic_shell_overlay_and_history(inert_host, width):
    from playwright.sync_api import sync_playwright, Error as PlaywrightError

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        try:
            page = browser.new_page(viewport={"width": width, "height": 840})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.goto(inert_host + ROUTE, wait_until="load", timeout=20000)
            except PlaywrightError as exc:
                if "ERR_BLOCKED_BY_ADMINISTRATOR" in str(exc):
                    pytest.skip("Chromium loopback navigation blocked by test environment")
                raise
            assert page.locator("body").get_attribute("data-mcel-shell-ready") == "true"
            assert page.locator("#mcel-applications-shell").get_attribute("data-mcel-canonical-layout-authority") == "mcel-layout-tree.v1"
            assert page.locator(".mcel-shell-app-card").count() == 21
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            page.get_by_role("button", name="Apps", exact=True).click()
            assert page.locator("#mcel-shell-launcher-panel").get_attribute("data-open") == "true"
            page.locator('.mcel-shell-app-card[data-app="chat-console"]').click()
            assert page.locator("body").get_attribute("data-mcel-shell-active-app") == "chat-console"
            assert page.locator("#mcel-shell-frame").get_attribute("src") == "/applications/chat-console?mcel-shell-child=1"
            assert page.locator(".mcel-shell-app-card[aria-current=page]").get_attribute("data-app") == "chat-console"
            assert page.url.endswith(ROUTE + "?app=chat-console")
            assert page.frame_locator("#mcel-shell-frame").locator("body").get_attribute("data-child") == "applications/chat-console?mcel-shell-child=1"
            page.go_back(wait_until="domcontentloaded")
            assert page.locator("body").get_attribute("data-mcel-shell-active-app") == "desktop"
            assert page.locator("#mcel-shell-frame").get_attribute("src") is None
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            assert errors == []
        finally:
            browser.close()


def test_browser_initial_deep_link(inert_host):
    from playwright.sync_api import sync_playwright, Error as PlaywrightError
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        try:
            page = browser.new_page()
            try:
                page.goto(inert_host + ROUTE + "?app=task-manager", wait_until="load")
            except PlaywrightError as exc:
                if "ERR_BLOCKED_BY_ADMINISTRATOR" in str(exc):
                    pytest.skip("Chromium loopback navigation blocked by test environment")
                raise
            assert page.locator("body").get_attribute("data-mcel-shell-active-app") == "task-manager"
            assert page.locator("#mcel-shell-frame").get_attribute("src") == "/applications/task-manager?mcel-shell-child=1"
            page.goto(inert_host + ROUTE + "?app=not-a-route")
            assert page.locator("body").get_attribute("data-mcel-shell-active-app") == "desktop"
            assert page.locator("#mcel-shell-frame").get_attribute("src") is None
        finally:
            browser.close()


@pytest.mark.parametrize("width", [390, 500, 1440])
def test_static_browser_semantic_mount_and_overlay_without_network(width):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        try:
            page = browser.new_page(viewport={"width": width, "height": 840})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.set_content(render_applications_shell_candidate(), wait_until="load")
            assert page.locator("body").get_attribute("data-mcel-shell-ready") == "true"
            assert page.locator("#mcel-applications-shell").get_attribute("data-mcel-canonical-layout-authority") == "mcel-layout-tree.v1"
            assert page.locator(".mcel-shell-app-card").count() == 21
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            page.get_by_role("button", name="Apps", exact=True).click()
            assert page.locator("#mcel-shell-launcher-panel").get_attribute("data-open") == "true"
            assert page.locator('.mcel-shell-app-card[data-app="chat-console"]').get_attribute("href") == "/applications/chat-console"
            assert errors == []
        finally:
            browser.close()


def test_shell_refuses_compiled_operational_effects():
    from copy import deepcopy
    from types import SimpleNamespace
    from unittest.mock import patch
    from main_computer.mcel_phase2c_candidate_host import CandidateHostError

    ir, _ = compile_applications_shell()
    compromised = deepcopy(ir)
    compromised["effects"] = [{"id": "effect:unapproved"}]
    with patch("main_computer.mcel_applications_shell_host.compile_dsl_application",
               return_value=SimpleNamespace(valid=True, normalized_ir=compromised)):
        with pytest.raises(CandidateHostError, match="operational authority"):
            compile_applications_shell()


def test_real_viewport_candidate_route_is_local_only_without_promoting_desktop():
    """Host integration; only runs when the real game bundle is present."""
    from unittest.mock import patch
    from main_computer.mcel_applications_shell_host import ROOT
    if not (ROOT / "game_projects/webgl-demo/game.json").exists():
        pytest.skip("Export lacks game_projects/webgl-demo required by the existing viewport")
    from main_computer.config import MainComputerConfig
    from main_computer.viewport_server import ViewportServer
    server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=Path.cwd()), verbose=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + ROUTE, timeout=20) as response:
            assert response.status == 200
            candidate = response.read().decode("utf-8")
            assert 'data-mcel-region-id="interface-region:semantic-applications-shell.application"' in candidate
        with urlopen(base + "/applications", timeout=20) as response:
            assert 'data-mcel-shell-canonical-promoted="true"' in response.read().decode("utf-8")
        with urlopen(base + "/applications?mcel-shell-child=1", timeout=20) as response:
            assert "Main Computer Applications</title>" in response.read().decode("utf-8")
        with patch("main_computer.viewport_route_dispatch._hard_halt_client_is_local", return_value=False):
            with pytest.raises(HTTPError) as exc:
                urlopen(base + ROUTE, timeout=20)
            assert exc.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_source_controls_launcher_labels_and_order_not_registry_copy():
    """A stale registry label/order cannot silently replace the original UI."""
    from copy import deepcopy
    from unittest.mock import patch
    from main_computer.mcel_applications_shell_host import (
        ROOT, _read_registry, legacy_desktop_reference,
    )
    stale = deepcopy(_read_registry(ROOT))
    stale["apps"].reverse()
    stale["apps"][0]["title"] = "STALENESS IN REGISTRY"
    stale["apps"][0]["description"] = "wrong display description"
    with patch("main_computer.mcel_applications_shell_host._read_registry", return_value=stale):
        _, actual = compile_applications_shell()
    original = legacy_desktop_reference()["launcher"]
    assert [(a["id"], a["route"], a["title"], a["description"]) for a in actual["apps"]] == [
        (a["id"], a["route"], a["title"], a["description"]) for a in original
    ]
    assert actual["apps"][0]["mount"] == "overview"
    assert {a["id"] for a in actual["apps"] if a["mount"] == "semantic"} == SEMANTIC_APPS


def test_candidate_still_refuses_route_and_inventory_drift():
    """Do not weaken canonical route or inventory acceptance for stale labels."""
    from copy import deepcopy
    from unittest.mock import patch
    from main_computer.mcel_applications_shell_host import ROOT, _read_registry
    from main_computer.mcel_phase2c_candidate_host import CandidateHostError

    baseline = _read_registry(ROOT)
    wrong_route = deepcopy(baseline)
    wrong_route["apps"][3]["route"] = "/applications/incorrect"
    with patch("main_computer.mcel_applications_shell_host._read_registry", return_value=wrong_route):
        with pytest.raises(CandidateHostError, match="route mismatch"):
            compile_applications_shell()

    missing_app = deepcopy(baseline)
    missing_app["apps"] = missing_app["apps"][:-1]
    with patch("main_computer.mcel_applications_shell_host._read_registry", return_value=missing_app):
        with pytest.raises(CandidateHostError, match="inventory differs"):
            compile_applications_shell()
