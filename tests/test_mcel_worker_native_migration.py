"""Native Worker migration accepts only typed MCEL operations, not a renamed legacy DOM."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import shutil
import subprocess

import pytest

from main_computer.mcel_worker_application import (
    compile_worker_application, render_worker_page, WorkerSemanticApplicationError,
)
from main_computer.viewport_routes_energy import ViewportEnergyRoutesMixin

ROOT = Path(__file__).resolve().parents[1]


def test_worker_native_source_has_generated_layout_and_distinct_authorities():
    ir, plan = compile_worker_application(ROOT)
    assert ir["application"]["appId"] == "worker"
    assert ir["layouts"] == []
    surface, = ir["surfaces"]
    assert not surface.get("htmlBindings")
    assert not surface.get("interfaceIntent", {}).get("hierarchy")
    assert plan["policy"] == "mcel.semantic-structure.p3"
    assert any(region["recipe"] == "navigation" for region in plan["regions"])
    assert any(region["recipe"] == "task" for region in plan["regions"])  # Worker ring, not Hub Apply
    assert {cap["id"] for cap in ir["capabilities"]} == {
        "capability:worker-observer", "capability:worker-hub-choice-control", "capability:worker-destination-control",
        "capability:worker-policy-control", "capability:worker-slh-target-control",
        "capability:worker-marketplace-observer", "capability:worker-marketplace-settings",
        "capability:worker-marketplace-actions", "capability:worker-enrollment",
    }
    assert {intent["id"] for intent in ir["intents"] if intent["risk"] == "consequential"} == {
        "intent:select-destination", "intent:commit-hub-choice", "intent:disconnect", "intent:save-policy",
        "intent:save-slh-target", "intent:retry-hub", "intent:save-seller", "intent:save-requester",
        "intent:register-offer", "intent:work-now", "intent:finish-work-now", "intent:pause-selling",
        "intent:request-enrollment", "intent:observe-enrollment",
    }


def test_worker_semantic_page_uses_live_mcel_runtime_without_legacy_worker_ownership():
    page = render_worker_page(ROOT)
    assert 'id="mcel-workbench"' in page
    assert 'id="mcel-generated-data"' in page
    assert "McelSemanticOperationalRuntime.mount" in page
    assert "McelSemanticWorkerPorts.create" in page
    assert "mcel.generated-composition.v1" in page
    # Wallet is a compatibility adapter, not a second legacy Worker app.
    assert 'function initWorkerWalletOnly()' in page
    assert 'initWorkerWalletOnly();' in page
    assert 'initWorkerApp();' not in page
    assert 'id="worker-connect-wallet"' in page
    assert 'id="worker-request-multisession-key"' in page
    assert 'id="worker-fund-hub-credit"' in page
    assert 'data-mcel-worker-phase="candidate"' in page
    routes = (ROOT / 'main_computer/viewport_route_dispatch.py').read_text(encoding='utf-8')
    assert 'route_path == "/applications/mcel-semantic-worker"' in routes
    assert 'route_path == "/api/applications/worker/mcel-observation"' in routes
    registry = json.loads((ROOT / 'tools/fixtures/mcel_semantic_applications_shell/registry.json').read_text())
    worker, = (app for app in registry["apps"] if app["id"] == "worker")
    assert worker["mount"] == "semantic"  # Explicit, rollback-capable canonical UI promotion.
    lab = (ROOT / 'main_computer/web/applications/scripts/mcel-lab.js').read_text()
    assert 'route: "/applications/mcel-semantic-worker"' in lab


def test_worker_native_refuses_authored_html_layout(tmp_path):
    source = ROOT / 'mcel_apps/worker/application.js'
    target = tmp_path / 'mcel_apps/worker'
    target.mkdir(parents=True)
    (target / 'application.js').write_bytes(source.read_bytes())
    (target / 'application.html').write_text('<h1>authored view</h1>')
    with pytest.raises(WorkerSemanticApplicationError, match='authored HTML'):
        compile_worker_application(tmp_path)


class WorkerReadOnly(ViewportEnergyRoutesMixin):
    def __init__(self):
        self.payload = None
        self.server = SimpleNamespace(worker_runtime_supervisor=SimpleNamespace(status=lambda: {
            "ok": True, "reason": "Idle policy", "runtime": {
                "state": "READY", "phase": "not_accepting", "active_jobs": 0,
                "allowed_to_accept": False,
            },
        }), signal=lambda *args, **kw: None)

    def _worker_ui_client_is_local(self):
        return True

    def _load_worker_settings(self):
        return {"selectedNetwork": "slh", "sellerEnabled": True,
                "sellerAvailabilityMode": "totally_idle", "remoteEnabled": False,
                "remoteMode": "ask-when-busy", "signedWorkerConnection": {
                    "signature": "MUST_NOT_LEAK_SIGNED_CONNECTION",
                    "privateKey": "MUST_NOT_LEAK_PRIVATE_KEY"},
                "managedSLHIdentityDir": "MUST_NOT_LEAK_IDENTITY_DIR"}

    def _worker_network_session_payload(self, settings, *, check_hub=False):
        assert check_hub is False
        return {"session": {"selected_network": "slh", "requested_ring": "3",
            "connection_status": "setup", "worker_id": "", "connection_error": "Enroll through SLH bank",
            "profile": {"kind": "managed-slh", "hub_url": "https://localhost:8899"},
            "signed_connection": settings['signedWorkerConnection']},
            "networks": [
                {"network": "mainnet", "display_name": "Mainnet"},
                {"network": "testnet", "display_name": "Testnet"},
                {"network": "test", "display_name": "Test"},
                {"network": "dev", "display_name": "Dev"},
            ]}

    def _worker_network_order(self):
        return ["mainnet", "testnet", "test", "dev"]

    def _save_worker_settings(self, *_args, **_kwargs):
        pytest.fail("An MCEL Worker observation must not write settings")

    def _fetch_hub_status(self, *_args, **_kwargs):
        pytest.fail("An MCEL Worker observation must not probe a Hub")

    def _worker_runtime_transition(self, *_args, **_kwargs):
        pytest.fail("An MCEL Worker observation must not sync the runtime")

    def _send_json(self, payload, status=None):
        self.payload = payload


def test_native_worker_observation_is_redacted_and_read_only():
    worker = WorkerReadOnly()
    worker._handle_worker_mcel_observation()
    data = worker.payload
    assert data["ok"] is True
    assert data["schema"] == "mcel.worker.observation.v1"
    assert data["hubChoice"] == "slh"
    assert data["destination"]["selectedNetwork"] == "slh"
    assert data["destination"]["slhEnrollment"] == "NOT_VERIFIED"
    assert data["slhTarget"] == {"hubUrl": "", "authenticationState": "NOT_VERIFIED"}
    assert data["destination"]["connectionStatus"] == "setup"
    assert data["runtime"]["allowedToAccept"] is False
    assert data["policy"]["sellerEnabled"] is True
    assert [d["network"] for d in data["destinations"]] == ["none", "mainnet", "testnet", "test", "dev", "slh"]
    assert "MUST_NOT_LEAK" not in json.dumps(data)
    assert "signed_connection" not in json.dumps(data)


def test_native_worker_observation_denies_remote_clients():
    worker = WorkerReadOnly()
    worker._worker_ui_client_is_local = lambda: False
    worker._load_worker_settings = lambda: pytest.fail("Unauthorized read reached settings")
    worker._handle_worker_mcel_observation()
    assert worker.payload["ok"] is False


def test_native_worker_port_enforces_operation_identity_and_minimal_settings_patch():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed to verify the browser capability boundary')
    script = (ROOT / 'main_computer/web/applications/scripts/mcel-semantic-worker-ports.js').read_text()
    test = r'''
const assert = require('node:assert/strict');
const calls=[];
const observation={ok:true,schema:'mcel.worker.observation.v1',hubChoice:'none',
  destinations:['none','mainnet','testnet','test','dev','slh'].map(network=>({network,label:network})),
  destination:{selectedNetwork:'none',connectionStatus:'disconnected',hub:'',chainId:'',
    requestedRing:3,assignedRing:'',workerId:'',explanation:'',slhEnrollment:'NOT_APPLICABLE'},
  slhTarget:{hubUrl:'',authenticationState:'NOT_VERIFIED'},
  runtime:{state:'READY',phase:'not_accepting',reason:'',activeJobs:0,allowedToAccept:false},
  policy:{sellerEnabled:false,availabilityMode:'totally_idle',remoteEnabled:false,remoteMode:'never'}};
const fetcher=async(url,req)=>{
  calls.push({url,req});
  if(url.endsWith('/network-session')){
    const body=JSON.parse(req.body);
    observation.destination.selectedNetwork=body.network;
    observation.destination.requestedRing=Number(body.requested_ring);
    observation.destination.slhEnrollment=body.network==='slh'?'NOT_VERIFIED':'NOT_APPLICABLE';
  }
  if(url.endsWith('/settings')){
    const body=JSON.parse(req.body);
    if(body.changed_fields.includes('mcelHubChoice')){
      observation.hubChoice=body.mcelHubChoice;
    }else if(body.changed_fields.includes('managedSLHUrl')){
      observation.slhTarget.hubUrl=body.managedSLHUrl;
    }else{
      Object.assign(observation.policy,{sellerEnabled:body.settings.sellerEnabled,
        availabilityMode:body.settings.sellerAvailabilityMode});
    }
  }
  return {ok:true,json:async()=>url.endsWith('/mcel-observation')?structuredClone(observation):{ok:true}};
};
function req(name,cap,op,method,risk,args={}){
  return {appId:'worker',intentId:'intent:'+name,effectId:'effect:'+name+'.request',
    capabilityId:'capability:'+cap,operation:op,runtimeMethod:method,risk,arguments:args};
}
(async()=>{
  const port=globalThis.McelSemanticWorkerPorts.create({appId:'worker',fetcher});
  const select=req('select-destination','worker-destination-control','select',
    'selectWorkerDestination','consequential',{network:'slh',requestedRing:3});
  assert.equal(port.authorize(select),false);
  const read=req('refresh-destinations','worker-observer','destinations',
    'observeWorkerDestinations','external-read');
  assert.equal(port.authorize(read),true);
  assert.equal((await port.invoke(read)).length,6);
  assert.equal(port.authorize(select),true);
  const choice=req('commit-hub-choice','worker-hub-choice-control','save',
    'saveWorkerHubChoice','consequential',{network:'slh'});
  assert.equal(port.authorize(choice),true);
  assert.equal(port.authorize({...choice,arguments:{network:'invalid'}}),false);
  assert.equal(port.authorize({...choice,arguments:{network:'slh',credential:'x'}}),false);
  assert.equal(await port.invoke(choice),'slh');
  assert.equal(observation.hubChoice,'slh');
  assert.equal(observation.destination.selectedNetwork,'none');
  const choicePatch=JSON.parse(calls.find(x=>x.url.endsWith('/settings')).req.body);
  assert.deepEqual(choicePatch,{mcelHubChoice:'slh',changed_fields:['mcelHubChoice']});
  assert.equal(calls.some(x=>x.url.endsWith('/network-session')),false);
  assert.equal(port.authorize({...select,capabilityId:'capability:worker-observer'}),false);
  assert.equal(port.authorize({...select,arguments:{network:'slh',requestedRing:3,credential:'abc'}}),false);
  assert.equal(port.authorize({...select,arguments:{network:'slh',requestedRing:99}}),false);
  const result=await port.invoke(select);
  assert.equal(result.selectedNetwork,'slh');
  assert.equal(result.slhEnrollment,'NOT_VERIFIED');
  const slh=req('save-slh-target','worker-slh-target-control','save',
    'saveWorkerSlhTarget','consequential',{hubUrl:'https://internal.example:8899'});
  assert.equal(port.authorize(slh),true);
  assert.equal(port.authorize({...slh,arguments:{hubUrl:'http://insecure.example'}}),false);
  assert.equal(port.authorize({...slh,arguments:{hubUrl:'https://internal.example:8899',secret:'x'}}),false);
  const savedSlh=await port.invoke(slh);
  assert.equal(savedSlh.authenticationState,'NOT_VERIFIED');
  assert.equal(savedSlh.hubUrl,'https://internal.example:8899');
  const slhPatch=JSON.parse(calls.filter(x=>x.url.endsWith('/settings')).at(-1).req.body);
  assert.deepEqual(slhPatch,{managedSLHUrl:'https://internal.example:8899',changed_fields:['managedSLHUrl']});
  const policy=req('save-policy' ,'worker-policy-control','save','saveWorkerPolicy','consequential',
    {sellerEnabled:true,availabilityMode:'ai_idle'});
  assert.equal(port.authorize(policy),true);
  assert.equal(port.authorize({...policy,arguments:{...policy.arguments,secret:'x'}}),false);
  await port.invoke(policy);
  const patch=JSON.parse(calls.filter(x=>x.url.endsWith('/settings')).at(-1).req.body);
  assert.deepEqual(patch.changed_fields,[
    'sellerEnabled','rentalEnabled','sellerAvailabilityMode']);
  assert.equal(patch.settings.signedWorkerConnection,undefined);
  assert.equal(patch.settings.workerHubRegistration,undefined);
  assert.equal(patch.settings.managedSLHDeviceId,undefined);
  assert.equal(calls.every(x=>x.req.credentials==='same-origin'),true);
  assert.equal(calls.some(x=>x.url.includes('multisession')||x.url.includes('enroll')||x.url.includes('offer-sync')),false);
  console.log('MCEL_WORKER_BOUNDARY_PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run([node, '-e', script + '\n' + test], text=True,
                            capture_output=True, timeout=20, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    assert 'MCEL_WORKER_BOUNDARY_PASS' in result.stdout
