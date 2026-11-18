"""SLH owns its Deploy/Redeploy enablement; backend remains operation authority.

All HTTP is mocked. No live deployment or local service mutation occurs.
"""
from __future__ import annotations

import shutil

from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import compile_slh_application, render_slh_page
from tests.test_mcel_slh_startup_ewg import MOCK
from tests.test_mcel_seh_two_action_lifecycle_ui import _setup


ACTION = '[data-mcel-operation-action="Deploy / Redeploy"]'


def test_deploy_declared_always_enabled_in_both_local_and_network_notebooks():
    ir, _ = compile_slh_application()
    nodes = {n['id']: n for n in ir['surfaces'][0]['nodes']}
    for target in ('local', 'network'):
        tab = nodes[f'surface-node:slh.{target}-seh-lifecycle']['tabs'][1]
        assert tab['actions']['Deploy / Redeploy']['alwaysEnabled'] is True
        assert 'alwaysEnabled' not in tab['actions']['Undeploy']


def test_deploy_never_disabled_by_failed_observation_or_authorization_clear():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True,
            executable_path=shutil.which('chromium') or None, args=['--no-sandbox'])
        try:
            page, notebook = _setup(browser, 'UNDEPLOYED')
            button = notebook.locator(ACTION)
            assert button.is_enabled()
            # Transient observation failure: render last verified values, but
            # do not disable this application-declared always-enabled action.
            page.evaluate('window.sehUiPhase=null')
            notebook.locator('[data-mcel-operation-tab="configuration"]').click()
            notebook.locator('[data-mcel-operation-tab="deployment"]').click()
            page.wait_for_function('''() => document.querySelector(
                '[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"] '
                + '[data-mcel-operation-status]')?.textContent.includes('showing last observed values')''')
            assert button.is_enabled()
            assert button.get_attribute('disabled') is None

            # Clicking while the observation has failed is an actual attempt.
            # The capability/backend still decides whether to accept it.
            page.evaluate('window.sehUiPhase="UNDEPLOYED"')
            button.click()
            page.wait_for_function('''() => window.sehUiCalls.some(c=>c.operation==='deploy')''')
            assert button.is_enabled()

            # Explicit UI authorization clear must not suppress this button.
            page.evaluate('''() => window.__mcelSlh.app.setNotebookAuthorization(
                false,{scope:'operations'})''')
            assert button.is_enabled()
            assert button.get_attribute('disabled') is None
        finally:
            browser.close()


def test_deploy_remains_enabled_while_request_is_in_flight():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True,
            executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page = browser.new_page(viewport={'width':1280,'height':900})
            page.evaluate(MOCK)
            # Ports capture their fetcher at mount, so intercept before mount.
            page.evaluate('''() => {
              window.releaseDeploy=null;
              const original=window.fetch;
              window.fetch=(url,options={})=>{
                if(String(url)==='/api/applications/slh/seh/deployment'){
                  const operation=JSON.parse(options.body||'{}').operation;
                  const ok=result=>({ok:true,json:async()=>({ok:true,result})});
                  if(operation==='inspect')return Promise.resolve(ok({generation:33,
                    selectedController:'coolify-c',applicationUuid:'mock-uuid',
                    controllers:[{identity:'coolify-c'}],deploymentStatus:'UNDEPLOYED'}));
                  if(operation==='operation-state')return Promise.resolve(ok({
                    schema:'main-computer.seh-management-operation-snapshot.v1',
                    state:'IDLE',busy:false,operation:null,last_operation:null}));
                  if(operation==='deploy')return new Promise(resolve=>{
                    window.releaseDeploy=()=>resolve({ok:false,json:async()=>({
                      ok:false,error:{code:'TEST_REFUSAL'}})});
                  });
                }
                return original(url,options);
              };
            }''')
            page.on('dialog',lambda dialog:dialog.accept())
            page.set_content(render_slh_page())
            page.wait_for_function('''() => !!window.__mcelSlh?.app?.readState(
              'state:seh-deployment-status')?.deploymentStatus''',timeout=5000)
            notebook=page.locator('[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"]')
            notebook.locator('[data-mcel-operation-tab="deployment"]').click()
            button=notebook.locator(ACTION)
            button.click()
            page.wait_for_function('''() => typeof window.releaseDeploy==='function' ''',timeout=5000)
            assert button.is_enabled()
            assert button.get_attribute('disabled') is None
            page.evaluate('window.releaseDeploy()')
            page.wait_for_function('''() => document.querySelector(
                '[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"] '
                + '[data-mcel-operation-status]')?.textContent.includes('Operation failed')''',timeout=5000)
            assert button.is_enabled()
        finally:
            browser.close()
