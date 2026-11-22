"""Wallet/MSK compatibility acceptance. No private key, chain or SLH writes on view selection."""
from pathlib import Path
import shutil

import pytest
from main_computer.mcel_worker_application import compile_worker_application, render_worker_page

ROOT = Path(__file__).resolve().parents[1]
WALLET = '0x'+'ab'*20

BACKEND = r'''() => {
  window.requests=[];
  const state={hubChoice:'testnet', session:'testnet', connection:'connected', ring:3,
    keys:[], wallet:'0xabababababababababababababababababababab'};
  window.workerState=state;
  const reply=data=>({ok:true,status:200,json:async()=>({ok:true,...data})});
  window.fetch=async (url,opts={}) => {
    const address=String(url), method=opts.method||'GET';
    const body=opts.body?JSON.parse(opts.body):null;
    window.requests.push({address,method,body});
    if(address.endsWith('/mcel-observation'))return reply({schema:'mcel.worker.observation.v1',
      destinations:[['none','None'],['mainnet','Mainnet'],['testnet','Testnet'],
        ['test','Local Test'],['dev','Local Dev'],['slh','SLH/SEH']]
        .map(([network,label])=>({network,label})),
      hubChoice:state.hubChoice,
      destination:{selectedNetwork:state.session,connectionStatus:state.connection,hub:'',chainId:'',
        requestedRing:state.ring,assignedRing:'',workerId:'',explanation:'',slhEnrollment:'NOT_APPLICABLE'},
      slhTarget:{hubUrl:'',authenticationState:'NOT_VERIFIED'},
      runtime:{state:'READY',phase:'not_accepting',reason:'',activeJobs:0,allowedToAccept:false},
      policy:{sellerEnabled:false,availabilityMode:'totally_idle',remoteEnabled:false,remoteMode:'never'}});
    if(address.endsWith('/network-session')) {
      if(method==='POST') {state.session=body.network;state.ring=Number(body.requested_ring);}
      return reply({session:{selected_network:state.session,connection_status:state.connection,requested_ring:state.ring,
        profile:{network:'testnet',chain_id:'42424241',hub_url:'https://test-hub.example',
        chain_rpc_url:'http://127.0.0.1:18545',display_name:'Testnet'}},
        networks:[{network:'testnet',chain_id:'42424241',hub_url:'https://test-hub.example',
          chain_rpc_url:'http://127.0.0.1:18545',display_name:'Testnet'}]});
    }
    if(address.endsWith('/settings')) {
      if(method==='POST'){if(body.mcelHubChoice)state.hubChoice=body.mcelHubChoice;return reply({});}
      return reply({settings:{}});
    }
    if(address.endsWith('/wallet-funding/config'))return reply({hub_credit_bridge_escrow_address:''});
    if(address.endsWith('/xlag/dev/faucet') && method==='GET')return reply({runtime_ready:false});
    if(address.endsWith('/multisession-key/request') && method==='POST') {
      state.keys=[{id:'msk-0001',status:'active',wallet_address:state.wallet,
        hub_url:'https://test-hub.example',cluster_id:'greatlibrary-testnet',created_at:'2026-11-20T00:00:00Z'}];
      return reply({key:state.keys[0],verification:{recovered_address:state.wallet}});
    }
    if(address.endsWith('/multisession-key/revoke') && method==='POST') {
      if(body.key_id!=='msk-0001' || body.signed_request?.message?.action!=='revoke_multisession_key' ||
          body.signed_request?.message?.key_id!=='msk-0001' ||
          body.signed_request?.message?.request_id!=='revoke:msk-0001' ||
          body.signed_request?.message?.cluster_id!=='greatlibrary-testnet')
        throw new Error('Unsigned or mismatched revocation must not be accepted');
      state.keys=[];return reply({revoked:true,keys:[],hub_revoke:{ok:true}});
    }
    if(address.endsWith('/multisession-keys/load') && method==='POST')return reply({keys:state.keys});
    if(address.endsWith('/wallet-funding/balance') && method==='POST')return reply({balance:{}});
    if(address.endsWith('/wallet-balance') && method==='POST')return reply({balance_base_units:'0'});
    throw new Error('Unexpected network request: '+method+' '+address);
  };
}'''


