// Real native MCEL Worker Testnet gate. No fixture routes, RPC interception,
// provider mocks, extension-store edits, seed import or blanket approvals.
// The official MetaMask disposable profile is created by the established tool.
import {chromium, expect} from '@playwright/test'
import {existsSync, readFileSync, mkdirSync, writeFileSync} from 'node:fs'
import {dirname,join} from 'node:path'
import {spawnSync} from 'node:child_process'
import {ensureDisposableMetaMaskUnlocked} from './metamask_cached_unlock.mjs'
import {inspectMetaMaskActiveUrl, requireVerifiedMetaMaskUrl, waitForVerifiedMetaMaskPreconnect} from './metamask_active_url.mjs'
import {clickMcelConfirmed} from './mcel_live_confirmations.mjs'
import {saveSellerOfferAfterRefresh} from './worker_offer_form_refresh.mjs'
import {createWorkerSettingsWriteTrace} from './worker_settings_write_trace.mjs'
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
import {planOfferAcceptancePolicy} from './worker_offer_live_policy.mjs'
import {waitForFreshOfferRequest,waitForFreshOfferResponse,createOfferSyncTracker} from './worker_offer_request_wait.mjs'
import {sellerPolicyReceipt,readSellerPolicyReceipt,writeSellerPolicyReceipt,clearSellerPolicyReceipt,
  sellerPolicyMatches,enforceSellerPolicy} from './worker_offer_policy_recovery.mjs'
import {priceOfWorkerOffer, waitForLiveWorker, waitForDeliveredWork,
  readWorkerLiveStatus, executeCaptainWithPriceRetry, captainFundingPreflightEvidence,
  boundedSmokeOfferRate} from './worker_work_cycle.mjs'
import {classifyRequesterPolicyRead} from './requester_policy_read.mjs'
import {ORIGIN,authenticateContext,revokeContext} from './secure_functional_auth.mjs'
import {mountCanonicalWorkerSurface} from './canonical_worker_surface.mjs'

const gate = process.env.MC_WORKER_LIVE_GATE
const allowed = new Set(['wallet-msk','offer-registration','work-cycle','requester-enforcement'])
const url = process.env.MC_WORKER_TEST_URL
const expectedUrl = ORIGIN + '/applications/worker'
const reportPath = process.env.MC_WORKER_LIVE_EVIDENCE
const walletPath = process.env.MC_WORKER_TEST_WALLET_FILE
const profile = process.env.MC_WORKER_TEST_PROFILE_DIR
const extension = process.env.MC_WORKER_TEST_EXTENSION_DIR
const markerPath = process.env.MC_WORKER_TEST_PROFILE_MARKER
const policyRecoveryPath=join(dirname(reportPath||'.'),'live-worker-offer-policy-recovery.json')
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
async function saveForm(page, label, postcondition, confirmationOptions={}) {
  const intent={'Save Requester Limits':'save-requester',
    'Save Seller Offer':'save-seller','Apply policy':'save-policy'}[label]
  if(!intent)throw Error('Unrecognized live MCEL form operation')
  await clickMcelConfirmed(page,page.getByRole('button',{name:label,exact:true}),intent,confirmationOptions)
  await waitFor(page,postcondition,'Saved form postcondition missing')
}
// Emergency recovery and normal cleanup use the existing, scoped Worker
// settings backend. Only seller policy/token fields are ever written. The
// request is authorized by --authorize-settings-change and followed by two
// independent backend reads to detect racing UI autosaves.
async function readSellerPolicy(page){
  return (await getJSON(page,'/api/applications/worker/settings')).settings
}
async function writeSellerPolicy(page,baseline){
  if(!auth('SETTINGS'))throw Error('OFFER_POLICY_SETTINGS_AUTH_REQUIRED')
  const result=await page.evaluate(async policy=>{
    const fields=['sellerEnabled','rentalEnabled','sellerAvailabilityMode']
    const settings={sellerEnabled:policy.sellerEnabled,rentalEnabled:policy.sellerEnabled,
      sellerAvailabilityMode:policy.sellerAvailabilityMode}
    if(Number.isSafeInteger(policy.sellerTargetTokens)){
      settings.sellerTargetTokens=policy.sellerTargetTokens
      fields.push('sellerTargetTokens')
    }
    if(typeof policy.sellerCreditsPerToken==='string'){
      settings.sellerCreditsPerToken=policy.sellerCreditsPerToken
      fields.push('sellerCreditsPerToken')
    }
    const response=await fetch('/api/applications/worker/settings',{
      method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json','Accept':'application/json'},
      body:JSON.stringify({settings,changed_fields:fields})
    })
    const body=await response.json().catch(()=>null)
    return {http:response.status,ok:body?.ok===true}
  },baseline)
  if(result.http!==200 || !result.ok)throw Error('OFFER_POLICY_BACKEND_WRITE_REFUSED')
}
async function restoreSellerPolicy(page,baseline){
  return enforceSellerPolicy({expected:baseline,read:()=>readSellerPolicy(page),
    write:policy=>writeSellerPolicy(page,policy),delay:sleep})
}
async function recoverInterruptedSellerPolicy(page){
  const saved=readSellerPolicyReceipt(policyRecoveryPath)
  if(!saved)return
  proof.stage='OFFER_POLICY_RECOVERY'
  const before=await readSellerPolicy(page)
  proof.offerPolicyRecovery={version:'offer-policy-recovery-v1',
    expectedMode:saved.sellerAvailabilityMode,
    observedMode:['totally_idle','ai_idle'].includes(before?.sellerAvailabilityMode)
      ?before.sellerAvailabilityMode:'unknown',restored:false}
  if(!auth('SETTINGS'))throw Error('OFFER_POLICY_SETTINGS_AUTH_REQUIRED')
  await restoreSellerPolicy(page,saved)
  clearSellerPolicyReceipt(policyRecoveryPath)
  proof.offerPolicyRecovery.restored=true
}

