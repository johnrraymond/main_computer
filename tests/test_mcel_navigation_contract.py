"""Semantic destinations remain browser-native links across MCEL shells."""
from copy import deepcopy

import pytest

from main_computer.mcel_navigation_contract import (
    NavigationContractError, make_destination, resolve_destination, validate_destination,
)
from main_computer.mcel_applications_shell_host import (
    compile_applications_shell, render_applications_shell_candidate,
)


def test_fully_addressable_destination_and_parameter_binding():
    detail = make_destination('slh.worker', '/applications/slh/workers/{workerId}',
                              access='administrator')
    assert detail['openModes'] == ['same-tab', 'new-tab', 'new-window']
    assert resolve_destination(detail, {'workerId': 'worker-42'}) == '/applications/slh/workers/worker-42'
    assert resolve_destination(make_destination('slh.overview', '/applications/slh')) == '/applications/slh'
    with pytest.raises(NavigationContractError, match='parameters-mismatch'):
        resolve_destination(detail, {})
    with pytest.raises(NavigationContractError, match='parameter-unsafe'):
        resolve_destination(detail, {'workerId':'../../secrets'})
    with pytest.raises(NavigationContractError, match='parameter-unsafe'):
        resolve_destination(detail, {'workerId':'x?mcel-shell-child=1'})


@pytest.mark.parametrize('href', [
    'javascript:alert(1)', '//evil.example/applications/slh',
    'https://127.0.0.1/applications/slh', '/applications/../secrets',
    '/applications/slh?mcel-shell-child=1', '/applications/slh#hidden',
    '/applications/slh/%2fetc', '/not-an-application',
])
def test_unportable_or_unsafe_destinations_are_rejected(href):
    with pytest.raises(NavigationContractError):
        make_destination('slh.worker', href)


def test_destination_requires_all_native_opening_modes():
    valid = make_destination('slh.overview', '/applications/slh')
    for modes in [['same-tab'], ['same-tab', 'new-tab'], ['new-window']]:
        bad = deepcopy(valid)
        bad['openModes'] = modes
        with pytest.raises(NavigationContractError, match='opening-modes-incomplete'):
            validate_destination(bad)


def test_promoted_shell_exposes_canonical_href_for_every_launcher_link():
    _, catalog = compile_applications_shell()
    page = render_applications_shell_candidate(canonical_promoted=True)
    assert page.count('class="desktop-icon"') == 20
    assert page.count('data-mcel-destination-id="applications.') == 20
    for app in catalog['apps']:
        assert f'href="{app["route"]}"' in page or app['id'] == 'desktop'
    assert 'data-shell-launch="chat-console" data-mcel-destination-id="applications.chat-console" href="/applications/chat-console"' in page


def test_browser_plain_click_mounts_but_modified_click_retains_native_link():
    from playwright.sync_api import sync_playwright
    import shutil
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page = browser.new_page(viewport={'width':1040,'height':840}, reduced_motion='reduce')
            page.set_content(render_applications_shell_candidate(canonical_promoted=True), wait_until='load')
            page.evaluate('history.pushState = function () {}')
            assert page.locator('body').get_attribute('data-mcel-shell-ready') == 'true'
            assert page.locator('.desktop-icon[data-app="chat-console"]').evaluate('e => e.tagName') == 'A'
            assert page.locator('.desktop-icon[data-app="chat-console"]').get_attribute('href') == '/applications/chat-console'
            # Capture the click after the app handler but suppress browser navigation.
            outcome = page.evaluate('''() => {
              const anchor = document.querySelector('.desktop-icon[data-app="chat-console"]');
              const results = {};
              for (const [name, opts] of Object.entries({
                ctrl: {ctrlKey:true}, shift: {shiftKey:true}, meta:{metaKey:true},
                plain:{}
              })) {
                const watch = event => { results[name] = event.defaultPrevented; event.preventDefault(); };
                document.addEventListener('click', watch, {once:true});
                anchor.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, button:0, ...opts}));
              }
              return {results, active:document.body.dataset.mcelShellActiveApp,
                canonicalHref:anchor.getAttribute('href')};
            }''')
            assert outcome == {
                'results': {'ctrl':False,'shift':False,'meta':False,'plain':True},
                'active':'chat-console','canonicalHref':'/applications/chat-console',
            }
            assert page.locator('#mcel-shell-launcher-panel').count() == 1
        finally:
            browser.close()


def test_navigation_oracle_refuses_button_in_place_of_real_link():
    from tools.mcel_applications_shell_oracle import navigation_link_errors
    _, registry = compile_applications_shell()
    records = {x['id']: x['route'] for x in registry['apps']}
    icon_ids = set(records) - {'desktop'}
    links = {
        'icons':[{'app':app,'tag':'A','href':records[app],
                  'destination':'applications.' + app} for app in icon_ids],
        'launcher':[{'app':app,'tag':'A','href':route,
                     'destination':'applications.' + app} for app,route in records.items()],
    }
    assert navigation_link_errors(links,registry,icon_ids) == []
    bad = deepcopy(links)
    bad['icons'][0]['tag'] = 'BUTTON'
    assert any('canonical-navigation-link-invalid' in e for e in
               navigation_link_errors(bad,registry,icon_ids))
    bad = deepcopy(links)
    bad['launcher'][1]['href'] += '?mcel-shell-child=1'
    assert any('canonical-navigation-link-invalid' in e for e in
               navigation_link_errors(bad,registry,icon_ids))