def test_wallet_compat_source_retains_legacy_functions_and_mcel_contract():
    ir, _ = compile_worker_application(ROOT)
    assert any(n['id']=='surface-node:worker.wallet-compat-anchor'
               for n in ir['surfaces'][0]['nodes'])
    html=render_worker_page(ROOT)
    for marker in ('id="worker-connect-wallet"','id="worker-disconnect-wallet"',
                   'id="worker-request-multisession-key"','id="worker-revoke-multisession-key"',
                   'id="worker-request-faucet"','id="worker-fund-hub-credit"',
                   'id="worker-confirm-recovery"'):
        assert marker in html
    assert 'function initWorkerWalletOnly()' in html
    assert 'window.MainComputerWalletLibrary =' in html
    assert 'initWorkerApp();' not in html
    assert 'workerStartRuntimeSync();' in html  # definition retained, not invoked by bridge
    assert html.count('id="worker-connect-wallet"')==1


def test_wallet_browser_mount_and_msk_actions_without_implicit_worker_mutations():
    pw=pytest.importorskip('playwright.sync_api')
    executable=shutil.which('chromium') or shutil.which('chromium-browser')
    with pw.sync_playwright() as driver:
        browser=driver.chromium.launch(headless=True,**({'executable_path':executable} if executable else {}))
        try:
            page=browser.new_page(viewport={'width':1050,'height':700})
            page.set_default_timeout(4500)
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.evaluate(BACKEND)
            page.set_content(render_worker_page(ROOT))
            page.wait_for_function("() => window.__mcelWorker?.app.readState('state:selected-network')==='testnet'")
            page.wait_for_function("() => document.getElementById('worker-wallet-session-context')?.textContent.includes('verified')")
            assert page.locator('#worker-connect-wallet').is_visible()
            assert page.locator('#worker-connect-wallet').locator('xpath=ancestor::*[@data-mcel-region][1]').count() == 1
            assert page.locator('#worker-connect-wallet').locator('xpath=ancestor::*[@data-mcel-region][1]').get_attribute('data-mcel-region') == page.locator('[data-mcel-node="surface-node:worker.hub-radio-group"]').locator('xpath=ancestor::*[@data-mcel-region][1]').get_attribute('data-mcel-region')
            assert page.locator('#worker-connect-wallet').is_visible()
            page.wait_for_function("() => workerWalletHookState==='idle'",timeout=9000)
            assert page.locator('#worker-connect-wallet').is_enabled(), page.evaluate("""() => ({context:document.getElementById('worker-wallet-session-context').textContent, verified:workerWalletOnlyContextVerified(), hook:workerWalletHookState, isDisabled:document.getElementById('worker-connect-wallet').disabled})""")
            assert page.evaluate('typeof window.MainComputerWalletLibrary.requestMultiSessionKeySignature')=='function'
            # Opening the Hub Choice wallet controls is a read-only operation.
            assert all(r['method']=='GET' for r in page.evaluate('window.requests'))
            page.locator('#worker-recovery-email-input').fill('example@example.com')
            page.locator('#worker-add-recovery-email-form button').click()
            assert page.locator('#worker-recovery-email-list').get_by_text('example@example.com').is_visible()
            # Signing is stubbed, but the actual legacy MSK request/revoke click
            # handlers and endpoints remain real and are used without rerouting.
            page.evaluate('''() => {
              workerBridgeState.wallet={connected:true,address:window.workerState.wallet,
                chainId:'0x28757b1',connectedAt:new Date().toISOString()};
              window.MainComputerWalletLibrary.requestMultiSessionKeySignature=async () => ({
                signature:'0xmocked',wallet_address:window.workerState.wallet,
                chain_id:'0x28757b1',message:{request_id:'test'},kind:'main_computer_multisession_key_request'});
              window.MainComputerWalletLibrary.revokeMultiSessionKeySignature=async ({requestContext,keyId}) => ({
                signature:'0xmocked',wallet_address:requestContext.wallet_address,
                chain_id:requestContext.chain_id,
                message:{action:'revoke_multisession_key',key_id:keyId,
                  cluster_id:requestContext.cluster_id,request_id:`revoke:${keyId}`},
                kind:'main_computer_multisession_key_request'});
              renderWorkerBridgeReadiness();
            }''')
            page.locator('#worker-request-multisession-key').click()
            page.wait_for_function("() => window.requests.some(r=>r.address.endsWith('/multisession-key/request') && r.method==='POST')")
            page.wait_for_function("() => document.getElementById('worker-multisession-key-id')?.textContent==='msk-0001'")
            page.locator('#worker-revoke-multisession-key').click()
            page.wait_for_function("() => window.requests.some(r=>r.address.endsWith('/multisession-key/revoke') && r.method==='POST')")
            page.wait_for_function("() => document.getElementById('worker-multisession-key-state')?.textContent.includes('No active key')")
            revocations = page.evaluate("window.requests.filter(r=>r.address.endsWith('/multisession-key/revoke') && r.method==='POST')")
            assert len(revocations) == 1
            assert revocations[0]['body']['signed_request']['message']['request_id'] == 'revoke:msk-0001'
            assert revocations[0]['body']['signed_request']['message']['cluster_id'] == 'greatlibrary-testnet'
            assert len([r for r in page.evaluate('window.requests') if r['address'].endswith('/network-session') and r['method']=='POST'])==0
            page.locator('[data-mcel-purpose="navigation"] [data-value="worker"]').click()
            assert page.locator('#worker-connect-wallet').is_hidden()
            page.locator('[data-mcel-purpose="navigation"] [data-value="hub-choice"]').click()
            assert page.locator('#worker-connect-wallet').is_visible()
            page.locator('[data-mcel-radio-choices] input[value="slh"]').check()
            assert page.locator('#worker-connect-wallet').is_hidden()
            assert errors==[],errors
        finally:
            browser.close()


