"""Identity notebook V1: generated semantics, target binding and real row actions.

Browser tests use a fake network boundary and do not mutate a resident ORG-SLH.
Backend authority lifecycle is separately tested in test_slh_identity_trash_backend.
"""
import json
import re
import shutil
from pathlib import Path
from unittest.mock import Mock

import pytest
from main_computer.mcel_slh_application import compile_slh_application,render_slh_page
from main_computer.mcel_slh_identity_notebook import execute_notebook,IdentityNotebookError

ROOT=Path(__file__).resolve().parents[1]
CONTRACT=json.loads((ROOT/'tests/fixtures/mcel_oracles/identity-notebook/oracle.v1.json').read_text())


def test_notebook_oracle_authority_placement_footer_and_semantic_actions():
    ir,plan=compile_slh_application()
    assert CONTRACT['schema']=='mcel.slh.organization-identity-notebook-oracle.v1'
    assert not ir['layouts'] and not ir['surfaces'][0].get('htmlBindings')
    nodes={n['id']:n for n in ir['surfaces'][0]['nodes']}
    notebook=nodes['surface-node:slh.organization-identity-notebook']
    assert notebook['nodeKind']=='record-notebook'
    assert notebook['title']==CONTRACT['title']
    assert [t['id'] for t in notebook['tabs']]==[t['id'] for t in CONTRACT['tabs']]
    for actual,wanted in zip(notebook['tabs'],CONTRACT['tabs']):
        assert actual['refresh']['ref']==wanted['read']
        assert {label:value['intent']['ref'] for label,value in actual['actions'].items()}==wanted['actions']
    assert notebook['retention']['refresh']['ref']==CONTRACT['footer']['read_policy']
    assert notebook['retention']['update']['ref']==CONTRACT['footer']['retention']
    assert notebook['empty']['ref']==CONTRACT['footer']['empty']
    assert notebook['tabs'][1]['actions']['Delete']['confirm']
    assert notebook['tabs'][2]['actions']['Remove']['confirm']
    assert next(r for r in plan['regions'] if any(m['ref']==notebook['id'] for m in r['members']))['purpose']=='workspace'
    recipe=[r['recipe'] for r in plan['regions']]
    assert recipe.index('task')<recipe.index('collection')
    assert recipe.index('commands')<recipe.index('collection')
    assert {'surface-node:slh.configure-org-slh','surface-node:slh.connect-network-slh','surface-node:slh.available-local-identities'}<=nodes.keys()
    for target in ('overview','network'):
        page=render_slh_page(target)
        assert 'record-notebook' in page and 'Organization Identities' in page
        assert 'setNotebookAuthorization' in page
        assert '/api/applications/slh/identity-notebook' in page


class FakeOrganization:
    def __init__(self):self.calls=[]
    def request(self,method,path,token,body):
        self.calls.append((method,path,token,body))
        assert token=='t'*32
        if path=='/v1/admin/enrollments':return {'enrollments':[
          {'id':'a'*32,'device_id':'worker1','device_name':'Worker One','requested_role':'worker','status':'pending','created_at':1760000000},
          {'id':'b'*32,'device_id':'worker2','device_name':'Worker Two','requested_role':'worker','status':'denied','created_at':1760000000}]}
        if path in ('/v1/admin/devices','/v1/admin/devices/trash'):
          status='active' if path.endswith('/devices') else 'revoked'
          return {'devices':[{'id':'worker1','name':'Worker One','role':'worker','status':status,
            'created_at':1760000000,'trashed_at':1760000001}]}
        if method=='PATCH':return {'remove_trash_after_30_days':body['remove_trash_after_30_days']}
        if path=='/v1/admin/policies':return {'remove_trash_after_30_days':True}
        if path=='/v1/admin/devices/trash/empty':return {'removed_count':1}
        return {'status':'APPROVED' if path.endswith('/approvals') else 'DENIED'}


