"""MCEL presents only two SEH deployment actions and preserves LMS receipts.

All browser HTTP calls are mocked; these tests never contact live LMS or Coolify.
"""
from __future__ import annotations

import shutil

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import render_slh_page,compile_slh_application
from tests.test_mcel_slh_startup_ewg import MOCK


def test_semantic_two_action_surface_and_bound_ports():
    ir,_=compile_slh_application()
    ids={x['id'] for x in ir['intents']}
    assert {'intent:seh-deployment-deploy','intent:seh-deployment-undeploy'} <= ids
    assert not any(id.startswith('intent:seh-deployment-preview-') or
                   id=='intent:seh-deployment-activate' for id in ids)
    for surface in ir['surfaces']:
        for node in surface['nodes']:
            if node['id'] in ('surface-node:slh.local-seh-lifecycle',
                              'surface-node:slh.network-seh-lifecycle'):
                actions=node['tabs'][1]['actions']
                assert list(actions)==['Deploy / Redeploy','Undeploy']
                assert actions['Deploy / Redeploy']['intent']['ref']=='intent:seh-deployment-deploy'
                assert actions['Undeploy']['intent']['ref']=='intent:seh-deployment-undeploy'
                conditions=actions['Undeploy']['enabledWhen']['any']
                assert {rule['equals'] for rule in conditions} == {
                    'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED',
                    'PUBLIC_RUNTIME_PRESENT_SLH_DISCONNECTED',
                    'APPLICATION_REGISTERED_NOT_RUNTIME_VERIFIED',
                    'APPLICATION_REGISTERED_RUNTIME_UNAVAILABLE',
                    'CONNECTED_VERIFIED',
                }
                assert all(rule['state']['ref']=='state:seh-deployment-status' and
                           rule['path']=='deploymentStatus' for rule in conditions)


def _setup(browser, phase):
    page=browser.new_page(viewport={'width':1280,'height':900})
    page.evaluate(MOCK)
    page.evaluate('''phase=>{
      window.sehUiCalls=[];
      window.sehUiPhase=phase;
      window.sehLastId=null;
      window.sehRows={};
      const original=window.fetch;
      window.fetch=async(url,options={})=>{
        if(String(url)==='/api/applications/slh/seh/deployment'){
          const body=JSON.parse(options.body||'{}');window.sehUiCalls.push(body);
          const success=result=>({ok:true,json:async()=>({ok:true,result})});
          if(body.operation==='inspect')return success({generation:33,
            selectedController:'coolify-c',applicationUuid:'mock-uuid',
            controllers:[{identity:'coolify-c'}],deploymentStatus:window.sehUiPhase});
          if(body.operation==='operation-state')return success({
            schema:'main-computer.seh-management-operation-snapshot.v1',
            state:'IDLE',busy:false,operation:null,last_operation:null});
          if(body.operation==='operation-status')return success(window.sehRows[body.arguments.operationId]);
          if(body.operation==='deploy'||body.operation==='undeploy'){
            const id=body.operationId;
            window.sehLastId=id;
            window.sehRows[id]={operation_id:id,phase:'RECORDED',
              action:body.operation+'-seh',stage:'MUTATION_EXECUTING',
              result:{status:body.operation==='deploy'?'CONNECTED_VERIFIED':'SEH_UNDEPLOYED',
                remote_mutation:body.operation==='undeploy'},
              output:[{seq:1,stream:'stderr',text:'[SEH +0.1s] harness: executing'}]};
            return success({outcome:'accepted-deferred',operationId:id,
              message:'LMS accepted',applicationUuid:'mock-uuid'});
          }
          return {ok:false,json:async()=>({ok:false,error:{code:'UNEXPECTED'}})};
        }
        return original(url,options);
      };
    }''',phase)
    page.on('dialog',lambda dialog:dialog.accept())
    page.set_content(render_slh_page())
    page.wait_for_function("()=>!!window.__mcelSlh?.app && window.__mcelSlh.app.readState('state:seh-deployment-status')?.deploymentStatus")
    tabs=page.locator('[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"]')
    tabs.locator('[data-mcel-operation-tab="deployment"]').click()
    return page,tabs


@pytest.mark.parametrize('phase,action,backend,result',[
    ('UNDEPLOYED','Deploy / Redeploy','deploy','CONNECTED_VERIFIED'),
    ('APPLICATION_REGISTERED_NOT_RUNTIME_VERIFIED','Undeploy','undeploy','SEH_UNDEPLOYED'),
    ('APPLICATION_REGISTERED_NOT_RUNTIME_VERIFIED','Deploy / Redeploy','deploy','CONNECTED_VERIFIED'),
])
def test_two_action_browser_modal_and_durable_receipt(phase,action,backend,result):
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,
            executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page,tabs=_setup(browser,phase)
            errors=[];page.on('pageerror',lambda err:errors.append(str(err)))
            assert tabs.locator('[data-mcel-operation-action]').all_text_contents()==[
                'Deploy / Redeploy','Undeploy']
            assert tabs.locator('[data-mcel-operation-action="Preflight Deploy"]').count()==0
            assert tabs.locator('[data-mcel-operation-action="Activate / Resume"]').count()==0
            tabs.locator('[data-mcel-operation-action="'+action+'"]').click()
            page.wait_for_function('''()=>window.sehUiCalls.some(c=>c.operation===
              (window.sehUiPhase==='UNDEPLOYED'?'deploy':window.sehLastId&&window.sehRows[window.sehLastId]?.action?.replace('-seh','')))''')
            assert page.evaluate('window.sehLastId')
            page.wait_for_function('''()=>window.__mcelSlh.managementOverlay.diagnostics().status==='recorded' ||
                window.__mcelSlh.managementOverlay.diagnostics().status==='RECORDED' ||
                document.querySelector('[data-mcel-lms-operation-modal]')?.textContent?.includes('''+repr(result)+''')''')
            assert page.locator('[data-mcel-lms-operation-modal]').is_visible()
            assert result in page.locator('[data-mcel-lms-operation-modal]').inner_text()
            assert all(c['operation'] not in ('preview-deploy','preview-activate',
                         'preview-undeploy','activate') for c in page.evaluate('window.sehUiCalls'))
            assert not errors,errors
        finally:
            browser.close()


def test_completed_operation_id_is_not_reused_for_next_deploy():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,
            executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page,tabs=_setup(browser,'APPLICATION_REGISTERED_NOT_RUNTIME_VERIFIED')
            button=tabs.locator('[data-mcel-operation-action="Deploy / Redeploy"]')
            button.click()
            page.wait_for_function("()=>window.__mcelSlh.managementOverlay.diagnostics().status==='recorded' || window.__mcelSlh.managementOverlay.diagnostics().status==='RECORDED' || document.querySelector('[data-mcel-lms-operation-modal]')?.textContent?.includes('CONNECTED_VERIFIED')")
            first=page.evaluate('window.sehLastId')
            page.locator('[data-mcel-lms-operation-modal] button',has_text='Dismiss').click()
            button.click()
            page.wait_for_function('''old=>window.sehLastId!==old''',arg=first)
            assert page.evaluate('window.sehLastId')!=first
        finally:
            browser.close()
