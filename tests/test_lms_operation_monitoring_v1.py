"""Operation state is durable and discoverable via both authorized ingress paths."""
from __future__ import annotations

import json
import shutil
import threading
import time

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import render_slh_page
from main_computer.slh.seh_deployment_management import SLHSEHDeploymentManager, SEHManagementRefused
from tests.test_lms_seh_ownership_cutover import Services, send
from tests.test_slh_seh_coolify_management_backend import root_state
from tests.test_mcel_seh_management_oracle_v1 import _browser_mock


def _wait_for_phase(manager, operation_id, wanted, timeout=4):
    stop=time.monotonic()+timeout
    while time.monotonic()<stop:
        row=manager.operation_status(operation_id)
        if row['phase'] in wanted:
            return row
        time.sleep(.02)
    raise AssertionError('Operation did not reach '+repr(wanted))


def test_local_accept_remote_reconnect_same_operation_and_completion(tmp_path):
    services=Services(tmp_path)
    entered=threading.Event()
    finish=threading.Event()
    attempts=[]
    def harness(args, *, control):
        attempts.append(args)
        entered.set()
        assert finish.wait(4), 'Test harness never released'
        return {'status':'REPLACEMENT_QUEUED_NOT_VERIFIED','application_uuid':'test-owned-app'}
    try:
        services.manager.harness_runner=harness
        operation_id='mcel-seh-cross-ingress-123456789'
        body={'action':'deploy-seh','execute':True,'expected_generation':1,
              'confirm':'DEPLOY_SEH','operation_id':operation_id}
        started=time.monotonic()
        status,result=send(services.lms.server_port,'POST','/v1/local/seh/deployment/harness',body)
        assert status==200 and result['status']=='ACCEPTED'
        assert time.monotonic()-started<1.5, 'HTTP request waited for harness execution'
        assert entered.wait(2)
        local_code,local=send(services.lms.server_port,'GET','/v1/local/seh/deployment/operations')
        remote_code,remote=send(services.org.server_port,'GET',
              '/v1/org/admin/seh/deployment/operations',headers={'Authorization':'Bearer good'})
        assert local_code==remote_code==200
        assert local['state']==remote['state']=='BUSY'
        assert local['operation']['operation_id']==remote['operation']['operation_id']==operation_id
        assert local['operation']['stage']==remote['operation']['stage']=='HARNESS_EXECUTING'
        assert local['operation']['cancelable'] is False
        assert send(services.org.server_port,'GET','/v1/org/admin/seh/deployment/operations')[0]==401
        assert send(services.lms.server_port,'POST','/v1/local/seh/deployment/operations/'+operation_id+'/cancel',{})[0]==409
        # No conflicting local or remote mutation can slip through.
        conflict={**body,'operation_id':'mcel-seh-conflicting-123456789'}
        c,denied=send(services.org.server_port,'POST','/v1/org/admin/seh/deployment/harness',conflict,
                       headers={'Authorization':'Bearer good'})
        assert c==409 and denied['error']['code']=='SEH_MANAGEMENT_BUSY'
        # Both observers remain responsive while the harness runs.
        assert send(services.lms.server_port,'GET','/v1/local/seh/deployment')[0]==200
        assert send(services.org.server_port,'GET','/v1/org/admin/seh/deployment',
                   headers={'Authorization':'Bearer good'})[0]==200
        finish.set()
        assert _wait_for_phase(services.manager,operation_id,{'RECORDED'})['result']['status']=='REPLACEMENT_QUEUED_NOT_VERIFIED'
        assert send(services.org.server_port,'GET','/v1/org/admin/seh/deployment/operations',
                    headers={'Authorization':'Bearer good'})[1]['state']=='IDLE'
        assert len(attempts)==1
    finally:
        finish.set()
        services.stop()


def test_accepted_cancellation_is_durable_before_harness_starts(tmp_path):
    manager=SLHSEHDeploymentManager(root=root_state(tmp_path))
    started=threading.Event()
    gate=threading.Event()
    invocations=[]
    runner=manager.operations._perform
    def delayed(operation_id, execute):
        started.set()
        gate.wait(3)
        runner(operation_id,execute)
    manager.operations._perform=delayed
    manager.harness_runner=lambda args,*,control:invocations.append(args) or {'status':'SHOULD_NOT_RUN'}
    operation_id='mcel-seh-cancel-before-start-12345678'
    body={'action':'deploy-seh','execute':True,'expected_generation':1,
          'confirm':'DEPLOY_SEH','operation_id':operation_id}
    try:
        assert manager.harness(body)['status']=='ACCEPTED'
        assert started.wait(1)
        snapshot=manager.operation_snapshot()
        assert snapshot['state']=='BUSY' and snapshot['operation']['cancelable'] is True
        canceled=manager.cancel_operation(operation_id)
        assert canceled['phase']=='CANCELED'
        gate.set()
        assert _wait_for_phase(manager,operation_id,{'CANCELED'})['result']['status']=='CANCELED'
        assert invocations==[]
    finally:
        gate.set()