def test_wallet_controls_refuse_mismatched_connected_hub():
    pw=pytest.importorskip('playwright.sync_api')
    executable=shutil.which('chromium') or shutil.which('chromium-browser')
    with pw.sync_playwright() as driver:
        browser=driver.chromium.launch(headless=True,**({'executable_path':executable} if executable else {}))
        try:
            page=browser.new_page()
            page.evaluate(BACKEND)
            page.evaluate("window.workerState.session='mainnet'")
            page.set_content(render_worker_page(ROOT))
            page.wait_for_function("() => window.__mcelWorker?.app.readState('state:selected-network')==='testnet'")
            page.wait_for_function("() => document.getElementById('worker-wallet-session-context')?.textContent.includes('matching connected')")
            assert page.locator('#worker-connect-wallet').is_visible()
            assert page.locator('#worker-connect-wallet').is_disabled()
            assert page.locator('#worker-request-multisession-key').is_disabled()
            assert not any(r['method']=='POST' for r in page.evaluate('window.requests'))
        finally:browser.close()


def test_hub_navigation_and_wallet_workspace_follow_radio_before_server_save():
    pw=pytest.importorskip('playwright.sync_api')
    executable=shutil.which('chromium') or shutil.which('chromium-browser')
    with pw.sync_playwright() as driver:
        browser=driver.chromium.launch(headless=True,**({'executable_path':executable} if executable else {}))
        try:
            page=browser.new_page(viewport={'width':1100,'height':700})
            errors=[]
            page.on('pageerror',lambda exc:errors.append(str(exc)))
            page.evaluate(BACKEND)
            page.evaluate('''() => {
              const original=window.fetch;
              window.fetch=async (url,opts={})=>{
                if(String(url).endsWith('/settings') && opts.method==='POST')
                  await new Promise(done=>setTimeout(done,250));
                return original(url,opts);
              };
            }''')
            page.set_content(render_worker_page(ROOT))
            page.wait_for_function("() => window.__mcelWorker?.app.readState('state:selected-network')==='testnet'")
            nav='[data-mcel-purpose="navigation"] [data-value="hub-choice"]'
            panel='#mcel-wallet-staging'
            assert page.locator(nav).inner_text()=='Hub Choice\nTestnet'
            assert page.locator('#worker-multisession-card').is_visible()
            # Local state, navigation subtitle and workspace all change in the
            # same event turn, without waiting for the saved Hub Choice POST.
            snapshot=page.evaluate('''() => {
              document.querySelector('[data-mcel-radio-choices] input[value="mainnet"]').click();
              return {
                selected:window.__mcelWorker.app.readState('state:selected-network'),
                navigation:document.querySelector('[data-value="hub-choice"]').innerText,
                subtitle:document.querySelector('[data-mcel-navigation-subtitle]').textContent,
                walletVisible:!!document.querySelector('.mcel-worker-wallet-compat.is-visible'),
                walletInHub:document.querySelector('.mcel-worker-wallet-compat')
                  .closest('[data-mcel-region]')===document.querySelector('[data-mcel-radio-choices]')
                  .closest('[data-mcel-region]')
              };
            }''')
            assert snapshot=={'selected':'mainnet','navigation':'Hub Choice\nMainnet',
                             'subtitle':'Mainnet','walletVisible':True,'walletInHub':True},snapshot
            page.locator('[data-mcel-radio-choices] input[value="slh"]').click()
            assert page.locator(nav).inner_text()=='Hub Choice\nSLH/SEH'
            assert not page.locator('#worker-multisession-card').is_visible()
            assert errors==[],errors
        finally:
            browser.close()


