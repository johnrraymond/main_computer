"""Worker enrollment capability, browser binding, transport and live-lane guards."""
import io
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer import mcel_worker_enrollment as bridge
from main_computer.mcel_worker_application import compile_worker_application, render_worker_page
from tools.mcel_worker_identity_live_acceptance import verify_enrollment_entry
from tools.mcel_worker_approval_live import permitted, ENROLLMENT, NOTEBOOK
from tools.mcel_functional_auth import ORIGIN

ROOT=Path(__file__).resolve().parents[1]


def test_worker_enrollment_capability_is_request_only():
    ir,_=compile_worker_application()
    assert verify_enrollment_entry(ir,{'checks':{}})=={'intent:request-enrollment'}
    cap=next(c for c in ir['capabilities'] if c['id']=='capability:worker-enrollment')
    assert {o['name'] for o in cap['operations']}=={'request','status'}
    intent=next(i for i in ir['intents'] if i['id']=='intent:request-enrollment')
    assert intent['confirmationRequired'] is True
    assert {p['sourceName'] for p in intent['input']}=={'transport','endpoint'}
    assert intent['requestArguments']==[{'name':'deviceId','state':{'ref':'state:selected-local-identity'}}]


@pytest.mark.parametrize('transport,endpoint',[('slh','https://127.0.0.1:8897'),('seh','ws://example.org'),('slh','https://example.org:8899'),('local','https://elsewhere'),('other','')])
def test_unadmitted_targets_refused_before_network(transport,endpoint,monkeypatch):
    monkeypatch.setattr(bridge,'_connection',lambda *a,**kw:pytest.fail('untrusted connection reached'))
    with pytest.raises(bridge.NetworkConnectionError):bridge.target_connection(transport,endpoint)


def test_remote_transport_requires_existing_pairing(monkeypatch):
    monkeypatch.setattr(bridge,'_read_trust',lambda _: {})
    monkeypatch.setattr(bridge,'_connection',lambda *a,**kw:pytest.fail('untrusted connection reached'))
    for kind,endpoint in [('slh','https://192.168.1.20:8899'),('seh','wss://relay.example.org')]:
        with pytest.raises(bridge.NetworkConnectionError,match='SLH_PAIRING_REQUIRED'):
            bridge.target_connection(kind,endpoint)


def test_route_origin_and_request_only(monkeypatch):
    calls=[]
    def handler(origin,action):
        raw=json.dumps({'action':action,'arguments':{'deviceId':'worker-test','transport':'local','endpoint':''}}).encode()
        return SimpleNamespace(headers={'Host':'127.0.0.1:8765','Origin':origin,'Content-Type':'application/json','Content-Length':str(len(raw))},
            client_address=('127.0.0.1',2000),server=SimpleNamespace(server_address=('127.0.0.1',8765)),
            rfile=io.BytesIO(raw),_send_json=lambda obj,status=200:calls.append((obj,int(status))))
    # The real executor refuses approval before any LMS or network call.
    monkeypatch.setattr(bridge,'SlhFirstRunController',lambda: SimpleNamespace(_identity=lambda _:pytest.fail('unadmitted LMS access')))
    bridge.handle_worker_enrollment(handler('https://other.example','request'))
    assert calls[-1][1]==403
    bridge.handle_worker_enrollment(handler('http://127.0.0.1:8765','approve'))
    assert calls[-1][1]==400


def test_live_guard_admits_only_disposable_identity_and_record():
    device='worker-e2e-test'; rid='a'*32
    args=dict(device=device,label='fixture',admin='owner-device',requests={rid})
    def permit(path,body):return permitted(ORIGIN+path,'POST',json.dumps(body),**args)
    body={'action':'request','arguments':{'deviceId':device,'transport':'local','endpoint':''}}
    assert permit(ENROLLMENT,body)
    assert not permit(ENROLLMENT,{**body,'action':'approve'})
    assert not permit(ENROLLMENT,{**body,'arguments':{**body['arguments'],'deviceId':'other'}})
    assert not permit(ENROLLMENT,{**body,'arguments':{**body['arguments'],'transport':'seh'}})
    nb={'target':'local','operation':'accept','arguments':{'recordId':rid},'sessionTicket':''}
    assert permit(NOTEBOOK,nb)
    assert not permit(NOTEBOOK,{**nb,'arguments':{'recordId':'b'*32}})
    assert not permit(NOTEBOOK,{**nb,'operation':'delete','arguments':{'recordId':'owner-device'}})
    assert permit(NOTEBOOK,{**nb,'operation':'delete','arguments':{'recordId':device}})
    assert not permit(NOTEBOOK,{**nb,'operation':'empty-trash','arguments':{}})
    assert not permit('/api/local/authenticator/identities',{'device_id':device})
    assert not permit('/api/applications/worker/settings',{})