def test_lms_restart_closes_interrupted_receipt_without_busy_lock(tmp_path):
    manager=SLHSEHDeploymentManager(root=root_state(tmp_path))
    operation_id='mcel-seh-restarted-operation-123456'
    row={'schema':'main-computer.seh-management-operation.v1',
         'operation_id':operation_id,'action':'deploy-seh','principal':'local-machine',
         'digest':'0'*64,'phase':'RUNNING','stage':'HARNESS_EXECUTING',
         'accepted_at':'2026-11-13T00:00:00+00:00'}
    manager.operations._write(row)
    new=SLHSEHDeploymentManager(root=manager.root,harness_runner=lambda *a,**k:pytest.fail('Resubmitted!'))
    state=new.operation_snapshot()
    assert state['state']=='IDLE' and state['busy'] is False
    assert state['last_operation']['operation_id']==operation_id
    assert state['last_operation']['result']['status']=='INCOMPLETE'
    assert state['last_operation']['result']['remote_mutation']=='UNKNOWN'
    assert new.operation_status(operation_id)['phase']=='RECORDED'
    # Identical replay cannot restart the orphaned operation. It uses the
    # journaled result; a different action must go through harness validation.
    new.operations.ensure_idle()


@pytest.mark.parametrize('old_stage', [None, 'RECONCILIATION_REQUIRED'])
def test_legacy_recovery_receipt_migrates_without_losing_evidence(tmp_path, old_stage):
    manager=SLHSEHDeploymentManager(root=root_state(tmp_path))
    operation_id='mcel-seh-old-recovery-123456789'
    row={'schema':'main-computer.seh-management-operation.v1',
         'operation_id':operation_id,'action':'deploy-seh','principal':'local-machine',
         'digest':'0'*64,'phase':'RECOVERY_REQUIRED',
         'result':{'status':'RECOVERY_REQUIRED'},
         'accepted_at':'2026-11-13T00:00:00+00:00'}
    details={'action':'deploy-seh','principal':'local-machine','expected_generation':1,
             'confirm':'DEPLOY_SEH','execute':True}
    row['digest']=manager.operations._digest(details)
    if old_stage is not None:
        row['stage']=old_stage
    manager.operations._write(row)
    snapshot=manager.operation_snapshot()
    assert snapshot['state']=='IDLE' and not snapshot['busy']
    assert snapshot['last_operation']['operation_id']==operation_id
    receipt=manager.operation_status(operation_id)
    assert receipt['phase']=='RECORDED'
    assert receipt['result']['status']=='INCOMPLETE'
    assert receipt['result']['remote_mutation']=='UNKNOWN'
    assert manager.operations._read(operation_id)['previous_stage']==old_stage
    # Exact replay is informational, never a second harness invocation.
    replay=manager.operations.submit(operation_id,details,
                                    lambda:pytest.fail('Legacy operation reexecuted'))
    assert replay['replayed_receipt'] and replay['status']=='INCOMPLETE'
    # Independently authorized local registry work isn't blocked by old history.
    manager.operations.ensure_idle()
    selected=manager.register({'name':'coolify-b','url':'https://coolify-b.example.org',
        'api_token':'PRIVATE-TEST-COOLIFY-TOKEN','expected_generation':1,
        'operation_id':'mcel-coolify-after-recovery-12345'})
    assert selected['remote_mutation'] is False


