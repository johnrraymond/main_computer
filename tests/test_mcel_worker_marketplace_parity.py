"""Native Worker seller/Requester parity and marketplace authority boundaries.

These probes execute the actual candidate port adapter in Node and the actual
compiled MCEL application in Chromium.  A stub legacy signer is used ONLY in
these tests; no real money, wallet signing, or Hub session is simulated as proven.
"""
from pathlib import Path
import json
import shutil
import subprocess

import pytest

from main_computer.mcel_worker_application import compile_worker_application, render_worker_page

ROOT = Path(__file__).resolve().parents[1]
PORT = ROOT / 'main_computer/web/applications/scripts/mcel-semantic-worker-ports.js'

NODE_PARITY = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const script=fs.readFileSync(process.argv[1],'utf8');
vm.runInThisContext(script,{filename:'mcel-semantic-worker-ports.js'});
let chosen='mainnet',active='mainnet';
let mockSettings={sellerEnabled:true,rentalEnabled:true,sellerAvailabilityMode:'ai_idle',
  sellerTargetTokens:1024,sellerCreditsPerToken:'0.001',maxConcurrency:1,
  models:'gemma4:26b-a4b-it-q4_K_M',capability:'chat.completions',executionMode:'worker_pull_v0',
  remoteEnabled:false,remoteMode:'ask-when-busy',remoteCreditsPerToken:'0.001',
  remoteMaxOutputTokens:1024,remoteDailyLimit:100000,remoteAskBeforeSpend:true,
  remoteOnlyWhenBusy:true,nodeId:'saved-node',endpoint:'http://127.0.0.1:8771'};
const traffic=[],signer=[],calls=[];
const reply=data=>({ok:true,status:200,json:async()=>({ok:true,...data})});
const fetcher=async(path,opts={})=>{
 const method=opts.method||'GET',body=opts.body?JSON.parse(opts.body):null;
 traffic.push({path,method,body});
 if(path.endsWith('/mcel-observation'))return reply({schema:'mcel.worker.observation.v1',
  hubChoice:chosen,destinations:[['none','None'],['mainnet','Mainnet'],['testnet','Testnet'],
  ['test','Local Test'],['dev','Local Dev'],['slh','SLH/SEH']].map(([network,label])=>({network,label})),
  destination:{selectedNetwork:active,connectionStatus:'connected',hub:'hub',chainId:'42424240',
   requestedRing:3,assignedRing:'3',workerId:'worker-1',explanation:'',slhEnrollment:'NOT_APPLICABLE'},
  slhTarget:{hubUrl:'',authenticationState:'NOT_VERIFIED'},
  runtime:{state:'READY',phase:'not_accepting',reason:'',activeJobs:0,allowedToAccept:false},
  policy:{sellerEnabled:mockSettings.sellerEnabled,availabilityMode:mockSettings.sellerAvailabilityMode,
    remoteEnabled:mockSettings.remoteEnabled,remoteMode:mockSettings.remoteMode}});
 if(path.endsWith('/settings')){
  if(method==='POST'){
    const items=body.settings||body;
    for(const field of body.changed_fields||[])mockSettings[field]=items[field];
  }
  return reply({settings:structuredClone(mockSettings)});
 }
 if(path.endsWith('/network-session'))return reply({session:{selected_network:active,
   connection_status:'connected',worker_id:'worker-1',assigned_ring:'3',
   hub_registration:{status:'accepted'}}});
 if(path.endsWith('/runtime-status'))return reply({runtime:{phase:'accepting',active_jobs:2,
   last_heartbeat_at:'2026-11-21T01:02:03Z'},workNowOverride:{active:true,
   expiresAt:'2999-01-01T00:00:00Z'}});
 throw Error('Unexpected URL '+path);
};
globalThis.__mcelWorker={app:{readState:name=>name==='state:selected-network'?chosen:null}};
globalThis.McelWorkerLegacyMarketplace={
 setSeller:(offer,settings)=>{signer.push({action:'setSeller',offer,settings:{nodeId:settings.nodeId,endpoint:settings.endpoint}});},
 refresh:async()=>{signer.push({action:'refresh'});},
 register:async()=>{signer.push({action:'register'});return {status:'accepted',worker_id:'worker-1'};},
 workNow:async seconds=>{signer.push({action:'workNow',seconds});return {ok:true};},
 finish:async()=>{signer.push({action:'finish'});return {ok:true,status:'draining'};}
};
const port=McelSemanticWorkerPorts.create({appId:'worker',fetcher});
const request=(name,cap,operation,method,risk,args={})=>({appId:'worker',
  intentId:'intent:'+name,effectId:'effect:'+name+'.request',capabilityId:'capability:'+cap,
  operation,runtimeMethod:method,risk,arguments:args});
