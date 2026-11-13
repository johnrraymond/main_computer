"""Network SLH connection: signed identity, explicit pin trust, and semantic placement."""
from __future__ import annotations

import hashlib
import json
import shutil

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import compile_slh_application, render_slh_page
from main_computer.mcel_slh_network_client import NetworkSlhClient, NetworkConnectionError


CERT = b'disposable-remote-org-leaf-certificate'
FINGERPRINT = hashlib.sha256(CERT).hexdigest()
CHALLENGE = {'version':1,'purpose':'slh.known_device.login', 'audience':'slh://'+'a'*64,
             'device_id':'dev-one','challenge_id':'a'*32,'nonce':'n'*32,
             'issued_at':1,'expires_at':61}


class PeerSocket:
    def __init__(self, cert=CERT): self.cert = cert
    def getpeercert(self, *, binary_form=False):
        assert binary_form
        return self.cert


class Reply:
    def __init__(self, status, data):
        self.status=status
        self._raw=json.dumps(data).encode()
    def getheader(self, name, default=''):
        return 'application/json; charset=utf-8' if name=='Content-Type' else default
    def read(self, size): return self._raw[:size]


class FakeOrganization:
    def __init__(self, cert=CERT, *, revoke=False):
        self.cert=cert
        self.revoke=revoke
        self.requests=[]
        self.connections=[]
    def create(self, host, port, *, timeout, context):
        parent=self
        class Conn:
            def __init__(self):self.sock=PeerSocket(parent.cert);self.current=None
            def connect(self): pass
            def close(self): pass
            def request(self, method, path, body=None, headers=None):
                self.current=(method,path,json.loads(body) if body is not None else None,headers)
                parent.requests.append(self.current)
            def getresponse(self):
                method,path,body,headers=self.current
                if path=='/v1/org/identity':return Reply(200,{'schema':'main-computer.slh-org-identity.v1','tls_sha256':hashlib.sha256(parent.cert).hexdigest()})
                if path=='/v1/org/status':return Reply(200,{'schema':'main-computer.slh-org-status.v1','operational':True})
                if path=='/v1/auth/challenge':
                    if parent.revoke:return Reply(403,{'error':{'code':'UNKNOWN_OR_REVOKED_DEVICE'}})
                    return Reply(200,{**CHALLENGE,'device_id':body['device_id']})
                if path=='/v1/auth/verify':return Reply(200,{'access_token':'some-opaque-token-of-sufficient-length-12345',
                    'device_id':body['device_id'],'role':'admin','expires_at':4102444800})
                if path=='/v1/auth/session':return Reply(200,{'active':True,'device_id':'dev-one','role':'admin'})
                if path=='/v1/org/admin/services':return Reply(200,{'worker_serving_enabled':True})
                if path=='/v1/auth/logout':return Reply(200,{'status':'LOGGED_OUT'})
                raise AssertionError(path)
        conn=Conn();self.connections.append(conn);return conn


def test_semantic_network_configuration_replaces_navigation_query():
    ir, composition=compile_slh_application()
    nav=[r for r in composition['regions'] if r['recipe']=='navigation']
    task=[r for r in composition['regions'] if r['recipe']=='task']
    assert len(nav)==1 and nav[0]['members'][0]['ref']=='surface-node:slh.available-local-identities'
    assert len(task)==3
    assert any(any(m['ref']=='surface-node:slh.connect-remote-seh' for m in r['members']) for r in task)
    assert any(any(m['ref']=='surface-node:slh.connect-network-slh' for m in r['members']) for r in task)
    node={n['id']:n for n in ir['surfaces'][0]['nodes']}['surface-node:slh.connect-network-slh']
    assert node['fields']=={'address':{'kind':'string'},'port':{'kind':'integer'}}
    assert 'tlsSha256' not in node['fields']
    assert 'Observation Query' not in render_slh_page('network')
    assert {'intent:connect-network-slh','intent:disconnect-network-slh'} <= {i['id'] for i in ir['intents']}