def test_incomplete_harness_result_is_terminal_and_new_request_runs_through_harness(tmp_path):
    manager=SLHSEHDeploymentManager(root=root_state(tmp_path))
    first={'action':'deploy-seh','execute':True,'expected_generation':1,
           'confirm':'DEPLOY_SEH','operation_id':'mcel-seh-first-incomplete-1234567'}
    second={**first,'operation_id':'mcel-seh-second-success-12345678'}
    calls=[]
    def runner(args,*,control):
        calls.append(args)
        return ({'status':'INCOMPLETE','failed_stage':'coolify_ack_unconfirmed',
                 'remote_mutation':'UNKNOWN'} if len(calls)==1 else
                {'status':'PLAN_ONLY','remote_mutation':False})
    manager.harness_runner=runner
    assert manager.harness(first)['status']=='ACCEPTED'
    previous=_wait_for_phase(manager,first['operation_id'],{'RECORDED'})
    assert previous['result']['status']=='INCOMPLETE'
    assert manager.operation_snapshot()['busy'] is False
    assert manager.harness(first)['replayed_receipt'] is True
    assert len(calls)==1
    # A fresh, expressly authorized request still reaches the existing
    # harness; it is responsible for rejecting unsafe Coolify mutations.
    assert manager.harness(second)['status']=='ACCEPTED'
    _wait_for_phase(manager,second['operation_id'],{'RECORDED'})
    assert len(calls)==2
    assert manager.operation_snapshot()['state']=='IDLE'


def test_browser_discovers_existing_lms_busy_on_local_and_after_remote_authentication():
    busy_id='mcel-seh-cross-ingress-123456789'
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,
            args=['--no-sandbox'])
        try:
            for target in ('overview','network'):
                page=browser.new_page(viewport={'width':1250,'height':800})
                errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('dialog',lambda d:d.accept())
                page.evaluate(_browser_mock())
                page.evaluate('''id=>{
                  const oldFetch=window.fetch;
                  window.fetch=async(url,options={})=>{
                    const body=JSON.parse(options.body||'{}');
                    if(String(url)==='/api/applications/slh/seh/deployment' && body.operation==='operation-state'){
                      window.lastLmsStatusTarget=body.target;
                      return {ok:true,json:async()=>({ok:true,result:{
                        schema:'main-computer.seh-management-operation-snapshot.v1',state:'BUSY',busy:true,
                        operation:{operation_id:id,action:'deploy-seh',stage:'HARNESS_EXECUTING',
                          phase:'RUNNING',cancelable:false}}})};
                    }
                    return oldFetch(url,options);
                  };
                }''',busy_id)
                page.set_content(render_slh_page(target))
                page.wait_for_function('()=>!!window.__mcelSlh?.managementOverlay')
                if target=='network':
                    # Before authorization there is no remote operation observation.
                    assert not page.locator('[data-mcel-lms-operation-modal]:visible').count()
                    form=page.locator('form[data-intent="intent:connect-network-slh"]')
                    form.locator('[name="address"]').fill('192.168.1.8')
                    form.locator('[name="port"]').fill('8899')
                    form.locator('[type="submit"]').click()
                page.wait_for_function('''()=>document.querySelector('[data-mcel-lms-operation-modal]')?.hidden===false''')
                assert busy_id in page.locator('[data-mcel-lms-operation-modal]').inner_text()
                assert page.evaluate('window.lastLmsStatusTarget')==('network' if target=='network' else 'local')
                assert not page.locator('[data-mcel-lms-operation-modal] button').filter(has_text='Cancel operation').is_visible()
                assert page.locator('[data-mcel-zone="navigator"] a').count()>0
                assert not errors,errors
                page.close()
        finally:
            browser.close()