def test_wallet_bridge_refresh_reconciles_button_after_hub_session_changes():
    """The native UI can select Testnet before the legacy wallet cache catches up.

    A successful backend refresh must re-enable Connect; a mismatched session
    must disable it. Neither state transition performs a wallet RPC or Hub POST.
    """
    pw=pytest.importorskip('playwright.sync_api')
    executable=shutil.which('chromium') or shutil.which('chromium-browser')
    with pw.sync_playwright() as driver:
        browser=driver.chromium.launch(headless=True,
            **({'executable_path':executable} if executable else {}))
        try:
            page=browser.new_page()
            page.evaluate(BACKEND)
            page.evaluate("window.workerState.session='mainnet'")
            page.set_content(render_worker_page(ROOT))
            page.wait_for_function("() => window.__mcelWorker?.app.readState('state:selected-network')==='testnet'")
            page.wait_for_function("() => document.getElementById('worker-wallet-session-context')?.textContent.includes('matching connected')")
            assert page.locator('#worker-connect-wallet').is_disabled()

            # The backend changes from Mainnet to the selected Testnet Hub.
            # The refresh must update the disabled control, not just its cache.
            page.evaluate("window.workerState.session='testnet'")
            page.evaluate("() => window.McelWorkerLegacyMarketplace.refresh()")
            assert 'Marketplace wallet context verified.' in page.locator('#worker-wallet-session-context').inner_text()
            assert page.locator('#worker-connect-wallet').is_enabled()

            # Passive hydration or an in-flight wallet request still wins over
            # a verified Hub context; the refresh must not force-enable it.
            page.evaluate("() => {workerWalletHookState='hydrating';renderWorkerBridgeReadiness()}")
            page.evaluate("() => window.McelWorkerLegacyMarketplace.refresh()")
            assert page.locator('#worker-connect-wallet').is_disabled()
            page.evaluate("() => {workerWalletHookState='idle';renderWorkerBridgeReadiness()}")
            page.evaluate("() => window.McelWorkerLegacyMarketplace.refresh()")
            assert page.locator('#worker-connect-wallet').is_enabled()

            # Conversely, a now-mismatched session must fail closed.
            page.evaluate("window.workerState.session='mainnet'")
            mismatch=page.evaluate("() => window.McelWorkerLegacyMarketplace.refresh().then(()=>'accepted',()=> 'rejected')")
            assert mismatch=='rejected'
            assert page.locator('#worker-connect-wallet').is_disabled()
            assert all(req['method']=='GET' for req in page.evaluate('window.requests'))
        finally:
            browser.close()
