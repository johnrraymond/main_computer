"""SEH management: executable semantic oracle, browser transport and authority tests.

Browser calls use fake services; the ORG-SLH/SEH transport boundary is tested
independently in test_seh_remote_admin_backend.py. No production state changes.
"""
from __future__ import annotations

import io
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from main_computer.mcel_slh_application import compile_slh_application, render_slh_page, ROUTES
from main_computer.mcel_slh_target_navigation import MANAGEMENT_ROUTES
from main_computer.mcel_slh_seh_lifecycle import execute_seh_lifecycle, SEHLifecycleError
from main_computer.mcel_slh_identity_notebook import execute_notebook
from main_computer.mcel_slh_remote_seh_route import handle_remote_seh_connect
from main_computer.mcel_slh_seh_lifecycle_route import handle_seh_lifecycle

ROOT=Path(__file__).resolve().parents[1]
CONTRACT=json.loads((ROOT/'tests/fixtures/mcel_oracles/seh-management-oracle.v1.json').read_text())


def test_compiled_oracle_semantics_navigation_and_authority():
    ir,composition=compile_slh_application()
    assert CONTRACT['schema']=='mcel.slh.seh-management-oracle.v1'
    assert ir['application']['appId']==CONTRACT['semantic_application']
    assert not ir['layouts'] and not ir['surfaces'][0].get('htmlBindings')
    assert {v:k for k,v in MANAGEMENT_ROUTES.items()} == {
        CONTRACT['navigation']['This Computer']:'local',
        CONTRACT['navigation']['Network SLH']:'network',
        CONTRACT['navigation']['Remote SEH']:'remote-seh'}
    assert ROUTES['/applications/slh/remote-seh']=='remote-seh'
    nodes={n['id']:n for n in ir['surfaces'][0]['nodes']}
    identity=nodes[CONTRACT['remote_seh']['reuse_notebook']]
    assert identity['nodeKind']=='record-notebook'
    for label,target in [('local','local'),('network','network')]:
        notebook=nodes[f'surface-node:slh.{label}-seh-lifecycle']
        assert notebook['nodeKind']==CONTRACT['seh_lifecycle']['node_kind']
        assert notebook['visibleWhen']['equals']==target
        assert notebook['title']==CONTRACT['seh_lifecycle']['title']
        assert [t['id'] for t in notebook['tabs']]==CONTRACT['seh_lifecycle']['tabs']
        assert notebook['tabs'][2]['refresh']['ref']==CONTRACT['seh_lifecycle']['read']
        assert {x['intent']['ref'] for x in notebook['tabs'][2]['actions'].values()}==set(CONTRACT['seh_lifecycle']['connection_actions'])
        assert not notebook['tabs'][0]['actions']
        assert {form['intent']['ref'] for form in notebook['tabs'][0]['forms']} == set(CONTRACT['seh_lifecycle']['controller_forms'])
        assert {x['intent']['ref'] for x in notebook['tabs'][1]['actions'].values()} == set(CONTRACT['seh_lifecycle']['deployment_actions'])
        assert notebook['tabs'][1]['state']['ref'] == 'state:seh-deployment-status'
        assert notebook['tabs'][0]['forms'][0]['secretFields'] == ['apiToken']
    assert CONTRACT['remote_seh']['slh_route_auto_discovered'] is True
    assert CONTRACT['remote_seh']['user_supplied_slh_id_forbidden'] is True
    assert nodes['surface-node:slh.connect-remote-seh']['nodeKind']=='form'
    assert nodes['surface-node:slh.connect-remote-seh']['intent']['ref']==CONTRACT['remote_seh']['connect']
    assert set(nodes['surface-node:slh.connect-remote-seh']['fields'])==set(CONTRACT['remote_seh']['connection_fields'])
    intents={i['id'] for i in ir['intents']}
    assert CONTRACT['remote_seh']['disconnect'] in intents
    recipes=[r['recipe'] for r in composition['regions']]
    assert recipes.index('task')<recipes.index('collection')
    assert recipes.index('commands')<recipes.index('collection')
    for view,target in [('overview','local'),('network','network'),('remote-seh','remote-seh')]:
        html=render_slh_page(view)
        assert f'data-mcel-slh-target="{target}"' in html
        assert all(f'href="{route}"' in html for route in MANAGEMENT_ROUTES.values())
        assert '<div id="mcel-workbench"' in html