def test_fixed_adapter_mapping_filters_status_and_preserves_real_ids():
    fake=FakeOrganization()
    invoke=lambda name,data={}:execute_notebook(name,data,target='local',local_token='t'*32,local_client=fake)
    pending=invoke('read-pending')
    assert pending==[{'id':'a'*32,'identity':'Worker One','role':'worker','requestedAt':'Oct 09, 2025'}]
    assert len(invoke('read-enrolled'))==1
    assert len(invoke('read-trash'))==1
    assert invoke('read-retention')=={'enabled':True}
    invoke('accept',{'recordId':'a'*32})
    invoke('reject',{'recordId':'a'*32})
    invoke('delete',{'recordId':'worker1'})
    invoke('reactivate',{'recordId':'worker1'})
    invoke('remove',{'recordId':'worker1'})
    assert invoke('set-retention',{'enabled':False})=={'enabled':False}
    assert invoke('empty-trash')['outcome']=='EMPTIED'
    assert ('POST','/v1/enrollment/approvals','t'*32,{'request_id':'a'*32}) in fake.calls
    assert ('POST','/v1/admin/devices/worker1/remove','t'*32,{'confirm':'REMOVE_DEVICE'}) in fake.calls
    assert ('POST','/v1/admin/devices/trash/empty','t'*32,{'confirm':'EMPTY_TRASH'}) in fake.calls
    assert ('PATCH','/v1/admin/policies','t'*32,{'remove_trash_after_30_days':False}) in fake.calls
    before=len(fake.calls)
    for operation,args in [('remove',{'recordId':'../unsafe'}),('accept',{'recordId':'wrong'}),
            ('set-retention',{'enabled':'true'}),('empty-trash',{'confirm':'EMPTY_TRASH'}),
            ('read-pending',{'recordId':'a'*32}),('wrong',{})]:
        with pytest.raises(IdentityNotebookError):invoke(operation,args)
    assert len(fake.calls)==before
    with pytest.raises(IdentityNotebookError) as error:
        execute_notebook('read-pending',{},target='local',local_client=fake)
    assert error.value.code=='ADMIN_SESSION_REQUIRED'