async function testRequester(page) {
  // Exercise the actual backend SQLite policy/reservation path under isolated
  // test values. Never change live requester settings or send any paid work.
  await navigate(page,'requester')
  // Read the actual server response, including the legitimate "not yet saved"
  // case. The backend's missing-policy semantics are deny-by-default, never
  // the editable form's displayed default daily limit.
  const read=await page.evaluate(async()=>{
    try {
      const response=await fetch('/api/applications/worker/settings',{
        credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}})
      return {http:response.status,body:await response.json().catch(()=>null)}
    }catch{return {http:0,body:null}}
  })
  const classified=classifyRequesterPolicyRead(read)
  proof.requesterPolicyReadDiagnostic=classified.diagnostic
  if(!classified.ok)throw Error('Requester policy unavailable')
  mark('requesterPolicyRead')
  proof.stage='requesterGuardVerify'
  const result=await page.evaluate(async()=>{
    try {
      const res=await fetch('/api/applications/chat-console/ai/remote-overflow/requester-verify',{
        method:'POST',credentials:'same-origin',cache:'no-store',
        headers:{'Content-Type':'application/json'},body:'{}'})
      return {http:res.status,data:await res.json().catch(()=>null)}
    }catch{return {http:0,data:null}}
  })
  const checks=['backendDeniedUnauthorizedSpend','budgetLimitDenied',
    'localBusyPolicyDenied','restartRetainedReservation','duplicateDenied',
    'consentReplayDenied','concurrentBudgetDenied','noPaidSubmission']
  const guardVerified=result.http===200 && result.data?.ok===true &&
    result.data?.isolated===true && result.data?.no_paid_submission===true &&
    checks.every(k=>result.data?.proofs?.[k]===true)
  proof.requesterGuardDiagnostic={version:'requester-guard-read-v1',
    httpStatus:Number.isInteger(result.http)&&result.http>=0&&result.http<=599?result.http:0,
    result:guardVerified?'verified':result.http===200?
      (result.data?.ok===true?'proof-incomplete':'backend-refused'):
      result.http===0?'transport-failed':'http-rejected'}
  if(!guardVerified)throw Error('Backend requester guard verification failed')
  for(const check of checks) mark(check)
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
    proof.offerRegistration={version:'offer-registration-v3',settingsAuthorized:false,
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
  const offer={version:'offer-registration-v3',settingsAuthorized:auth('SETTINGS'),
    priorHubStatus:'unknown',uiOutcome:'unknown',
    sellerEnabledBefore:null,sellerEnabledAtRegistration:null,settingsChanged:false,
    availabilityModeBefore:'unknown',availabilityModeAtRegistration:'unknown',
    availabilityModeBaseline:'unknown',
    availabilityRestored:null,restoration:{policyAttempted:false,policyVerified:null,
      offerAttempted:false,offerVerified:null,failedAt:'none'},
    button:null,confirmationCompleted:false,requestSeen:false,responseSeen:false,httpStatus:null,
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
    const task=captureLiveOfferMoment({context,workerPage:page,reportPath,
      extensionId,gate,phase}).then(data=>proof.offerMoments.push(data),()=>{})
    offerMomentTasks.push(task)
    return task
  }
  const offerRequests=createOfferSyncTracker()
  const offerResponses=[]
  const onOfferRequest=request=>{
    if(offerRequests.observeRequest(request))offer.requestSeen=true
  }
  const onOfferResponse=response=>{
    if(offerRequests.observeResponse(response)){
      offer.responseSeen=true
      offer.httpStatus=response.status()
      offerResponses.push(response)
      if(response.status()>=400)snapshotOfferMoment('offer-http-failed')
    }
  }
  const onOfferRequestFailed=request=>{
    if(offerRequests.observeFailure(request))offer.transportFailed=true
  }
  page.on('request',onOfferRequest)
  page.on('response',onOfferResponse)
  page.on('requestfailed',onOfferRequestFailed)
  let sellerBefore=null
  let sellerRestoreBaseline=null
  let changedSeller=false
  let changedOfferTokens=false
  let changedOfferPrice=false
  let startedWork=false
  let cleanupFailure=null
  try {
    proof.stage='OFFER_POLICY_PREFLIGHT'
    sellerBefore=(await getJSON(page,'/api/applications/worker/settings')).settings
    offer.sellerEnabledBefore=sellerBefore.sellerEnabled===true
    const availability=planOfferAcceptancePolicy(sellerBefore,auth('SETTINGS'))
    offer.availabilityModeBefore=availability.originalMode
    // The caller can explicitly configure the historical, pre-test seller
    // mode. Only settings-authorized runs may reconcile a previously leaked
    // temporary ai_idle mode. Keep the observed starting mode distinct from
    // the target baseline; never claim it was originally totally_idle.
    const expectedMode=auth('SETTINGS')
      ?process.env.MC_WORKER_TEST_SELLER_ORIGINAL_MODE:null
    if(expectedMode && !['totally_idle','ai_idle'].includes(expectedMode))
      throw Error('OFFER_ORIGINAL_POLICY_MODE_UNVERIFIED')
    sellerRestoreBaseline=(expectedMode && expectedMode!==sellerBefore.sellerAvailabilityMode)
      ?{...sellerBefore,sellerAvailabilityMode:expectedMode}:sellerBefore
    const baselineCorrection=sellerRestoreBaseline.sellerAvailabilityMode!==sellerBefore.sellerAvailabilityMode
    offer.availabilityModeBaseline=sellerRestoreBaseline.sellerAvailabilityMode
    // Record the original settings before the FIRST mutation. If Chromium
    // dies, the next authorized gate recovers this receipt before issuing an MSK.
    const plannedTokens=gate==='work-cycle'
      ?Math.min(Number(sellerBefore.sellerTargetTokens||0),512):null
    const plannedPrice=gate==='work-cycle'
      ?boundedSmokeOfferRate(sellerBefore.sellerCreditsPerToken).testRate:null
    if(availability.requiresChange || baselineCorrection || (gate==='work-cycle' &&
        (plannedTokens!==Number(sellerBefore.sellerTargetTokens) ||
         plannedPrice!==String(sellerBefore.sellerCreditsPerToken)))){
      if(!auth('SETTINGS'))throw Error('OFFER_POLICY_SETTINGS_AUTH_REQUIRED')
      writeSellerPolicyReceipt(policyRecoveryPath,gate==='work-cycle' ? sellerRestoreBaseline : {
        sellerEnabled:sellerRestoreBaseline.sellerEnabled,
        sellerAvailabilityMode:sellerRestoreBaseline.sellerAvailabilityMode,
      })
      changedSeller=true
    }
    if(availability.requiresChange){
      // An interactive Windows session correctly blocks 'totally_idle'. With
      // explicit settings authorization, temporarily exercise the existing
      // 'ai_idle' policy instead. The server still verifies local AI capacity.
      // Do not invoke Work now, forge idle status or bypass the policy engine.
      changedSeller=true
      proof.stage='OFFER_TEMPORARY_AVAILABILITY_POLICY'
      const transition={version:'offer-policy-transition-v1',
        lastAction:'inspection',expectedMode:availability.modeForTest,
        expectedSellerEnabled:true,controls:null,backendAfter:null,errorKind:'none'}
      offer.policyTransition=transition
      // The previous catch screenshot was too late: cleanup already changed
      // what the operator saw. Capture the policy form before touching it.
      const inspectPolicyControls=async()=>{
        try {return await page.evaluate(()=>{
          const enabled=document.querySelector('input[name="sellerEnabled"]')
          const mode=document.querySelector('select[name="availabilityMode"]')
          const apply=[...document.querySelectorAll('button')]
            .filter(b=>b.textContent?.trim()==='Apply policy')
          return {sellerControlPresent:!!enabled,sellerChecked:enabled?!!enabled.checked:null,
            modeControlPresent:!!mode,modeDisabled:mode?!!mode.disabled:null,
            selectedMode:mode&&['totally_idle','ai_idle'].includes(mode.value)?mode.value:'unknown',
            applyCount:apply.length,applyDisabled:apply.length===1?!!apply[0].disabled:null}
        })}catch{return {inspection:'unavailable'}}
      }
      transition.controls=await inspectPolicyControls()
      await snapshotOfferMoment('policy-before')
      try{
        if(availability.changeEnabled){
          transition.lastAction='enable-seller'
          await page.locator('input[name="sellerEnabled"]').check()
        }
        if(availability.changeMode){
          transition.lastAction='select-ai-idle'
          await page.locator('select[name="availabilityMode"]').selectOption(availability.modeForTest)
        }
        transition.lastAction='apply-policy'
        await saveForm(page,'Apply policy',async()=>{
          const actual=(await getJSON(page,'/api/applications/worker/settings')).settings
          return actual.sellerEnabled===true &&
            actual.sellerAvailabilityMode===availability.modeForTest
        })
        transition.lastAction='verified'
        transition.backendAfter={sellerEnabled:true,availabilityMode:availability.modeForTest}
      }catch(policyError){
        const reason=String(policyError?.message||'')
        transition.errorKind=/strict mode violation/i.test(reason)?'duplicate-control':
          /Saved form postcondition missing/.test(reason)?'save-postcondition-timeout':
          /Timeout|timeout/.test(reason)?'ui-timeout':
          /not visible|not enabled|disabled/.test(reason)?'ui-not-ready':
          /OFFER_POLICY_BACKEND_WRITE_REFUSED/.test(reason)?'backend-write-refused':
          'other-policy-error'
        transition.controlsAtFailure=await inspectPolicyControls()
        // Screenshot the still-failing form BEFORE the finally restoration.
        await snapshotOfferMoment('policy-failed')
        try{
          const saved=await Promise.race([readSellerPolicy(page),
            sleep(4000).then(()=>null)])
          transition.backendAfter=saved?{
            sellerEnabled:saved.sellerEnabled===true,
            availabilityMode:['totally_idle','ai_idle'].includes(saved.sellerAvailabilityMode)
              ?saved.sellerAvailabilityMode:'unknown'}:null
        }catch{transition.backendAfter=null}
        proof.policyBrowserFaults=browserFaults.slice(-12)
        proof.policyBrowserNetworkFailures=browserNetworkFailures.slice(-12)
        throw policyError
      }
    }
    if(gate==='work-cycle'){
      proof.stage='OFFER_TEMPORARY_OFFER_PRICE'
      if(!auth('SETTINGS'))throw Error('Marketplace setting changes not authorized')
      const tokens=Math.min(Number(sellerBefore.sellerTargetTokens||0),512)
      if(!Number.isInteger(tokens)||tokens<1)throw Error('Invalid original seller token count')
      const rate=boundedSmokeOfferRate(sellerBefore.sellerCreditsPerToken).testRate
      changedOfferTokens=tokens!==Number(sellerBefore.sellerTargetTokens)
      changedOfferPrice=rate!==String(sellerBefore.sellerCreditsPerToken)
      if(changedOfferTokens || changedOfferPrice){
        // A real disposable Hub offer, priced and paid at the new rate.
        // Do not alter Captain ledger, funding limits, or signature checks.
        changedSeller=true
        const pt={version:'offer-price-transition-v1',expectedRate:rate,
          expectedTokens:tokens,formRate:'unknown',formTokens:null,
          editAttempts:0,inputResets:0,staleConfirmations:0,
          confirmationRateMatched:false,confirmationTokensMatched:false,
          requestRate:'unknown',requestTokens:null,requestSeen:false,
          responseHttp:null,backendRate:'unknown',backendTokens:null,
          operationResult:'unknown',errorKind:'none',writeTrace:[]}
        offer.priceTransition=pt
        const rateInput=page.locator('input[name="sellerCreditsPerToken"]')
        const tokenInput=page.locator('input[name="sellerTargetTokens"]')
        // Read ONLY bounded seller price fields from real settings writes.
        // No credential, wallet address, body or unrelated settings are stored.
        const safeRate=value=>/^\d+(?:\.\d{1,18})?$/.test(String(value))
          ?String(value).slice(0,32):'invalid'
        const priceWriteTrace=createWorkerSettingsWriteTrace()
        const detachPriceTrace=priceWriteTrace.attach(page)
        const sellerRequests=new WeakSet()
        const onSellerRequest=req=>{
          try{
            if(req.method()!=='POST' || new URL(req.url()).pathname!=='/api/applications/worker/settings')return
            const body=req.postDataJSON()
            if(!Array.isArray(body?.changed_fields) ||
               !body.changed_fields.includes('sellerCreditsPerToken'))return
            pt.requestSeen=true
            pt.requestRate=safeRate(body.settings?.sellerCreditsPerToken)
            pt.requestTokens=Number.isSafeInteger(body.settings?.sellerTargetTokens)
              ?body.settings.sellerTargetTokens:null
            sellerRequests.add(req)
          }catch{}
        }
        const onSellerResponse=res=>{
          if(sellerRequests.has(res.request()))pt.responseHttp=res.status()
        }
        page.on('request',onSellerRequest)
        page.on('response',onSellerResponse)
        try{
          // Capture before editing so the multi-target screenshot process
          // cannot give a pending seller refresh time to overwrite the edit.
          await snapshotOfferMoment('offer-price-before-edit')
          const observeDialog=dialog=>{
            const lines=String(dialog.message()||'').split(/\r?\n/)
            pt.confirmationRateMatched=lines.includes(`Seller Credits Per Token: ${rate}`)
            pt.confirmationTokensMatched=lines.includes(`Seller Target Tokens: ${tokens}`)
          }
          const savedEdit=await saveSellerOfferAfterRefresh({
            expectedRate:rate, expectedTokens:tokens,
            mutationSubmitted:()=>pt.requestSeen,
            delay:sleep,
            writeFields:async()=>{
              // These are ordinary MCEL form-control edits. Do not bypass
              // form confirmation or write settings directly through fetch.
              if(changedOfferTokens)await tokenInput.fill(String(tokens))
              if(changedOfferPrice)await rateInput.fill(rate)
            },
            readFields:async()=>({rate:safeRate(await rateInput.inputValue()),
              tokens:Number(await tokenInput.inputValue())}),
            onSample:({attempt,rate:observedRate,tokens:observedTokens,matched})=>{
              pt.editAttempts=attempt
              pt.formRate=observedRate
              pt.formTokens=observedTokens
              if(!matched)pt.inputResets++
            },
            onStaleConfirmation:()=>{pt.staleConfirmations++},
            submit:async()=>{
              pt.confirmationRateMatched=false
              pt.confirmationTokensMatched=false
              page.on('dialog',observeDialog)
              try{
                await saveForm(page,'Save Seller Offer',async()=>{
                  const saved=(await getJSON(page,'/api/applications/worker/settings')).settings
                  return saved.sellerTargetTokens===tokens &&
                    String(saved.sellerCreditsPerToken)===rate
                },{expectedSellerFields:{sellerCreditsPerToken:rate,sellerTargetTokens:tokens}})
              }finally{page.off('dialog',observeDialog)}
            },
          })
          pt.writeTrace=await priceWriteTrace.finish()
          pt.editAttempts=savedEdit.attempts
          pt.inputResets=savedEdit.resets
          pt.staleConfirmations=savedEdit.staleConfirmations
          pt.operationResult='verified'
          pt.backendRate=rate;pt.backendTokens=tokens
        }catch(error){
          pt.writeTrace=await priceWriteTrace.finish()
          const reason=String(error?.message||'')
          pt.errorKind=/OFFER_PRICE_INPUT_RESET_BEFORE_SAVE/.test(reason)
            ?'form-value-reset':/OFFER_PRICE_FORM_REFRESH_DID_NOT_SETTLE/.test(reason)
            ?'persistent-form-refresh':/Unexpected MCEL confirmation refused/.test(reason)
            ?'stale-confirmation':/Saved form postcondition missing/.test(reason)
            ?'backend-postcondition-timeout':/Timeout|timeout/.test(reason)
            ?'ui-timeout':'other-save-error'
          try{
            const saved=(await getJSON(page,'/api/applications/worker/settings')).settings
            pt.backendRate=safeRate(saved.sellerCreditsPerToken)
            pt.backendTokens=Number.isSafeInteger(saved.sellerTargetTokens)
              ?saved.sellerTargetTokens:null
          }catch{pt.backendRate='unavailable'}
          try{
            const msg=await page.locator('[data-mcel-form-result="seller-offer-settings"]').textContent({timeout:1000})
            pt.operationResult=/Operation completed/i.test(msg||'')?'completed':
              /Operation refused/i.test(msg||'')?'refused':
              /Applying/i.test(msg||'')?'applying':'unknown'
          }catch{}
          await snapshotOfferMoment('offer-price-failed')
          throw error
        }finally{
          page.off('request',onSellerRequest)
          page.off('response',onSellerResponse)
          detachPriceTrace()
        }
      }
    }
    offer.settingsChanged=changedSeller
    const registrationSettings=(await getJSON(page,'/api/applications/worker/settings')).settings
    offer.sellerEnabledAtRegistration=registrationSettings.sellerEnabled===true
    offer.availabilityModeAtRegistration=registrationSettings.sellerAvailabilityMode
    if(!offer.sellerEnabledAtRegistration)throw Error('OFFER_SELLER_DISABLED_SETTINGS_AUTH_REQUIRED')
    if(offer.availabilityModeAtRegistration!==availability.modeForTest)
      throw Error('OFFER_AVAILABILITY_POLICY_UNVERIFIED')
    // This invokes the actual native button -> typed MCEL port -> original
    // authenticated registration signer. No direct Hub registration shortcut.
    proof.stage='OFFER_CONFIRMATION'
    // Capture the PRE-CLICK Hub state. It may be an old failure, and MUST
    // NOT be treated as the result of the newly confirmed MCEL operation.
    const priorSession=(await getJSON(page,'/api/applications/worker/network-session')).session||{}
    offer.priorHubStatus=['not_submitted','submitting','accepted','failed','stale'].includes(
      priorSession.signed_connection?.hub_registration_status)
      ?priorSession.signed_connection.hub_registration_status:'unknown'
    offer.button=await page.locator('button[data-intent="intent:register-offer"]').evaluateAll(buttons=>({
      count:buttons.length,enabled:buttons.length===1&&!buttons[0].disabled,
      visible:buttons.length===1&&buttons[0].getClientRects().length>0}))
    if(offer.button.count!==1 || !offer.button.enabled || !offer.button.visible)
      throw Error('OFFER_REGISTRATION_BUTTON_NOT_READY')
    // Arm only after policy saves and the pre-click Hub snapshot. A POST from
    // an earlier settings operation must not count as this offer attempt.
    offerRequests.arm()
    await clickMcelConfirmed(page,page.locator('button[data-intent="intent:register-offer"]'),'register-offer')
    offer.confirmationCompleted=true
    snapshotOfferMoment('offer-confirmed')
    // MCEL's confirmation only acknowledges authorization. The capability
    // call runs asynchronously and may still be refreshing its session and
    // saving the seller form. Do not sample an old `failed` Hub state yet.
    proof.stage='OFFER_SYNC_REQUEST_VERIFY'
    try{
      await waitForFreshOfferRequest({requestSeen:()=>offer.requestSeen,
        transportFailed:()=>offer.transportFailed,sleep,timeoutMs:30000})
    }catch(error){
      // No POST means no NEW Hub registration failure. Classify the current
      // UI outcome without copying arbitrary error text or wallet data.
      try{offer.uiOutcome=await page.evaluate(()=>{
        const pill=String(document.querySelector('#worker-registration-status-pill')?.textContent||'').trim()
        const message=String(document.querySelector('#worker-save-status')?.textContent||'')
        if(pill==='Offer: registration failed'||/Worker offer registration failed:/.test(message))return 'worker-registration-failed'
        if(pill==='Offer: pending')return 'worker-offer-pending'
        if(pill==='Offer: registered')return 'worker-offer-registered'
        return 'not-observed'
      })}catch{offer.uiOutcome='inspection-unavailable'}
      snapshotOfferMoment('offer-hub-failed')
      throw error
    }
    // A fresh POST is not evidence of its result. The persisted Hub status can
    // still say failed from a previous registration while this POST is in flight.
    proof.stage='OFFER_SYNC_RESPONSE_VERIFY'
    try{
      await waitForFreshOfferResponse({responseSeen:()=>offer.responseSeen,
        transportFailed:()=>offer.transportFailed,sleep,timeoutMs:90000})
    }catch(error){
      snapshotOfferMoment('offer-hub-failed')
      throw error
    }
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
    // Verify spend capacity BEFORE activating Work Now. A rejected read-only
    // ledger preflight must not leave an unnecessary runtime work override.
    proof.stage='CAPTAIN_FUNDING_PREFLIGHT'
    if(!auth('WALLET') || !auth('SPEND'))throw Error('CAPTAIN_WALLET_SIGNING_AUTH_REQUIRED')
    const preflight=spawnSync(process.env.MC_WORKER_TEST_PYTHON||'python',[
      '-m','tools.worker_captain_funding_preflight','--required-credit-wei',price.requiredCreditWei,
      '--ceiling-credits',String(price.priceCeilingCredits),
      '--authorize-captain-wallet-signing'],
      {cwd:process.env.MC_WORKER_TEST_ROOT,encoding:'utf8',timeout:30000,maxBuffer:65536})
    const funding=captainFundingPreflightEvidence({
      status:preflight.status,signal:preflight.signal,error:preflight.error,
      stdout:preflight.stdout,stderr:preflight.stderr,
      expectedRequiredWei:price.requiredCreditWei})
    proof.captainFundingPreflight=funding
    if(!funding.verified)throw Error('CAPTAIN_FUNDING_PREFLIGHT_NOT_VERIFIED')
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
    // Never refill the treasury or run a second price retry implicitly.
    proof.stage='CAPTAIN_EXECUTION'
    const receipt=await executeCaptainWithPriceRetry({
      python:process.env.MC_WORKER_TEST_PYTHON||'python',cwd:process.env.MC_WORKER_TEST_ROOT,
      model:String(saved.models||'').split(',')[0].trim(),
      priceCeilingCredits:price.priceCeilingCredits,
      bridgeFundingRequired:funding.bridgeFundingRequired,
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
        // On some failures the returned HTTP text is generic. Inspect the
        // backend's saved Hub-auth failure, but store only its allowlisted
        // reason code. Never persist or print the raw backend text.
        if(offer.apiRejectionDetail==='other-msk-rejection' ||
           offer.apiRejectionDetail==='not-msk-specific'){
          try{
            const session=(await getJSON(page,'/api/applications/worker/network-session')).session||{}
            const savedDetail=offerApiRejectionDetail({error:
              session.signed_connection?.hub_registration_error||''})
            if(savedDetail!=='not-msk-specific')offer.apiRejectionDetail=savedDetail
          }catch{}
        }
      }
      if(offer.syncStatus==='deferred' && offer.deferredReason==='none')
        offer.deferredReason=offerDeferralCause(body?.reason)
    }
    // An already-accepted offer must not be relabeled as an offer failure
    // when the later Captain funding/execution stage is what failed.
    offer.failure=proof.proofs.hubOfferAccepted===true
      ?'none':offerFailureCause(error?.message,offer)
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
      // Restore independently of UI navigation/dialogs, including on failure.
      // This is the same settings endpoint used by native MCEL save-policy;
      // the allowlist prevents changing wallet or Hub connection state.
      const baseline=sellerPolicyReceipt((changedOfferTokens||changedOfferPrice) ? sellerRestoreBaseline : {
        sellerEnabled:sellerRestoreBaseline.sellerEnabled,
        sellerAvailabilityMode:sellerRestoreBaseline.sellerAvailabilityMode,
      })
      offer.restoration.policyAttempted=true
      try{
        await restoreSellerPolicy(page,baseline)
      }catch{
        // If the combined policy+price write failed, still try restoring the
        // seller availability independently before reporting a cleanup error.
        try{
          await restoreSellerPolicy(page,sellerPolicyReceipt({
            sellerEnabled:sellerRestoreBaseline.sellerEnabled,
            sellerAvailabilityMode:sellerRestoreBaseline.sellerAvailabilityMode}))
        }catch{}
      }
      const observed=await readSellerPolicy(page).catch(()=>null)
      const policyRestored=Boolean(observed &&
        observed.sellerEnabled===sellerRestoreBaseline.sellerEnabled &&
        observed.sellerAvailabilityMode===sellerRestoreBaseline.sellerAvailabilityMode)
      offer.availabilityRestored=policyRestored
      offer.restoration.policyVerified=policyRestored
      if(!policyRestored){
        offer.restoration.failedAt='policy'
        cleanupFailure='seller-policy-restore-unverified'
      }
      if(changedOfferTokens||changedOfferPrice){
        offer.restoration.offerAttempted=true
        const priceRestored=Boolean(observed &&
          (!changedOfferTokens || observed.sellerTargetTokens===sellerBefore.sellerTargetTokens) &&
          (!changedOfferPrice || String(observed.sellerCreditsPerToken)===String(sellerBefore.sellerCreditsPerToken)))
        offer.restoration.offerVerified=priceRestored
        if(!priceRestored){
          if(offer.restoration.failedAt==='none')offer.restoration.failedAt='offer'
          cleanupFailure=cleanupFailure||'seller-offer-restore-unverified'
        }
      }
      if(!cleanupFailure){
        try{clearSellerPolicyReceipt(policyRecoveryPath)}
        catch{cleanupFailure='seller-policy-recovery-receipt-not-cleared'}
      }
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
  let context=null, workerPage=null, authStarted=false
  try{
    proof.stage='BROWSER_LAUNCH'
    context=await chromium.launchPersistentContext(profile||'',{
      channel:'chromium',headless:false,
      args:walletGate?[`--disable-extensions-except=${extension}`,`--load-extension=${extension}`]:[],
      viewport:{width:1440,height:920},ignoreHTTPSErrors:false,serviceWorkers:'block'})
    proof.stage='GATEWAY_AUTHENTICATION'
    // Never revoke a pre-existing session: fresh disposable profiles only.
    if((await context.cookies(ORIGIN)).some(c=>c.name==='__Host-MC-Session'))throw Error('context-not-disposable')
    authStarted=true
    await authenticateContext(context)
    proof.gatewayAuthenticated=true
    let page=await context.newPage()
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
        if(link.origin!==ORIGIN)return null
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
    const mounted=await mountCanonicalWorkerSurface(page,expectedUrl)
    page=mounted.page
    workerPage=page
    proof.canonicalRoute=mounted.proof
    proof.stage='NATIVE_MCEL_MOUNTED'
    // Repair a previously interrupted policy edit BEFORE wallet signing or offer submission.
    if(gate==='offer-registration'||gate==='work-cycle')await recoverInterruptedSellerPolicy(page)
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
      proof.stage='METAMASK_PRECONNECT_URL_CHECK'
      const before=await waitForVerifiedMetaMaskPreconnect(context,extensionId,publicWallet.address,{
        onObservation:(evidence,checks)=>{
          proof.metaMaskPreconnectUrl=evidence
          proof.metaMaskPreconnectChecks=checks
        }
      })
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
    if(proof.offerRegistration?.version==='offer-registration-v3' &&
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
    let revoked=false
    try {if(authStarted)revoked=await revokeContext(context)} catch {}
    try {await context?.close();proof.cleanupOk=revoked} catch {proof.cleanupOk=false}
    proof.gatewaySessionRevoked=revoked
    mkdirSync(dirname(reportPath),{recursive:true})
    writeFileSync(reportPath,JSON.stringify(proof,null,2)+'\n',{mode:0o600})
  }
  if (!proof.ok || !proof.cleanupOk)process.exitCode=1
}
main().catch(error=>{
  // Fail closed and retain a bounded receipt for an exception outside main's
  // normal try/finally. Imported-module failures are classified by Python.
  try{
    if(reportPath && !existsSync(reportPath)){
      proof.stage='NODE_UNHANDLED_FAILURE'
      proof.errorClass=error instanceof Error ? error.name : 'UnknownError'
      mkdirSync(dirname(reportPath),{recursive:true})
      writeFileSync(reportPath,JSON.stringify(proof,null,2)+'\n',{mode:0o600})
    }
  }catch{}
  process.exitCode=1
})