class FakeLocal:
    def __init__(self):self.calls=[]
    def request(self,method,path,token,body):
        self.calls.append((method,path,token,body))
        assert token=='t'*32
        return {'schema':'main-computer.slh-seh-connection.v1','configuration_owner':'org-slh',
                'relay_role':'opaque-passthrough','connection_enabled':True,
                'resident_gateway_ready':True,'connected':True,'phase':'CONNECTED','last_error_class':None}

class FakeNetwork:
    def __init__(self):self.calls=[]
    def seh_connection_request(self,ticket,*,action=None):
        self.calls.append((ticket,action))
        assert ticket=='n'*40
        return FakeLocal().request('GET','/v1/org/admin/seh/connection','t'*32,None)


def test_lifecycle_authority_scope_preserves_transport_and_denials():
    local,network=FakeLocal(),FakeNetwork()
    assert execute_seh_lifecycle('status',target='local',local_token='t'*32,local_client=local)['connected']
    assert execute_seh_lifecycle('stop',target='local',local_token='t'*32,local_client=local)['phase']=='CONNECTED'
    assert execute_seh_lifecycle('restart',target='network',remote_ticket='n'*40,network_client=network)['gatewayReady']
    assert local.calls[0][:2]==('GET','/v1/org/admin/seh/connection')
    assert local.calls[1][3]=={'action':'stop'}
    assert network.calls==[('n'*40,'restart')]
    count=len(local.calls)
    for target,operation in [('remote-seh','stop'),('network','deploy'),('local','redeploy'),('seh','status')]:
        with pytest.raises(SEHLifecycleError):
            execute_seh_lifecycle(operation,target=target,local_token='t'*32,local_client=local)
    with pytest.raises(SEHLifecycleError):
        execute_seh_lifecycle('stop',target='local',local_token='t'*32,remote_ticket='n'*40,local_client=local)
    assert len(local.calls)==count
    class Incorrect(FakeLocal):
        def request(self,*args):return {'schema':'wrong'}
    with pytest.raises(SEHLifecycleError,match='SEH_LIFECYCLE_RESPONSE_INVALID'):
        execute_seh_lifecycle('status',target='local',local_token='t'*32,local_client=Incorrect())


def test_remote_seh_identity_notebook_uses_original_operations_and_transport():
    class FakeSEH:
        def __init__(self):self.calls=[]
        def admin_request(self,ticket,method,path,body=None):
            self.calls.append((ticket,method,path,body))
            return {'devices':[]}
    client=FakeSEH()
    assert execute_notebook('read-enrolled',{},target='remote-seh',remote_ticket='n'*40,seh_client=client)==[]
    assert client.calls==[('n'*40,'GET','/v1/admin/devices',None)]
    with pytest.raises(Exception):
        execute_notebook('unknown-operation',{},target='remote-seh',remote_ticket='n'*40,seh_client=client)
    assert len(client.calls)==1


def _request_handler(*, operation_header, body=None, target=None):
    body=body or {}
    data=json.dumps(body).encode()
    headers={'Host':'127.0.0.1:8866','Origin':'http://127.0.0.1:8866',
             'Sec-Fetch-Site':'same-origin','Content-Type':'application/json',
             'Content-Length':str(len(data)),'X-MCEL-SLH-Operation':operation_header}
    return SimpleNamespace(client_address=('127.0.0.1',33333),server=SimpleNamespace(server_address=('127.0.0.1',8866)),
                           headers=headers,rfile=io.BytesIO(data),sent=[],
                           _send_json=lambda value,status=200:None)


def test_remote_seh_and_lifecycle_routes_refuse_cross_origin(monkeypatch):
    for handler_function, operation, body in [
        (handle_remote_seh_connect,'remote-seh-connect',{'action':'connect','arguments':{}}),
        (handle_seh_lifecycle,'seh-lifecycle',{'target':'remote-seh','operation':'stop','sessionTicket':''})]:
        handler=_request_handler(operation_header=operation,body=body)
        handler.headers['Origin']='https://attacker.example'
        handler._send_json=lambda value,status=200:handler.sent.append((status,value))
        handler_function(handler)
        assert handler.sent[0][0]==403
        assert handler.sent[0][1]['error']['code']=='LOCAL_ORIGIN_REQUIRED'


