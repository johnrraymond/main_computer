"""Canonical shell authority, unwrapped child identity, route safety, and visual continuity."""
from __future__ import annotations

import threading
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from unittest.mock import patch

import pytest

from main_computer.config import MainComputerConfig
from main_computer.viewport_server import ViewportServer
from main_computer.mcel_applications_shell_host import (
    render_applications_shell_candidate,
    compile_applications_shell,
)


@pytest.fixture(scope="module")
def host():
    server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=Path.cwd()), verbose=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def read(host, path):
    with urlopen(host + path, timeout=30) as resp:
        assert resp.status == 200
        return resp.read().decode("utf-8")


def test_compiled_canonical_shell_cannot_claim_child_effects():
    ir, registry = compile_applications_shell()
    assert not ir["capabilities"] and not ir["intents"] and not ir["effects"]
    assert len(registry["apps"]) == 21
    page = render_applications_shell_candidate(canonical_promoted=True)
    assert 'data-mcel-shell-canonical-promoted="true"' in page
    assert 'id="mcel-shell-launcher-panel"' in page
    assert 'MCEL SHELL · CANONICAL' in page
    assert 'href="/applications?mcel-shell-child=1">Legacy desktop' in page


@pytest.mark.parametrize('path,expected', [
    ('/applications', 'desktop'),
    ('/applications/chat-console', 'chat-console'),
    ('/applications/calculator', 'calculator'),
    ('/applications/code-editor', 'code-editor'),
    ('/applications/file-explorer', 'file-explorer'),
    ('/applications/task-manager', 'task-manager'),
    ('/applications/task-manager/connections', 'task-manager'),
    ('/applications/document', 'document'),
    ('/applications/website-builder/hub-site', 'website-builder'),
])
def test_canonical_routes_use_persistent_parent(host, path, expected):
    page = read(host, path)
    assert 'data-mcel-shell-canonical-promoted="true"' in page
    assert 'id="mcel-shell-launcher-panel"' in page
    assert 'id="mcel-shell-frame"' in page
    assert 'id="mcel-applications-shell-registry"' in page
    # Application identity is encoded in the canonical JS registry and route.
    assert f'"id":"{expected}"' in page


@pytest.mark.parametrize('path,proof', [
    ('/applications?mcel-shell-child=1', 'Main Computer Applications</title>'),
    ('/applications/chat-console?mcel-shell-child=1', 'mcel.chat-console.composition.v1'),
    ('/applications/calculator?mcel-shell-child=1', 'id="mcel-expanded-ir"'),
    ('/applications/code-editor?mcel-shell-child=1', 'id="mcel-workbench-ir"'),
    ('/applications/task-manager/connections?mcel-shell-child=1', 'const initialView="connections";'),
    ('/applications/document?mcel-shell-child=1', 'id="mcel-native-child-chrome"'),
    ('/applications/website-builder/hub-site?mcel-shell-child=1', 'id="mcel-native-child-chrome"'),
])
def test_native_children_remain_accepted_nonrecursive_applications(host, path, proof):
    page = read(host, path)
    assert proof in page
    assert 'data-mcel-shell-canonical-promoted="true"' not in page


def test_same_origin_iframe_remains_native_after_child_router_drops_query(host):
    for app, proof in [('chat-console', 'mcel.chat-console.composition.v1'),
                       ('document', 'id="mcel-native-child-chrome"')]:
        url = host + '/applications/' + app
        request = Request(url, headers={'Sec-Fetch-Dest':'iframe', 'Sec-Fetch-Site':'same-origin'})
        with urlopen(request, timeout=30) as response:
            page = response.read().decode('utf-8')
        assert proof in page
        assert 'data-mcel-shell-canonical-promoted="true"' not in page
    request = Request(host + '/applications/document', headers={'Sec-Fetch-Dest':'iframe', 'Sec-Fetch-Site':'cross-site'})
    with urlopen(request, timeout=30) as response:
        assert 'data-mcel-shell-canonical-promoted="true"' in response.read().decode('utf-8')


def test_preview_remains_opt_in_and_rollback_remains_available(host):
    page = read(host, '/applications/chat-console?mcel-shell-preview=1')
    assert 'data-mcel-shell-route-preview="true"' in page
    assert 'data-mcel-shell-canonical-promoted="false"' in page
    standalone = read(host, '/applications/chat-console?mcel-shell-child=1')
    assert 'mcel.chat-console.composition.v1' in standalone
    assert 'id="mcel-shell-launcher-panel"' not in standalone


def test_rejects_remote_access_to_promoted_shell_and_semantic_child(host):
    from main_computer import viewport_route_dispatch
    with patch.object(viewport_route_dispatch, "_hard_halt_client_is_local", return_value=False):
        for path in ('/applications', '/applications/chat-console',
                     '/applications/chat-console?mcel-shell-child=1'):
            with pytest.raises(HTTPError) as exc:
                urlopen(host + path, timeout=30)
            assert exc.value.code == 403


def test_browser_shell_canonical_mode_keeps_overlay_on_all_selection_changes():
    from playwright.sync_api import sync_playwright
    import shutil
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page = browser.new_page(viewport={"width": 1040, "height": 840}, reduced_motion="reduce")
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.set_content(render_applications_shell_candidate(canonical_promoted=True), wait_until="load")
            page.evaluate('history.pushState = function () {}')  # about:blank cannot change path
            assert page.locator('body').get_attribute('data-mcel-shell-ready') == 'true'
            assert page.evaluate('window.__mcelApplicationsShell.canonicalPromoted') is True
            page.evaluate('window.__rootRail = document.getElementById("mcel-shell-launcher-panel")')
            for app in ['chat-console', 'document', 'calculator', 'file-explorer', 'task-manager']:
                page.evaluate('(app) => window.__mcelApplicationsShell.select(app, true)', app)
                assert page.locator('body').get_attribute('data-mcel-shell-active-app') == app
                assert page.locator('#mcel-shell-frame').get_attribute('src') == f'/applications/{app}?mcel-shell-child=1'
                assert page.evaluate('window.__rootRail === document.getElementById("mcel-shell-launcher-panel")')
                page.mouse.move(1000, 400)
                page.mouse.move(20, 360)
                assert abs(page.locator('#mcel-shell-launcher-panel').evaluate('(el) => el.getBoundingClientRect().left')) < 3
            assert errors == []
        finally:
            browser.close()
