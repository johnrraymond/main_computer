// Native Windows Playwright runner: no Synpress CLI/cache builder required.
// Uses Synpress MetaMask's browser interaction *library* with a real extension.
// Only the disposable test-only Chromium profile is used.
import { chromium, expect } from '@playwright/test'
import { MetaMask } from '@synthetixio/synpress/playwright'
import { importDisposableWallet, safeExtensionTabsDiagnostics, selectMetaMaskPage, verifyWalletHomeIdentity } from './metamask_onboarding.mjs'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { completeWorkerWalletConnection, installWorkerWalletTrace, readWorkerWalletTrace, startWorkerWalletConnection } from './worker_wallet_approvals.mjs'
import { resolve } from 'node:path'
import {probeRpcClients, rpcProbeSummary} from './rpc_client_probe.mjs'
import {approveWorkerMskSignature} from './metamask_sidepanel_cdp.mjs'
import {isMskEndpointRequest, waitForWorkerMsk, workerMskEvent} from './worker_msk_outcome.mjs'

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
    // Persistent profile may have auto-locked since the previous acceptance run.
    const locked = activePage.getByTestId('unlock-password')
    if (await locked.isVisible().catch(() => false)) {
      await locked.fill(walletSecret.password)
      await activePage.getByRole('button', {name: /unlock/i}).click()
    }
  }
  return new MetaMask(context, activePage, walletSecret.password, extensionId)
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
  if (mode !== 'wallet' && mode !== 'worker') throw new Error('Unsupported acceptance mode')
  const evidence = { schema: 'main-computer.worker-browser-acceptance.v1', mode, network: 'testnet', stages: [] }
  const stages = evidence.stages
  const reportDir = resolve(process.env.MC_WORKER_TEST_REPORT_DIR || 'test-results')
  mkdirSync(reportDir, { recursive: true })
  let startedWork = false
  let changedPaidPolicy = false
  const logs = []
  let mskApiStatus = null
  let mskApiRequestFailed = false
  let rpcProbeBefore = null
  page.on('response', response => {
    if (!isMskEndpointRequest(response.request())) return
    mskApiStatus = response.status()
    console.log('Worker acceptance: worker.msk.local-api-http.' + mskApiStatus)
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
    await startWorkerWalletConnection(page, {
      onProgress: phase => console.log('Worker acceptance:', phase),
    })
    const approvals = await completeWorkerWalletConnection({
      context, page, extensionId, expectedAddress: walletSecret.address,
      onProgress: phase => console.log('Worker acceptance:', phase),
    })
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
    const state = (await page.locator('#worker-multisession-key-state').innerText()).trim()
    const alreadyActive = state === 'Active'
    if (alreadyActive && mode === 'wallet') {
      throw new Error('Test wallet already has an active MSK. Use a fresh disposable wallet for a new signing test; no automatic revocation.')
    }
    if (!alreadyActive) {
      await page.locator('#worker-request-multisession-key').click()
      // Wallet signs locally; Hub must independently verify the signature.
      await approveWorkerMskSignature(context, page, extensionId, walletSecret.address, {
        onProgress: phase => console.log('Worker acceptance:', phase),
      })
      await waitForWorkerMsk({
        readState: () => page.locator('#worker-multisession-key-state').innerText(),
        readStatus: () => page.locator('#worker-save-status').innerText().catch(() => ''),
        readEvents: () => logs.filter(v => v.startsWith('msk.')).map(v => v.slice(4)),
        readApiStatus: () => mskApiStatus,
        readApiRequestFailed: () => mskApiRequestFailed,
        onProgress: phase => console.log('Worker acceptance: worker.msk.phase.' + phase),
      })
      stages.push('hub-msk-issued')
    } else {
      stages.push('existing-msk-reused-for-registration')
    }
    const keyId = (await page.locator('#worker-multisession-key-id').innerText()).trim()
    if (!keyId || keyId === '—') throw new Error('No Hub-issued multi-session key ID is available')
    evidence.keyIssued = !alreadyActive
    evidence.keyId = keyId

    if (mode === 'worker') {
      // Optional, explicitly authorized mode: worker advertises compute.
      const enabled = page.locator('#worker-rental-enabled')
      if (!(await enabled.isChecked())) { await enabled.check(); changedPaidPolicy = true }
      await page.locator('#worker-network-ring').selectOption('3')
      await expect(page.locator('#worker-network-work-now')).toBeEnabled()
      await page.locator('#worker-network-work-now').click()
      await expect(page.locator('#worker-work-now-dialog')).toBeVisible()
      await page.locator('#worker-work-now-15').click()
      startedWork = true
      await expect(page.locator('#worker-network-hub-registration')).toHaveText('Accepted', {timeout: 45_000})
      const registered = (await page.locator('#worker-network-hub-registration').innerText()).trim()
      if (/failed|error|rejected|wrong chain|unreachable/i.test(registered)) throw new Error('Hub registration failed: ' + registered)
      const workerId = (await page.locator('#worker-network-worker-id').innerText()).trim()
      if (!workerId || workerId === '—') throw new Error('No Hub worker identity was reported')
      evidence.workerId = workerId
      stages.push('hub-worker-registered')
      // This mode certifies registration, not paid execution or settlement.
    }
    evidence.ok = true
  } catch (error) {
    evidence.ok = false
    evidence.error = error instanceof Error ? error.message : String(error)
    evidence.workerWalletEventTypes = await readWorkerWalletTrace(page)
    // Repeat after the network switch, when MetaMask's own UI may be showing
    // an RPC connection failure. Distinguish HTTP rejection from CORS/network
    // errors without logging request bodies, secrets, or extension HTML.
    if (rpcProbeBefore) {
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
        await page.locator('#worker-work-now-finish').click()
        stages.push('work-session-finish-requested')
      } catch (error) {
        evidence.ok = false
        evidence.cleanupError = error instanceof Error ? error.message : String(error)
      }
    }
    if (changedPaidPolicy) {
      try { await page.locator('#worker-rental-enabled').uncheck() }
      catch (error) {
        evidence.ok = false
        evidence.cleanupError = error instanceof Error ? error.message : String(error)
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
