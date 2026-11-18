"""Shared MCEL operation-notebook outcome/eligibility contract.

All backend requests are mocked in Chromium. No SLH, LMS, SEH or Coolify
mutation is performed. In particular, a deferred request acknowledgement is
not a failed operation and may not become a permanent action eligibility gate.
"""
from __future__ import annotations

import shutil

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import render_slh_page
from tests.test_mcel_seh_two_action_lifecycle_ui import _setup
from tests.test_mcel_slh_startup_ewg import MOCK


NOTEBOOK = '[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"]'


def _browser(playwright):
    return playwright.chromium.launch(headless=True,
        executable_path=shutil.which('chromium') or None, args=['--no-sandbox'])


def _mock_outcome_page(browser, *, outcome, operation):
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    page.evaluate(MOCK)
    page.evaluate('''args => {
      window.sehUiCalls=[];
      window.sehPhase='PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED';
      window.operationOutcome=args.outcome;
      const previous=window.fetch;
      window.fetch=async(url, options={})=>{
        if(String(url)==='/api/applications/slh/seh/deployment'){
          const body=JSON.parse(options.body||'{}');window.sehUiCalls.push(body);
          const ok=result=>({ok:true,json:async()=>({ok:true,result})});
          if(body.operation==='inspect')return ok({generation:33,
            selectedController:'coolify-c',applicationUuid:'mock-uuid',
            controllers:[{identity:'coolify-c'}],deploymentStatus:window.sehPhase});
          if(body.operation==='operation-state')return ok({
            schema:'main-computer.seh-management-operation-snapshot.v1',
            state:'IDLE',busy:false,operation:null,last_operation:null});
          if(body.operation===args.operation)return ok({
            outcome:window.operationOutcome,message:'TEST_ACKNOWLEDGEMENT',
            operationId:body.operationId||'mock-operation',applicationUuid:'mock-uuid'});
          return {ok:false,json:async()=>({ok:false,error:{code:'TEST_NOT_ALLOWED'}})};
        }
        return previous(url,options);
      };
    }''', {'outcome': outcome, 'operation': operation})
    page.on('dialog', lambda dialog: dialog.accept())
    page.set_content(render_slh_page())
    page.wait_for_function("()=>!!window.__mcelSlh?.app?.readState('state:seh-deployment-status')?.deploymentStatus")
    return page, page.locator(NOTEBOOK)


def test_deferred_lms_deploy_does_not_disable_undeploy_or_recreate_chrome():
    with sync_playwright() as pw:
        browser=_browser(pw)
        try:
            page, notebook = _setup(browser, 'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED')
            page.on('pageerror', lambda err: pytest.fail(str(err)))
            deploy=notebook.locator('[data-mcel-operation-action="Deploy / Redeploy"]')
            undeploy=notebook.locator('[data-mcel-operation-action="Undeploy"]')
            assert undeploy.is_enabled()
            page.evaluate('''() => {
              const root=document.querySelector(''' + repr(NOTEBOOK) + ''');
              window.__reactivity={
                deploy:root.querySelector('[data-mcel-operation-action="Deploy / Redeploy"]'),
                undeploy:root.querySelector('[data-mcel-operation-action="Undeploy"]'),
                field:root.querySelector('[data-mcel-operation-field="deploymentStatus"]'),
                status:root.querySelector('[data-mcel-operation-status]')
              };
            }''')
            deploy.click()
            page.wait_for_function('''() => document.querySelector(''' + repr(NOTEBOOK) + ''')
              ?.querySelector('[data-mcel-operation-status]')?.textContent
              ?.includes('Operation acknowledged; completion not yet verified')''')
            assert undeploy.is_enabled()
            assert 'refused' not in notebook.locator('[data-mcel-operation-status]').inner_text().lower()
            assert page.evaluate('''() => {
              const root=document.querySelector(''' + repr(NOTEBOOK) + ''');
              return root.querySelector('[data-mcel-operation-action="Deploy / Redeploy"]')===window.__reactivity.deploy &&
                root.querySelector('[data-mcel-operation-action="Undeploy"]')===window.__reactivity.undeploy &&
                root.querySelector('[data-mcel-operation-field="deploymentStatus"]')===window.__reactivity.field &&
                root.querySelector('[data-mcel-operation-status]')===window.__reactivity.status;
            }''')
            # The LMS monitor calls the existing read intent on settlement.
            # Reconciled observations must not need a tab switch or remount.
            page.evaluate("window.sehUiPhase='SEH_UNDEPLOYED'")
            assert page.evaluate("async()=>await window.__mcelSlh.app.execute('intent:read-seh-deployment')")['disposition']=='completed'
            assert notebook.locator('[data-mcel-operation-field="deploymentStatus"]').inner_text()=='SEH_UNDEPLOYED'
            assert undeploy.is_disabled(), "Undeploy must be disabled after authoritative undeploy observation"
            assert page.evaluate("""() => {
              const root=document.querySelector('[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"]');
              return root.querySelector('[data-mcel-operation-action="Undeploy"]')===window.__reactivity.undeploy &&
                root.querySelector('[data-mcel-operation-field="deploymentStatus"]')===window.__reactivity.field;
            }""")
        finally:
            browser.close()