def test_worker_enrollment_port():
    node=shutil.which('node')
    if not node:pytest.skip('Node unavailable')
    js=r'''
const assert=require('node:assert/strict'),fs=require('fs'),vm=require('vm');
vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));
let writes=0, forge=false;
const device='worker-test',rid='a'.repeat(32);
const ok=v=>({ok:true,status:200,json:async()=>v});
const port=McelSemanticWorkerPorts.create({appId:'worker',fetcher:async (url,opt)=>{
 if(url.endsWith('/identities'))return ok({schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:device,explanation:'',identities:[{deviceId:device,label:'Worker',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'}]});
 if(url.endsWith('/enrollment')){writes++;let b=JSON.parse(opt.body);assert.deepEqual(b.arguments,{deviceId:device,transport:'local',endpoint:''});return ok({ok:true,enrollment:{...b.arguments,status:forge?'AUTHENTICATED':b.action==='request'?'PENDING_ADMIN':'AUTHENTICATED',requestId:b.action==='request'?rid:'',role:forge?'admin':b.action==='status'?'worker':''}})}
 throw Error('unexpected endpoint');
}});
const req=(name,operation,runtimeMethod,args,cap='worker-enrollment',risk='consequential')=>({appId:'worker',intentId:'intent:'+name,effectId:'effect:'+name+'.request',capabilityId:'capability:'+cap,operation,runtimeMethod,risk,arguments:args});
(async()=>{
 const args={deviceId:device,transport:'local',endpoint:''};
 const request=req('request-enrollment','request','requestWorkerEnrollment',args);
 await assert.rejects(()=>port.invoke(request),/identity-not-observed/);
 await port.invoke(req('refresh-identities','identities','observeWorkerIdentities',{},'worker-observer','external-read'));
 const pending=await port.invoke(request);assert.equal(pending.status,'PENDING_ADMIN');assert.equal(writes,1);
 const status=req('observe-enrollment','status','observeWorkerEnrollment',args);
 assert.equal((await port.invoke(status)).role,'worker');
 await assert.rejects(()=>port.invoke({...request,arguments:{...args,deviceId:'unknown'}}),/identity-not-observed/);
 assert(!port.authorize({...request,arguments:{...args,role:'admin'}}));
 forge=true;await assert.rejects(()=>port.invoke(status),/enrollment-receipt-invalid/);
})().catch(e=>{console.error(e);process.exit(1)});
'''
    run=subprocess.run([node,'-e',js,str(ROOT/'main_computer/web/applications/scripts/mcel-semantic-worker-ports.js')],capture_output=True,text=True,timeout=20)
    assert run.returncode==0,run.stderr


def test_worker_enrollment_browser_controls():
    from tests.test_mcel_worker_hub_navigation import MOCK_LOCAL_BACKEND
    from tests.test_mcel_worker_identity_navigation import IDENTITIES_MOCK
    pw=pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as driver:
        chrome=shutil.which('chromium') or shutil.which('chromium-browser')
        try:browser=driver.chromium.launch(headless=True,**({'executable_path':chrome} if chrome else {}))
        except Exception as exc:pytest.skip('Chromium unavailable: '+type(exc).__name__)
        try:
            page=browser.new_page();page.set_default_timeout(8000)
            page.on('dialog',lambda d:d.accept())
            page.evaluate(MOCK_LOCAL_BACKEND);page.evaluate(IDENTITIES_MOCK)
            page.evaluate('''() => {const previous=window.fetch;window.enrollmentCalls=[];window.fetch=async(url,opts)=>{
              if(!String(url).endsWith('/worker/enrollment'))return previous(url,opts);
              const body=JSON.parse(opts.body);window.enrollmentCalls.push(body);
              return {ok:true,status:200,json:async()=>({ok:true,enrollment:{...body.arguments,status:body.action==='request'?'PENDING_ADMIN':'AUTHENTICATED',requestId:body.action==='request'?'a'.repeat(32):'',role:body.action==='request'?'':'worker'}})};
            };}''')
            page.set_content(render_worker_page())
            page.wait_for_function("() => window.__mcelWorker?.app.readState('state:local-identities')?.length===2")
            page.wait_for_function("() => window.__mcelWorker.app.readState('state:selected-network')==='testnet'")
            page.locator('[data-mcel-node="surface-node:worker.worker-local-identities"] button[data-select-key="device-002"]').click()
            assert page.evaluate('window.enrollmentCalls.length')==0
            page.locator('form[data-intent="intent:request-enrollment"] button[type="submit"]').click()
            page.wait_for_function("() => window.__mcelWorker.app.readState('state:worker-enrollment')?.status==='PENDING_ADMIN'")
            page.locator('form[data-intent="intent:observe-enrollment"] button[type="submit"]').click()
            page.wait_for_function("() => window.__mcelWorker.app.readState('state:worker-enrollment')?.status==='AUTHENTICATED'")
            calls=page.evaluate('window.enrollmentCalls')
            assert [x['action'] for x in calls]==['request','status']
            assert all(x['arguments']=={'deviceId':'device-002','transport':'local','endpoint':''} for x in calls)
        finally:browser.close()