def test_first_connection_prompts_before_signing_then_pins_and_authenticates(tmp_path):
    peer=FakeOrganization()
    signed=[]
    client=NetworkSlhClient(trust_path=tmp_path/'trusted.json',connection_factory=peer.create,
        signer=lambda dev,challenge: signed.append((dev,challenge)) or 'signature')
    pending=client.connect('10.0.0.15',8899,'dev-one')
    assert pending=={'pairingRequired':True,'endpoint':'10.0.0.15:8899','tlsSha256':FINGERPRINT}
    assert not signed and not (tmp_path/'trusted.json').exists()
    result=client.connect('10.0.0.15',8899,'dev-one',confirm_pin=FINGERPRINT)
    assert result['authenticationState']=='AUTHENTICATED' and result['role']=='admin'
    assert result['services']=='WORKER_SERVICE_ENABLED'
    assert signed[0][0]=='dev-one' and signed[0][1]['purpose']=='slh.known_device.login'
    assert json.loads((tmp_path/'trusted.json').read_text())=={'10.0.0.15:8899':FINGERPRINT}
    assert not any('access_token' in k for k in result)
    assert client.disconnect(result['sessionTicket'])['authenticationState']=='DISCONNECTED'
    assert peer.requests[-1][1]=='/v1/auth/logout'
    # A known peer reconnects with no confirmation and still authenticates.
    assert client.connect('10.0.0.15',8899,'dev-one')['authenticationState']=='AUTHENTICATED'


def test_cert_change_fails_before_any_signing_or_api_request(tmp_path):
    peer=FakeOrganization()
    signatures=[]
    client=NetworkSlhClient(trust_path=tmp_path/'trusted.json',connection_factory=peer.create,
        signer=lambda dev,proof: signatures.append(proof) or 'sig')
    client.connect('192.168.5.4',8899,'dev-one',confirm_pin=FINGERPRINT)
    peer.requests.clear();signatures.clear()
    peer.cert=b'OTHER CERTIFICATE'
    with pytest.raises(NetworkConnectionError,match='NETWORK_TLS_IDENTITY_CHANGED'):
        client.connect('192.168.5.4',8899,'dev-one')
    assert not peer.requests and not signatures


def test_unrecognized_remote_identity_cannot_enroll_automatically(tmp_path):
    peer=FakeOrganization(revoke=True)
    client=NetworkSlhClient(trust_path=tmp_path/'trusted.json',connection_factory=peer.create,
        signer=lambda dev,proof: pytest.fail('unrecognized identity must not sign'))
    with pytest.raises(NetworkConnectionError,match='UNKNOWN_OR_REVOKED_DEVICE'):
        client.connect('10.0.0.15',8899,'dev-one',confirm_pin=FINGERPRINT)
    assert [p for _,p,*_ in peer.requests if p.startswith('/v1/auth')]==['/v1/auth/challenge']