@pytest.mark.parametrize('target',['local','network'])
def test_browser_three_tabs_fixed_footer_scope_and_actions(target):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':1280,'height':900})
            page.on('dialog',lambda dialog:dialog.accept())
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.evaluate('''() => {
              window.calls=[];window.rows={pending:[{id:'a'.repeat(32),identity:'Worker A',role:'worker',requestedAt:'Nov 11, 2026'},
                {id:'b'.repeat(32),identity:'Worker B',role:'worker',requestedAt:'Nov 11, 2026'}],
                enrolled:[{id:'worker1',identity:'Worker One',role:'worker',createdAt:'Nov 11, 2026'}],
                trash:[{id:'worker2',identity:'Worker Two',role:'worker',trashedAt:'Nov 10, 2026'}]};
              window.policy=true;window.failNext='';
              window.fetch=async(url,opts={})=>{
                const request={url:String(url),method:opts.method||'GET',
                  body:opts.body?JSON.parse(opts.body):null};window.calls.push(request);
                if(url==='/api/applications/slh/identities')return {ok:true,json:async()=>({
                  schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'one',
                  identities:[{deviceId:'one',label:'One',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'}],explanation:''})};
                if(url==='/api/applications/slh/observe?view=overview')return {ok:true,json:async()=>({
                  ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',
                  overview:{organizationState:'running',organizationEndpoint:'0.0.0.0:8899'}})};
                if(url==='/api/applications/slh/observe?view=workers')return {ok:true,json:async()=>({
                  schema:'mcel.slh.first-slice-observation.v1',view:'workers',
                  workerSummary:{observationState:'AUTHENTICATED'}})};
                if(url==='/api/applications/slh/setup/action' && request.body.action==='logout')return {ok:true,json:async()=>({outcome:'SIGNED_OUT',message:'Logged out',deviceId:''})};
                if(url==='/api/applications/slh/setup/action')return {ok:true,json:async()=>({
                  outcome:'AUTHENTICATED',access_token:'t'.repeat(40),message:'Logged in',deviceId:'one'})};
                if(url==='/api/applications/slh/network/connect'){
                  if(request.body?.action==='disconnect')return {ok:true,json:async()=>({authenticationState:'DISCONNECTED'})};
                  return {ok:true,json:async()=>({pairingRequired:false,sessionTicket:'s'.repeat(40),endpoint:'192.168.1.8:8899',
                    tlsSha256:'a'.repeat(64),organizationState:'RUNNING',authenticationState:'AUTHENTICATED',role:'admin',
                    deviceId:'one',services:'AUTHENTICATED',observedAt:'2026-11-11',explanation:'Trusted'})};
                }
                if(url==='/api/applications/slh/identity-notebook'){
                  const op=request.body.operation;
                  if(op===window.failNext){window.failNext='';return {ok:false,json:async()=>({ok:false,error:{code:'INSPECTION_REQUIRED'}})};}
                  if(op==='read-pending')return {ok:true,json:async()=>({ok:true,result:window.rows.pending})};
                  if(op==='read-enrolled')return {ok:true,json:async()=>({ok:true,result:window.rows.enrolled})};
                  if(op==='read-trash')return {ok:true,json:async()=>({ok:true,result:window.rows.trash})};
                  if(op==='read-retention')return {ok:true,json:async()=>({ok:true,result:{enabled:window.policy}})};
                  if(op==='accept'){
                    const id=request.body.arguments.recordId;
                    const record=window.rows.pending.find(x=>x.id===id);
                    window.rows.pending=window.rows.pending.filter(x=>x.id!==id);
                    window.rows.enrolled.push({id:'worker3',identity:record.identity,role:'worker',createdAt:'Nov 11, 2026'});
                  }
                  if(op==='reject'){
                    window.rows.pending=window.rows.pending.filter(x=>x.id!==request.body.arguments.recordId);
                  }
                  if(op==='remove'){
                    window.rows.trash=window.rows.trash.filter(x=>x.id!==request.body.arguments.recordId);
                  }
                  if(op==='delete'){
                    const id=request.body.arguments.recordId;
                    const record=window.rows.enrolled.find(x=>x.id===id);
                    window.rows.enrolled=window.rows.enrolled.filter(x=>x.id!==id);
                    window.rows.trash.push({id,identity:record.identity,role:'worker',trashedAt:'Nov 11, 2026'});
                  }
                  if(op==='reactivate'){
                    const id=request.body.arguments.recordId;
                    const record=window.rows.trash.find(x=>x.id===id);
                    window.rows.trash=window.rows.trash.filter(x=>x.id!==id);
                    window.rows.enrolled.push({id,identity:record.identity,role:'worker',createdAt:'Nov 11, 2026'});
                  }
                  if(op==='set-retention'){
                    window.policy=request.body.arguments.enabled;
                    return {ok:true,json:async()=>({ok:true,result:{enabled:window.policy}})};
                  }
                  if(op==='empty-trash')window.rows.trash=[];
                  return {ok:true,json:async()=>({ok:true,result:{outcome:'COMPLETED',message:'Success'}})};
                }
                throw Error('Unexpected '+url);
              };
            }''')
            page.set_content(render_slh_page('overview' if target=='local' else 'network'))
            area=page.locator('[data-mcel-notebook]')
            area.wait_for()
            assert area.locator('[data-mcel-notebook-tab]').all_text_contents()==['Pending','Enrolled','Trash']
            assert area.locator('[data-mcel-notebook-status]').inner_text()=='Administrator login required'
            assert area.locator('[data-mcel-notebook-scroll] [data-record-key]').count()==0
            assert not page.evaluate("window.calls.some(c=>c.url.endsWith('/identity-notebook'))")
            if target=='local':
                page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='one'")
                page.evaluate("async () => await window.__mcelSlh.app.execute('intent:login-administrator')")
            else:
                page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='one'")
                page.locator('form[data-intent="intent:connect-network-slh"] [name="address"]').fill('192.168.1.8')
                page.locator('form[data-intent="intent:connect-network-slh"] button[type="submit"]').click()
            page.wait_for_function("() => window.calls.some(c=>c.body?.operation==='read-trash')")
            page.wait_for_function("() => document.querySelector('[data-mcel-notebook-scroll] [data-record-key]')")
            area.locator('[data-record-key="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"] [data-mcel-notebook-action="Accept"]').click()
            page.wait_for_function("() => window.calls.some(c=>c.body?.operation==='accept')")
            page.wait_for_function("() => document.querySelector('[data-record-key=\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\"] [data-mcel-notebook-action=\"Reject\"]:not(:disabled)')")
            area.locator('[data-record-key="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"] [data-mcel-notebook-action="Reject"]').click()
            page.wait_for_function("() => window.calls.some(c=>c.body?.operation==='reject')")
            page.wait_for_function("() => !document.querySelector('[data-record-key=\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\"]')")
            area.locator('[data-mcel-notebook-tab="enrolled"]').click()
            page.wait_for_function("() => document.querySelector('[data-mcel-notebook-scroll] [data-record-key=\"worker1\"]')")
            area.locator('[data-record-key="worker1"] [data-mcel-notebook-action="Delete"]').click()
            page.wait_for_function("() => window.calls.some(c=>c.body?.operation==='delete')")
            area.locator('[data-mcel-notebook-tab="trash"]').click()
            page.wait_for_function("() => document.querySelector('[data-mcel-notebook-scroll] [data-record-key=\"worker1\"]')")
            area.locator('[data-record-key="worker1"] [data-mcel-notebook-action="Reactivate"]').click()
            page.wait_for_function("() => window.calls.some(c=>c.body?.operation==='reactivate')")
            area.locator('[data-mcel-notebook-tab="enrolled"]').click()
            page.wait_for_function("() => document.querySelector('[data-record-key=\"worker1\"] [data-mcel-notebook-action=\"Delete\"]:not(:disabled)')")
            area.locator('[data-record-key="worker1"] [data-mcel-notebook-action="Delete"]').click()
            page.wait_for_function("() => window.calls.filter(c=>c.body?.operation==='delete').length>=2")
            area.locator('[data-mcel-notebook-tab="trash"]').click()
            page.wait_for_function("() => document.querySelector('[data-record-key=\"worker1\"] [data-mcel-notebook-action=\"Remove\"]:not(:disabled)')")
            area.locator('[data-record-key="worker1"] [data-mcel-notebook-action="Remove"]').click()
            page.wait_for_function("() => window.calls.some(c=>c.body?.operation==='remove')")
            page.wait_for_function("() => !document.querySelector('[data-record-key=\"worker1\"]')")
            assert area.locator('[data-mcel-notebook-footer]').is_visible()
            assert area.locator('[data-mcel-notebook-retention]').is_checked()
            assert page.evaluate("() => !document.querySelector('[data-mcel-notebook-scroll]').contains(document.querySelector('[data-mcel-notebook-footer]'))")
            area.locator('[data-mcel-notebook-retention]').uncheck()
            page.wait_for_function("() => window.calls.some(c=>c.body?.operation==='set-retention'&&c.body.arguments.enabled===false)")
            page.wait_for_function("() => document.querySelector('[data-mcel-notebook-retention]').checked===false")
            area.locator('[data-mcel-notebook-empty]').click()
            page.wait_for_function("() => window.calls.some(c=>c.body?.operation==='empty-trash')")
            page.wait_for_function("() => document.querySelector('[data-mcel-notebook-empty]').disabled")
            for call in page.evaluate("window.calls.filter(c=>c.url.endsWith('/identity-notebook'))"):
                assert call['body']['target']==target
                assert call['body']['sessionTicket']==('s'*40 if target=='network' else '')
            # A refused authority operation must leave the row in place and
            # appear in the notebook status and existing right-side receipt rail.
            area.locator('[data-mcel-notebook-tab="enrolled"]').click()
            page.wait_for_function("() => document.querySelector('[data-record-key=\"worker3\"]')")
            page.evaluate("window.failNext='delete'")
            area.locator('[data-record-key="worker3"] [data-mcel-notebook-action="Delete"]').click()
            page.wait_for_function("() => document.querySelector('[data-mcel-notebook-status]').textContent.includes('Operation refused')")
            assert area.locator('[data-record-key="worker3"]').count()==1
            assert 'INSPECTION_REQUIRED' in page.locator('[data-mcel-purpose="status"]').inner_text()
            # Leaving the target revokes visibility without triggering more reads.
            before=page.evaluate("window.calls.filter(c=>c.url.endsWith('/identity-notebook')).length")
            if target=='local':
                page.evaluate("async () => await window.__mcelSlh.app.execute('intent:logout-administrator')")
            else:
                page.locator('button[data-intent="intent:disconnect-network-slh"]').click()
            page.wait_for_function("() => document.querySelector('[data-mcel-notebook-status]').textContent.includes('Administrator login required')")
            assert area.locator('[data-record-key]').count()==0
            assert page.evaluate("window.calls.filter(c=>c.url.endsWith('/identity-notebook')).length") == before
            assert errors==[],errors
        finally:browser.close()