def _browser_mock():
    return '''() => {
      window.calls=[]; window.pending=[{id:'a'.repeat(32),identity:'Enrolling worker',role:'worker',requestedAt:'Nov 12, 2026'}];
      window.sehStatus={phase:'CONNECTED',connectionEnabled:true,connected:true,gatewayReady:true,
        configurationOwner:'ORG-SLH',relayRole:'Opaque passthrough',lastErrorClass:''};
      window.fetch=async(url,options={})=>{
        const body=options.body?JSON.parse(options.body):{};
        window.calls.push({url:String(url),body,headers:options.headers});
        const reply=(result,ok=true)=>({ok,json:async()=>result});
        if(url==='/api/applications/slh/identities')return reply({schema:'mcel.slh.identities.v4b',
          state:'AVAILABLE',defaultDeviceId:'one',identities:[{deviceId:'one',label:'One',
          fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'}],explanation:''});
        if(url==='/api/applications/slh/observe?view=overview')return reply({schema:'mcel.slh.first-slice-observation.v1',
          view:'overview',overview:{organizationState:'running',organizationEndpoint:'0.0.0.0:8899'}});
        if(url==='/api/applications/slh/observe?view=workers')return reply({schema:'mcel.slh.first-slice-observation.v1',
          view:'workers',workerSummary:{observationState:'AUTHENTICATED'}});
        if(url==='/api/applications/slh/setup/action')return reply({outcome:'AUTHENTICATED',
          access_token:'t'.repeat(40),message:'Signed in',deviceId:'one'});
        if(url==='/api/applications/slh/network/connect' || url==='/api/applications/slh/remote-seh/connect'){
          if(body.action==='disconnect')return reply({authenticationState:'DISCONNECTED',explanation:'Logout'});
          return reply({pairingRequired:false,sessionTicket:'n'.repeat(40),endpoint:'seh:wss://relay.example/org1',
            tlsSha256:'a'.repeat(64),organizationState:'RUNNING',authenticationState:'AUTHENTICATED',role:'admin',
            deviceId:'one',services:'IDENTITY_ADMINISTRATION_ONLY',observedAt:'2026-11-12',explanation:'Pinned'});
        }
        if(url==='/api/applications/slh/seh/deployment'){
          if(body.operation==='inspect')return reply({ok:true,result:{generation:33,
            selectedController:'coolify-a',applicationUuid:'existing-uuid',
            deploymentStatus:'APPLICATION_REGISTERED_NOT_RUNTIME_VERIFIED',
            controllers:[{identity:'coolify-a'},{identity:'coolify-c'}]}});
          if(body.operation.startsWith('preview-'))return reply({ok:true,result:{outcome:'PLAN_ONLY',message:'Read-only plan',applicationUuid:'existing-uuid'}});
          return reply({ok:true,result:{outcome:'REPLACEMENT_QUEUED_NOT_VERIFIED',message:'Queued, not verified',applicationUuid:'new-uuid'}});
        }
        if(url==='/api/applications/slh/seh/lifecycle'){
          if(body.operation==='stop'){window.sehStatus.connected=false;window.sehStatus.phase='STOPPED';}
          return reply({ok:true,result:{...window.sehStatus}});
        }
        if(url==='/api/applications/slh/identity-notebook'){
          if(body.operation==='read-pending')return reply({ok:true,result:window.pending});
          if(body.operation==='read-enrolled'||body.operation==='read-trash')return reply({ok:true,result:[]});
          if(body.operation==='read-retention')return reply({ok:true,result:{enabled:true}});
          if(body.operation==='accept')window.pending=[];
          return reply({ok:true,result:{outcome:'COMPLETED',message:'Done'}});
        }
        throw Error('Unexpected request: '+url);
      };
    }'''


