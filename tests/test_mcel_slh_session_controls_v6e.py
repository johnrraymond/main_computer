"""V6-E: Login/Logout controls survive repeated transitions and expiry of observed evidence."""
from __future__ import annotations

import shutil

from main_computer.mcel_slh_application import observe_slh, render_slh_page


def _overview():
    return observe_slh('overview', probe=lambda: ('RUNNING', {
        'schema': 'main-computer.slh-local-status.v1',
        'local_service': {'state': 'running', 'binding': 'loopback'},
        'initialization': {'state': 'INITIALIZED'},
        'administrator_enrollment': {'state': 'ENROLLED'},
        'organization_service': {
            'state': 'running', 'configured': True, 'bind_host': '127.0.0.1',
            'api_port': 8899, 'listening': True,
            'network_access': 'LOOPBACK_ONLY', 'tls_state': 'VERIFIED_LOCAL',
        },
    }))


def test_login_logout_remains_available_across_multiple_sessions_and_expired_evidence():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page = browser.new_page()
            page.clock.install()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('dialog', lambda dialog: dialog.accept())
            page.evaluate('''overview => {
              window.v6e = {overview, authenticated:false, requests:[], logins:0, logouts:0};
              window.fetch = async (url, options={}) => {
                const state=window.v6e;
                state.requests.push(url);
                if(url==="/api/applications/slh/identities") return {ok:true,json:async()=>({
                  schema:"mcel.slh.identities.v4b",state:"AVAILABLE",defaultDeviceId:"739-ABC-OR-123-ZX-26-11-09",
                  explanation:"Identity discovered",identities:[{deviceId:"739-ABC-OR-123-ZX-26-11-09",
                    label:"Default identity",fingerprint:"a".repeat(64),defaultStatus:"DEFAULT"},
                    {deviceId:"123-ABC-OR-456-ZX-26-11-09",label:"Additional identity",
                     fingerprint:"b".repeat(64),defaultStatus:"ADDITIONAL"}]})};
                if(url==="/api/applications/slh/observe?view=overview") {
                  const snapshot=structuredClone(state.overview);
                  snapshot.overview.observedAt=new Date().toISOString();
                  return {ok:true,json:async()=>snapshot};
                }
                if(url==="/api/applications/slh/observe?view=workers") {
                  const snapshot=structuredClone(state.overview);
                  snapshot.view="workers";
                  snapshot.workers=[];
                  snapshot.workerSummary={observationState:state.authenticated?"AUTHENTICATED":"AUTH_REQUIRED",
                    explanation:"Test",observedAt:new Date().toISOString(),source:"Oracle",
                    enrolledCount:"0",connectedCount:"0"};
                  return {ok:true,json:async()=>snapshot};
                }
                if(url==="/api/applications/slh/setup/action") {
                  const action=JSON.parse(options.body).action;
                  if(action==="login") {state.authenticated=true;state.logins++;
                    return {ok:true,json:async()=>({access_token:"X".repeat(64),role:"admin"})};}
                  if(action==="logout") {state.authenticated=false;state.logouts++;
                    return {ok:true,json:async()=>({outcome:"SIGNED_OUT"})};}
                }
                throw Error("Unexpected endpoint: "+url);
              };
            }''', _overview())
            page.set_content(render_slh_page(), wait_until='load')
            page.get_by_text('SLH observation complete', exact=False).wait_for(timeout=15000)
            page.locator('[data-record-key="123-ABC-OR-456-ZX-26-11-09"] button[data-select-key="123-ABC-OR-456-ZX-26-11-09"]').click()
            assert page.evaluate('window.__mcelSlh.app.readState("state:selected-local-identity")') == '123-ABC-OR-456-ZX-26-11-09'
            login = page.locator('button[data-intent="intent:login-administrator"]')
            logout = page.locator('button[data-intent="intent:logout-administrator"]')
            for iteration in range(3):
                login.wait_for(state='visible', timeout=10000)
                login.click()
                page.get_by_text('Organization administrator authenticated', exact=True).wait_for(timeout=10000)
                logout.wait_for(state='visible', timeout=10000)
                # Simulate the operator leaving the application open for longer
                # than the runtime's 60-second derived-state freshness window.
                page.clock.run_for(70000)
                logout.wait_for(state='visible', timeout=10000)
                logout.click()
                page.get_by_text('Organization session not established', exact=True).wait_for(timeout=10000)
                login.wait_for(state='visible', timeout=10000)
                # Polling must not override an explicitly selected non-default identity.
                assert page.evaluate('window.__mcelSlh.app.readState("state:selected-local-identity")') == '123-ABC-OR-456-ZX-26-11-09'
            assert page.evaluate('window.v6e.logins') == 3
            assert page.evaluate('window.v6e.logouts') == 3
            assert errors == [], errors
        finally:
            browser.close()