def test_connected_remote_non_admin_cannot_read_or_mutate_organization_notebook():
    from playwright.sync_api import sync_playwright
    script=(ROOT/'main_computer/web/applications/scripts/mcel-semantic-slh-network-ports.js').read_text()
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page()
            page.goto('about:blank')
            page.add_script_tag(content=script)
            result=page.evaluate('''async () => {
              const calls=[];
              const adapter=window.McelSemanticSlhNetworkPorts.create({appId:'slh',fetcher:async(url,opts)=>{
                calls.push(String(url));return {ok:true,json:async()=>({pairingRequired:false,sessionTicket:'s'.repeat(40),
                  endpoint:'192.168.1.8:8899',tlsSha256:'a'.repeat(64),organizationState:'RUNNING',
                  authenticationState:'AUTHENTICATED',role:'requester',deviceId:'one',services:'AUTHENTICATED',
                  observedAt:'2026-11-11',explanation:'Authenticated, not administrator'})};
              }});
              let authorized=null;adapter.onConnectionChanged(v=>authorized=v);
              await adapter.invoke({appId:'slh',intentId:'intent:connect-network-slh',
                capabilityId:'capability:network-slh-connection',effectId:'effect:connect-network-slh.request',
                operation:'connect',runtimeMethod:'connectNetworkSlh',risk:'consequential',
                arguments:{address:'192.168.1.8',port:8899,adminDeviceId:'one'}});
              const read={appId:'slh',intentId:'intent:read-organization-trash',
                capabilityId:'capability:organization-identity-observer',
                effectId:'effect:read-organization-trash.request',operation:'read-trash',
                runtimeMethod:'observeOrganizationTrash',risk:'external-read',arguments:{}};
              let refused=false;
              try{await adapter.invoke(read);}catch(e){refused=String(e).includes('ADMIN_SESSION_REQUIRED');}
              return {authorized, permitted:await adapter.authorize(read),refused,calls};
            }''')
            assert result=={'authorized':False,'permitted':False,'refused':True,
                            'calls':['/api/applications/slh/network/connect']}
        finally:browser.close()