@pytest.mark.parametrize('target',['overview','network','remote-seh'])
def test_browser_navigation_notebooks_and_target_specific_authority(target):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':1280,'height':900})
            errors=[]
            page.on('pageerror',lambda err:errors.append(str(err)))
            page.on('dialog',lambda dialog:dialog.accept())
            page.evaluate(_browser_mock())
            page.set_content(render_slh_page(target))
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='one'")
            assert page.locator('[data-mcel-notebook]').count()==1
            assert page.locator('[data-mcel-notebook-tab]').all_text_contents()==['Pending','Enrolled','Trash']
            if target=='remote-seh':
                assert page.get_by_role('tab',name='Remote SEH').get_attribute('aria-selected')=='true'
                assert page.locator('[data-mcel-operation-notebook]:visible').count()==0
                form=page.locator('form[data-intent="intent:connect-remote-seh"]')
                assert form.is_visible()
                assert form.locator('[name="slhId"]').count()==0
                form.locator('[name="sehUrl"]').fill('wss://relay.example')
                form.locator('[type="submit"]').click()
                page.wait_for_function("() => window.calls.some(c=>c.url.endsWith('/remote-seh/connect'))")
                assert page.evaluate("window.calls.filter(c=>c.url.endsWith('/remote-seh/connect')).every(c=>!Object.hasOwn(c.body.arguments,'slhId'))")
                page.wait_for_function("() => window.calls.some(c=>c.body?.target==='remote-seh' && c.body?.operation==='read-pending')")
                assert page.locator('[data-mcel-operation-action]:visible').count()==0
                assert not page.locator('form[data-intent="intent:connect-network-slh"]').is_visible()
                assert not page.locator('button[data-intent="intent:toggle-organization-enablement"]').is_visible()
                row=page.locator('[data-record-key="'+'a'*32+'"]')
                row.locator('[data-mcel-notebook-action="Accept"]').click()
                page.wait_for_function("() => window.calls.some(c=>c.body?.target==='remote-seh' && c.body?.operation==='accept')")
            else:
                assert page.locator('[data-mcel-operation-notebook]:visible').count()==1
                if target=='overview':
                    page.evaluate("async () => await window.__mcelSlh.app.execute('intent:login-administrator')")
                    session_target='local'
                else:
                    form=page.locator('form[data-intent="intent:connect-network-slh"]')
                    form.locator('[name="address"]').fill('192.168.1.8')
                    form.locator('[name="port"]').fill('8899')
                    form.locator('[type="submit"]').click()
                    session_target='network'
                page.wait_for_function("() => window.calls.some(c=>c.url.endsWith('/seh/deployment'))")
                assert page.locator('[data-mcel-operation-notebook]:visible [data-mcel-operation-tab]').all_text_contents()==["Configuration","Deployment","Connection","Status","Diagnostics"]
                page.locator('[data-mcel-operation-notebook]:visible [data-mcel-operation-tab="connection"]').click()
                page.wait_for_function("() => window.calls.some(c=>c.url.endsWith('/seh/lifecycle'))")
                page.wait_for_function("""() => document.querySelector('[data-mcel-operation-notebook]:not([hidden]) [data-mcel-operation-action="Stop"]:not(:disabled)')""")
                page.locator('[data-mcel-operation-notebook]:visible [data-mcel-operation-action="Stop"]').click()
                page.wait_for_function("() => window.calls.some(c=>c.url.endsWith('/seh/lifecycle') && c.body.operation==='stop')")
                assert page.evaluate("window.calls.some(c=>c.url.endsWith('/seh/lifecycle') && c.body.target==='"+session_target+"')")
                assert not page.evaluate("window.calls.some(c=>c.body?.target==='remote-seh')")
            assert errors==[],errors
        finally:
            browser.close()


def test_network_tickets_cannot_cross_direct_and_seh_management_paths():
    from main_computer.mcel_slh_network_client import NetworkSlhClient, NetworkConnectionError, _SESSIONS, _SESSION_LOCK
    from main_computer.mcel_seh_remote_client import RemoteSEHClient
    direct_id='direct-'+'d'*40
    seh_id='relay-'+'s'*40
    with _SESSION_LOCK:
        _SESSIONS[direct_id]={'transport':None}
        _SESSIONS[seh_id]={'transport':{'kind':'seh'}}
    try:
        direct=NetworkSlhClient()
        seh=RemoteSEHClient()
        for client,ticket in ((direct,seh_id),(seh,direct_id)):
            with pytest.raises(NetworkConnectionError,match='NETWORK_SESSION_TARGET_MISMATCH'):
                client.admin_request(ticket,'GET','/v1/admin/devices')
            with pytest.raises(NetworkConnectionError,match='NETWORK_SESSION_TARGET_MISMATCH'):
                client.disconnect(ticket)
        with pytest.raises(NetworkConnectionError,match='SEH_OPERATION_NOT_ALLOWED'):
            seh.seh_connection_request(seh_id,action='stop')
        with pytest.raises(NetworkConnectionError,match='NETWORK_SESSION_TARGET_MISMATCH'):
            direct.seh_connection_request(seh_id,action='restart')
    finally:
        with _SESSION_LOCK:
            _SESSIONS.pop(direct_id,None)
            _SESSIONS.pop(seh_id,None)


