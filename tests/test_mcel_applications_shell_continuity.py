"""Prevent silent loss of the host-owned launcher during application navigation.

This is a candidate/opt-in routing gate. It must not turn standalone
canonical URLs into shell routes until a separate promotion is accepted.
"""
from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from main_computer.mcel_applications_shell_host import render_applications_shell_candidate


def test_route_preview_is_explicit_and_contains_the_host_overlay():
    page = render_applications_shell_candidate(route_preview=True)
    assert 'data-mcel-shell-route-preview="true"' in page
    assert 'id="mcel-shell-launcher-panel"' in page
    assert 'id="mcel-shell-frame"' in page
    assert 'id="mcel-applications-shell"' in page
    assert 'id="mcel-applications-shell-ir"' in page
    assert render_applications_shell_candidate().count('data-mcel-shell-route-preview="false"') == 1


def test_route_preview_is_opt_in_and_children_are_not_shell_wrapped():
    from main_computer import viewport_route_dispatch as routes
    source = Path(routes.__file__).read_text(encoding="utf-8")
    assert 'parse_qs(urlsplit(self.path).query).get("mcel-shell-preview") == ["1"]' in source
    assert 'render_applications_shell_candidate(route_preview=True)' in source
    assert '"Shell route preview is local-only."' in source
    script = Path('main_computer/web/applications/scripts/mcel-applications-shell.js').read_text(encoding="utf-8")
    assert 'frame.src = childHref(app);' in script
    assert 'target.searchParams.set("mcel-shell-preview", "1")' in script


@pytest.mark.parametrize('width', [390, 500, 1040, 1440])
def test_switching_semantic_and_legacy_apps_never_removes_shell(width):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page = browser.new_page(viewport={'width': width, 'height': 840}, reduced_motion='reduce')
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.set_content(render_applications_shell_candidate(), wait_until='load')
            page.evaluate('history.pushState = function () {}')
            panel = page.locator('#mcel-shell-launcher-panel')
            assert page.locator('body').get_attribute('data-mcel-shell-ready') == 'true'
            page.evaluate('window.__originalPanel = document.querySelector("#mcel-shell-launcher-panel")')
            for app in ('chat-console', 'terminal', 'calculator', 'document', 'code-editor'):
                # Every transition must retain the same parent element.
                page.evaluate('(app) => window.__mcelApplicationsShell.select(app, true)', app)
                assert page.locator('body').get_attribute('data-mcel-shell-active-app') == app
                assert page.locator('#mcel-shell-frame').get_attribute('src') == '/applications/' + app + '?mcel-shell-child=1'
                assert page.locator('#mcel-shell-application-mount').get_attribute('data-mode') in ('semantic', 'legacy')
                assert page.locator('.mcel-shell-app-card').count() == 21
                assert panel.evaluate('(el) => el.isConnected') is True
                assert page.evaluate('document.querySelector("#mcel-shell-launcher-panel") === window.__originalPanel') is True
                page.mouse.move(width - 8, 410)
                page.mouse.move(20, 360)
                assert abs(panel.evaluate('(el) => el.getBoundingClientRect().left')) < 3
            assert errors == []
        finally:
            browser.close()


def test_actual_viewport_route_preview_is_shell_but_canonical_child_is_not():
    """HTTP acceptance: the opt-in preview and the native child cannot recurse."""
    from http.server import HTTPServer
    from threading import Thread
    from urllib.request import urlopen
    from urllib.error import HTTPError
    from unittest.mock import patch
    from main_computer.config import MainComputerConfig
    from main_computer.mcel_applications_shell_host import ROOT
    from main_computer.viewport_server import ViewportServer

    if not (ROOT / 'game_projects/webgl-demo/game.json').exists():
        pytest.skip('Viewport source game fixture not present in export')
    host = ViewportServer(('127.0.0.1', 0), MainComputerConfig(workspace=ROOT), verbose=False)
    thread = Thread(target=host.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{host.server_port}'
    try:
        for app in ('', '/chat-console', '/calculator', '/terminal'):
            path = '/applications' + app + '?mcel-shell-preview=1'
            with urlopen(base + path, timeout=25) as r:
                html = r.read().decode()
            assert 'data-mcel-shell-route-preview="true"' in html
            assert 'id="mcel-shell-launcher-panel"' in html
            assert 'id="mcel-shell-frame"' in html
        # Already accepted canonical runtime is not rewritten or nested.
        with urlopen(base + '/applications/chat-console?mcel-shell-child=1', timeout=25) as r:
            child = r.read().decode()
        assert 'mcel.chat-console.composition.v1' in child
        assert 'data-mcel-shell-route-preview="true"' not in child
        assert 'id="mcel-shell-launcher-panel"' not in child
        # The shell-preview endpoint does not expose local app authority remotely.
        with patch('main_computer.viewport_route_dispatch._hard_halt_client_is_local', return_value=False):
            with pytest.raises(HTTPError) as exc:
                urlopen(base + '/applications/chat-console?mcel-shell-preview=1', timeout=25)
            assert exc.value.code == 403
    finally:
        host.shutdown()
        host.server_close()
        thread.join(timeout=5)
