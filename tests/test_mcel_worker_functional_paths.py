"""Browser-level Worker marketplace functional paths (fully isolated backend).

This is NOT a real wallet, real Hub or spending enforcement acceptance test.
The actual generated semantic UI dispatches into the production capability port;
only the external marketplace signer and HTTP responses are disposable mocks.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from main_computer.mcel_worker_application import render_worker_page
from tests.test_mcel_worker_marketplace_parity import BROWSER_BACKEND

ROOT = Path(__file__).resolve().parents[1]


def test_marketplace_buttons_reach_existing_signer_and_finish_pause_are_distinct():
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as driver:
        # A missing system executable does not imply Chromium is unavailable:
        # Playwright may have its own installed Chromium in its browser cache.
        executable = shutil.which('chromium') or shutil.which('chromium-browser')
        browser = driver.chromium.launch(
            headless=True, **({'executable_path': executable} if executable else {})
        )
        try:
            page = browser.new_page(viewport={'width': 1120, 'height': 760})
            page.set_default_timeout(7500)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('dialog', lambda dialog: dialog.accept())
            page.evaluate(BROWSER_BACKEND)
            page.set_content(render_worker_page(ROOT), wait_until='domcontentloaded')
            page.wait_for_function('''() =>
                window.__mcelWorker?.app.readState('state:selected-network')==='mainnet' ''')
            page.locator('[data-mcel-purpose="navigation"] [data-value="worker"]').click()
            page.wait_for_function('''() => window.__mcelWorker?.app.readState(
                'state:seller-configuration')?.sellerTargetTokens===1024''', timeout=6000)
            page.wait_for_function('''() => window.__mcelWorker?.app.readState(
                'state:marketplace-status')?.network==='mainnet' ''', timeout=6000)
            page.evaluate('''() => {
              window.workerActionCalls = [];
              window.McelWorkerLegacyMarketplace = Object.freeze({
                setSeller: (seller, settings) => {window.workerActionCalls.push('setSeller')},
                refresh: async () => {window.workerActionCalls.push('refresh')},
                register: async () => {window.workerActionCalls.push('register'); return {status:'accepted',worker_id:'mock-worker'}},
                workNow: async (seconds) => {window.workerActionCalls.push(['workNow',seconds]);return {ok:true}},
                finish: async () => {window.workerActionCalls.push('finish');return {status:'acknowledged'}}
              });
            }''')
            page.locator('button[data-intent="intent:register-offer"]').click()
            page.wait_for_function('''() => window.workerActionCalls.includes('register')''')
            page.get_by_role('button', name='Start Work Now').click()
            page.wait_for_function('''() => window.workerActionCalls.some(x=>Array.isArray(x)&&x[0]==='workNow')''')
            page.locator('button[data-intent="intent:finish-work-now"]').click()
            page.wait_for_function('''() => window.workerActionCalls.includes('finish')''')
            page.locator('button[data-intent="intent:pause-selling"]').click()
            page.wait_for_function('''() => window.mock.settings.sellerEnabled===false''')
            calls = page.evaluate('window.workerActionCalls')
            assert 'register' in calls and 'finish' in calls and ['workNow',3600] in calls
            assert page.evaluate('''() => window.requestLog.filter(
                r=>r.method==='POST'&&r.path.endsWith('/settings')).some(
                r=>JSON.stringify(r.body.changed_fields) === JSON.stringify(['sellerEnabled','rentalEnabled']))''')
            assert errors == [], errors
        finally:
            browser.close()


def test_hub_retry_and_disconnect_are_explicit_and_preserve_saved_choice():
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as driver:
        # A missing system executable does not imply Chromium is unavailable:
        # Playwright may have its own installed Chromium in its browser cache.
        executable = shutil.which('chromium') or shutil.which('chromium-browser')
        browser = driver.chromium.launch(
            headless=True, **({'executable_path': executable} if executable else {})
        )
        try:
            page = browser.new_page(viewport={'width': 1120, 'height': 760})
            page.set_default_timeout(6500)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('dialog', lambda dialog: dialog.accept())
            page.evaluate(BROWSER_BACKEND)
            page.set_content(render_worker_page(ROOT), wait_until='domcontentloaded')
            page.wait_for_function('''() => window.__mcelWorker?.app.readState('state:selected-network')==='mainnet' ''')
            page.locator('button[data-intent="intent:retry-hub"]').click()
            page.wait_for_function('''() => window.requestLog.some(x=>x.method==='POST' &&
                x.path.endsWith('/network-session') && x.body.network==='mainnet')''')
            page.locator('button[data-intent="intent:disconnect"]').click()
            page.wait_for_function('''() => window.mock.active==='none' ''')
            page.wait_for_function('''() => window.__mcelWorker.app.readState('state:destination')?.selectedNetwork==='none' ''')
            assert page.evaluate('window.mock.settings.mcelHubChoice') == 'mainnet'
            assert page.evaluate('window.__mcelWorker.app.readState("state:selected-network")') == 'mainnet'
            assert errors == [], errors
        finally:
            browser.close()
