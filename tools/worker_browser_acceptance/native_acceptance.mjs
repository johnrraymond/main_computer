// Native Windows Playwright runner: no Synpress CLI/cache builder required.
// Uses Synpress MetaMask's browser interaction *library* with a real extension.
// Only the disposable test-only Chromium profile is used.
import { chromium, expect } from '@playwright/test'
import { MetaMask } from '@synthetixio/synpress/playwright'
import { importDisposableWallet, safeExtensionTabsDiagnostics, selectMetaMaskPage, verifyWalletHomeIdentity } from './metamask_onboarding.mjs'
import {ensureDisposableMetaMaskUnlocked} from './metamask_cached_unlock.mjs'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { completeWorkerWalletConnection, installWorkerWalletTrace, readWorkerWalletTrace, startWorkerWalletConnection } from './worker_wallet_approvals.mjs'
import { resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import {probeRpcClients, rpcProbeSummary} from './rpc_client_probe.mjs'
import {approveWorkerMskSignature} from './metamask_sidepanel_cdp.mjs'
import {executeCaptainWithPriceRetry, priceOfWorkerOffer, waitForLiveWorker, waitForDeliveredWork, readWorkerLiveStatus, captainWorkerAtFailure, captainWorkerExecutionAtFailure, prepareWorkerForWorkNow, postWorkNowDuration, readWorkNowRejection, boundedSmokeOfferTokens, setWorkerOfferTargetTokens} from './worker_work_cycle.mjs'
import {isMskEndpointRequest, waitForWorkerMsk, workerMskEvent, readMskApiRejection, safeMskApiRejection, formatMskFailureWithApiReason, assertServedWorkerMskContract} from './worker_msk_outcome.mjs'

const walletFile = process.env.MC_WORKER_TEST_WALLET_FILE
const extensionDir = process.env.MC_WORKER_TEST_EXTENSION_DIR
const profileDir = process.env.MC_WORKER_TEST_PROFILE_DIR
const markerFile = process.env.MC_WORKER_TEST_PROFILE_MARKER
if (!walletFile || !extensionDir || !profileDir || !markerFile) {
  throw new Error('Use python tools/worker_browser_acceptance.py run or cache-wallet')
}
const walletSecret = JSON.parse(readFileSync(walletFile, 'utf8'))
if (walletSecret.version !== 1 || !/^0x[0-9a-f]{40}$/i.test(walletSecret.address) ||
    !walletSecret.mnemonic || !walletSecret.password) {
  throw new Error('Test wallet is invalid')
}

async function findExtensionId(context) {
  let workers = context.serviceWorkers()
  if (!workers.length) {
    await context.waitForEvent('serviceworker', {timeout: 45_000})
    workers = context.serviceWorkers()
  }
  const source = workers.find(worker => worker.url().startsWith('chrome-extension://'))
  if (!source) throw new Error('Real MetaMask extension service worker did not start')
  return new URL(source.url()).host
}

async function prepareWallet(context, walletPage, extensionId) {
  let activePage = walletPage
  const marker = existsSync(markerFile) ? readFileSync(markerFile, 'utf8').trim().toLowerCase() : ''
  if (marker !== walletSecret.address.toLowerCase()) {
    // The real MetaMask extension performs actual onboarding and account import.
    // Synpress v4 unconditionally clicks the removed #metametrics-opt-in
    // element in MetaMask v13; drive current *real* onboarding controls.
    try {
      activePage = await importDisposableWallet(activePage, walletSecret.mnemonic.trim(), walletSecret.password, {context, extensionId})
      // A wallet-home tab is not sufficient by itself. Verify that its
      // displayed public address matches the disposable wallet generated
      // for this run before configuring a network or writing a cache marker.
      await verifyWalletHomeIdentity(activePage, walletSecret.address, extensionId)
    } catch (error) {
      // The thrown error identifies the actual tab where onboarding stopped.
      // This companion report counts only safe categories across extension tabs.
      console.error('MetaMask extension tabs:', JSON.stringify(
        await safeExtensionTabsDiagnostics(context, extensionId, activePage)))
      throw error
    }
    // Cache only verified disposable wallet identity. Network negotiation is
    // application behavior and belongs to Worker's own Connect Wallet flow.
    writeFileSync(markerFile, walletSecret.address.toLowerCase() + '\n', {mode: 0o600})
  } else {
    // A cached MetaMask profile can be locked even when the Worker's provider
    // still answers read-only RPC. Unlock the isolated test wallet and verify
    // its public identity before proceeding to account/signature approvals.
    activePage = await ensureDisposableMetaMaskUnlocked({
      context, extensionId, expectedAddress: walletSecret.address,
      password: walletSecret.password,
      onProgress: phase => console.log('Worker acceptance:', phase),
    })
  }
  return new MetaMask(context, activePage, walletSecret.password, extensionId)
}

// All network calls are GETs. Uses Captain's own read-only wallet preparation
// to identify the requester, not the disposable *Worker* MetaMask account.
// Never log the subprocess response: it can contain runtime wallet metadata.
function requireCaptainFunding({python, cwd, requiredCreditWei, priceCeilingCredits}) {
  const probe = spawnSync(python, [
    '-m', 'tools.worker_captain_funding_preflight',
    '--required-credit-wei', requiredCreditWei,
    '--ceiling-credits', String(priceCeilingCredits),
  ], {cwd, shell:false, windowsHide:true, encoding:'utf8', timeout:30_000, maxBuffer:65536})
  let evidence = null
  try { evidence = JSON.parse(String(probe.stdout || '').trim()) } catch {}
  if (probe.status !== 0 || evidence?.ok !== true || evidence?.fundable !== true) {
    const reason = evidence?.reason && /^[a-z0-9_-]{1,70}$/.test(evidence.reason)
      ? evidence.reason : 'read_only_funding_probe_unavailable'
    throw new Error('Captain funding preflight refused: ' + reason +
      '. No Captain request was submitted. Run the read-only captain funding preflight for account totals.')
  }
  if (typeof evidence.bridgeFundingRequired !== 'boolean') {
    throw new Error('Captain funding preflight returned no bounded-bridge decision. No request submitted.')
  }
  console.log('Worker acceptance: captain.read-only-funding-preflight.' + evidence.verdict)
  return evidence.bridgeFundingRequired
}

async function main() {
  const action = process.env.MC_WORKER_TEST_ACTION || 'run'
  const context = await chromium.launchPersistentContext(profileDir, {
    channel: 'chromium', headless: false,
    args: [`--disable-extensions-except=${extensionDir}`, `--load-extension=${extensionDir}`],
    viewport: {width: 1440, height: 920},
  })
  try {
    const extensionId = await findExtensionId(context)
    // An unpacked extension has a path-derived ID unless its manifest carries
    // a public-key identity. Mother V2.1 allows the production MetaMask origin,
    // not arbitrary extension IDs. Print only this public ID for route preview.
    console.log('Disposable MetaMask extension ID:', extensionId)
    if (extensionId !== 'nkbihfbeogaeaoehlefnkodbefgpgknn') {
      console.log('RPC Origin preflight: unpacked MetaMask differs from the production extension ID; testnet RPC may reject extension-origin requests')
      console.log('Mother preview: python mother_mutate_harness.py rpc-browser-compat-v2 --network testnet --node testnetb-super1 --host coolify-b --test-extension-id ' + extensionId)
    }
    if (action === 'rpc-probe') {
      // Reuse the existing isolated profile. No onboarding, wallet rotation,
      // approvals, RPC substitution, or Worker Connect action occurs here.
      const page = await context.newPage()
      await page.goto(process.env.MC_WORKER_TEST_URL || 'http://127.0.0.1:8765/applications/worker', {waitUntil: 'domcontentloaded'})
      const result = await probeRpcClients(context, page, extensionId, {openExtensionPage: true, probeCDPServiceWorker: true})
      const reportDir = resolve(process.env.MC_WORKER_TEST_REPORT_DIR || 'test-results')
      mkdirSync(reportDir, {recursive: true})
      const output = resolve(reportDir, 'rpc-origin-probe.json')
      writeFileSync(output, JSON.stringify(result, null, 2) + '\n', {mode: 0o600})
      console.log('RPC origin differential:', JSON.stringify(rpcProbeSummary(result)))
      console.log('Read-only RPC origin evidence:', output)
      return
    }
    const freshProfile = !existsSync(markerFile) || readFileSync(markerFile, 'utf8').trim().toLowerCase() !== walletSecret.address.toLowerCase()
    const walletPage = await selectMetaMaskPage(context, extensionId, {fresh: freshProfile})
    if (freshProfile) console.log('MetaMask onboarding tab:', JSON.stringify(
      await safeExtensionTabsDiagnostics(context, extensionId, walletPage)))
    const metamask = await prepareWallet(context, walletPage, extensionId)
    if (action === 'cache-wallet') {
      console.log('Disposable MetaMask test profile initialized:', walletSecret.address)
      return
    }
    const page = await context.newPage()
    await runWorkerAcceptance({context, page, metamask, extensionId})
  } finally {
    await context.close()
  }
}

async function runWorkerAcceptance({context, page, metamask, extensionId}) {
  const mode = process.env.MC_WORKER_TEST_MODE || 'wallet'
  if (!['wallet', 'worker', 'work-cycle'].includes(mode)) throw new Error('Unsupported acceptance mode')
  if (mode === 'work-cycle' && process.env.MC_WORKER_TEST_AUTHORIZE_WORK_CYCLE !== 'explicit') {
    throw new Error('Work-cycle requires explicit testnet work/credit authorization from the Python launcher')
  }
  const evidence = { schema: 'main-computer.worker-browser-acceptance.v1', mode, network: 'testnet', stages: [] }
  const stages = evidence.stages
  const reportDir = resolve(process.env.MC_WORKER_TEST_REPORT_DIR || 'test-results')
  mkdirSync(reportDir, { recursive: true })
  let startedWork = false
  let changedPaidPolicy = false
  let originalOfferTargetTokens = null
  const logs = []
  let mskApiStatus = null
  let mskApiRequestFailed = false
  let mskApiRejection = null
  let mskApiRejectionCapture = null
  let rpcProbeBefore = null
  page.on('response', response => {
    if (!isMskEndpointRequest(response.request())) return
    mskApiStatus = response.status()
    console.log('Worker acceptance: worker.msk.local-api-http.' + mskApiStatus)
    if (mskApiStatus >= 400) {
      // Capture only a failed local MSK response. Never touch the signed POST
      // payload or successful response (which can contain issued key material).
      mskApiRejectionCapture = readMskApiRejection(response).then(detail => {
        mskApiRejection = detail
        if (detail) console.log('Worker acceptance: worker.msk.local-api-rejection', JSON.stringify(detail))
      }).catch(() => {
        mskApiRejection = {httpStatus: mskApiStatus, reason: 'response-capture-unavailable'}
      })
    }
  })
  page.on('requestfailed', request => {
    if (!isMskEndpointRequest(request)) return
    mskApiRequestFailed = true
    console.log('Worker acceptance: worker.msk.local-api-transport-failed')
  })
  page.on('dialog', async dialog => {
    if (dialog.type() === 'confirm' && startedWork) await dialog.accept()
    else await dialog.dismiss()
  })
  // Event names only: do not capture signatures, signed requests, or auth tokens.
  page.on('console', msg => {
    const s = msg.text()
    const mskEvent = workerMskEvent(s)
    if (mskEvent && !logs.includes('msk.' + mskEvent)) {
      logs.push('msk.' + mskEvent)
      // Event NAMES only; never copy the event's console arguments.
      console.log('Worker acceptance: worker.msk.' + mskEvent)
    }
    if (s.includes('connect.finalized.connected')) logs.push('wallet.connected')
    // Event names only; never log the provider's request arguments or the
    // extension's DOM. These let the report distinguish approval from RPC.
    const walletEvent = /\[worker-wallet\]\s+(connect\.[a-zA-Z0-9_.-]+)/.exec(s)
    if (walletEvent && !logs.includes(walletEvent[1])) logs.push(walletEvent[1])
  })
  try {
    await page.goto(process.env.MC_WORKER_TEST_URL || 'http://127.0.0.1:8765/applications/worker', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('#worker-app')).toBeVisible()
    // The viewport snapshots embedded scripts when its Python module loads.
    // Detect a stale served wallet.js before rotating through approval dialogs
    // and submitting another signed request the Stable Hub must reject.
    await assertServedWorkerMskContract(page)
    stages.push('served-msk-contract-verified')
    console.log('Worker served MSK signing contract verified')
    await page.locator('#worker-auto-hub-testnet').check()
    await expect(page.locator('#worker-auto-hub-testnet')).toBeChecked()
    await expect(page.locator('#worker-network-chain-id')).toContainText('42424241')
    stages.push('testnet-hub-selected')
    console.log('Worker page opened; Testnet selected')

    // The Worker application, not Synpress, configures the selected chain.
    // Verify the real MetaMask provider is injected, but do not require that a
    // freshly imported wallet was already switched to testnet before Connect.
    const hasRealProvider = await page.evaluate(() => Boolean(window.ethereum?.isMetaMask))
    expect(hasRealProvider).toBe(true)
    stages.push('real-metamask-provider-injected')

    // Read-only differentiation: the local web page and the actual unpacked
    // extension are separate network clients. The probe never changes any
    // wallet or application state and cannot satisfy an acceptance gate.
    rpcProbeBefore = await probeRpcClients(context, page, extensionId)
    evidence.rpcProbeBeforeConnect = rpcProbeBefore
    console.log('RPC client preflight:', JSON.stringify(rpcProbeSummary(rpcProbeBefore)))

    // Clicking the real Worker UI requests network selection/repair, validates
    // RPC, then requests account access. Approve only the matching network and
    // this local dapp's permission dialogs in the genuine extension.
    console.log('Worker Connect Wallet: delegating network negotiation and RPC proof to Worker')
    await installWorkerWalletTrace(page)
    const connectStart = await startWorkerWalletConnection(page, {
      // Only registration/work-cycle can reuse an already connected disposable
      // profile. A fresh wallet acceptance run must exercise Connect Wallet.
      allowAlreadyConnected: mode === 'worker' || mode === 'work-cycle',
      onProgress: phase => console.log('Worker acceptance:', phase),
    })
    const approvals = connectStart.alreadyConnected ? [] : await completeWorkerWalletConnection({
      context, page, extensionId, expectedAddress: walletSecret.address,
      onProgress: phase => console.log('Worker acceptance:', phase),
    })
    // Even when Connect was skipped, the *same* account/chain/RPC proof below
    // is mandatory. A stale UI session or wrong MetaMask account must fail.
    if (connectStart.alreadyConnected) stages.push('existing-wallet-session-reused-pending-proof')
    stages.push('worker-driven-network-and-wallet-approvals')
    evidence.walletApprovalTypes = approvals
    evidence.workerWalletEventTypes = await readWorkerWalletTrace(page)
    await expect(page.locator('#worker-primary-wallet-status')).toContainText('0x', {timeout: 60_000})
    const wallet = await page.evaluate(async () => {
      const p = window.ethereum
      const [account] = await p.request({method: 'eth_accounts'})
      return { account, chainId: await p.request({method: 'eth_chainId'}),
               blockNumber: await p.request({method: 'eth_blockNumber'}) }
    })
    expect(wallet.account).toMatch(/^0x[0-9a-f]{40}$/i)
    expect(wallet.account.toLowerCase()).toBe(walletSecret.address.toLowerCase())
    const shownWallet = ((await page.locator('#worker-primary-wallet-status').innerText()) || '').toLowerCase()
    expect(shownWallet).toContain(wallet.account.slice(0, 6).toLowerCase())
    expect(shownWallet).toContain(wallet.account.slice(-4).toLowerCase())
    expect(wallet.chainId).toBe('0x28757b1')
    expect(wallet.blockNumber).toMatch(/^0x[0-9a-f]+$/i)
    stages.push('metamask-rpc-proven')
    console.log('Worker wallet address, testnet chain ID, and real block-number RPC verified')
    stages.push('worker-wallet-connected-and-rpc-proven')
    evidence.walletAddress = wallet.account
    evidence.chainId = wallet.chainId

    // The wallet test exercises real key issuance. Registration mode may
    // reuse the key issued by an earlier wallet run. The Hub will validate
    // that key independently when accepting Worker registration.
    await expect(page.locator('#worker-multisession-key-state')).toBeVisible({timeout: 10_000})
    const state = (await page.locator('#worker-multisession-key-state').innerText()).trim()
    const alreadyActive = /^(?:Active|Active server-side key)$/.test(state)
    console.log('Worker acceptance: worker.msk.state.' + (alreadyActive ? 'active' : 'not-active'))
    if (alreadyActive && mode === 'wallet') {
      throw new Error('Test wallet already has an active MSK. Use a fresh disposable wallet for a new signing test; no automatic revocation.')
    }
    if (!alreadyActive) {
      // MetaMask may auto-lock between initial RPC/account verification and
      // a signature. Verify cached test-profile readiness immediately before
      // requesting a new key (without touching arbitrary side-panel prompts).
      await ensureDisposableMetaMaskUnlocked({
        context, extensionId, expectedAddress: walletSecret.address,
        password: walletSecret.password,
        onProgress: phase => console.log('Worker acceptance:', phase),
      })
      console.log('Worker acceptance: worker.msk.disposable-wallet-unlocked')
      await page.locator('#worker-request-multisession-key').click()
      // Wallet signs locally; Hub must independently verify the signature.
      await approveWorkerMskSignature(context, page, extensionId, walletSecret.address, {
        onProgress: phase => console.log('Worker acceptance:', phase),
      })
      try {
        await waitForWorkerMsk({
          readState: () => page.locator('#worker-multisession-key-state').innerText(),
          readStatus: () => page.locator('#worker-save-status').innerText().catch(() => ''),
          readEvents: () => logs.filter(v => v.startsWith('msk.')).map(v => v.slice(4)),
          readApiStatus: () => mskApiStatus,
          readApiRequestFailed: () => mskApiRequestFailed,
          onProgress: phase => console.log('Worker acceptance: worker.msk.phase.' + phase),
        })
      } catch (error) {
        // The Playwright response event can precede completion of response.json().
        // Join that bounded local error capture before reporting the failure.
        if (mskApiRejectionCapture) {
          await Promise.race([
            mskApiRejectionCapture,
            new Promise(resolve => setTimeout(resolve, 2000)),
          ])
        }
        if (mskApiRejection) evidence.mskApiRejection = mskApiRejection
        throw new Error(formatMskFailureWithApiReason(error, mskApiRejection))
      }
      stages.push('hub-msk-issued')
    } else {
      stages.push('existing-msk-reused-for-registration')
    }
    const keyId = (await page.locator('#worker-multisession-key-id').innerText()).trim()
    if (!keyId || keyId === '—') throw new Error('No Hub-issued multi-session key ID is available')
    evidence.keyIssued = !alreadyActive
    evidence.keyId = keyId

    if (mode === 'worker' || mode === 'work-cycle') {
      // Optional, explicitly authorized mode: worker advertises compute.
      // Do not re-select ring 3 when already selected: its change handler
      // resets the network session and can race with the Work-now request.
      // Likewise, wait until any paid-work settings autosave has completed.
      if (mode === 'work-cycle') {
        // Do not run a paid functional test at the normal large request
        // reservation when a 512-token ACK budget suffices. Remember the
        // original settings before changing anything, including save failures.
        const offer = boundedSmokeOfferTokens(
          await page.locator('#worker-offer-target-tokens').inputValue())
        if (offer.testTokens !== offer.originalTokens) {
          originalOfferTargetTokens = offer.originalTokens
          await setWorkerOfferTargetTokens(page, offer.testTokens)
          stages.push('worker-test-offer-temporarily-priced')
          console.log('Worker acceptance: worker.work-cycle.test-target-tokens.' + offer.testTokens)
        }
      }
      await prepareWorkerForWorkNow(page, {
        onProgress: phase => console.log('Worker acceptance:', phase),
        onPaidPolicyChanged: () => { changedPaidPolicy = true },
      })
      await expect(page.locator('#worker-network-work-now')).toBeEnabled({timeout: 30_000})
      console.log('Worker acceptance: worker.work-now.control-ready')
      await page.locator('#worker-network-work-now').click()
      await expect(page.locator('#worker-work-now-dialog')).toBeVisible()
      console.log('Worker acceptance: worker.work-now.dialog-open')
      // A successful button click only opens a dialog. Selecting a duration
      // must actually POST to the backend before Hub registration can start.
      // Mark cleanup eligible as soon as POST is dispatched, including when
      // the response times out after the server may have applied an override.
      const workNowResponse = await postWorkNowDuration(page, {
        onProgress: phase => {
          if (phase === 'worker.work-now.duration-submitted') startedWork = true
          console.log('Worker acceptance:', phase)
        },
      })
      const workNowRejection = await readWorkNowRejection(workNowResponse)
      if (workNowRejection) {
        evidence.workNowRejection = workNowRejection
        // Report the sanitized local or Hub refusal immediately, before any
        // optional RPC probe or Work-now cleanup can delay the final error.
        console.error('Worker acceptance: worker.work-now.local-api-rejection', JSON.stringify(workNowRejection))
        throw new Error('Worker Work now registration refused (HTTP ' + workNowRejection.httpStatus + '): ' + workNowRejection.reason)
      }
      await expect(page.locator('#worker-network-hub-registration')).toHaveText('Accepted', {timeout: 45_000})
      const registered = (await page.locator('#worker-network-hub-registration').innerText()).trim()
      if (/failed|error|rejected|wrong chain|unreachable/i.test(registered)) throw new Error('Hub registration failed: ' + registered)
      const workerId = (await page.locator('#worker-network-worker-id').innerText()).trim()
      if (!workerId || workerId === '—') throw new Error('No Hub worker identity was reported')
      evidence.workerId = workerId
      stages.push('hub-worker-registered')
      if (mode === 'work-cycle') {
        // Only run a credit-bearing Captain request when the browser Worker
        // is actually selectable on the testnet Hub. No mainnet fallback.
        const advertisedModel = (await page.locator('#worker-offer-models').inputValue()).split(',')[0].trim()
        const price = priceOfWorkerOffer({
          targetTokens: await page.locator('#worker-offer-target-tokens').inputValue(),
          creditPerToken: await page.locator('#worker-offer-credits-per-token').inputValue(),
        })
        // A live WebSocket alone does not prove that the Hub's market
        // advertises this exact offer; verify the authenticated Hub echo
        // before allowing the Captain to spend any credits.
        await waitForLiveWorker({page, workerId, expectedPriceWei: price.requiredCreditWei})
        stages.push('worker-live-session-ready')
        console.log('Worker acceptance: worker.work-cycle.live-market-price-verified')
        console.log('Worker acceptance: worker.work-cycle.offer-price-credit-wei.' + price.requiredCreditWei)
        const bridgeFundingRequired = requireCaptainFunding({
          python: process.env.MC_WORKER_TEST_PYTHON || 'python',
          cwd: process.env.MC_WORKER_TEST_ROOT,
          ...price,
        })
        console.log('Worker acceptance: worker.work-cycle.captain-testnet-submitting')
        const receipt = await executeCaptainWithPriceRetry({
          python: process.env.MC_WORKER_TEST_PYTHON || 'python',
          model: advertisedModel,
          cwd: process.env.MC_WORKER_TEST_ROOT,
          priceCeilingCredits: price.priceCeilingCredits,
          bridgeFundingRequired,
          onRetry: () => console.log('Worker acceptance: captain.worker-not-live.retry-two-credits'),
        })
        evidence.captainRequest = {requestId: receipt.requestId, sessionId: receipt.sessionId, state: receipt.state}
        stages.push('captain-request-completed')
        console.log('Worker acceptance: worker.work-cycle.captain-testnet-completed')
        const delivery = await waitForDeliveredWork({page, workerId, captain: receipt})
        evidence.workDelivery = delivery
        stages.push('captain-request-executed-on-browser-worker')
        console.log('Worker acceptance: worker.work-cycle.worker-result-verified')
      }
      // Registration-only mode does not certify execution or settlement.
    }
    evidence.ok = true
  } catch (error) {
    evidence.ok = false
    evidence.error = error instanceof Error ? error.message : String(error)
    if (mode === 'work-cycle' && error?.captainProgress) {
      // No replacement submission: observe the exact accepted session already
      // paid/reserved for, and report the Worker's in-flight execution state.
      evidence.captainProgress = error.captainProgress
      console.error('Worker acceptance: captain.accepted-not-terminal', JSON.stringify(error.captainProgress))
      try {
        evidence.captainWorkerProgress = captainWorkerExecutionAtFailure(
          await readWorkerLiveStatus(page), error.captainProgress)
      } catch {
        evidence.captainWorkerProgress = {statusReadable: false, verdict: 'worker-status-unavailable'}
      }
      console.error('Worker acceptance: captain.worker-execution-at-timeout',
        JSON.stringify(evidence.captainWorkerProgress))
    }
    if (mode === 'work-cycle' && error?.captainEvidence) {
      // Both observations come from this same acceptance run. No second
      // Captain command, no Hub mutation and no new spending authorization.
      evidence.captainFailure = error.captainEvidence
      console.error('Worker acceptance: captain.failure-diagnosis', JSON.stringify(evidence.captainFailure))
      try {
        evidence.workerAtCaptainFailure = captainWorkerAtFailure(await readWorkerLiveStatus(page))
        console.error('Worker acceptance: captain.worker-at-failure', JSON.stringify(evidence.workerAtCaptainFailure))
      } catch {
        evidence.workerAtCaptainFailure = {statusReadable: false}
        console.error('Worker acceptance: captain.worker-at-failure', JSON.stringify(evidence.workerAtCaptainFailure))
      }
    }
    evidence.workerWalletEventTypes = await readWorkerWalletTrace(page)
    // Repeat after the network switch, when MetaMask's own UI may be showing
    // an RPC connection failure. Distinguish HTTP rejection from CORS/network
    // errors without logging request bodies, secrets, or extension HTML.
    if (rpcProbeBefore && !evidence.workNowRejection) {
      try {
        evidence.rpcProbeAfterFailure = await probeRpcClients(context, page, extensionId)
        console.error('RPC clients at failure:', JSON.stringify(rpcProbeSummary(evidence.rpcProbeAfterFailure)))
      } catch {
        console.error('RPC clients at failure: read-only probe unavailable')
      }
    }
    await page.screenshot({path: resolve(reportDir, 'worker-failure.png')}).catch(() => undefined)
    throw error
  } finally {
    if (startedWork) {
      // The acceptance harness must not leave a work session running.
      try {
        await page.locator('#worker-network-work-now').click()
        const finishing = page.waitForResponse(r => {
          try { return r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/applications/worker/work-now' }
          catch { return false }
        }, {timeout: 60_000})
        await page.locator('#worker-work-now-finish').click()
        const response = await finishing
        if (!response.ok()) throw new Error('Work-now finish refused (HTTP ' + response.status() + ')')
        const body = await response.json()
        if (body?.ok !== true) throw new Error('Work-now finish not confirmed by local backend')
        stages.push('work-session-finish-confirmed')
      } catch (error) {
        evidence.ok = false
        evidence.cleanupError = error instanceof Error ? error.message : String(error)
      }
    }
    if (changedPaidPolicy) {
      try {
        const saved = page.waitForResponse(r => {
          try { return r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/applications/worker/settings' }
          catch { return false }
        }, {timeout: 30_000})
        await page.locator('#worker-rental-enabled').uncheck()
        const response = await saved
        if (!response.ok()) throw new Error('Worker paid-work policy restore failed (HTTP ' + response.status() + ')')
        const body = await response.json()
        if (body?.ok !== true || body?.settings?.sellerEnabled !== false)
          throw new Error('Worker paid-work policy restore was not confirmed')
      } catch (error) {
        evidence.ok = false
        evidence.cleanupError = error instanceof Error ? error.message : String(error)
      }
    }
    if (originalOfferTargetTokens !== null) {
      try {
        await setWorkerOfferTargetTokens(page, originalOfferTargetTokens)
        stages.push('worker-test-offer-restored')
        console.log('Worker acceptance: worker.work-cycle.original-offer-restored')
      } catch (error) {
        evidence.ok = false
        evidence.cleanupError = 'Worker offer token budget restoration failed: ' +
          (error instanceof Error ? error.message : String(error))
      }
    }
    evidence.events = logs
    writeFileSync(resolve(reportDir, 'acceptance.json'), JSON.stringify(evidence, null, 2) + '\n', {mode: 0o600})
    if (evidence.cleanupError) throw new Error('Work session cleanup failed: ' + evidence.cleanupError)
  }
}

main().catch(error => {
  console.error('WORKER_BROWSER_ACCEPTANCE_FAILED:', error instanceof Error ? error.message : String(error))
  process.exitCode = 1
})
