"""SEH preflight is now harness-internal, while diagnostics remain accessible.

The older Preflight Deploy/Activate/Undeploy buttons have been retired from
MCEL. These tests assert that an unavailable LMS still produces a visible
operation outcome rather than a silent button failure.
"""
from __future__ import annotations

import shutil

from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import render_slh_page,compile_slh_application
from tests.test_mcel_slh_startup_ewg import MOCK


def test_only_two_deployment_actions_exposed_and_preflight_still_backend_owned():
    ir,_=compile_slh_application()
    node=next(n for n in ir['surfaces'][0]['nodes']
              if n['id']=='surface-node:slh.local-seh-lifecycle')
    assert set(node['tabs'][1]['actions'])=={'Deploy / Redeploy','Undeploy'}
    from main_computer.mcel_slh_seh_deployment import _HARNESS
    # Explicit previews remain available to direct diagnostic callers, but
    # are not independent steps in the normal operator UI.
    assert _HARNESS['preview-deploy']==('deploy-seh',False)
    assert _HARNESS['preview-undeploy']==('undeploy-seh',False)


def test_failed_deploy_submission_is_visible_in_existing_modal():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,
            executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1280,'height':800})
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('dialog',lambda d:d.accept())
            page.evaluate(MOCK)
            page.evaluate('''()=>{
                window.sehCalls=[];
                const original=window.fetch;
                window.fetch=async(url,opts={})=>{
                  if(String(url)==='/api/applications/slh/seh/deployment'){
                    const req=JSON.parse(opts.body||'{}');window.sehCalls.push(req);
                    const response=result=>({ok:true,json:async()=>({ok:true,result})});
                    if(req.operation==='operation-state')return response({
                      schema:'main-computer.seh-management-operation-snapshot.v1',
                      state:'IDLE',busy:false,operation:null,last_operation:null});
                    if(req.operation==='inspect')return response({
                      generation:33,selectedController:'coolify-c',controllers:[{identity:'coolify-c'}],
                      deploymentStatus:'UNDEPLOYED',applicationUuid:'previous-uuid'});
                    return {ok:false,json:async()=>({ok:false,
                      error:{code:'LMS_MANAGEMENT_UNAVAILABLE'}})};
                  }
                  return original(url,opts);
                };
            }''')
            page.set_content(render_slh_page())
            page.wait_for_function('''()=>window.__mcelSlh?.app?.readState(
              'state:seh-deployment-status')?.deploymentStatus==='UNDEPLOYED' ''')
            notebook=page.locator('[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"]')
            notebook.locator('[data-mcel-operation-tab="deployment"]').click()
            assert notebook.locator('[data-mcel-operation-action]').all_text_contents()==[
                'Deploy / Redeploy','Undeploy']
            notebook.locator('[data-mcel-operation-action="Deploy / Redeploy"]').click()
            page.wait_for_function("()=>document.querySelector('[data-mcel-lms-operation-modal]')?.textContent?.includes('LMS_MANAGEMENT_UNAVAILABLE')")
            modal=page.locator('[data-mcel-lms-operation-modal]')
            assert modal.is_visible()
            assert 'LMS_MANAGEMENT_UNAVAILABLE' in modal.inner_text()
            assert 'Preflight' not in modal.inner_text()
            assert any(req['operation']=='deploy' for req in page.evaluate('window.sehCalls'))
            assert not errors,errors
        finally:
            browser.close()
