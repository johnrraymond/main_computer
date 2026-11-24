"""MCEL Worker: reliable identity startup and common T/F/C ownership."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest

from main_computer import mcel_applications_registry as presentation_registry
from main_computer.mcel_applications_registry import read_registry, resolve_presentation
from main_computer.mcel_worker_application import render_worker_page
from tests.test_mcel_worker_hub_navigation import MOCK_LOCAL_BACKEND
from tests.test_mcel_worker_identity_navigation import IDENTITIES_MOCK

ROOT = Path(__file__).resolve().parents[1]


def test_worker_uses_exact_system_profiles_after_canonical_promotion():
    registry = read_registry(ROOT)
    entry = next(app for app in registry['apps'] if app['id'] == 'worker')
    assert entry['mount'] == 'semantic'
    assert resolve_presentation(registry, 'worker')['eligible'] is True
    selection = resolve_presentation(registry, 'worker', semantic_candidate=True)
    assert selection['eligible'] and selection['rolloutEnabled']
    assert selection['profiles'] == registry['presentation']['system']
    html = render_worker_page(ROOT)
    assert html.count('id="mcel-application-presentation-selection"') == 1
    assert html.count('McelApplicationPresentationRegistry.connect(') == 1
    assert html.index('McelUnifiedPresentationController') < html.index('global.McelSemanticOperationalRuntime = Object.freeze(')
    assert 'worker.wallet-compat-anchor' in html
    assert 'initWorkerApp();' not in html


def test_candidate_profiles_inherit_independently():
    registry = deepcopy(read_registry(ROOT))
    registry['presentation']['system'] = {
        'theme': 'theme-saas', 'form': 'form-wireframe', 'chrome': 'chrome-vulcan'
    }
    with patch.object(presentation_registry, 'read_registry', return_value=registry):
        html = render_worker_page(ROOT)
    assert '"theme":"theme-saas","form":"form-wireframe","chrome":"chrome-vulcan"' in html
    assert '"eligible":true,"rolloutEnabled":true' in html
    assert resolve_presentation(registry, 'worker', promoted=False, semantic_candidate=True)['eligible'] is False


def browser_page(html: str, *, transient_failure: bool = False):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as driver:
        path = shutil.which('chromium') or shutil.which('chromium-browser')
        try:
            browser = driver.chromium.launch(headless=True, **({'executable_path': path} if path else {}))
        except Exception as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        try:
            page = browser.new_page(viewport={'width': 880, 'height': 560})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.evaluate(MOCK_LOCAL_BACKEND)
            page.evaluate(IDENTITIES_MOCK)
            if transient_failure:
                page.evaluate('''() => {
                  window.workerIdentityAttempts = 0;
                  const prior = window.fetch;
                  window.fetch = async (url, options = {}) => {
                    if (String(url)==='/api/applications/slh/identities' && ++window.workerIdentityAttempts===1)
                      return {ok:false,status:503,json:async()=>({error:'temporarily-unavailable'})};
                    return prior(url,options);
                  };
                }''')
            page.set_content(html, wait_until='domcontentloaded')
            page.wait_for_function('''() =>
              window.__mcelWorker?.app.readState('state:local-identities')?.length===2
              && document.querySelector('[data-mcel-node="surface-node:worker.worker-local-identities"]')?.getClientRects().length>0''',
              timeout=10000)
            report = page.evaluate('''() => {
              const root=document.getElementById('mcel-workbench');
              const node=document.querySelector('[data-mcel-node="surface-node:worker.worker-local-identities"]');
              return {
                report:window.__mcelPresentationReport,
                selections:window.__mcelSystemPresentationSelection,
                mounted:root.getAttribute('data-mcel-semantic-operational'),
                identities:window.__mcelWorker.app.readState('state:local-identities'),
                identityStatus:window.__mcelWorker.app.readState('state:local-identity-status'),
                visible:!!node?.getClientRects().length,
                attempts:window.workerIdentityAttempts||0,
                postCount:(window.workerRequests||[]).filter(item=>item.method==='POST').length,
                errors:document.getElementById('mcel-workbench-status').dataset.error,
                form:root.getAttribute('data-mcel-form-profile'),
                chrome:root.getAttribute('data-mcel-semantic-chrome'),
                accent:getComputedStyle(root).getPropertyValue('--mcel-theme-accent')
              };
            }''')
            assert errors == [], errors
            assert report['mounted'] == 'worker'
            assert report['visible'] and len(report['identities']) == 2
            assert report['identityStatus']['state'] == 'AVAILABLE'
            assert report['postCount'] == 0
            assert report['report']['status'] == 'applied'
            assert report['report']['steps'] == {'theme': 'applied', 'form': 'applied', 'chrome': 'applied'}
            if transient_failure:
                assert report['attempts'] >= 3, report
                assert report['errors'] == 'false', report
            return report
        finally:
            browser.close()


def test_startup_recovers_a_first_failed_identity_read_without_twiddle():
    report = browser_page(render_worker_page(ROOT), transient_failure=True)
    assert report['form'] == 'form-current'
    assert report['chrome'] == 'chrome-tng-side-rails'


def test_policy_applies_to_worker_main_surface_without_affecting_wallet():
    registry = deepcopy(read_registry(ROOT))
    registry['presentation']['system'] = {
        'theme': 'theme-saas', 'form': 'form-wireframe', 'chrome': 'chrome-vulcan'
    }
    with patch.object(presentation_registry, 'read_registry', return_value=registry):
        html = render_worker_page(ROOT)
    report = browser_page(html)
    assert report['selections']['profiles'] == registry['presentation']['system']
    assert report['form'] == 'form-wireframe'
    assert report['chrome'] == 'chrome-vulcan'
    assert report['accent'].strip() == '#64e4ff'
