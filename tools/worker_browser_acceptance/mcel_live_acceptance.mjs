// Real native MCEL Worker Testnet gate. No fixture routes, RPC interception,
// provider mocks, extension-store edits, seed import or blanket approvals.
// The official MetaMask disposable profile is created by the established tool.
import {chromium, expect} from '@playwright/test'
import {existsSync, readFileSync, mkdirSync, writeFileSync} from 'node:fs'
import {dirname} from 'node:path'
import {spawnSync} from 'node:child_process'
import {ensureDisposableMetaMaskUnlocked} from './metamask_cached_unlock.mjs'
import {inspectMetaMaskActiveUrl, requireVerifiedMetaMaskUrl} from './metamask_active_url.mjs'
import {clickMcelConfirmed} from './mcel_live_confirmations.mjs'
import {classifyNativeMskInspection} from './mcel_live_msk_inspection.mjs'
import {completeWorkerWalletConnection, installWorkerWalletTrace,
  startWorkerWalletConnection} from './worker_wallet_approvals.mjs'
import {approveWorkerMskSignature} from './metamask_sidepanel_cdp.mjs'
import {captureLiveFailureScreenshots, captureLiveMskMoment, captureLiveOfferMoment} from './mcel_live_failure_screenshots.mjs'
import {waitForWorkerMsk, workerMskEvent, isMskEndpointRequest, isMskRevocationEndpointRequest, readMskApiRejection, classifyMskRevocationRejection, assertServedWorkerMskContract} from './worker_msk_outcome.mjs'
import {classifyWalletConnectFailure, safeWalletConnectState} from './mcel_wallet_connect_diagnostics.mjs'
import {safeWalletProgress, safeWalletLifecycleEvent, safeWalletDiagnosticTimeline, safeWalletExtensionEvidence, safeWalletBackendSession} from './mcel_wallet_connect_evidence.mjs'
import {safeWalletApprovalDiagnostics} from './worker_wallet_approvals.mjs'
import {offerFailureCause, offerApiRejectionCategory, offerApiRejectionDetail,
  assessOfferHubAcceptance, offerDeferralCause} from './worker_offer_live_outcome.mjs'
import {priceOfWorkerOffer, waitForLiveWorker, waitForDeliveredWork,
  readWorkerLiveStatus, executeCaptainWithPriceRetry} from './worker_work_cycle.mjs'

const gate = process.env.MC_WORKER_LIVE_GATE
const allowed = new Set(['wallet-msk','offer-registration','work-cycle','requester-enforcement'])
const url = process.env.MC_WORKER_TEST_URL
const expectedUrl = 'http://127.0.0.1:8765/applications/mcel-semantic-worker'
const reportPath = process.env.MC_WORKER_LIVE_EVIDENCE
const walletPath = process.env.MC_WORKER_TEST_WALLET_FILE
const profile = process.env.MC_WORKER_TEST_PROFILE_DIR
const extension = process.env.MC_WORKER_TEST_EXTENSION_DIR
const markerPath = process.env.MC_WORKER_TEST_PROFILE_MARKER
const auth = name => process.env['MC_WORKER_LIVE_AUTHORIZE_' + name] === 'yes'
const needed = gate === 'work-cycle' ? ['WALLET','OFFER','SPEND','SETTINGS'] :
  gate === 'offer-registration' ? ['WALLET','OFFER'] :
  gate === 'wallet-msk' ? ['WALLET'] : ['SETTINGS']
if (!allowed.has(gate) || url !== expectedUrl || !reportPath ||
    needed.some(name => !auth(name))) throw Error('Unauthorized native Testnet live gate')
const walletGate = gate !== 'requester-enforcement'
let publicWallet = null
let extensionIdForDiagnostics = null
const walletProgress = []
const browserFaults = []
const browserNetworkFailures = []
const progress = phase => {
  const safe=safeWalletProgress(phase)
  if(safe && (walletProgress.length===0 || walletProgress.at(-1)!==safe)){
    walletProgress.push(safe);if(walletProgress.length>64)walletProgress.shift()
  }
}
if (walletGate) {
  if (!walletPath || !profile || !extension || !markerPath ||
      !existsSync(walletPath) || !existsSync(markerPath)) throw Error('Disposable wallet not provisioned')
  // Wallet file is read only inside this local process; its secret never enters
  // evidence, console, HTTP request diagnostics, screenshots or test failures.
  const wallet = JSON.parse(readFileSync(walletPath,'utf8'))
  if (wallet.version !== 1 || !/^0x[0-9a-f]{40}$/i.test(wallet.address) ||
      !wallet.password || !wallet.mnemonic) throw Error('Invalid disposable wallet')
  publicWallet = {address: wallet.address.toLowerCase(),password: wallet.password}
  if (readFileSync(markerPath,'utf8').trim().toLowerCase() !== publicWallet.address)
    throw Error('MetaMask profile has a different disposable account')
}
const proof = {schema:'mcel.worker.native-live-evidence.v1', gate,
  diagnosticVersion:'wallet-connect-v3',mskSigningDiagnosticVersion:'msk-signing-v1',surface:'native-mcel',network:'testnet',ok:false,cleanupOk:false,proofs:{},
  stage:'PREFLIGHT',errorClass:null}