def test_lifecycle_route_refuses_remote_seh_even_with_local_admin_bearer(monkeypatch):
    handler=_request_handler(operation_header='seh-lifecycle',
        body={'target':'remote-seh','operation':'stop','sessionTicket':''})
    handler.headers['Authorization']='Bearer '+'t'*32
    handler._send_json=lambda value,status=200:handler.sent.append((status,value))
    handle_seh_lifecycle(handler)
    assert handler.sent==[(403,{'error':{'code':'SEH_LIFECYCLE_TARGET_REFUSED'}})]


def test_remote_seh_connect_route_allows_only_exact_session_operations(monkeypatch):
    class Client:
        def connect(self,*args,**kwargs):
            return {'pairingRequired':True,'tlsSha256':'a'*64}
        def disconnect(self,ticket):return {'authenticationState':'DISCONNECTED'}
    monkeypatch.setattr('main_computer.mcel_seh_remote_client.RemoteSEHClient',Client)
    valid=_request_handler(operation_header='remote-seh-connect',body={
        'action':'connect','arguments':{'sehUrl':'wss://relay.example','adminDeviceId':'admin1'}})
    valid._send_json=lambda value,status=200:valid.sent.append((status,value))
    handle_remote_seh_connect(valid)
    assert valid.sent[0][0]==200 and valid.sent[0][1]['pairingRequired'] is True
    unwanted_field=_request_handler(operation_header='remote-seh-connect',body={
        'action':'connect','arguments':{'sehUrl':'wss://relay.example','slhId':'org1','adminDeviceId':'admin1'}})
    unwanted_field._send_json=lambda value,status=200:unwanted_field.sent.append((status,value))
    handle_remote_seh_connect(unwanted_field)
    assert unwanted_field.sent==[(400,{'error':{'code':'INVALID_REMOTE_SEH_REQUEST'}})]
    forbidden=_request_handler(operation_header='remote-seh-connect',body={
        'action':'restart','arguments':{'sessionTicket':'n'*40}})
    forbidden._send_json=lambda value,status=200:forbidden.sent.append((status,value))
    handle_remote_seh_connect(forbidden)
    assert forbidden.sent==[(400,{'error':{'code':'INVALID_REMOTE_SEH_REQUEST'}})]


def test_seh_notebook_mount_is_fail_closed_and_reports_refused_lifecycle_action():
    """A valid generated operation notebook must mount; a refused action is not success."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':1040,'height':900})
            errors=[]
            page.on('pageerror',lambda err:errors.append(str(err)))
            page.on('dialog',lambda dialog:dialog.accept())
            page.evaluate(_browser_mock())
            page.evaluate('''() => {
              const original=window.fetch;
              window.fetch=async(url,options={})=>{
                if(String(url)==='/api/applications/slh/seh/lifecycle' &&
                   JSON.parse(options.body||'{}').operation==='stop')
                  return {ok:false,json:async()=>({error:{code:'OPERATION_REFUSED'}})};
                return original(url,options);
              };
            }''')
            page.set_content(render_slh_page('overview'))
            page.wait_for_function('() => !!window.__mcelSlh?.app')
            assert 'SLH semantic mount refused' not in page.locator('body').inner_text()
            assert page.locator('form[data-intent="intent:configure-and-enable-organization"] button[type="reset"]').count()==0
            assert page.locator('[data-mcel-operation-notebook]:visible').count()==1
            page.locator('[data-mcel-operation-tab="connection"]:visible').click()
            action=page.locator('[data-mcel-operation-action="Stop"]:visible')
            assert action.is_disabled()  # no administrator session yet
            page.evaluate("async () => await window.__mcelSlh.app.execute('intent:login-administrator')")
            page.wait_for_function("() => !!document.querySelector('[data-mcel-operation-action=Stop]:not(:disabled)')")
            action.click()
            page.wait_for_function("() => document.querySelector('[data-mcel-operation-status]')?.textContent?.includes('Operation refused')")
            assert page.locator('[data-mcel-operation-field="phase"]:visible').inner_text()=='CONNECTED'
            assert errors==[],errors
        finally:
            browser.close()