def test_browser_modal_dismisses_without_cancelling_and_recovery_does_not_lock_workspace():
    """Dismissal is presentation-only; reconnect/status polling retains LMS truth."""
    busy_id='mcel-seh-dismissible-123456789'
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,
            executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1260,'height':760})
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.evaluate(_browser_mock())
            page.evaluate('''id=>{
              window.lmsMode='BUSY';window.cancelCalls=0;
              const oldFetch=window.fetch;
              window.fetch=async(url,options={})=>{
                const body=JSON.parse(options.body||'{}');
                if(String(url)==='/api/applications/slh/seh/deployment' && body.operation==='operation-state'){
                  if(window.lmsMode==='UNAVAILABLE')throw Error('test LMS unreachable');
                  return {ok:true,json:async()=>({ok:true,result:{
                    schema:'main-computer.seh-management-operation-snapshot.v1',
                    state:window.lmsMode==='INCOMPLETE'?'IDLE':window.lmsMode,
                    busy:window.lmsMode==='BUSY',
                    operation:window.lmsMode==='BUSY'?{operation_id:id,action:'deploy-seh',
                      stage:'HARNESS_EXECUTING',phase:'RUNNING',cancelable:false}:null,
                    last_operation:window.lmsMode==='INCOMPLETE'?{
                      operation_id:id,action:'deploy-seh',stage:'HARNESS_RESULT_UNCONFIRMED',
                      phase:'RECORDED',cancelable:false,
                      result:{status:'INCOMPLETE',remote_mutation:'UNKNOWN'}}:null}})};
                }
                if(body.operation==='cancel-operation'){window.cancelCalls++;throw Error('unsafe');}
                return oldFetch(url,options);
              };
            }''',busy_id)
            page.set_content(render_slh_page('overview'))
            modal=page.locator('[data-mcel-lms-operation-modal]')
            notice=page.locator('[data-mcel-lms-operation-indicator]')
            page.wait_for_function('''()=>document.querySelector('[data-mcel-lms-operation-modal]')?.hidden===false''')
            assert busy_id in modal.inner_text()
            assert modal.get_by_role('button',name='Dismiss').is_visible()
            assert not modal.get_by_role('button',name='Cancel operation').is_visible()
            # The dialog is centered in the visible workspace and fits the viewport.
            box=modal.bounding_box()
            card=page.locator('[data-mcel-lms-operation-panel]').bounding_box()
            assert box and card
            assert card['width'] <= 680 and card['height'] < 760
            assert abs(card['x']+card['width']/2-(box['x']+box['width']/2)) < 3
            assert abs(card['y']+card['height']/2-(box['y']+box['height']/2)) < 3
            modal.get_by_role('button',name='Dismiss').click()
            assert modal.is_hidden()
            assert notice.is_visible()
            assert page.evaluate('''()=>[...document.querySelector('[data-mcel-zone="workspace"]').children]
              .filter(n=>!n.hasAttribute('data-mcel-lms-operation-modal') &&
                !n.hasAttribute('data-mcel-lms-operation-indicator')).every(n=>!n.inert)''')
            page.wait_for_timeout(1500)
            assert modal.is_hidden(), 'Polling must not reopen a dismissed operation'
            assert page.evaluate('window.cancelCalls')==0
            notice.get_by_role('button',name='View operation').click()
            assert modal.is_visible()
            page.keyboard.press('Escape')
            assert modal.is_hidden()
            # Recovery is durable but must never hold the workspace inert.
            page.evaluate("window.lmsMode='INCOMPLETE'")
            page.evaluate('window.__mcelSlh.managementOverlay.refresh()')
            page.wait_for_function('''()=>document.querySelector('[data-mcel-lms-operation-indicator]')?.innerText.includes('incomplete')''')
            assert modal.is_hidden()
            assert notice.get_by_role('button',name='View receipt').is_visible()
            notice.get_by_role('button',name='View receipt').click()
            assert modal.is_visible()
            assert modal.get_attribute('data-mcel-blocking')=='false'
            assert page.evaluate('''()=>[...document.querySelector('[data-mcel-zone="workspace"]').children].every(n=>!n.inert)''')
            modal.get_by_role('button',name='Dismiss').click()
            assert modal.is_hidden()
            assert notice.is_visible()
            # An unavailable LMS remains observable without forcing a browser lock.
            page.evaluate("window.lmsMode='UNAVAILABLE'")
            page.evaluate('window.__mcelSlh.managementOverlay.refresh()')
            page.wait_for_function('''()=>document.querySelector('[data-mcel-lms-operation-indicator]')?.innerText.includes('unavailable')''')
            assert modal.is_hidden()
            assert notice.is_visible()
            assert page.evaluate('window.cancelCalls')==0
            assert not errors,errors
        finally:
            browser.close()