const mark = key => {proof.proofs[key] = true; proof.stage = key}
const sleep = ms => new Promise(resolve => setTimeout(resolve,ms))
async function getJSON(page, path) {
  const result = await page.evaluate(async path => {
    const res = await fetch(path,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}})
    return {http:res.status,body:await res.json().catch(()=>null)}
  },path)
  if (result.http !== 200 || result.body?.ok !== true) throw Error('Live read failed: ' + path)
  return result.body
}
async function waitFor(page, condition, reason, deadlineMs=25000) {
  const expiry=Date.now()+deadlineMs
  while(Date.now()<expiry) {try {if(await condition())return} catch{} await sleep(600)}
  throw Error(reason)
}
async function navigate(page, name) {
  await page.locator(`[data-mcel-purpose="navigation"] [data-value="${name}"]`).click()
  await page.waitForFunction(name=>window.__mcelWorker?.app?.readState('state:worker-view')===name,name)
}
async function saveForm(page, label, postcondition) {
  const intent={'Save Requester Limits':'save-requester',
    'Save Seller Offer':'save-seller','Apply policy':'save-policy'}[label]
  if(!intent)throw Error('Unrecognized live MCEL form operation')
  await clickMcelConfirmed(page,page.getByRole('button',{name:label,exact:true}),intent)
  await waitFor(page,postcondition,'Saved form postcondition missing')
}
async function testRequester(page) {
  // No wallet and no Hub submission. Both checks run against the real local
  // requester policy/decision engine. Backend-owned state must override a
  // client claiming permission to spend.
  await navigate(page,'requester')
  const saved=(await getJSON(page,'/api/applications/worker/settings')).settings
  if (!saved || typeof saved.remoteEnabled !== 'boolean') throw Error('Requester policy unavailable')
  mark('requesterPolicyRead')
  let changed=false
  async function configure(enabled,limit) {
    await page.locator('input[name="remoteDailyLimit"]').fill(String(limit))
    const checkbox=page.locator('input[name="remoteEnabled"]')
    if(enabled)await checkbox.check();else await checkbox.uncheck()
    await saveForm(page,'Save Requester Limits',async()=>{
      const s=(await getJSON(page,'/api/applications/worker/settings')).settings
      return s.remoteEnabled===enabled && s.remoteDailyLimit===limit
    })
  }
  const assess=async() => {
    const result=await page.evaluate(async()=>{
      const res=await fetch('/api/applications/chat-console/ai/remote-overflow/assess',{
        method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({remote_overflow_enabled:true,authorization_granted_by_user:true,
          remote_hub_current_request:true,local_only:false,max_output_tokens:128,
          prompt:'Live functional policy refusal check (no execution requested)'})})
      return {http:res.status,data:await res.json().catch(()=>null)}
    })
    if(result.http!==200 || result.data?.ok!==true)throw Error('Requester assessment unavailable')
    return result.data.remote_overflow||{}
  }
  try {
    changed=true
    await configure(false,0)
    const denied=await assess()
    const explicitDenial=Array.isArray(denied.cards) && denied.cards.some(card=>
      card?.key==='remote_overflow_policy' && card?.status==='blocked' &&
      card?.details?.reason_code==='remote_overflow_disabled')
    if (!explicitDenial || ['remote-hub','remote_hub','remote','submit'].includes(denied.action))
      throw Error('Backend did not explicitly refuse disabled requester spending')
    mark('backendDeniedUnauthorizedSpend')
    // A daily-budget check while remoteEnabled=true could briefly allow
    // concurrent clients to spend. The current backend does not expose a
    // transactionally isolated zero-budget verifier. Do not fabricate that
    // proof or enable paid work just to manufacture a PASS.
    mark('noPaidSubmission')
  } finally {
    if(changed){
      const fields=['remoteEnabled','remoteMode','remoteCreditsPerToken','remoteMaxOutputTokens',
        'remoteDailyLimit','remoteAskBeforeSpend','remoteOnlyWhenBusy']
      for(const name of ['remoteDailyLimit','remoteCreditsPerToken','remoteMaxOutputTokens'])
        await page.locator(`input[name="${name}"]`).fill(String(saved[name]))
      for(const name of ['remoteEnabled','remoteAskBeforeSpend','remoteOnlyWhenBusy']){
        const input=page.locator(`input[name="${name}"]`)
        if(saved[name])await input.check();else await input.uncheck()
      }
      const mode=page.locator('[name="remoteMode"]')
      if(await mode.count())await mode.selectOption(String(saved.remoteMode))
      await saveForm(page,'Save Requester Limits',async()=>{
        const actual=(await getJSON(page,'/api/applications/worker/settings')).settings
        return fields.every(key=>String(actual[key])===String(saved[key]))
      })
    }
  }
}
async function testMarketplace(page,context,extensionId){
  proof.stage='HUB_CHOICE'
  await page.waitForFunction(()=>window.__mcelWorker?.app?.readState('state:destinations')?.length===6)
  if(await page.locator('[data-mcel-radio-choices] input[value="testnet"]').count())
    await page.locator('[data-mcel-radio-choices] input[value="testnet"]').check()
  await page.waitForFunction(()=>window.__mcelWorker?.app?.readState('state:selected-network')==='testnet')
  const choice=await getJSON(page,'/api/applications/worker/mcel-observation')
  if(choice.hubChoice!=='testnet')throw Error('Hub Choice not saved as Testnet')
  proof.stage='HUB_SESSION_CONNECT'
  await navigate(page,'worker')
  const ring=page.locator('input[name="requestedRing"]')
  if((await ring.inputValue())!=='3')await ring.fill('3')
  await clickMcelConfirmed(page,page.getByRole('button',{name:'Update worker ring'}),
    'select-destination',{expectedRing:3})
  await waitFor(page,async()=>{
    const s=(await getJSON(page,'/api/applications/worker/network-session')).session||{}
    return s.selected_network==='testnet' && String(s.requested_ring)==='3' &&
      s.connection_status==='connected'
  },'Real Testnet Hub session not verified')
  // Fail before creating any new Hub MSK if the offer-only gate lacks
  // authorization to enable a disabled seller. The signing permission is
  // not permission to mutate seller policy.
  if(gate==='offer-registration' && !auth('SETTINGS')){
    proof.stage='OFFER_POLICY_PREFLIGHT'
    const settings=(await getJSON(page,'/api/applications/worker/settings')).settings
    proof.offerRegistration={version:'offer-registration-v2',settingsAuthorized:false,
      sellerEnabledBefore:settings.sellerEnabled===true,
      sellerEnabledAtRegistration:null,settingsChanged:false,
      confirmationCompleted:false,requestSeen:false,httpStatus:null,
      transportFailed:false,hubRegistrationStatus:'unknown',workerIdPresent:false,
      networkMatch:false,apiRejection:'none',apiRejectionDetail:'not-msk-specific',failure:'none'}
    if(settings.sellerEnabled!==true)
      throw Error('OFFER_SELLER_DISABLED_SETTINGS_AUTH_REQUIRED')
  }
  await navigate(page,'hub-choice')
  await assertServedWorkerMskContract(page)
  proof.stage='WALLET_PROVIDER_CHECK'
  const isMetaMask=await page.evaluate(()=>Boolean(window.ethereum?.isMetaMask))
  if(!isMetaMask)throw Error('Real MetaMask provider not injected')
  mark('realExtension')
  // Capture the actual control transition sequence. Never retain control text,
  // status text, wallet addresses or provider event payloads.
  await page.evaluate(()=>{
    const panel=document.querySelector('.mcel-worker-wallet-compat')
    const started=performance.now(), timeline=[]
    const statusPhase=text=>{
      if(/^Connect failed:/i.test(text))return 'connect-failed'
      if(/Checking browser wallet network/i.test(text))return 'network-preflight'
      if(/network update/i.test(text))return 'network-update-requested'
      if(/MetaMask RPC ready/i.test(text))return 'rpc-ready'
      if(/RPC backoff|retrying in/i.test(text))return 'rpc-backoff'
      if(/switching (?:wallet|to)|network switch/i.test(text))return 'network-switch-requested'
      if(/Opening browser wallet account request/i.test(text))return 'account-requested'
      if(/Wallet account request accepted/i.test(text))return 'account-approved'
      if(/verifying signer|finalizing/i.test(text))return 'finalizing'
      if(/Connected wallet/i.test(text))return 'connected'
      return 'other'
    }
    let last=''
    const capture=kind=>{
      const btn=panel?.querySelector('#worker-connect-wallet')
      const text=String(btn?.textContent||'').trim()
      const b=/^Connect Wallet$/i.test(text)?'idle':/^Checking Wallet/i.test(text)?'hydrating':
        /^Wallet Open/i.test(text)?'requesting':/^Finalizing/i.test(text)?'finalizing':
        /^Connected$/i.test(text)?'connected':'other'
      const context=String(panel?.querySelector('#worker-wallet-session-context')?.textContent||'')
      const state={button:b,disabled:Boolean(btn?.disabled),busy:btn?.getAttribute('aria-busy')==='true',
        handlerBound:btn?.dataset?.workerBound==='true'||btn?.dataset?.workerWalletBound==='true',
        panelVisible:Boolean(panel?.classList.contains('is-visible')),
        contextVerified:context.includes('Marketplace wallet context verified.'),
        hubChoice:window.__mcelWorker?.app?.readState('state:selected-network'),
        status:statusPhase(String(panel?.querySelector('#worker-save-status')?.textContent||''))}
      const key=JSON.stringify(state)
      if(kind==='mutation'&&last===key)return
      last=key
      timeline.push({kind,elapsedMs:Math.round(performance.now()-started),state})
      if(timeline.length>60)timeline.shift()
    }
    capture('start')
    if(panel)new MutationObserver(()=>capture('mutation')).observe(panel,{
      subtree:true,attributes:true,attributeFilter:['disabled','aria-busy','class','data-worker-bound','data-worker-wallet-bound'],
      childList:true,characterData:true})
    window.__mcWalletConnectTimeline=timeline
    window.__mcWalletConnectCapture=capture
  })
  // The MCEL port owns the Hub choice, while the legacy signer uses its own
  // in-page Worker session cache. The already-established backend session is
  // not proof that this cache has received the same state. Await the existing
  // read-only bridge refresh before attempting the real Connect Wallet click.
  proof.stage='WALLET_CONTEXT_REFRESH'
  await page.evaluate(async()=>{
    if(typeof window.McelWorkerLegacyMarketplace?.refresh!=='function')
      throw Error('MCEL wallet bridge refresh unavailable')
    await window.McelWorkerLegacyMarketplace.refresh()
  })
  proof.stage='WALLET_PANEL_READY'
  await page.locator('.mcel-worker-wallet-compat.is-visible #worker-connect-wallet')
    .waitFor({state:'visible',timeout:15000})
  await installWorkerWalletTrace(page)
  proof.stage='WALLET_CONNECT'
  const connect=await startWorkerWalletConnection(page,{allowAlreadyConnected:true,onProgress:progress})
  if(!connect.alreadyConnected){
    await completeWorkerWalletConnection({context,page,extensionId,
      expectedAddress:publicWallet.address,onProgress:progress})
  }
  proof.stage='WALLET_RPC_VERIFICATION'
  await waitFor(page,async()=>{
    return page.evaluate(async()=>{
      const p=window.ethereum;if(!p)return false
      const accounts=await p.request({method:'eth_accounts'})
      const chain=await p.request({method:'eth_chainId'})
      const block=await p.request({method:'eth_blockNumber'})
      return Array.isArray(accounts)&&accounts[0]?.toLowerCase()===window.__mcelExpectedWallet &&
        chain==='0x28757b1' && /^0x[0-9a-f]+$/i.test(block)
    })
  },'Real wallet, chain or RPC verification failed',45000)
  mark('testnetChain');mark('walletConnected')
  const mskEvents=[]
  let mskApiStatus=null, mskRequestFailed=false
  const mskSigning={events:mskEvents,approvalProgress:[],clicked:false,approved:false,
    hubVerified:false,apiStatus:null,apiRequestFailed:false,buttonBefore:null}
  proof.mskSigning=mskSigning
  proof.mskMoments=[]
  const momentTasks=[]
  const momentSeen=new Set()
  const snapshotMoment=phase=>{
    if(momentSeen.has(phase))return
    if(!['request-start','approval-target','request-failed','revoke-start','revoke-approval-target','revoke-failed'].includes(phase))return
    momentSeen.add(phase)
    // Do not block the provider request or click approval while capturing.
    // We await all started captures before the browser is closed on failure.
    const task=captureLiveMskMoment({context,workerPage:page,reportPath,
      extensionId,gate,phase}).then(data=>proof.mskMoments.push(data),()=>{})
    momentTasks.push(task)
  }
  proof.mskMomentTasks=momentTasks // transient; removed before writing JSON
  const approvalProgress = phase => {
    if(typeof phase!=='string' || !/^msk\.(?:target\.(?:home|approval|none)|inspect\.[a-z-]{1,60}|cdp\.(?:unavailable|target-unavailable))$/.test(phase) &&
        !['metamask.msk-signature','metamask.msk-revocation-signature'].includes(phase))return
    if(phase==='msk.target.approval' || phase==='msk.target.home')snapshotMoment('approval-target')
    const events=mskSigning.approvalProgress
    if(events.at(-1)!==phase){events.push(phase);if(events.length>32)events.shift()}
  }
  page.on('console', msg=>{
    const event=workerMskEvent(msg.text())
    if(event){
      mskEvents.push(event);if(mskEvents.length>32)mskEvents.shift()
      if(event==='request.start')snapshotMoment('request-start')
      if(event==='request.failed')snapshotMoment('request-failed')
      if(event==='revoke.start')snapshotMoment('revoke-start')
      if(event==='revoke.failed')snapshotMoment('revoke-failed')
    }
  })
  page.on('response',response=>{
    if(isMskEndpointRequest(response.request())){
      mskApiStatus=response.status()
      mskSigning.apiStatus=mskApiStatus
    }
  })
  page.on('requestfailed',request=>{
    if(isMskEndpointRequest(request)){
      mskRequestFailed=true
      mskSigning.apiRequestFailed=true
    }
  })
  proof.stage='MSK_STATE_INSPECTION'
  // This read is intentionally non-mutating. The existing disposable wallet may
  // already hold a Hub-issued key from a prior acceptance run; that cannot
  // certify fresh signing and MUST NOT be silently revoked or replaced.
  const inspection=await page.evaluate(()=>{
    const panel=document.querySelector('.mcel-worker-wallet-compat')
    const stateNodes=panel?.querySelectorAll('#worker-multisession-key-state')||[]
    return {
      panelPresent:Boolean(panel),
      panelVisible:Boolean(panel?.classList.contains('is-visible')),
      stateCount:stateNodes.length,
      requestCount:panel?.querySelectorAll('#worker-request-multisession-key').length||0,
      revokeCount:panel?.querySelectorAll('#worker-revoke-multisession-key').length||0,
      stateText:stateNodes.length===1?stateNodes[0].textContent||'':''
    }
  })
  const disposition=classifyNativeMskInspection(inspection)
  // Only the bounded classification is recorded. Never serialize DOM text,
  // key IDs, wallet internals, or the backend MSK response.
  proof.mskInspection=disposition
  if(disposition!=='NO_ACTIVE_KEY' && disposition!=='PREEXISTING_ACTIVE'){
    proof.stage='MSK_'+disposition
    throw Error('Native MCEL MSK state is not verifiably readable')
  }
  const active=disposition==='PREEXISTING_ACTIVE'
  if(active && gate==='wallet-msk'){
    proof.stage='MSK_PREEXISTING_ACTIVE'
    throw Error('A prior MSK is active. Refusing fresh-signing certification without a fresh disposable wallet; no existing key was revoked.')
  }
  if(!active){
    proof.stage='MSK_PRE_SIGN_UNLOCK'
    await ensureDisposableMetaMaskUnlocked({context,extensionId,expectedAddress:publicWallet.address,password:publicWallet.password})
    proof.stage='MSK_SIGNING_CLICK'
    mskSigning.buttonBefore=await page.evaluate(()=>{
      const buttons=document.querySelectorAll('#worker-request-multisession-key')
      const button=buttons.length===1?buttons[0]:null
      return {count:buttons.length,enabled:!!button&&!button.disabled,
        visible:!!button&&button.getClientRects().length>0}
    })
    if(mskSigning.buttonBefore.count!==1 || !mskSigning.buttonBefore.enabled ||
       !mskSigning.buttonBefore.visible)throw Error('MSK_REQUEST_BUTTON_NOT_READY')
    await page.locator('#worker-request-multisession-key').click({timeout:10000})
    mskSigning.clicked=true
    proof.stage='MSK_SIGNING_APPROVAL'
    await approveWorkerMskSignature(context,page,extensionId,publicWallet.address,
      {onDiagnostic:approvalProgress})
    mskSigning.approved=true
    proof.stage='MSK_ISSUANCE_VERIFY'
    await waitForWorkerMsk({readState:()=>page.locator('#worker-multisession-key-state').innerText(),
      readStatus:()=>page.locator('#worker-save-status').innerText(),
      readEvents:()=>mskEvents,readApiStatus:()=>mskApiStatus,
      readApiRequestFailed:()=>mskRequestFailed})
  }
  const keyId=(await page.locator('#worker-multisession-key-id').innerText()).trim()
  if(!keyId||keyId==='—')throw Error('Hub multi-session key not issued')
  if(!/^(?:Active|Active server-side key)$/.test((await page.locator('#worker-multisession-key-state').innerText()).trim()))
    throw Error('Hub multi-session key has no active server state')
  mskSigning.hubVerified=true
  mark('hubMskVerified')
  if(gate==='wallet-msk'){
    // Real server-side revocation is a separate life-cycle acceptance check.
    // We revoke only a newly issued key from the disposable test profile.
    const revocation={version:'msk-revocation-v1',click:false,approval:false,
      requestSeen:false,requestTransportFailed:false,httpStatus:null,localOk:null,
      revoked:null,hubOk:null,uiVerified:false,rejectionCategory:null,rejectionDetail:null}
    proof.mskRevocation=revocation
    proof.stage='MSK_REVOCATION_SIGNING'
    const observedRequest=request=>{
      if(isMskRevocationEndpointRequest(request))revocation.requestSeen=true
    }
    const failedRequest=request=>{
      if(isMskRevocationEndpointRequest(request))revocation.requestTransportFailed=true
    }
    page.on('request',observedRequest)
    page.on('requestfailed',failedRequest)
    // Attach the waiter BEFORE clicking, and settle its rejection immediately;
    // no dangling 60-second promise can hide the original Worker-side failure.
    const revoked=page.waitForResponse(r=>isMskRevocationEndpointRequest(r.request()),
      {timeout:60000}).then(response=>({response}),()=>({timeout:true}))
    try {
      await page.locator('#worker-revoke-multisession-key').click()
      revocation.click=true
      // The production handler refuses missing/contradictory Hub identity
      // before asking MetaMask to sign. Do not misreport that as a missing
      // signature popup after a 60-second approval search.
      proof.stage='MSK_REVOCATION_CONTEXT_VERIFY'
      const revokePreflight=await page.locator('#worker-save-status').innerText()
      if(/^Cannot revoke: the active key (?:cluster differs|has no verified cluster ID)/.test(revokePreflight))
        throw Error('MSK_REVOCATION_CONTEXT_UNVERIFIED')
      proof.stage='MSK_REVOCATION_APPROVAL'
      await approveWorkerMskSignature(context,page,extensionId,publicWallet.address,
        {action:'revoke',expectedKeyId:keyId,onDiagnostic:phase=>{
          approvalProgress(phase)
          if(phase==='msk.target.approval'||phase==='msk.target.home')
            snapshotMoment('revoke-approval-target')
        }})
      revocation.approval=true
      proof.stage='MSK_REVOCATION_RESPONSE_WAIT'
      const outcome=await revoked
      if(!outcome.response)throw Error('MSK_REVOCATION_RESPONSE_MISSING')
      const response=outcome.response
      revocation.httpStatus=response.status()
      proof.stage='MSK_REVOCATION_HTTP_VERIFY'
      if(!response.ok()){
        const rejection=await readMskApiRejection(response)
        revocation.rejectionCategory=classifyMskRevocationRejection(rejection)
        // readMskApiRejection strips signatures, wallet addresses, tokens and URLs.
        // Preserve that bounded failure reason: HTTP 400 alone is not diagnostic.
        revocation.rejectionDetail=String(rejection?.reason || 'unavailable').slice(0,300)
        throw Error('MSK_REVOCATION_HTTP_REJECTED')
      }
      // Successful response is inspected for booleans only. Key IDs and
      // signing payloads must not enter the evidence or console.
      const result=await response.json().catch(()=>null)
      revocation.localOk=result?.ok===true
      revocation.revoked=result?.revoked===true
      revocation.hubOk=result?.hub_revoke?.ok===true
      proof.stage='MSK_REVOCATION_HUB_VERIFY'
      if(!revocation.localOk || !revocation.revoked || !revocation.hubOk)
        throw Error('MSK_REVOCATION_HUB_UNVERIFIED')
      proof.stage='MSK_REVOCATION_STATE_VERIFY'
      await waitFor(page,async()=>{
        const status=(await page.locator('#worker-multisession-key-state').innerText()).trim()
        const id=(await page.locator('#worker-multisession-key-id').innerText()).trim()
        return status==='No active key' && id!==keyId
      },'MSK_REVOCATION_UI_STILL_ACTIVE',30000)
      revocation.uiVerified=true
      mark('hubMskRevoked')
    } finally {
      page.off('request',observedRequest)
      page.off('requestfailed',failedRequest)
    }
    return
  }
  await navigate(page,'worker')
  const offer={version:'offer-registration-v2',settingsAuthorized:auth('SETTINGS'),
    sellerEnabledBefore:null,sellerEnabledAtRegistration:null,settingsChanged:false,
    button:null,confirmationCompleted:false,requestSeen:false,httpStatus:null,
    transportFailed:false,hubRegistrationStatus:'unknown',workerIdPresent:false,
    networkMatch:false,apiRejection:'none',failure:'none',syncStatus:'unknown',
    deferredReason:'none',hubReceiptVerified:false,workerIdentityMatched:false,
    hubUrlMatched:false,assignedRingMatched:false,backendHubRegistered:false}
  proof.offerRegistration=offer
  proof.offerMoments=[]
  const offerMomentTasks=[]
  proof.offerMomentTasks=offerMomentTasks
  const offerMomentSeen=new Set()
  const snapshotOfferMoment=phase=>{
    if(offerMomentSeen.has(phase))return
    offerMomentSeen.add(phase)
    offerMomentTasks.push(captureLiveOfferMoment({context,workerPage:page,reportPath,
      extensionId,gate,phase}).then(data=>proof.offerMoments.push(data),()=>{}))
  }
  const offerResponses=[]
  const onOfferRequest=request=>{
    try{if(new URL(request.url()).pathname==='/api/applications/worker/offer-sync' &&
        request.method()==='POST')offer.requestSeen=true}catch{}
  }
  const onOfferResponse=response=>{
    try{if(new URL(response.url()).pathname==='/api/applications/worker/offer-sync' &&
        response.request().method()==='POST'){
      offer.requestSeen=true;offer.httpStatus=response.status();offerResponses.push(response)
      if(response.status()>=400)snapshotOfferMoment('offer-http-failed')
    }}catch{}
  }
  const onOfferRequestFailed=request=>{
    try{if(new URL(request.url()).pathname==='/api/applications/worker/offer-sync' &&
        request.method()==='POST')offer.transportFailed=true}catch{}
  }
  page.on('request',onOfferRequest)
  page.on('response',onOfferResponse)
  page.on('requestfailed',onOfferRequestFailed)
  let sellerBefore=null
  let changedSeller=false
  let startedWork=false
  let cleanupFailure=null
  try {
    proof.stage='OFFER_POLICY_PREFLIGHT'
    sellerBefore=(await getJSON(page,'/api/applications/worker/settings')).settings
    offer.sellerEnabledBefore=sellerBefore.sellerEnabled===true
    if(gate==='work-cycle'){
      if(!auth('SETTINGS'))throw Error('Marketplace setting changes not authorized')
      const tokens=Math.min(Number(sellerBefore.sellerTargetTokens||0),512)
      if(!Number.isInteger(tokens)||tokens<1)throw Error('Invalid original seller token count')
      if(sellerBefore.sellerEnabled!==true || tokens!==Number(sellerBefore.sellerTargetTokens)){
        changedSeller=true
        if(tokens!==Number(sellerBefore.sellerTargetTokens))
          await page.locator('input[name="sellerTargetTokens"]').fill(String(tokens))
        await saveForm(page,'Save Seller Offer',async()=>
          (await getJSON(page,'/api/applications/worker/settings')).settings.sellerTargetTokens===tokens)
        if(sellerBefore.sellerEnabled!==true){
          const check=page.locator('input[name="sellerEnabled"]')
          await check.check()
          await saveForm(page,'Apply policy',async()=>
            (await getJSON(page,'/api/applications/worker/settings')).settings.sellerEnabled===true)
        }
      }
    } else if(sellerBefore.sellerEnabled!==true){
      // Offer signing alone is not consent to change seller policy. An explicit
      // --authorize-settings-change may temporarily enable selling, after
      // which the original setting must be restored in finally.
      if(!auth('SETTINGS'))throw Error('OFFER_SELLER_DISABLED_SETTINGS_AUTH_REQUIRED')
      changedSeller=true
      await page.locator('input[name="sellerEnabled"]').check()
      await saveForm(page,'Apply policy',async()=>
        (await getJSON(page,'/api/applications/worker/settings')).settings.sellerEnabled===true)
    }
    offer.settingsChanged=changedSeller
    offer.sellerEnabledAtRegistration=(await getJSON(page,'/api/applications/worker/settings')).settings.sellerEnabled===true
    if(!offer.sellerEnabledAtRegistration)throw Error('OFFER_SELLER_DISABLED_SETTINGS_AUTH_REQUIRED')
    // This invokes the actual native button -> typed MCEL port -> original
    // authenticated registration signer. No direct Hub registration shortcut.
    proof.stage='OFFER_CONFIRMATION'
    offer.button=await page.locator('button[data-intent="intent:register-offer"]').evaluateAll(buttons=>({
      count:buttons.length,enabled:buttons.length===1&&!buttons[0].disabled,
      visible:buttons.length===1&&buttons[0].getClientRects().length>0}))
    if(offer.button.count!==1 || !offer.button.enabled || !offer.button.visible)
      throw Error('OFFER_REGISTRATION_BUTTON_NOT_READY')
    await clickMcelConfirmed(page,page.locator('button[data-intent="intent:register-offer"]'),'register-offer')
    offer.confirmationCompleted=true
    snapshotOfferMoment('offer-confirmed')
    proof.stage='OFFER_HUB_ACCEPTANCE_VERIFY'
    let accepted=false
    let sync=null
    const deadline=Date.now()+60000
    while(Date.now()<deadline){
      if(offer.httpStatus!==null && offer.httpStatus>=400)throw Error('OFFER_SYNC_HTTP_REJECTED')
      if(offer.transportFailed)throw Error('OFFER_SYNC_TRANSPORT_FAILED')
      // Read the actual application-level response; HTTP 200 can mean
      // "deferred", not that the Hub accepted anything.
      if(!sync && offerResponses.length){
        sync=await offerResponses.at(-1).json().catch(()=>null)
        offer.syncStatus=['accepted','deferred','failed','pending','unchanged'].includes(sync?.status)
          ? sync.status : 'unknown'
        if(offer.syncStatus==='deferred'){
          offer.deferredReason=offerDeferralCause(sync?.reason)
          snapshotOfferMoment('offer-hub-failed')
          throw Error('OFFER_SYNC_DEFERRED')
        }
        if(offer.syncStatus==='failed'){
          snapshotOfferMoment('offer-hub-failed')
          throw Error('OFFER_HUB_REGISTRATION_FAILED')
        }
        if(offer.syncStatus!=='accepted')throw Error('OFFER_SYNC_NOT_ACCEPTED')
      }
      const s=(await getJSON(page,'/api/applications/worker/network-session')).session||{}
      const result=assessOfferHubAcceptance(s,sync)
      offer.networkMatch=result.networkMatch
      offer.hubRegistrationStatus=result.hubStatus
      offer.workerIdPresent=result.serverWorkerIdPresent
      offer.hubReceiptVerified=result.receiptVerified
      offer.workerIdentityMatched=result.workerMatch
      offer.hubUrlMatched=result.hubMatch
      offer.assignedRingMatched=result.ringMatch
      offer.backendHubRegistered=result.registered
      if(result.hubStatus==='failed'){
        snapshotOfferMoment('offer-hub-failed')
        throw Error('OFFER_HUB_REGISTRATION_FAILED')
      }
      if(result.verified){accepted=true;break}
      await sleep(600)
    }
    if(!accepted)throw Error('OFFER_POSTCONDITION_UNVERIFIED')
    mark('hubOfferAccepted')
    if(gate==='offer-registration')return
    const saved=(await getJSON(page,'/api/applications/worker/settings')).settings
    const price=priceOfWorkerOffer({targetTokens:saved.sellerTargetTokens,creditPerToken:saved.sellerCreditsPerToken})
    if(price.priceCeilingCredits>2)throw Error('Price exceeds Testnet credit ceiling')
    const workerId=(await getJSON(page,'/api/applications/worker/network-session')).session.worker_id
    // Mark cleanup necessary before sending the effect: a server may apply
    // the override before the browser sees its confirmation.
    startedWork=true
    proof.stage='WORK_NOW_START'
    await page.locator('input[name="minutes"]').fill('15')
    await clickMcelConfirmed(page,page.getByRole('button',{name:'Start Work Now'}),'work-now')
    await waitFor(page,async()=>{
      const status=await getJSON(page,'/api/applications/worker/runtime-status')
      const exp=status.workNowOverride?.expiresAt||status.runtime?.workNowOverride?.expiresAt
      return Boolean(exp)&&Date.parse(exp)>Date.now()
    },'Work Now did not receive verified server override',60000)
    await waitForLiveWorker({page,workerId,expectedPriceWei:price.requiredCreditWei,timeoutMs:60000})
    mark('hubSessionLive')
    // Read-only Captain funding preflight is mandatory before spending.
    proof.stage='CAPTAIN_FUNDING_PREFLIGHT'
    const preflight=spawnSync(process.env.MC_WORKER_TEST_PYTHON||'python',[
      '-m','tools.worker_captain_funding_preflight','--required-credit-wei',price.requiredCreditWei,
      '--ceiling-credits',String(price.priceCeilingCredits)],
      {cwd:process.env.MC_WORKER_TEST_ROOT,encoding:'utf8',timeout:30000,maxBuffer:65536})
    let funding=null
    try{funding=JSON.parse(String(preflight.stdout||'').trim())}catch{}
    if(preflight.status!==0||funding?.ok!==true||funding?.fundable!==true)
      throw Error('Captain funding preflight did not verify enough bounded Testnet credits')
    // Never refill the treasury or run a second price retry implicitly.
    proof.stage='CAPTAIN_EXECUTION'
    const receipt=await executeCaptainWithPriceRetry({
      python:process.env.MC_WORKER_TEST_PYTHON||'python',cwd:process.env.MC_WORKER_TEST_ROOT,
      model:String(saved.models||'').split(',')[0].trim(),
      priceCeilingCredits:price.priceCeilingCredits,
      bridgeFundingRequired:Boolean(funding.bridgeFundingRequired),
    })
    if(receipt.state!=='completed')throw Error('Testnet Captain request did not complete')
    mark('captainCompleted')
    await waitForDeliveredWork({page,workerId,captain:receipt,timeoutMs:60000})
    mark('workerDelivered')
  } catch(error){
    // Resolve the observed local offer-sync rejection before browser cleanup.
    // Only store a categorical reason, never raw Hub text or signed payloads.
    if(offerResponses.length){
      const last=offerResponses.at(-1)
      const body=await last.json().catch(()=>null)
      if(!['accepted','deferred','failed','pending','unchanged'].includes(offer.syncStatus))
        offer.syncStatus=['accepted','deferred','failed','pending','unchanged'].includes(body?.status)
          ? body.status : 'unknown'
      // An HTTP 200 deferral is not an API rejection. Report the saved policy
      // reason separately; do not mislabel it as an unknown Hub failure.
      if(last.status()>=400 || body?.ok===false){
        offer.apiRejection=offerApiRejectionCategory(body)
        offer.apiRejectionDetail=offerApiRejectionDetail(body)
      }
      if(offer.syncStatus==='deferred' && offer.deferredReason==='none')
        offer.deferredReason=offerDeferralCause(body?.reason)
    }
    offer.failure=offerFailureCause(error?.message,offer)
    throw error
  } finally {
    page.off('request',onOfferRequest)
    page.off('response',onOfferResponse)
    page.off('requestfailed',onOfferRequestFailed)
    if(startedWork){
      try{
        await clickMcelConfirmed(page,page.locator('button[data-intent="intent:finish-work-now"]'),'finish-work-now')
        await waitFor(page,async()=>{
          const status=await getJSON(page,'/api/applications/worker/runtime-status')
          const exp=status.workNowOverride?.expiresAt||status.runtime?.workNowOverride?.expiresAt
          return !exp || Date.parse(exp)<=Date.now()
        },'Work Now cleanup postcondition missing',60000)
      }catch{cleanupFailure='finish-unverified'}
    }
    if(changedSeller && sellerBefore){
      try{
        await page.locator('input[name="sellerTargetTokens"]').fill(String(sellerBefore.sellerTargetTokens))
        await saveForm(page,'Save Seller Offer',async()=>
          (await getJSON(page,'/api/applications/worker/settings')).settings.sellerTargetTokens===sellerBefore.sellerTargetTokens)
        const check=page.locator('input[name="sellerEnabled"]')
        if(sellerBefore.sellerEnabled)await check.check();else await check.uncheck()
        await saveForm(page,'Apply policy',async()=>
          (await getJSON(page,'/api/applications/worker/settings')).settings.sellerEnabled===sellerBefore.sellerEnabled)
      }catch{cleanupFailure='seller-restore-unverified'}
    }
    if(cleanupFailure)throw Error(cleanupFailure)
  }
}
async function captureBackendSession(page){
  if(!page || page.isClosed())return safeWalletBackendSession()
  try{
    return safeWalletBackendSession(await page.evaluate(async()=>{
      const r=await fetch('/api/applications/worker/network-session',{
        credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}})
      const json=await r.json().catch(()=>null)
      const s=json?.session||{}
      return {httpOk:r.status===200&&json?.ok===true,
        selectedNetwork:s.selected_network,status:s.connection_status,ring:s.requested_ring}
    }))
  }catch{return safeWalletBackendSession()}
}
async function captureWalletTimeline(page){
  if(!page || page.isClosed())return []
  try{return safeWalletDiagnosticTimeline(await page.evaluate(()=>{
    if(typeof window.__mcWalletConnectCapture==='function')window.__mcWalletConnectCapture('end')
    const raw=window.__mcWalletConnectTimeline
    return Array.isArray(raw)?raw.slice(-60):[]
  }))}catch{return []}
}
async function captureConnectDiagnostics(page) {
  if(!page || page.isClosed()) return safeWalletConnectState()
  try {
    return safeWalletConnectState(await page.evaluate(()=>{
      const panel=document.querySelector('.mcel-worker-wallet-compat')
      const button=panel?.querySelector('#worker-connect-wallet')
      const label=String(button?.textContent||'').trim()
      const states={'Connect Wallet':'idle','Connected':'connected'}
      const buttonState=states[label] || (/^Checking Wallet/i.test(label)?'hydrating':
        /^Wallet Open/i.test(label)?'requesting':/^Finalizing/i.test(label)?'finalizing':'other')
      return {panelVisible:Boolean(panel?.classList.contains('is-visible')),
        buttonPresent:Boolean(button), handlerBound:button?.dataset?.workerBound==='true'||
          button?.dataset?.workerWalletBound==='true', disabled:Boolean(button?.disabled),
        busy:button?.getAttribute('aria-busy')==='true',buttonState,
        hubChoice:window.__mcelWorker?.app?.readState('state:selected-network')}
    }))
  } catch {return safeWalletConnectState()}
}
async function main(){
  let context=null, workerPage=null
  try{
    proof.stage='BROWSER_LAUNCH'
    context=await chromium.launchPersistentContext(profile||'',{
      channel:'chromium',headless:false,
      args:walletGate?[`--disable-extensions-except=${extension}`,`--load-extension=${extension}`]:[],
      viewport:{width:1440,height:920}})
    const page=await context.newPage()
    workerPage=page
    // Collect only fixed fault classes and endpoint categories. Raw browser
    // exception strings, console messages, request URLs and bodies are secret-
    // bearing and MUST NOT enter diagnostics or reports.
    page.on('pageerror',error=>{
      const name=error?.name
      browserFaults.push(['TypeError','ReferenceError','SyntaxError','RangeError','Error'].includes(name)
        ?name:'OtherError')
      if(browserFaults.length>20)browserFaults.shift()
    })
    const requestKind=request=>{
      try{
        const link=new URL(request.url())
        if(link.origin!=='http://127.0.0.1:8765')return null
        if(link.pathname.startsWith('/api/applications/worker/'))return 'worker-api'
        if(link.pathname.startsWith('/applications/'))return 'worker-app'
        return null
      }catch{return null}
    }
    page.on('requestfailed',request=>{
      const kind=requestKind(request)
      if(kind)browserNetworkFailures.push(kind+':transport')
      if(browserNetworkFailures.length>20)browserNetworkFailures.shift()
    })
    page.on('response',response=>{
      const kind=requestKind(response.request())
      const status=response.status()
      if(kind&&status>=400)browserNetworkFailures.push(kind+(status<500?':4xx':':5xx'))
      if(browserNetworkFailures.length>20)browserNetworkFailures.shift()
    })
    proof.stage='NATIVE_ROUTE_LOAD'
    await page.addInitScript(()=>{
      const trace=[],early=[]
      Object.defineProperty(window,'__mcWorkerWalletTrace',{value:trace,configurable:false})
      Object.defineProperty(window,'__mcWorkerEarlyEvents',{value:early,configurable:false})
      window.addEventListener('main-computer-worker-wallet',event=>{
        const type=event?.detail?.type
        if(typeof type!=='string'||! /^(?:connect|provider\.(?:hydrate|refresh|accountsChanged|chainChanged))\.[A-Za-z0-9_.-]{1,96}$/.test(type))return
        early.push(type);if(early.length>64)early.shift()
        if(type.startsWith('connect.')){trace.push(type);if(trace.length>96)trace.shift()}
      })
    })
    await page.goto(url,{waitUntil:'domcontentloaded'})
    await page.waitForFunction(()=>window.__mcelWorker?.app?.readState('state:destinations')?.length===6,null,{timeout:45000})
    if(page.url()!==expectedUrl || !(await page.locator('[data-mcel-radio-choices]').count()))
      throw Error('Native MCEL Worker did not mount')
    proof.stage='NATIVE_MCEL_MOUNTED'
    if(gate==='requester-enforcement') await testRequester(page)
    else{
      proof.stage='EXTENSION_DISCOVERY'
      const workers=context.serviceWorkers()
      const extensionWorker=workers.find(w=>w.url().startsWith('chrome-extension://'))||
        await context.waitForEvent('serviceworker',{timeout:45000})
      const extensionId=new URL(extensionWorker.url()).host
      extensionIdForDiagnostics=extensionId
      if(!/^[a-p]{32}$/.test(extensionId))throw Error('Unverified MetaMask extension ID')
      // A cached profile may reopen with MetaMask locked even when it was
      // verified as unlocked by cache-wallet. Unlock BEFORE Hub changes or
      // wallet connection; the existing second check guards MSK signing.
      proof.stage='METAMASK_PRECONNECT_UNLOCK'
      // Playwright can start with the extension service worker but no
      // MetaMask home tab. Open only the verified extension's own home page
      // so the existing unlock helper can prove the public account identity.
      if (!context.pages().some(p => p.url().startsWith(`chrome-extension://${extensionId}/home.html`))) {
        const home = await context.newPage()
        await home.goto(`chrome-extension://${extensionId}/home.html`,
          {waitUntil:'domcontentloaded',timeout:20000})
      }
      // Inspect the exact selected extension route before unlock. A locked
      // disposable wallet legitimately starts at #/onboarding/unlock;
      // foreground/visibility remain diagnostics rather than unlock gates.
      const before=await inspectMetaMaskActiveUrl(context,extensionId,publicWallet.address,{activate:true})
      proof.metaMaskPreconnectUrl=before.evidence
      proof.stage='METAMASK_PRECONNECT_URL_CHECK'
      requireVerifiedMetaMaskUrl(before.evidence)
      // An unlocked wallet home for the wrong disposable account is a wrong
      // browser profile, not an unlock prompt. Refuse rather than retrying it.
      if(before.evidence.selectedScreen==='wallet-home' &&
         !before.evidence.walletIdentityMatched)
        throw Error('METAMASK_HOME_ACCOUNT_MISMATCH')
      proof.stage='METAMASK_PRECONNECT_UNLOCK'
      proof.metaMaskUnlockProgress=[]
      await ensureDisposableMetaMaskUnlocked({context,extensionId,
        expectedAddress:publicWallet.address,password:publicWallet.password,
        onProgress: event=>{
          if(event==='metamask.disposable-profile-unlock-submitted')
            proof.metaMaskUnlockProgress.push('unlock-submitted')
          else if(event==='metamask.disposable-profile-identity-verified')
            proof.metaMaskUnlockProgress.push('identity-verified')
        }})
      const after=await inspectMetaMaskActiveUrl(context,extensionId,publicWallet.address,{activate:true})
      proof.metaMaskPreconnectAfterUnlock=after.evidence
      proof.stage='METAMASK_PRECONNECT_HOME_URL_CHECK'
      requireVerifiedMetaMaskUrl(after.evidence,{requireHome:true})
      // The *real selected MetaMask target* must now show the wallet home
      // and the exact disposable public account. OS foreground status is
      // advisory: it can remain hidden even if the UI was unlocked.
      // Return the DApp to front before operating Connect Wallet.
      await page.bringToFront()
      proof.stage='WORKER_ACTIVE_URL_CHECK'
      const workerCurrentUrl=page.url()
      const workerVisibility=await page.evaluate(()=>document.visibilityState)
      proof.workerActiveUrl={expectedUrl,
        actualUrl:workerCurrentUrl===expectedUrl?workerCurrentUrl:'unverified',
        routeMatched:workerCurrentUrl===expectedUrl,
        visible:workerVisibility==='visible'}
      // The target route and mounted UI establish the correct DApp page.
      // Visibility alone is not an authorization or page identity signal.
      if(!proof.workerActiveUrl.routeMatched)
        throw Error('WORKER_ACTIVE_URL_UNVERIFIED')
      proof.stage='METAMASK_PRECONNECT_UNLOCKED'
      await page.evaluate(address=>{window.__mcelExpectedWallet=address},publicWallet.address)
      await testMarketplace(page,context,extensionId)
    }
    proof.ok=true
    proof.verifiedAt = new Date().toISOString()
  }catch(error){
    // This includes early policy refusals raised before issuing an MSK.
    if(proof.offerRegistration?.version==='offer-registration-v2' &&
       proof.offerRegistration.failure==='none')
      proof.offerRegistration.failure=offerFailureCause(error?.message,proof.offerRegistration)
    // Finish transient event-time images before closing Chromium. In-flight
    // screenshots are NOT the post-failure screenshots taken below.
    if(Array.isArray(proof.mskMomentTasks)){
      await Promise.allSettled(proof.mskMomentTasks)
    }
    if(Array.isArray(proof.offerMomentTasks))await Promise.allSettled(proof.offerMomentTasks)
    // Never serialize raw message text, which might include sensitive contents
    // of a provider request, signed operation or logged HTTP response.
    proof.errorClass=error instanceof Error ? error.name : 'UnknownError'
    // Preserve what was on screen BEFORE context.close(), including transient
    // MetaMask approvals that disappear when the browser is torn down.
    proof.failureScreenshots=await captureLiveFailureScreenshots({
      context,workerPage,reportPath,extensionId:extensionIdForDiagnostics,gate,
    })
    if(proof.stage.startsWith('METAMASK_PRECONNECT') || proof.stage==='WORKER_ACTIVE_URL_CHECK') {
      // Keep a post-failure snapshot even when the preconnect helper times out.
      // Do not emit raw browser errors, address text or extension DOM.
      if(context && extensionIdForDiagnostics && publicWallet) {
        try { proof.metaMaskPreconnectFailure=(await inspectMetaMaskActiveUrl(
          context,extensionIdForDiagnostics,publicWallet.address)).evidence }
        catch { proof.metaMaskPreconnectFailure={selectedRoute:'inspection-unavailable'} }
      }
      const reason=String(error?.message||'')
      const fixedReasons=[
        'METAMASK_ACTIVE_URL_UNVERIFIED','METAMASK_HOME_IDENTITY_UNVERIFIED',
        'METAMASK_HOME_ACCOUNT_MISMATCH','METAMASK_FOREGROUND_UNVERIFIED',
        'WORKER_ACTIVE_URL_UNVERIFIED',
      ]
      proof.metaMaskPreconnectFailureReason=fixedReasons.includes(reason)?reason:
        reason.startsWith('Cached disposable MetaMask unlock incomplete (')
          ?'METAMASK_UNLOCK_INCOMPLETE':
        reason==='Cached disposable MetaMask unlock refused: Unlock remains disabled'
          ?'METAMASK_UNLOCK_BUTTON_DISABLED':
        reason==='Cached disposable MetaMask unlock refused: incorrect test password'
          ?'METAMASK_UNLOCK_PASSWORD_REJECTED':'METAMASK_UNLOCK_OR_IDENTITY_UNVERIFIED'
      // Extract only a tiny, fixed allowlist of helper state identifiers.
      // Raw Playwright errors can contain wallet secrets and are never emitted.
      const state=reason.match(/^Cached disposable MetaMask unlock incomplete \(([a-z-]+)\); no wallet profile certified$/)?.[1]
      if(['no-extension-home','wallet-home-not-verified',
          'unlock-field-transitioning','unlock-page-transitioning',
          'unlock-submit-transitioning','unlock-submitted-awaiting-verified-home'].includes(state))
        proof.metaMaskUnlockLastState=state
    }
    if(proof.stage.startsWith('MSK_') && workerPage) {
      const reason=String(error?.message||'')
      const approval=reason.match(/^MetaMask MSK signature approval refused or unavailable \(([a-z-]{1,60})\)/)
      if(proof.mskRevocation && typeof proof.mskRevocation==='object'){
        const known={
          MSK_REVOCATION_CONTEXT_UNVERIFIED:'context-unverified',
          MSK_REVOCATION_RESPONSE_MISSING:'response-missing',
          MSK_REVOCATION_HTTP_REJECTED:'http-rejected',
          MSK_REVOCATION_HUB_UNVERIFIED:'hub-unverified',
          MSK_REVOCATION_UI_STILL_ACTIVE:'ui-still-active',
        }
        proof.mskRevocation.failure=known[reason] ||
          (proof.stage==='MSK_REVOCATION_APPROVAL'?'approval-failed':
           proof.stage==='MSK_REVOCATION_SIGNING'?'click-failed':'unclassified')
        try{
          proof.mskRevocation.uiState=await workerPage.evaluate(()=>{
            const state=String(document.querySelector('#worker-multisession-key-state')?.textContent||'').trim()
            const button=document.querySelector('#worker-revoke-multisession-key')
            const text=String(document.querySelector('#worker-save-status')?.textContent||'')
            const outcome=/Failed to revoke multi-session key/i.test(text)?'worker-revoke-failed':
              /revoked in the local backend and Hub/i.test(text)?'worker-revoked':
              /sign|wallet/i.test(text)?'wallet-state':'other'
            return {keyActive:/^Active(?: server-side key)?$/.test(state),
              keyInactive:state==='No active key',buttonDisabled:button?.disabled===true,
              workerOutcome:outcome}
          })
        }catch{proof.mskRevocation.uiState={workerOutcome:'inspection-unavailable'}}
      }
      proof.mskFailureCode=reason==='MSK_REQUEST_BUTTON_NOT_READY'
        ?'request-button-not-ready':approval ?'signature-'+approval[1]:
        reason.startsWith('MSK signature refused: untrusted')?'signature-origin-refused':
        proof.stage==='MSK_SIGNING_CLICK'?'request-click-failed':
        proof.stage==='MSK_ISSUANCE_VERIFY'?'hub-issuance-unverified':'signing-unclassified'
      if(proof.mskRevocation && typeof proof.mskRevocation.failure==='string')
        proof.mskFailureCode='revocation-'+proof.mskRevocation.failure
      try {
        proof.mskUiState=await workerPage.evaluate(()=>{
          const button=document.querySelector('#worker-request-multisession-key')
          const status=String(document.querySelector('#worker-save-status')?.textContent||'')
          const category=/Waiting for wallet signature/i.test(status)?'awaiting-signature':
            /Requesting multi-session key from local app/i.test(status)?'hub-request-pending':
            /Multi-session key request failed/i.test(status)?'request-failed':
            /Hub issued multi-session key/i.test(status)?'issued':
            /Wallet signing library is not loaded/i.test(status)?'signer-missing':
            /Revoke the active multi-session key/i.test(status)?'active-key-blocked':
            /Connect a Worker wallet/i.test(status)?'wallet-not-connected':'other'
          return {buttonPresent:!!button,buttonDisabled:button?!!button.disabled:null,
            status:category}
        })
      } catch {proof.mskUiState={status:'inspection-unavailable'}}
    }
    if(['WALLET_CONTEXT_REFRESH','WALLET_PANEL_READY','WALLET_CONNECT'].includes(proof.stage)){
      proof.walletConnectFailure=proof.stage==='WALLET_PANEL_READY'
        ? 'WALLET_PANEL_HIDDEN' : classifyWalletConnectFailure(error?.message)
      proof.walletConnectState=await captureConnectDiagnostics(workerPage)
      proof.walletConnectEvents=(await readWorkerWalletTrace(workerPage)).filter(safeWalletLifecycleEvent).slice(-24)
      proof.walletConnectProgress=walletProgress.slice(-32)
      proof.walletBrowserFaults=browserFaults.slice(-20)
      proof.walletBrowserNetworkFailures=browserNetworkFailures.slice(-20)
      proof.walletConnectTimeline=await captureWalletTimeline(workerPage)
      proof.walletBackendSession=await captureBackendSession(workerPage)
      try {
        const early=await workerPage?.evaluate(()=>Array.isArray(window.__mcWorkerEarlyEvents)
          ?window.__mcWorkerEarlyEvents.slice(-32):[])
        proof.walletEarlyEvents=Array.isArray(early)
          ?early.filter(safeWalletLifecycleEvent):[]
      } catch {proof.walletEarlyEvents=[]}
      if(context && extensionIdForDiagnostics){
        try{
          const extension=await Promise.race([
            safeWalletApprovalDiagnostics(context,extensionIdForDiagnostics,workerPage),
            sleep(7000).then(()=>null)
          ])
          proof.walletExtension=extension?safeWalletExtensionEvidence(extension):safeWalletExtensionEvidence()
        }catch{proof.walletExtension=safeWalletExtensionEvidence()}
      }
    }
  }finally{
    // Avoid JSON serialization of promises or callbacks. Some failure paths
    // already awaited these captures; success paths must also settle safely.
    if(Array.isArray(proof.mskMomentTasks))await Promise.allSettled(proof.mskMomentTasks)
    if(Array.isArray(proof.offerMomentTasks))await Promise.allSettled(proof.offerMomentTasks)
    delete proof.mskMomentTasks
    delete proof.offerMomentTasks
    try {await context?.close();proof.cleanupOk=true} catch {proof.cleanupOk=false}
    mkdirSync(dirname(reportPath),{recursive:true})
    writeFileSync(reportPath,JSON.stringify(proof,null,2)+'\n',{mode:0o600})
  }
  if (!proof.ok || !proof.cleanupOk)process.exitCode=1
}
main().catch(()=>{process.exitCode=1})