const read=(n,op,method)=>request(n,'worker-marketplace-observer',op,method,'external-read');
const write=(n,cap,op,method,args={})=>request(n,cap,op,method,'consequential',args);
(async()=>{
 // Read-only startup cannot mutate local settings or Hub sessions.
 await port.invoke(request('refresh-destinations','worker-observer','destinations','observeWorkerDestinations','external-read'));
 const seller=await port.invoke(read('refresh-seller','seller','observeMarketplaceSeller'));
 assert.strictEqual(seller.sellerTargetTokens,1024);
 const requester=await port.invoke(read('refresh-requester','requester','observeMarketplaceRequester'));
 assert.strictEqual(requester.remoteDailyLimit,100000);
 const runtime=await port.invoke(read('refresh-marketplace-status','status','observeMarketplaceStatus'));
 assert.strictEqual(runtime.registrationStatus,'accepted');
 assert.strictEqual(runtime.workNowState,'ACTIVE');
 assert.strictEqual(runtime.activeJobs,2);
 assert(!traffic.some(x=>x.method==='POST'),'initial observation must be read-only');
 const sellerArgs={sellerTargetTokens:2048,sellerCreditsPerToken:'0.004',maxConcurrency:3,
  models:'local-model-1',capability:'chat.completions',executionMode:'worker_pull_v0'};
 const saveSeller=write('save-seller','worker-marketplace-settings','seller','saveMarketplaceSeller',sellerArgs);
 assert(port.authorize(saveSeller));
 await port.invoke(saveSeller);
 assert.strictEqual(mockSettings.nodeId,'saved-node');
 const sellerWrite=traffic.find(x=>x.method==='POST');
 assert.deepStrictEqual(sellerWrite.body.changed_fields.sort(),Object.keys(sellerArgs).sort());
 assert(!('remoteEnabled' in sellerWrite.body.settings));
 const requesterArgs={remoteEnabled:true,remoteMode:'auto-when-busy',remoteCreditsPerToken:'0.002',
  remoteMaxOutputTokens:512,remoteDailyLimit:25000,remoteAskBeforeSpend:true,remoteOnlyWhenBusy:true};
 await port.invoke(write('save-requester','worker-marketplace-settings','requester','saveMarketplaceRequester',requesterArgs));
 const requesterWrite=traffic.filter(x=>x.method==='POST')[1];
 assert.deepStrictEqual(requesterWrite.body.changed_fields.sort(),Object.keys(requesterArgs).sort());
 assert(!('sellerTargetTokens' in requesterWrite.body.settings));
 const register=write('register-offer','worker-marketplace-actions','register','registerMarketplaceOffer');
 const registered=await port.invoke(register);
 assert.strictEqual(registered.status,'ACCEPTED');
 assert.strictEqual(signer.at(-1).action,'register');
 assert.strictEqual(signer.find(x=>x.action==='setSeller').settings.nodeId,'saved-node');
 const work=await port.invoke(write('work-now','worker-marketplace-actions','work-now','startMarketplaceWorkNow',{minutes:45}));
 assert.strictEqual(work.status,'WORK_NOW');
 assert.strictEqual(signer.at(-1).seconds,2700);
 const finish=await port.invoke(write('finish-work-now','worker-marketplace-actions','finish','finishMarketplaceWorkNow'));
 assert.strictEqual(finish.status,'FINISH_REQUESTED');
 assert.strictEqual(signer.at(-1).action,'finish');
 // Failed authorization must never reach the signing bridge.
 chosen='testnet'; const before=signer.length;
 await assert.rejects(()=>port.invoke(register),/matching-connected-marketplace-session-required/);
 await assert.rejects(()=>port.invoke(write('finish-work-now','worker-marketplace-actions','finish','finishMarketplaceWorkNow')),
    /matching-connected-marketplace-session-required/);
 assert.strictEqual(signer.length,before);
 assert(!port.authorize(write('work-now','worker-marketplace-actions','work-now','startMarketplaceWorkNow',{minutes:0})));
 assert(!port.authorize(write('save-seller','worker-marketplace-settings','seller','saveMarketplaceSeller',
  {...sellerArgs,unknown:'forbidden'})));
 assert(!port.authorize(write('save-requester','worker-marketplace-settings','requester','saveMarketplaceRequester',
  {...requesterArgs,remoteDailyLimit:-1})));
 console.log(JSON.stringify({result:'PASS',writes:traffic.filter(x=>x.method==='POST').length,
   registered:registered.status,work:work.status,finish:finish.status,signedActions:signer.length}));
})().catch(e=>{console.error(e);process.exitCode=1});
'''


def test_native_worker_marketplace_ports_preserve_legacy_authority_and_scoped_settings():
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Node.js unavailable')
    result = subprocess.run([executable, '-e', NODE_PARITY, str(PORT)],
                            capture_output=True, text=True, timeout=25, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['result'] == 'PASS'


def test_semantic_worker_exposes_all_parity_controls_without_promotion():
    ir, plan = compile_worker_application(ROOT)
    intents = {i['id'] for i in ir['intents']}
    assert {'intent:save-seller', 'intent:save-requester', 'intent:register-offer',
            'intent:work-now', 'intent:finish-work-now', 'intent:pause-selling',
            'intent:retry-hub', 'intent:disconnect'} <= intents
    page = render_worker_page(ROOT)
    for caption in ('Save Seller Offer', 'Requester Spending and Consent',
                    'Register / Update Offer', 'Finish after current request',
                    'Start Work Now', 'Save Requester Limits'):
        assert caption in page, caption
    assert 'initWorkerWalletOnly();' in page
    assert 'initWorkerApp();' not in page
    assert 'McelWorkerLegacyMarketplace' in page
    assert all('mcel.semantic-structure.p3' in str(plan.get('policy', '')) for _ in [0])


BROWSER_BACKEND = r'''
() => {
 const state={hubChoice:'mainnet',active:'mainnet',settings:{
  mcelHubChoice:'mainnet',sellerEnabled:true,rentalEnabled:true,
  sellerAvailabilityMode:'totally_idle',remoteEnabled:false,remoteMode:'ask-when-busy',
  sellerTargetTokens:1024,sellerCreditsPerToken:'0.001',maxConcurrency:1,
  models:'gemma4:26b-a4b-it-q4_K_M',capability:'chat.completions',executionMode:'worker_pull_v0',
  remoteCreditsPerToken:'0.001',remoteMaxOutputTokens:1024,remoteDailyLimit:100000,
  remoteAskBeforeSpend:true,remoteOnlyWhenBusy:true
 }};
 window.mock=state;window.requestLog=[];
 const reply=(data,status=200)=>({ok:status<400,status,json:async()=>data});
 window.fetch=async(url,options={})=>{
 const path=String(url),method=options.method||'GET';
 window.requestLog.push({path,method,...(options.body?{body:JSON.parse(options.body)}:{})});
 if(path.endsWith('/api/applications/slh/identities'))return reply({schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'primary',explanation:'',identities:[{deviceId:'primary',label:'Default identity',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'}]});
 if(path.endsWith('/api/applications/worker/settings')){
  if(method==='POST'){
   const body=JSON.parse(options.body);const items=body.settings||body;
   for(const key of (body.changed_fields||Object.keys(items))) state.settings[key]=items[key];
  }
  return reply({ok:true,settings:{...state.settings}});
 }
 if(path.endsWith('/api/applications/worker/network-session')){
  if(method==='POST'){const data=JSON.parse(options.body);state.active=data.network;}
  return reply({ok:true,session:{selected_network:state.active,connection_status:'connected',requested_ring:'3',assigned_ring:'3',worker_id:'w1',hub_registration:{status:'accepted'},profile:{hub_url:'http://127.0.0.1:3001'},signed_connection:{}},networks:[]});
 }
 if(path.endsWith('/api/applications/worker/runtime-status'))return reply({ok:true,runtime:{phase:'not_accepting',active_jobs:0,last_heartbeat_at:'yesterday'},workNowOverride:{}});
 if(path.endsWith('/api/applications/worker/mcel-observation'))return reply({
  ok:true,schema:'mcel.worker.observation.v1',hubChoice:state.hubChoice,
  destinations:[{network:'none',label:'None'},{network:'mainnet',label:'Mainnet'},
    {network:'testnet',label:'Testnet'},{network:'test',label:'Local Test'},
    {network:'dev',label:'Local Dev'},{network:'slh',label:'SLH/SEH'}],
  destination:{selectedNetwork:state.active,connectionStatus:'connected',hub:'http://127.0.0.1:3001',chainId:'42424240',requestedRing:3,assignedRing:'3',workerId:'w1',explanation:'',slhEnrollment:'NOT_APPLICABLE'},
  slhTarget:{hubUrl:'https://localhost:8898',authenticationState:'NOT_VERIFIED'},
  runtime:{state:'READY',phase:'not_accepting',reason:'',activeJobs:0,allowedToAccept:false},
  policy:{sellerEnabled:state.settings.sellerEnabled,availabilityMode:state.settings.sellerAvailabilityMode,remoteEnabled:state.settings.remoteEnabled,remoteMode:state.settings.remoteMode}
 });
 return reply({ok:false,error:'Unexpected '+method+' '+path},404);
 };
}
'''


def test_rendered_native_marketplace_roles_and_explicit_actions():
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as driver:
        # Match the other Worker browser tests: prefer a system Chromium when
        # present, otherwise let Playwright launch its managed browser.
        binary = shutil.which('chromium') or shutil.which('chromium-browser')
        browser = driver.chromium.launch(
            headless=True, **({'executable_path': binary} if binary else {})
        )
        try:
            page = browser.new_page(viewport={'width': 1120, 'height': 720})
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.on('dialog', lambda dialog: dialog.accept())
            page.evaluate(BROWSER_BACKEND)
            page.set_content(render_worker_page(ROOT))
            page.wait_for_function("() => window.__mcelWorker?.app.readState('state:destinations')?.length===6")
            assert not any(item['method']=='POST' for item in page.evaluate('window.requestLog'))
            page.locator('[data-mcel-purpose="navigation"] [data-value="worker"]').click()
            page.wait_for_function("() => window.__mcelWorker.app.readState('state:seller-configuration')?.sellerTargetTokens===1024")
            page.locator('input[name="sellerTargetTokens"]').fill('2048')
            page.get_by_role('button', name='Save Seller Offer').click()
            page.wait_for_function("() => window.mock.settings.sellerTargetTokens===2048")
            assert page.locator('input[name="sellerTargetTokens"]').input_value() == '2048'
            assert page.locator('button[data-intent="intent:register-offer"]').is_visible()
            assert page.locator('button[data-intent="intent:finish-work-now"]').is_visible()
            assert page.locator('input[name="minutes"]').is_visible()
            page.locator('[data-mcel-purpose="navigation"] [data-value="requester"]').click()
            page.wait_for_function("() => window.__mcelWorker.app.readState('state:requester-configuration')?.remoteDailyLimit===100000")
            page.locator('input[name="remoteDailyLimit"]').fill('25000')
            page.get_by_role('button', name='Save Requester Limits').click()
            page.wait_for_function("() => window.mock.settings.remoteDailyLimit===25000")
            assert page.locator('input[name="sellerTargetTokens"]').is_hidden()
            assert page.locator('input[name="remoteDailyLimit"]').is_visible()
            writes = page.evaluate('window.requestLog.filter(r=>r.method==="POST" && r.path.endsWith("/settings"))')
            assert [set(item['body']['changed_fields']) for item in writes] == [
                {'sellerTargetTokens','sellerCreditsPerToken','maxConcurrency','models','capability','executionMode'},
                {'remoteEnabled','remoteMode','remoteCreditsPerToken','remoteMaxOutputTokens','remoteDailyLimit',
                 'remoteAskBeforeSpend','remoteOnlyWhenBusy'},
            ]
            assert errors == [], errors
        finally:
            browser.close()
