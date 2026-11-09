"""Browser red/green oracle: unchanged state fixtures before and after MCEL repair.

All network responses are synthetic. These tests NEVER start, initialize, sign
in to, or change the operator's real SLH. Existing disposable SLH tests remain
the independent backend-effect acceptance lane.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import observe_slh, render_slh_page
from tools import mcel_operational_workflow_oracle as oracle

CONTRACT = oracle.read_contract()
CASES = {case['id']: case for case in CONTRACT['scenarios']}


def response_for(case_id: str) -> dict:
    observation = CASES[case_id]['observation']
    state = observation['local']
    if state == 'RUNNING':
        data = {'schema': 'main-computer.slh-local-status.v1',
                'local_service': {'state': 'running', 'binding': 'loopback', 'host': '127.0.0.1'},
                'initialization': {'initialized': observation['initialized'],
                                   'setup_phase': 'INITIALIZED' if observation['initialized'] else 'UNCONFIGURED'},
                # V1's established-state scenarios are still valid, but V3
                # requires the formerly implicit administrator and listener
                # evidence to be explicit. Do not change the frozen V1 oracle.
                'administrator_enrollment': {'state': 'ENROLLED' if observation['initialized']
                        else 'NOT_ESTABLISHED', 'local_credential': 'NOT_VERIFIED'},
                'organization_service': {
                    'state': observation['organization'],
                    'configured': observation['organization'] != 'not_configured',
                    'bind_host': ('127.0.0.1' if observation['organization'] != 'not_configured' else None),
                    'api_port': (8899 if observation['organization'] != 'not_configured' else None),
                    'listening': observation['organization'] == 'running',
                    'network_access': ('LOOPBACK_ONLY' if observation['organization'] != 'not_configured'
                                       else 'NOT_CONFIGURED')}}
    else:
        data = {}
    result = observe_slh('overview', probe=lambda: (state, data))
    if not observation['fresh']:
        # A valid-looking cached response is NOT fresh authority.
        result['overview']['observedAt'] = '2000-01-01T00:00:00+00:00'
        result['overview']['observationState'] = 'STALE'
    return result


def browser_page(browser, initial: str = 'running-unauthenticated-screenshot', *,
                 transitions=None, fail_action=None):
    page = browser.new_page(viewport={'width': 1200, 'height': 900})
    failures = []
    page.on('pageerror', lambda exc: failures.append(str(exc)))
    page.on('dialog', lambda dialog: dialog.accept())
    page.evaluate('''({cases,initial,transitions,failAction})=>{
       window.oracleFixture = {cases, current:initial, requests:[], transitions:transitions||{}, failAction:failAction||null};
       window.fetch = async (url,options={})=>{
         const state=window.oracleFixture;
         state.requests.push({url,method:options.method||"GET"});
         if(url==="/api/applications/slh/observe?view=overview" && options.method==="GET")
           return {ok:true,json:async()=>state.cases[state.current]};
         if(url==="/api/applications/slh/observe?view=workers" && options.method==="GET"){
           const payload=structuredClone(state.cases[state.current]);
           payload.view="workers";
           payload.workerSummary={observationState:state.current==="running-authenticated"?"AUTHENTICATED":"AUTH_REQUIRED",explanation:"Synthetic inventory",
             observedAt:"2026-11-08T00:00:00Z",source:"Synthetic fixture",
             enrolledCount:"NOT_OBSERVED",connectedCount:"NOT_OBSERVED"};
           payload.workers=[];
           return {ok:true,json:async()=>payload};
         }
         if(url==="/api/applications/slh/setup/action" && options.method==="POST"){
           const action=JSON.parse(options.body).action;
           if(state.failAction===action)return {ok:false,json:async()=>({error:{code:"SYNTHETIC_OPERATION_FAILED"}})};
           if(state.transitions[action])state.current=state.transitions[action];
           if(action==="login")return {ok:true,json:async()=>({access_token:"X".repeat(64),role:"admin"})};
           if(action==="logout")return {ok:true,json:async()=>({outcome:"SIGNED_OUT"})};
           return {ok:true,json:async()=>({outcome:action==="activate"?"RUNNING":"CONFIGURED",
             message:"Synthetic operation receipt; not backend evidence", deviceId:"admin-pc"})};
         }
         throw Error("Unexpected oracle HTTP request: "+url);
       };
    }''', {'cases': {x: response_for(x) for x in CASES}, 'initial': initial,
           'transitions': transitions or {}, 'failAction': fail_action})
    page.set_content(render_slh_page(), wait_until='load')
    page.get_by_text('SLH observation complete', exact=False).wait_for(timeout=20000)
    return page, failures


@pytest.fixture(scope='module')
def chromium():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        yield browser
        browser.close()


@pytest.mark.parametrize('case_id', [
    'local-offline', 'local-uninitialized', 'organization-unconfigured',
    'organization-disabled', 'running-unauthenticated-screenshot',
    'organization-failed', 'status-unverified', 'observation-stale',
])
def test_rendered_controls_follow_authoritative_observation(chromium, case_id):
    page, errors = browser_page(chromium, case_id)
    try:
        visible = oracle.visible_intents(page)
        report = oracle.evaluate_visible_case(CONTRACT, case_id, visible)
        assert report['status'] == 'PASS', report
        assert not errors, errors
    finally:
        page.close()


def test_screenshot_stage_has_one_visible_administrator_identity_input(chromium):
    page, errors = browser_page(chromium)
    try:
        visible = page.locator('form[data-intent] input[name="adminDeviceId"]:visible').count()
        assert visible <= CONTRACT['shared_input']['max_simultaneously_visible_editors'], {
            'visible_administrator_id_inputs': visible,
            'max_allowed': CONTRACT['shared_input']['max_simultaneously_visible_editors'],
        }
        assert errors == []
    finally:
        page.close()


def test_stale_observation_refuses_programmatic_consequential_execution(chromium):
    """Visibility alone is insufficient. App.execute must not attempt a mutation."""
    page, errors = browser_page(chromium, 'observation-stale')
    try:
        result = page.evaluate('''async()=>window.__mcelSlh.app.execute(
          "intent:activate-organization", {adminDeviceId:"admin-pc"})''')
        posts = page.evaluate('''()=>window.oracleFixture.requests.filter(
          x=>x.method==="POST")''')
        assert not posts, {'unauthorized_consequential_attempts': posts, 'receipt': result}
        assert result['disposition'] in ('refused-before-attempt', 'failed'), result
        assert errors == []
    finally:
        page.close()


def test_verified_transition_recomputes_next_action(chromium):
    """No optimistic transition from click alone; the post-action GET drives it."""
    page, errors = browser_page(chromium, 'organization-disabled',
                                transitions={'activate': 'running-unauthenticated-screenshot'})
    try:
        before = oracle.evaluate_visible_case(CONTRACT, 'organization-disabled',
                                               oracle.visible_intents(page))
        assert before['status'] == 'PASS', before
        form = page.locator('form[data-intent="intent:activate-organization"]')
        form.locator('input[name="adminDeviceId"]').fill('admin-pc')
        form.locator('button[type="submit"]').click()
        page.wait_for_function('''()=>window.oracleFixture.current==="running-unauthenticated-screenshot" &&
           window.oracleFixture.requests.filter(x=>x.url.includes("view=overview")).length>=3''',
                               timeout=15000)
        after = oracle.evaluate_visible_case(CONTRACT, 'running-unauthenticated-screenshot',
                                              oracle.visible_intents(page))
        assert after['status'] == 'PASS', after
        assert errors == []
    finally:
        page.close()


def test_native_urls_remain_real_clickable_links(chromium):
    page, errors = browser_page(chromium)
    try:
        for destination in CONTRACT['navigation']['overview'], CONTRACT['navigation']['worker_bank']:
            link = page.locator(f'nav a[href="{destination}"]')
            assert link.count() == 1
            assert link.get_attribute('onclick') is None
            assert link.get_attribute('target') is None
        assert errors == []
    finally:
        page.close()


def test_login_then_logout_recompute_available_operations(chromium):
    """Administrator session is separate authority, not local initialization."""
    page, errors = browser_page(chromium, transitions={
        'login': 'running-authenticated',
        'logout': 'running-unauthenticated-screenshot',
    })
    try:
        form = page.locator('form[data-intent="intent:login-administrator"]')
        form.locator('input[name="adminDeviceId"]').fill('admin-pc')
        form.locator('button[type="submit"]').click()
        page.get_by_text('Organization administrator authenticated', exact=True).wait_for(timeout=15000)
        logged_in = oracle.evaluate_visible_case(CONTRACT, 'running-authenticated',
                                                  oracle.visible_intents(page))
        assert logged_in['status'] == 'PASS', logged_in
        assert 'intent:logout-administrator' in oracle.visible_intents(page)
        page.locator('button[data-intent="intent:logout-administrator"]').click()
        page.get_by_text('Organization session not established', exact=True).wait_for(timeout=15000)
        logged_out = oracle.evaluate_visible_case(CONTRACT, 'running-unauthenticated-screenshot',
                                                   oracle.visible_intents(page))
        assert logged_out['status'] == 'PASS', logged_out
        assert 'X'*64 not in json.dumps(page.evaluate('window.__mcelSlh.app.receipts()'))
        assert errors == []
    finally:
        page.close()


def test_failed_activation_does_not_promote_to_login(chromium):
    page, errors = browser_page(chromium, 'organization-disabled', fail_action='activate')
    try:
        form = page.locator('form[data-intent="intent:activate-organization"]')
        form.locator('input[name="adminDeviceId"]').fill('admin-pc')
        form.locator('button[type="submit"]').click()
        page.wait_for_function('''()=>window.__mcelSlh.app.receipts().some(
            x=>x.intentId==="intent:activate-organization" && x.disposition!=="completed")''', timeout=15000)
        assert page.evaluate('window.oracleFixture.current') == 'organization-disabled'
        after = oracle.evaluate_visible_case(CONTRACT, 'organization-disabled', oracle.visible_intents(page))
        assert after['status'] == 'PASS', after
        assert errors == []
    finally:
        page.close()
