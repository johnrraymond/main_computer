"""Semantic Chat Console cutover: exact source, real notebook, reversible route.

No live provider, terminal, filesystem, or process effects are executed here.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from main_computer.config import MainComputerConfig
from main_computer.mcel_chat_console_host import compile_chat_console, render_chat_console_page
from main_computer.viewport_route_dispatch import _semantic_canonical_route
from main_computer.viewport_server import ViewportServer


@pytest.fixture(scope="module")
def local_host():
    server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=Path.cwd()), verbose=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _html(local_host, path):
    with urlopen(local_host + path, timeout=20) as response:
        assert response.status == 200
        return response.read().decode("utf-8")


def _payload(html):
    found = re.search(r'<script id="mcel-chat-canonical-ir" type="application/json">(.*?)</script>', html, re.S)
    assert found
    return json.loads(found.group(1))


def test_semantic_source_declares_complete_host_without_new_authority():
    ir = compile_chat_console()
    assert ir["application"]["appId"] == "semantic-chat-console"
    assert not ir["layouts"]
    assert not ir["capabilities"] and not ir["effects"] and not ir["intents"]
    regions = {r["id"].split(".")[-1] for r in ir["surfaces"][0]["interfaceIntent"]["regions"]}
    assert regions == {"application", "shell", "header", "status", "thread-layout", "threads", "workspace", "notebook"}


@pytest.mark.parametrize("prefix", ("applications", "apps", "app"))
def test_canonical_aliases_are_exact(prefix):
    assert _semantic_canonical_route(f"/{prefix}/chat-console") == ("chat-console", None)
    assert _semantic_canonical_route(f"/{prefix}/chat-console/other") is None
    assert _semantic_canonical_route(f"/{prefix}/chat-console-extra") is None


def test_canonical_and_diagnostic_source_agree(local_host):
    canonical = _html(local_host, "/applications/chat-console")
    candidate = _html(local_host, "/applications/mcel-semantic-chat-console")
    assert _payload(canonical) == _payload(candidate)
    assert _payload(canonical) == compile_chat_console()
    assert "Main Computer Applications</title>" not in canonical
    assert 'id="chat-console-notebook"' in canonical
    assert 'id="mcel-chat-canonical-status"' in canonical
    for region in ("application", "shell", "header", "status", "thread-layout", "threads", "workspace", "notebook"):
        assert canonical.count(f'data-mcel-region-id="interface-region:semantic-chat-console.{region}"') == 1
    assert 'MainComputerMCELChatConsole?.compose' in canonical
    assert 'window.MainComputerChatConsole' in canonical
    assert 'const chatConsoleStorageKey = "main-computer-chat-console-v1"' in canonical
    assert 'chatConsoleMountEmbedded' in canonical
    assert 'chat-console-js-worker-source' in canonical
    assert '<!-- @include' not in canonical


def test_desktop_and_existing_semantic_apps_unchanged(local_host):
    desktop = _html(local_host, "/applications")
    assert "Main Computer Applications</title>" in desktop
    assert '"chat-console"]);' in desktop  # desktop-icon navigation now uses canonical URL
    assert 'window.location.assign(applicationPath(normalizedApp))' in desktop
    assert 'id="mcel-workbench-ir"' in _html(local_host, "/applications/code-editor")
    assert 'id="mcel-expanded-ir"' in _html(local_host, "/applications/calculator")


def test_canonical_and_candidate_retain_local_only_boundary(local_host):
    with patch("main_computer.viewport_route_dispatch._hard_halt_client_is_local", return_value=False):
        for route in ("/applications/chat-console", "/applications/mcel-semantic-chat-console"):
            with pytest.raises(HTTPError) as exc:
                urlopen(local_host + route, timeout=20)
            assert exc.value.code == 403


def _open_browser():
    from playwright.sync_api import sync_playwright
    return sync_playwright()


def _page_with_isolated_storage(page, html):
    # about:blank has no persistent host storage. Supply an ephemeral map only
    # in this browser test, never in the real served application.
    page.evaluate('''() => {
      const v=new Map();Object.defineProperty(window,'localStorage',{
        configurable:true,value:{getItem:k=>v.get(k)||null,
          setItem:(k,x)=>v.set(k,String(x)),removeItem:k=>v.delete(k),clear:()=>v.clear()}
      });
    }''')
    page.set_content(html, wait_until="domcontentloaded")


@pytest.mark.parametrize("width", (320, 390, 500, 680, 1100))
def test_canonical_browser_composes_and_fits(width):
    with _open_browser() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            _page_with_isolated_storage(page, render_chat_console_page())
            assert page.locator("body").get_attribute("data-mcel-chat-ready") == "true"
            assert page.locator("#chat-console-app").get_attribute("data-mcel-chat-contract") == "mcel.chat-console.composition.v1"
            assert page.locator(".chat-cell").count() == 1
            assert page.locator(".chat-cell textarea").count() == 1
            assert page.locator(".chat-thread-list").count() == 1
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            assert page.evaluate('''() => [...document.querySelectorAll('.chat-cell-tab')].every(e =>
                e.getBoundingClientRect().right <= document.getElementById('chat-console-notebook').getBoundingClientRect().right + 1)''')
            assert errors == []
        finally:
            browser.close()


def test_actual_javascript_worker_and_independent_thread_controls():
    with _open_browser() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        try:
            page = browser.new_page(viewport={"width": 500, "height": 850})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            _page_with_isolated_storage(page, render_chat_console_page())
            page.evaluate('''() => MainComputerChatConsole.addCell('javascript','console.log("SEMANTIC_CUTOVER_OK"); vars.cutover = 42;')''')
            page.locator('.chat-cell').last.get_by_role('button', name='Run').click()
            page.wait_for_function('''() => chatConsoleState?.variables?.cutover === 42''', timeout=10000)
            assert 'SEMANTIC_CUTOVER_OK' in page.locator('#chat-console-notebook').inner_text()
            assert page.locator('body').get_attribute('data-mcel-chat-ready') == 'true'
            assert page.locator('#chat-console-app').get_attribute('data-mcel-chat-contract') == 'mcel.chat-console.composition.v1'
            assert page.locator('.chat-cell').count() >= 3
            # The legacy shell provided calculator.js as a transitive dependency.
            # The standalone semantic host must keep Calc cells using the same
            # deterministic CalculatorCore, not silently degrade to an error.
            page.evaluate("() => MainComputerChatConsole.addCell('calculator','2+2')")
            page.locator('.chat-cell').last.get_by_role('button', name='Calculate').click()
            assert '2+2 = 4' in page.locator('#chat-console-notebook').inner_text()
            assert errors == []
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        finally:
            browser.close()


def test_broken_compositor_is_not_mistaken_for_success(monkeypatch):
    import main_computer.mcel_chat_console_host as host
    monkeypatch.setattr(host, 'SCRIPTS', tuple(x for x in host.SCRIPTS if not x.endswith('/mcel-semantic-chat-console.js')))
    with _open_browser() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        try:
            page = browser.new_page()
            _page_with_isolated_storage(page, host.render_chat_console_page())
            assert page.locator('body').get_attribute('data-mcel-chat-ready') is None
            assert page.locator('#mcel-chat-canonical-status').get_attribute('data-error') == 'true'
            assert 'semantic-chat-composition-incomplete' in page.locator('#mcel-chat-canonical-status').inner_text()
        finally:
            browser.close()