@pytest.mark.parametrize('outcome', ['accepted-deferred','command-returned','recorded'])
def test_nonterminal_acknowledgements_do_not_gate_future_actions(outcome):
    with sync_playwright() as pw:
        browser=_browser(pw)
        try:
            page, notebook=_mock_outcome_page(browser, outcome=outcome, operation='undeploy')
            notebook.locator('[data-mcel-operation-tab="deployment"]').click()
            button=notebook.locator('[data-mcel-operation-action="Undeploy"]')
            assert button.is_enabled()
            button.click()
            page.wait_for_function('''() => document.querySelector(''' + repr(NOTEBOOK) + ''')
              ?.querySelector('[data-mcel-operation-status]')?.textContent
              ?.includes('Operation acknowledged; completion not yet verified')''')
            assert button.is_enabled()
            assert page.evaluate('window.sehUiCalls.some(c=>c.operation===\'undeploy\')')
        finally:
            browser.close()


@pytest.mark.parametrize('outcome,expected', [
    ('refused','Operation refused'),
    ('failed','Operation failed'),
    ('unknown','Operation outcome unverified'),
])
def test_real_refusal_is_reported_without_poisoning_unrelated_eligibility(outcome,expected):
    with sync_playwright() as pw:
        browser=_browser(pw)
        try:
            page, notebook=_mock_outcome_page(browser, outcome=outcome, operation='undeploy')
            notebook.locator('[data-mcel-operation-tab="deployment"]').click()
            button=notebook.locator('[data-mcel-operation-action="Undeploy"]')
            button.click()
            page.wait_for_function('''() => document.querySelector(''' + repr(NOTEBOOK) + ''')
              ?.querySelector('[data-mcel-operation-status]')?.textContent
              ?.includes(''' + repr(expected) + ''')''')
            assert button.is_enabled()
            assert notebook.locator('[data-mcel-operation-action="Deploy / Redeploy"]').is_enabled()
        finally:
            browser.close()


def test_deferred_form_acknowledgement_does_not_disable_semantic_forms():
    with sync_playwright() as pw:
        browser=_browser(pw)
        try:
            page, notebook=_mock_outcome_page(browser,outcome='accepted-deferred',operation='select')
            form=notebook.locator('[data-mcel-operation-form="intent:seh-deployment-select"]')
            assert form.locator('button[type="submit"]').is_enabled()
            form.locator('select[name="name"]').select_option('coolify-c')
            form.locator('button[type="submit"]').click()
            page.wait_for_function('''() => document.querySelector(''' + repr(NOTEBOOK) + ''')
              ?.querySelector('[data-mcel-operation-status]')?.textContent
              ?.includes('Operation acknowledged; completion not yet verified')''')
            assert form.locator('button[type="submit"]').is_enabled()
            assert page.evaluate('window.sehUiCalls.some(c=>c.operation===\'select\')')
        finally:
            browser.close()


def test_undeploy_eligibility_tracks_authoritative_deployment_phase_in_place():
    """A changing observation updates both action edges, without remounting.

    This is the user-reproduced defect: UNDEPLOYED with Undeploy still enabled
    after an explicit successful read. All transport responses are mocked.
    """
    with sync_playwright() as pw:
        browser=_browser(pw)
        try:
            page, notebook=_setup(browser, 'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED')
            errors=[]
            page.on('pageerror', lambda error: errors.append(str(error)))
            undeploy=notebook.locator('[data-mcel-operation-action="Undeploy"]')
            deploy=notebook.locator('[data-mcel-operation-action="Deploy / Redeploy"]')
            assert undeploy.is_enabled()
            page.evaluate('''() => {
              const root=document.querySelector(''' + repr(NOTEBOOK) + ''');
              window.__phaseControls={
                undeploy:root.querySelector('[data-mcel-operation-action="Undeploy"]'),
                deploy:root.querySelector('[data-mcel-operation-action="Deploy / Redeploy"]'),
                status:root.querySelector('[data-mcel-operation-field="deploymentStatus"]')
              };
            }''')
            for phase,allowed in [
                ('UNDEPLOYED',False),
                ('TARGET_SELECTED',False),
                ('APPLICATION_REGISTERED_NOT_RUNTIME_VERIFIED',True),
                ('PUBLIC_RUNTIME_PRESENT_SLH_DISCONNECTED',True),
                ('APPLICATION_REGISTERED_RUNTIME_UNAVAILABLE',True),
                ('SEH_UNDEPLOYED',False),
                ('PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED',True),
            ]:
                page.evaluate('phase=>window.sehUiPhase=phase',phase)
                receipt=page.evaluate('''async()=>await window.__mcelSlh.app.execute(
                  'intent:read-seh-deployment')''')
                assert receipt['disposition']=='completed',receipt
                assert notebook.locator('[data-mcel-operation-field="deploymentStatus"]').inner_text()==phase
                assert undeploy.is_enabled() is allowed, phase
                assert deploy.is_enabled(), phase
                assert page.evaluate('''() => {
                  const root=document.querySelector(''' + repr(NOTEBOOK) + ''');
                  return root.querySelector('[data-mcel-operation-action="Undeploy"]')===window.__phaseControls.undeploy &&
                    root.querySelector('[data-mcel-operation-action="Deploy / Redeploy"]')===window.__phaseControls.deploy &&
                    root.querySelector('[data-mcel-operation-field="deploymentStatus"]')===window.__phaseControls.status;
                }'''), phase
            assert not errors, errors
        finally:
            browser.close()