@pytest.mark.parametrize('accepted',[False,True])
def test_browser_deploy_modal_opens_before_backend_and_settles_without_navigation(accepted):
    # A delayed/failing LMS request must never delay or erase the modal.
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,
            executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1220,'height':740})
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('dialog',lambda dialog:dialog.accept())
            page.evaluate(_browser_mock())
            page.evaluate('''() => {
              window.mcelDeploy={mode:'IDLE',calls:0,release:null};
              const old=window.fetch;
              window.fetch=(url,options={})=>{
                if(String(url)!=='/api/applications/slh/seh/deployment')return old(url,options);
                const body=JSON.parse(options.body||'{}');
                if(body.operation==='operation-state'){
                  const active=window.mcelDeploy.mode==='BUSY';
                  return Promise.resolve({ok:true,json:async()=>({ok:true,result:{
                    schema:'main-computer.seh-management-operation-snapshot.v1',
                    state:active?'BUSY':'IDLE',busy:active,
                    operation:active?{operation_id:'mcel-seh-modal-123456789abcdef',
                      action:'deploy-seh',phase:'RUNNING',stage:'HARNESS_EXECUTING',cancelable:false}:null}})});
                }
                if(body.operation==='operation-status')return Promise.resolve({ok:true,json:async()=>({ok:true,
                  result:{operation_id:'mcel-seh-modal-123456789abcdef',phase:'RECORDED',
                    stage:'HARNESS_RETURNED',action:'deploy-seh',
                    result:{status:'REPLACEMENT_QUEUED_NOT_VERIFIED',application_uuid:'app-new-test'}}})});
                if(body.operation==='inspect')return Promise.resolve({ok:true,json:async()=>({ok:true,result:{
                  generation:33,selectedController:'coolify-c',applicationUuid:'',
                  deploymentStatus:'UNDEPLOYED',controllers:[{identity:'coolify-c'}]}})});
                if(body.operation==='deploy'){
                  window.mcelDeploy.calls++;
                  return new Promise(resolve=>{window.mcelDeploy.release=resolve;});
                }
                return old(url,options);
              };
            }''')
            page.set_content(render_slh_page('overview'))
            page.wait_for_function('''()=>window.__mcelSlh && document.querySelector(
              '[data-mcel-operation-tab="deployment"]')''')
            page.locator('[data-mcel-operation-tab="deployment"]:visible').first.click()
            page.locator('[data-mcel-operation-action="Deploy"]:visible').first.click()
            modal=page.locator('[data-mcel-lms-operation-modal]')
            page.wait_for_function('''()=>window.mcelDeploy.calls===1''')
            # Backend has not responded, yet the modal must already be visible.
            assert modal.is_visible()
            assert 'Submitting Deploy SEH' in modal.inner_text()
            log=modal.locator('[data-mcel-lms-operation-results]')
            assert 'Submitting to LMS' in log.inner_text()
            assert modal.get_by_role('button',name='Dismiss').is_visible()
            assert page.evaluate('window.mcelDeploy.release!==null')
            assert page.locator('[data-mcel-modal-region="output"]').evaluate(
                '(node)=>getComputedStyle(node).overflowY')=='auto'
            # Even before LMS responds, Dismiss is presentation-only.
            modal.get_by_role('button',name='Dismiss').click()
            assert modal.is_hidden()
            page.locator('[data-mcel-lms-operation-indicator] button').click()
            assert modal.is_visible()
            if accepted:
                page.evaluate('''()=>{
                  window.mcelDeploy.mode='BUSY';
                  window.mcelDeploy.release({ok:true,json:async()=>({ok:true,result:{
                    outcome:'accepted-deferred',message:'LMS accepted the operation',
                    operationId:'mcel-seh-modal-123456789abcdef',applicationUuid:''}})});
                }''')
                page.wait_for_function('''()=>document.querySelector(
                  '[data-mcel-lms-operation-results]')?.textContent.includes('LMS acknowledged operation')''')
                page.evaluate("window.mcelDeploy.mode='IDLE'")
                page.evaluate('window.__mcelSlh.managementOverlay.refresh()')
                page.wait_for_function('''()=>document.querySelector(
                  '[data-mcel-lms-operation-results]')?.textContent.includes('app-new-test')''')
                assert 'REPLACEMENT_QUEUED_NOT_VERIFIED' in log.inner_text()
                assert 'app-new-test' in log.inner_text()
            else:
                page.evaluate('''()=>window.mcelDeploy.release({ok:false,
                  json:async()=>({ok:false,error:{code:'LMS_MANAGEMENT_UNAVAILABLE'}})})''')
                page.wait_for_function('''()=>document.querySelector(
                  '[data-mcel-lms-operation-results]')?.textContent.includes('LMS_MANAGEMENT_UNAVAILABLE')''')
                assert modal.is_visible()
                page.wait_for_timeout(1400)  # An IDLE poll must not erase an error.
                assert modal.is_visible()
                assert 'LMS_MANAGEMENT_UNAVAILABLE' in log.inner_text()
            modal.get_by_role('button',name='Dismiss').click()
            assert modal.is_hidden()
            page.locator('[data-mcel-lms-operation-indicator] button').click()
            assert modal.is_visible(), 'Reopening must not require leaving the page'
            assert not errors,errors
        finally:
            browser.close()
