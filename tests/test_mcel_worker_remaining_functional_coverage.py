"""Five remaining isolated Worker intent proofs before live cutover.

Node runs the actual production MCEL Worker port with in-memory transport.
Chromium runs the generated application and controls with mocked HTTP only.
These tests NEVER use a real Hub, wallet, or production settings database.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from main_computer.mcel_worker_application import render_worker_page
from tests.test_mcel_worker_marketplace_parity import BROWSER_BACKEND

ROOT = Path(__file__).resolve().parents[1]
PORT = ROOT / 'main_computer/web/applications/scripts/mcel-semantic-worker-ports.js'

NODE_BOUNDARY = r'''
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'),{filename:process.argv[1]});
const action=process.argv[2];
const state={hubChoice:'mainnet',active:'none',ring:2,sellerEnabled:true,
 rentalEnabled:true,remoteEnabled:true,sellerAvailabilityMode:'totally_idle',
 sellerTargetTokens:1024,requests:[],rejectSession:false,ignoreSession:false,
 ignoreSettings:false};
const clone=value=>structuredClone(value);
const reply=(data,status=200)=>({ok:status<400,status,json:async()=>clone(data)});
const fetcher=async(path,opts={})=>{
 const method=opts.method||'GET',body=opts.body?JSON.parse(opts.body):null;
 state.requests.push({path,method,body});
 if(path.endsWith('/mcel-observation')){
  const destinations=[['none','None'],['mainnet','Mainnet'],['testnet','Testnet'],
    ['test','Local Test'],['dev','Local Dev'],['slh','SLH/SEH']]
    .map(([network,label])=>({network,label}));
  return reply({ok:true,schema:'mcel.worker.observation.v1',hubChoice:state.hubChoice,
    destinations,destination:{selectedNetwork:state.active,
      connectionStatus:state.active==='none'?'disconnected':'connected',
      hub:'',chainId:'',requestedRing:state.ring,assignedRing:'',workerId:'',
      explanation:'',slhEnrollment:state.active==='slh'?'NOT_VERIFIED':'NOT_APPLICABLE'},
    slhTarget:{hubUrl:'',authenticationState:'NOT_VERIFIED'},
    runtime:{state:'READY',phase:'not_accepting',reason:'',activeJobs:0,allowedToAccept:false},
    policy:{sellerEnabled:state.sellerEnabled,availabilityMode:state.sellerAvailabilityMode,
      remoteEnabled:state.remoteEnabled,remoteMode:'ask-when-busy'}});
 }
 if(path.endsWith('/settings')){
  if(method==='POST' && !state.ignoreSettings){
   const settings=body.settings||body;
   for(const key of body.changed_fields||[])state[key]=settings[key];
  }
  return reply({ok:true,settings:{sellerEnabled:state.sellerEnabled,
    rentalEnabled:state.rentalEnabled,remoteEnabled:state.remoteEnabled}});
 }
 if(path.endsWith('/network-session')){
  if(method==='POST'){
   if(state.rejectSession)return reply({ok:false,error:'hub refused'},409);
   if(!state.ignoreSession){state.active=body.network;state.ring=Number(body.requested_ring);}
  }
  return reply({ok:true,session:{selected_network:state.active}});
 }
 throw Error('unexpected '+method+' '+path);
};
const port=McelSemanticWorkerPorts.create({appId:'worker',fetcher});
const request=(name,cap,op,method,risk,args={})=>({appId:'worker',
 intentId:'intent:'+name,effectId:'effect:'+name+'.request',capabilityId:'capability:'+cap,
 operation:op,runtimeMethod:method,risk,arguments:args});
const observe=()=>port.invoke(request('refresh-destinations','worker-observer','destinations',
 'observeWorkerDestinations','external-read'));
const writes=()=>state.requests.filter(r=>r.method==='POST');
(async()=>{
 await observe();
 if(action==='pause'){
  const pause=request('pause-selling','worker-marketplace-actions','pause',
    'pauseMarketplaceSelling','consequential');
  assert(port.authorize(pause));
  const output=await port.invoke(pause);
  assert.equal(output.status,'PAUSED');
  assert.deepEqual(writes().map(r=>r.path),['/api/applications/worker/settings']);
  assert.deepEqual(writes()[0].body.changed_fields,['sellerEnabled','rentalEnabled']);
  assert.deepEqual(writes()[0].body.settings,{sellerEnabled:false,rentalEnabled:false});
  assert.equal(state.sellerEnabled,false);assert.equal(state.rentalEnabled,false);
  assert.equal(state.remoteEnabled,true);assert.equal(state.sellerTargetTokens,1024);
  assert.equal(state.hubChoice,'mainnet');assert.equal(state.active,'none');
  assert.equal(writes().length,1);
  state.sellerEnabled=true;state.rentalEnabled=true;state.ignoreSettings=true;
  await assert.rejects(()=>port.invoke(pause),/pause-postcondition-unverified/);
  assert.equal(state.sellerEnabled,true);
 }else if(action==='retry'){
  const retry=request('retry-hub','worker-destination-control','retry',
    'retryWorkerDestination','consequential');
  assert(port.authorize(retry));
  const output=await port.invoke(retry);
  assert.equal(output.selectedNetwork,'mainnet');
  assert.equal(output.requestedRing,2);
  assert.equal(state.active,'mainnet');assert.equal(state.hubChoice,'mainnet');
  assert.deepEqual(writes()[0].body,{network:'mainnet',requested_ring:'2'});
  assert.equal(writes().length,1);
  state.hubChoice='slh';await observe();const before=writes().length;
  await assert.rejects(()=>port.invoke(retry),/marketplace-hub-required/);
  assert.equal(writes().length,before,'no retry to SLH using marketplace authority');
  state.hubChoice='mainnet';state.active='none';state.rejectSession=true;await observe();
  await assert.rejects(()=>port.invoke(retry),/hub refused/);
  assert.equal(state.active,'none','refused session must not mutate active Hub');
  state.rejectSession=false;state.ignoreSession=true;await observe();
  await assert.rejects(()=>port.invoke(retry),/retry-postcondition-unverified/);
 }else throw Error('unknown case '+action);
 console.log(JSON.stringify({result:'PASS',case:action,writes:writes().length}));
})().catch(error=>{console.error(error);process.exitCode=1});
'''


def _node_boundary(kind: str) -> None:
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Node.js unavailable')
    run = subprocess.run([executable, '-e', NODE_BOUNDARY, str(PORT), kind],
                         capture_output=True, text=True, timeout=25, check=False)
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(run.stdout)['result'] == 'PASS'


def test_pause_selling_adapter_enforces_scoped_disable_and_verified_state():
    _node_boundary('pause')


def test_retry_hub_adapter_reconciles_selection_and_refuses_unverified_retries():
    _node_boundary('retry')


def _browser():
    pw = pytest.importorskip('playwright.sync_api')
    return pw


def _mount_worker(page, *, intercept_runtime=False):
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('dialog', lambda dialog: dialog.accept())
    page.evaluate(BROWSER_BACKEND)
    if intercept_runtime:
        # The port captures fetch at construction. Install the response shim
        # BEFORE mounting the real application, not after MCEL initializes.
        page.evaluate('''() => {
          const originalFetch=window.fetch;
          window.workerRuntimePhase='not_accepting';
          window.fetch=async (url, options={}) => {
            const response=await originalFetch(url, options);
            if(!String(url).endsWith('/mcel-observation')) return response;
            const data=await response.json();
            data.runtime.phase=window.workerRuntimePhase;
            data.runtime.activeJobs=window.workerRuntimePhase==='accepting'?2:0;
            data.runtime.allowedToAccept=window.workerRuntimePhase==='accepting';
            return {...response,json:async()=>data};
          };
        }''')
    page.set_content(render_worker_page(ROOT), wait_until='domcontentloaded')
    page.wait_for_function('''() => window.__mcelWorker?.app.readState('state:selected-network')==='mainnet' ''')
    page.locator('[data-mcel-purpose="navigation"] [data-value="worker"]').click()
    page.wait_for_function('''() => window.__mcelWorker?.app.readState('state:policy')?.sellerEnabled===true''')
    return errors


def test_refresh_policy_ui_reads_current_server_policy_without_writing():
    pw = _browser()
    with pw.sync_playwright() as driver:
        executable = shutil.which('chromium') or shutil.which('chromium-browser')
        browser = driver.chromium.launch(headless=True, **({'executable_path': executable} if executable else {}))
        try:
            page = browser.new_page(viewport={'width': 1120, 'height': 760})
            page.set_default_timeout(9000)
            errors = _mount_worker(page)
            page.evaluate('''() => {
              window.mock.settings.sellerAvailabilityMode = 'ai_idle';
              window.mock.settings.sellerEnabled = false;
              window.mock.settings.rentalEnabled = false;
              window.requestLog=[];
            }''')
            page.locator('button[data-intent="intent:refresh-policy"]').click()
            page.wait_for_function('''() => {
              const p=window.__mcelWorker.app.readState('state:policy');
              return p?.sellerEnabled===false && p?.availabilityMode==='ai_idle';
            }''')
            requests=page.evaluate('window.requestLog')
            assert any(r['path'].endswith('/mcel-observation') for r in requests)
            assert not any(r['method']=='POST' for r in requests)
            assert errors == [], errors
        finally:
            browser.close()


def test_refresh_runtime_ui_reconciles_new_observation_without_writing():
    pw = _browser()
    with pw.sync_playwright() as driver:
        executable = shutil.which('chromium') or shutil.which('chromium-browser')
        browser = driver.chromium.launch(headless=True, **({'executable_path': executable} if executable else {}))
        try:
            page = browser.new_page(viewport={'width': 1120, 'height': 760})
            page.set_default_timeout(9000)
            errors = _mount_worker(page, intercept_runtime=True)
            page.evaluate('''() => {window.workerRuntimePhase='accepting'; window.requestLog=[];}''')
            page.locator('button[data-intent="intent:refresh-runtime"]').click()
            page.wait_for_function('''() => {
              const r=window.__mcelWorker.app.readState('state:runtime');
              return r?.phase==='accepting' && r.activeJobs===2 && r.allowedToAccept===true;
            }''')
            requests=page.evaluate('window.requestLog')
            assert any(r['path'].endswith('/mcel-observation') for r in requests)
            assert not any(r['method']=='POST' for r in requests)
            assert errors == [], errors
        finally:
            browser.close()


def test_save_policy_ui_scopes_settings_and_verifies_published_policy():
    pw = _browser()
    with pw.sync_playwright() as driver:
        executable = shutil.which('chromium') or shutil.which('chromium-browser')
        browser = driver.chromium.launch(headless=True, **({'executable_path': executable} if executable else {}))
        try:
            page = browser.new_page(viewport={'width': 1120, 'height': 760})
            page.set_default_timeout(9000)
            errors = _mount_worker(page)
            page.locator('input[name="sellerEnabled"]').uncheck()
            page.locator('select[name="availabilityMode"]').select_option('ai_idle')
            page.get_by_role('button', name='Apply policy').click()
            page.wait_for_function('''() => {
              const p=window.__mcelWorker.app.readState('state:policy');
              return p?.sellerEnabled===false && p?.availabilityMode==='ai_idle';
            }''')
            writes=page.evaluate('''() => window.requestLog.filter(
              r=>r.method==='POST' && r.path.endsWith('/settings'))''')
            assert len(writes)==1, writes
            assert writes[0]['body']['changed_fields']==['sellerEnabled','rentalEnabled','sellerAvailabilityMode']
            assert writes[0]['body']['settings']=={
                'sellerEnabled': False,'rentalEnabled': False,'sellerAvailabilityMode':'ai_idle'}
            assert page.evaluate('window.mock.settings.mcelHubChoice')=='mainnet'
            assert page.evaluate('window.mock.settings.remoteEnabled') is False
            assert errors == [], errors
        finally:
            browser.close()