def test_network_browser_connect_uses_current_selected_identity_and_inspector():
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':1280,'height':800})
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.evaluate('''()=>{
              const store={};Object.defineProperty(window,'sessionStorage',{configurable:true,value:{
                getItem:k=>store[k]||null,setItem:(k,v)=>store[k]=v,removeItem:k=>delete store[k]}});
              window.networkCalls=[];window.confirmations=[];
              window.confirm=message=>{window.confirmations.push(message);return true};
              window.fetch=async(url,options={})=>{
                if(url==='/api/applications/slh/identities')return {ok:true,json:async()=>({
                  schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'dev-one',
                  identities:[{deviceId:'dev-one',label:'Default',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'},
                    {deviceId:'dev-two',label:'Second',fingerprint:'b'.repeat(64),defaultStatus:'ADDITIONAL'}],
                  explanation:'ok'})};
                if(url==='/api/applications/slh/network/connect'){
                  const input=JSON.parse(options.body);window.networkCalls.push(input);
                  if(input.action==='disconnect')return {ok:true,json:async()=>({authenticationState:'DISCONNECTED',explanation:'Remote session revoked'})};
                  if(!input.arguments.confirmPin)return {ok:true,json:async()=>({
                    pairingRequired:true,endpoint:'10.0.0.15:8899',tlsSha256:'c'.repeat(64)})};
                  return {ok:true,json:async()=>({pairingRequired:false,sessionTicket:'ticket-'+'x'.repeat(40),
                    endpoint:'10.0.0.15:8899',tlsSha256:'c'.repeat(64),organizationState:'RUNNING',
                    authenticationState:'AUTHENTICATED',role:'admin',deviceId:input.arguments.adminDeviceId,
                    services:'WORKER_SERVICE_ENABLED',observedAt:'2026-11-11T00:00:00Z',explanation:'Connected'})};
                }
                throw Error('Unexpected request: '+url);
              };
            }''')
            page.set_content(render_slh_page('network'))
            page.wait_for_function("()=>window.__mcelSlh?.app.readState('state:local-identities')?.length===2")
            page.locator('[data-select-key="dev-two"]').click()
            form=page.locator('form[data-intent="intent:connect-network-slh"]')
            assert form.count()==1
            assert page.locator('form[data-intent="intent:inspect-network-slh"]').count()==0
            assert page.locator('input[name="tlsSha256"]').count()==0
            assert form.locator('input[name="address"]').count()==1
            form.locator('input[name="address"]').fill('10.0.0.15')
            form.locator('button[type="submit"]').click()
            page.wait_for_function("()=>window.__mcelSlh?.app.readState('state:network-session')?.authenticationState==='AUTHENTICATED'")
            requests=page.evaluate('window.networkCalls')
            assert len(requests)==2
            assert all(q['arguments']['adminDeviceId']=='dev-two' for q in requests)
            assert page.evaluate('window.confirmations').__len__()==1
            inspector=page.locator('[data-mcel-purpose="inspection"]')
            assert 'Network connection' in inspector.inner_text()
            assert 'AUTHENTICATED' in inspector.inner_text()
            assert 'Current service' not in inspector.inner_text()
            assert form.bounding_box()['x'] < inspector.bounding_box()['x']
            page.locator('[data-intent="intent:disconnect-network-slh"]').click()
            page.wait_for_function("()=>window.__mcelSlh.app.readState('state:network-session')?.authenticationState==='DISCONNECTED'")
            assert page.evaluate('window.networkCalls')[-1]['action']=='disconnect'
            assert not errors,errors
        finally:
            browser.close()


def test_network_connect_http_route_requires_loopback_same_origin_and_header(monkeypatch):
    import io
    from types import SimpleNamespace
    from main_computer.mcel_slh_network_route import handle_network_slh_connect
    called=[]
    class FakeClient:
        def connect(self,*args,**kwargs):
            called.append((args,kwargs))
            return {'pairingRequired':True,'endpoint':'10.0.0.15:8899','tlsSha256':FINGERPRINT}
    monkeypatch.setattr('main_computer.mcel_slh_network_client.NetworkSlhClient',FakeClient)
    payload=json.dumps({'action':'connect','arguments':{'address':'10.0.0.15','port':8899,
                                'adminDeviceId':'dev-one'}}).encode()
    class Handler:
        client_address=('127.0.0.1',44000)
        server=SimpleNamespace(server_address=('127.0.0.1',8765))
        headers={'Host':'127.0.0.1:8765','Origin':'http://127.0.0.1:8765',
                'Sec-Fetch-Site':'same-origin','Content-Type':'application/json',
                'X-MCEL-SLH-Operation':'network-connect','Content-Length':str(len(payload))}
        def __init__(self): self.sent=[];self.rfile=io.BytesIO(payload);self.headers=dict(self.headers)
        def _send_json(self,data,status=200):self.sent.append((status,data))
    bad=Handler();bad.headers['Origin']='https://evil.example'
    handle_network_slh_connect(bad)
    assert bad.sent[0][0]==403 and not called
    bad=Handler();bad.headers['X-MCEL-SLH-Operation']='other'
    handle_network_slh_connect(bad)
    assert bad.sent[0][0]==403 and not called
    good=Handler()
    handle_network_slh_connect(good)
    assert good.sent[0][0]==200 and good.sent[0][1]['pairingRequired'] is True
    assert called[0][0]==('10.0.0.15',8899,'dev-one')


def test_remote_login_does_not_allow_public_or_loopback_targets(tmp_path):
    peer=FakeOrganization()
    client=NetworkSlhClient(trust_path=tmp_path/'trusted.json',connection_factory=peer.create)
    for address in ('127.0.0.1','0.0.0.0','8.8.8.8','169.254.4.3','http://10.0.0.15'):
        with pytest.raises(NetworkConnectionError):
            client.connect(address,8899,'dev-one')
    assert not peer.requests
