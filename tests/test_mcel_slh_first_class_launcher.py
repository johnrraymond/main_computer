"""SLH launcher promotion: registry, canonical shell, and native operations.

The MCEL shell owns navigation only. The SLH child owns its original views,
local-only guard, capability bindings, and presentation lifecycle.
"""
from __future__ import annotations

import threading
from pathlib import Path
from urllib.request import urlopen

import pytest

from main_computer.config import MainComputerConfig
from main_computer.mcel_applications_registry import read_registry, resolve_presentation
from main_computer.mcel_applications_shell_host import (
    compile_applications_shell, legacy_desktop_reference, render_applications_shell_candidate,
)
from main_computer.mcel_slh_application import ROUTES
from main_computer.viewport_server import ViewportServer
from main_computer.viewport_state import _application_route_target

ROOT = Path(__file__).resolve().parents[1]


def test_slh_is_one_canonical_launcher_app_and_inherits_system_presentation():
    registry = read_registry(ROOT)
    items = [app for app in registry['apps'] if app['id'] == 'slh']
    assert len(items) == 1
    assert items[0]['route'] == '/applications/slh'
    assert items[0]['mount'] == 'semantic'
    assert not any(app['id'] == 'slh' for app in registry.get('runtimeApps', []))
    policy = resolve_presentation(registry, 'slh')
    assert policy['rolloutEnabled'] is True
    assert policy['profiles'] == registry['presentation']['system']
    assert set(policy['profiles']) == {'theme', 'form', 'chrome'}
    _, compiled = compile_applications_shell(ROOT)
    assert len(compiled['apps']) == len(registry['apps']) == 22
    assert [a['id'] for a in compiled['apps']].count('slh') == 1
    original = legacy_desktop_reference(ROOT)
    assert [app['app'] for app in original['icons']].count('slh') == 1
    assert 'slh' in original['svg']


def test_slh_has_both_a_desktop_anchor_and_a_slideout_anchor():
    html = render_applications_shell_candidate(ROOT, canonical_promoted=True)
    assert 'data-shell-launch="slh"' in html
    assert 'data-mcel-destination-id="applications.slh" href="/applications/slh"' in html
    assert '"id":"slh","title":"Secure Local Hub"' in html
    assert html.count('data-shell-launch="slh"') == 1


@pytest.mark.parametrize('route', list(ROUTES))
def test_slh_operational_routes_are_shell_addressable_and_not_legacy_fallback(route):
    assert _application_route_target(route) == 'slh'
    assert _application_route_target(route + '?mcel-shell-child=1') == 'slh'


@pytest.mark.parametrize('route', ['/applications/slh/not-a-view', '/applications/slh/network/unknown',
                                   '/applications/slh/not/a/route'])
def test_invalid_slh_deep_routes_are_not_accepted(route):
    assert _application_route_target(route) is None


@pytest.fixture(scope='module')
def server():
    instance = ViewportServer(('127.0.0.1', 0), MainComputerConfig(workspace=ROOT), verbose=False)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{instance.server_port}'
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def _get(server, route):
    with urlopen(server + route, timeout=30) as response:
        assert response.status == 200
        return response.read().decode('utf-8')


@pytest.mark.parametrize('route', list(ROUTES))
def test_slh_direct_route_has_shell_but_native_child_preserves_operations(server, route):
    parent = _get(server, route)
    assert 'data-mcel-shell-canonical-promoted="true"' in parent
    assert 'id="mcel-shell-launcher-panel"' in parent
    assert 'id="mcel-shell-frame"' in parent
    child = _get(server, route + '?mcel-shell-child=1')
    assert 'id="mcel-shell-launcher-panel"' not in child
    assert 'data-mcel-region-id' in child
    assert '"appId":"slh"' in child


def test_slh_desktop_icon_and_apps_rail_navigate_without_nested_shell(server):
    from playwright.sync_api import sync_playwright
    import shutil
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page = browser.new_page(viewport={'width':1040,'height':800}, reduced_motion='reduce')
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.set_content(render_applications_shell_candidate(ROOT, canonical_promoted=True), wait_until='domcontentloaded')
            page.evaluate('history.pushState = function () {}')
            icon = page.locator('.desktop-icon[data-app="slh"]')
            assert icon.count() == 1
            assert icon.get_attribute('href') == '/applications/slh'
            assert icon.locator('svg').count() == 1
            icon.click()
            assert page.locator('body').get_attribute('data-mcel-shell-active-app') == 'slh'
            assert page.locator('body').get_attribute('data-mcel-shell-active-app') == 'slh'
            assert page.locator('#mcel-shell-frame').get_attribute('src') == '/applications/slh?mcel-shell-child=1'
            assert page.locator('.mcel-shell-app-card[aria-current="page"]').get_attribute('data-app') == 'slh'
            assert page.locator('#mcel-shell-launcher-panel').count() == 1
            # The real native child is verified separately by the HTTP route tests.
            page.locator('.mcel-shell-app-card[data-app="calculator"]').click()
            assert page.locator('body').get_attribute('data-mcel-shell-active-app') == 'calculator'
            page.locator('.mcel-shell-app-card[data-app="slh"]').click()
            assert page.locator('body').get_attribute('data-mcel-shell-active-app') == 'slh'
            assert not errors, errors
        finally:
            browser.close()


def test_slh_deep_link_is_retained_inside_shell(server):
    # Route-faithful parent and native child must remain independently addressable.
    assert 'id="mcel-shell-frame"' in _get(server, '/applications/slh/network/workers')
    assert 'id="mcel-shell-launcher-panel"' not in _get(server, '/applications/slh/network/workers?mcel-shell-child=1')
