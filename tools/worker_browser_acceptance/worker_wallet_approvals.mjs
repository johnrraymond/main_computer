import {advanceSidepanelApproval} from './metamask_sidepanel_cdp.mjs'
// Approve ONLY known, test-scoped MetaMask UI requests produced by the actual
// Worker Connect Wallet handler. No provider mocks, extension-store edits,
// synthetic RPC responses, or generic "Confirm" fallback.
// The real Worker makes wallet_addEthereumChain and wallet_switchEthereumChain requests.
const EXPECTED_CHAIN = 'Main Computer Testnet'
const EXPECTED_ORIGIN = /\b(?:127\.0\.0\.1|localhost):8765\b(?![.\w-])/i
const EXPECTED_RPC = /(?:https?:\/\/)?testnet-rpc\.greatlibrary\.io\b(?![.\w-])/i
const EXTENSION_RE = /^[a-p]{32}$/
const ADD_HEADING = /^(?:Add Main Computer Testnet|Add (?:a |this |the )?network)$/i
const SWITCH_HEADING = /^(?:Switch (?:to )?Main Computer Testnet|Switch (?:the |to |this )?network)$/i
const CONNECT_HEADING = /^(?:Connect (?:with MetaMask|to this site|to site|account)|Select (?:an? )?account)/i
// MetaMask 13.53 displays only the HOST (without :8765) on its current
// "Connect this website with MetaMask" permission screen. Never treat a bare
// hostname as authorization unless the active Worker page has the exact local
// scheme, host, port and path expected by this acceptance harness.
function trustedWorkerPage(page) {
  try {
    const url = new URL(page.url())
    return url.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(url.hostname) &&
      url.port === '8765' && url.pathname === '/applications/worker'
  } catch { return false }
}

function isExtensionPage(page, extensionId) {
  try { return page.url().startsWith(`chrome-extension://${extensionId}/`) && !page.isClosed?.() }
  catch { return false }
}
function pageKind(page) {
  try {
    const filename = new URL(page.url()).pathname.split('/').pop()?.toLowerCase() || ''
    // No route fragments or query strings in diagnostics: extension URLs can
    // carry data that must not be logged.
    return /^(notification|popup|sidepanel|side-panel|sidebar|home)\.html$/.test(filename)
      ? filename.replace('.html', '') : 'other-extension'
  } catch { return 'unknown' }
}
async function visibleText(page, text) {
  return Boolean(await page.getByText(text).first().isVisible().catch(() => false))
}
async function visibleButton(page, name) {
  const item = page.getByRole('button', {name, exact: true}).first()
  return (await item.isVisible().catch(() => false)) && (await item.isEnabled().catch(() => false)) ? item : null
}
async function clickFirst(page, names) {
  for (const name of names) {
    const item = await visibleButton(page, name)
    if (item) { await item.click({timeout: 4_000}); return name }
  }
  return null
}

async function permissionState(page, workerPage = null) {
  const [chain, origin, rpc, add, change, connect] = await Promise.all([
    visibleText(page, EXPECTED_CHAIN), visibleText(page, EXPECTED_ORIGIN),
    visibleText(page, EXPECTED_RPC), visibleText(page, ADD_HEADING),
    visibleText(page, SWITCH_HEADING), visibleText(page, CONNECT_HEADING),
  ])
  const modernConnect = trustedWorkerPage(workerPage) &&
    await visibleText(page, /^(?:127\.0\.0\.1|localhost)$/i) &&
    await visibleText(page, /Connect this website with MetaMask/i) &&
    await visibleText(page, /See addresses, balances, and activity/i)
  return {chain, origin, rpc, add, change, connect, modernConnect}
}

// The screenshot for MetaMask 13.53 says "Add Main Computer Testnet" rather
// than "Add a network", and has a button labelled "Confirm". Require all
// three independent display anchors: network name, local origin and RPC host.
// These checks apply even when Chrome renders the approval in a side panel or
// an extension home.html tab; no other home controls are clicked.
export async function advanceWorkerWalletApproval(context, extensionId, workerPage = null) {
  if (!EXTENSION_RE.test(extensionId)) throw new Error('Worker approval refused: invalid extension identity')
  for (const page of context.pages()) {
    if (!isExtensionPage(page, extensionId)) continue
    const state = await permissionState(page, workerPage)
    if (state.chain && state.origin && state.rpc && state.add) {
      const action = await clickFirst(page, ['Confirm', 'Add network', 'Add this network', 'Approve'])
      if (action) return {type: 'network-add', action}
    }
    if (state.chain && state.origin && state.change) {
      const action = await clickFirst(page, ['Switch network', 'Switch', 'Confirm', 'Approve'])
      if (action) return {type: 'network-switch', action}
    }
    if ((state.origin && state.connect) || state.modernConnect) {
      const action = await clickFirst(page, ['Next', 'Connect', 'Confirm', 'Connect to site'])
      if (action) return {type: 'account-connect', action}
    }
    // IMPORTANT: signature, transaction and unrelated confirmations are never
    // approved here, even if they contain the testnet name.
  }
  return await advanceSidepanelApproval(context, workerPage, extensionId)
}

// The Worker panel can render before worker.js has installed its click handler.
// A Playwright click on that early DOM is silently lost. Wait for the actual
// binding and for hydration to release the button, then demand evidence that
// the single click started the application's real connection state machine.
async function safeConnectControlState(page) {
  try {
    return await page.evaluate(() => {
      const button = document.querySelector('#worker-connect-wallet')
      const text = String(button?.textContent || '').trim()
      return {
        present: Boolean(button),
        handlerBound: button?.dataset?.workerBound === 'true',
        disabled: Boolean(button?.disabled),
        busy: button?.getAttribute('aria-busy') === 'true',
        state: /^Connect Wallet$/i.test(text) ? 'idle'
          : /^Checking Wallet/i.test(text) ? 'hydrating'
          : /^Wallet Open/i.test(text) ? 'requesting'
          : /^Finalizing/i.test(text) ? 'finalizing'
          : /^Connected$/i.test(text) ? 'connected' : 'other'
      }
    })
  } catch { return {available: false} }
}

export async function startWorkerWalletConnection(page, {
  readinessMs = 45_000, clickTimeoutMs = 10_000, activityMs = 12_000,
  onProgress = () => {}
} = {}) {
  try {
    await page.waitForFunction(() => {
      const button = document.querySelector('#worker-connect-wallet')
      return Boolean(button?.dataset?.workerBound === 'true' && !button.disabled)
    }, null, {timeout: readinessMs})
  } catch {
    throw new Error('Worker Connect Wallet never became handler-bound and enabled: '
      + JSON.stringify(await safeConnectControlState(page)))
  }
  onProgress('worker.connect.control-ready')
  try {
    await page.locator('#worker-connect-wallet').click({timeout: clickTimeoutMs})
  } catch {
    throw new Error('Worker Connect Wallet click could not be dispatched: '
      + JSON.stringify(await safeConnectControlState(page)))
  }
  onProgress('worker.connect.click-dispatched')
  try {
    await page.waitForFunction(() => {
      const trace = window.__mcWorkerWalletTrace
      if (Array.isArray(trace) && trace.some(type => /^connect\./.test(type))) return true
      const button = document.querySelector('#worker-connect-wallet')
      return button?.getAttribute('aria-busy') === 'true'
    }, null, {timeout: activityMs})
  } catch {
    throw new Error('Worker Connect Wallet click produced no connection event or busy state: '
      + JSON.stringify(await safeConnectControlState(page)))
  }
  if ((await readWorkerWalletTrace(page)).includes('connect.ignored.busy')) {
    throw new Error('Worker ignored Connect Wallet because another wallet operation was active: '
      + JSON.stringify(await safeConnectControlState(page)))
  }
  onProgress('worker.connect.activity-observed')
}

// Observe the Worker's own wallet state-machine events without copying the
// event payload (addresses, signatures, RPC parameters, and other secrets).
// This is diagnostic instrumentation on the test page, not a provider stub.
export async function installWorkerWalletTrace(page) {
  await page.evaluate(() => {
    if (Array.isArray(window.__mcWorkerWalletTrace)) return
    const trace = []
    Object.defineProperty(window, '__mcWorkerWalletTrace', {value: trace, configurable: false})
    window.addEventListener('main-computer-worker-wallet', event => {
      const type = event?.detail?.type
      if (typeof type === 'string' && /^connect\.[a-zA-Z0-9_.-]{1,96}$/.test(type)) {
        trace.push(type)
        if (trace.length > 96) trace.shift()
      }
    })
  })
}

export async function readWorkerWalletTrace(page) {
  if (typeof page.evaluate !== 'function') return []
  try {
    const types = await page.evaluate(() => Array.isArray(window.__mcWorkerWalletTrace)
      ? window.__mcWorkerWalletTrace.slice(-96) : [])
    return Array.isArray(types) ? types.filter(type =>
      typeof type === 'string' && /^connect\.[a-zA-Z0-9_.-]{1,96}$/.test(type)) : []
  } catch { return [] }
}

// Some Chromium extension side panels have a DevTools target but not a
// Playwright Page. Read target CLASSIFICATIONS only; never dump URLs, HTML,
// page text, or target identifiers. This probe does not interact with MetaMask.
async function safeExtensionTargetKinds(context, page, extensionId) {
  if (!page || typeof context.newCDPSession !== 'function') return []
  let session
  try {
    session = await context.newCDPSession(page)
    const result = await session.send('Target.getTargets')
    const types = []
    for (const target of result?.targetInfos || []) {
      if (typeof target.url !== 'string' || !target.url.startsWith(`chrome-extension://${extensionId}/`)) continue
      const basename = new URL(target.url).pathname.split('/').pop()?.toLowerCase() || ''
      const kind = ['notification.html','sidepanel.html','popup.html','home.html'].includes(basename)
        ? basename.slice(0, -5) : 'other-extension'
      types.push({kind, type: ['page','other','background_page','service_worker'].includes(target.type)
        ? target.type : 'other'})
    }
    return types.slice(0, 16)
  } catch { return [] }
  finally { await session?.detach?.().catch(() => undefined) }
}

// Only safe classifications and counts: never copy DOM, screenshot an
// extension, record recovery phrases, account secrets or signature payloads.
export async function safeWalletApprovalDiagnostics(context, extensionId, workerPage = null) {
  const pages = context.pages().filter(page => isExtensionPage(page, extensionId))
  const states = []
  for (const page of pages) {
    const p = await permissionState(page, workerPage)
    states.push({kind: pageKind(page), networkAdd: p.chain && p.add,
      originMatched: p.origin, rpcMatched: p.rpc,
      networkSwitch: p.chain && p.change, accountConnect: (p.origin && p.connect) || p.modernConnect})
  }
  return {extensionPages: pages.length, pages: states,
    cdpExtensionTargets: await safeExtensionTargetKinds(context, workerPage, extensionId)}
}

function statusPhase(text) {
  if (/^Connect failed:/i.test(text)) return 'connect-failed'
  if (/Checking browser wallet network/i.test(text)) return 'network-preflight'
  if (/network update/i.test(text)) return 'network-update-requested'
  if (/MetaMask RPC ready/i.test(text)) return 'rpc-ready'
  if (/RPC backoff|retrying in/i.test(text)) return 'rpc-backoff'
  if (/switching (?:wallet|to)|network switch/i.test(text)) return 'network-switch-requested'
  if (/Opening browser wallet account request/i.test(text)) return 'account-requested'
  if (/Wallet account request accepted/i.test(text)) return 'account-approved'
  if (/verifying signer|finalizing/i.test(text)) return 'finalizing'
  if (/Connected wallet/i.test(text)) return 'connected'
  return null // Never print a misleading "worker.other" progress phase.
}

// Error text comes from the Worker's status UI, not the extension. Redact
// credentials, query strings and long opaque tokens before reporting it.
function safeWorkerError(text) {
  return String(text || 'unknown Worker error')
    .replace(/https?:\/\/[^\s]+/gi, match => {
      try { return new URL(match).origin } catch { return '[URL]' }
    })
    .replace(/\b(?:0x)?[a-f0-9]{64,}\b/gi, '[opaque-id]')
    .slice(0, 280)
}

async function boundedApprovalProbe(approve, timeoutMs) {
  let timer
  try {
    return await Promise.race([
      Promise.resolve().then(approve),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(
          'MetaMask approval inspection stalled; no unverified approval was accepted')), timeoutMs)
      }),
    ])
  } finally {
    clearTimeout(timer)
  }
}

export async function completeWorkerWalletConnection({context, page, extensionId, expectedAddress,
  deadlineMs = 150_000, pollMs = 300, unrecognizedMs = 15_000, approvalProbeMs = 12_000,
  now = () => Date.now(), sleep = ms => new Promise(r => setTimeout(r, ms)),
  approve = advanceWorkerWalletApproval, diagnostics = safeWalletApprovalDiagnostics,
  onProgress = () => {}} = {}) {
  const start = now()
  const expectedPrefix = expectedAddress.slice(0, 6).toLowerCase()
  const expectedSuffix = expectedAddress.slice(-4).toLowerCase()
  const approvals = []
  let lastPhase = ''
  let lastApproval = start
  let observedCount = 0
  let lastEvent = 'none'
  let transitionStart = null
  let transitionReported = false
  while (now() - start < deadlineMs) {
    const shown = await page.locator('#worker-primary-wallet-status').innerText().catch(() => '')
    if (shown.toLowerCase().includes(expectedPrefix) && shown.toLowerCase().includes(expectedSuffix)) return approvals

    const eventTypes = await readWorkerWalletTrace(page)
    for (const type of eventTypes.slice(observedCount)) {
      lastEvent = type
      onProgress('worker.' + type)
    }
    observedCount = eventTypes.length
    if (eventTypes.includes('connect.failed')) {
      const status = await page.locator('#worker-save-status').innerText().catch(() => '')
      const error = /^Connect failed:/i.test(status)
        ? `: ${safeWorkerError(status)}` : ''
      throw new Error(`Worker emitted connect.failed after [${approvals.join(', ') || 'no approvals'}]${error}`)
    }

    const status = await page.locator('#worker-save-status').innerText().catch(() => '')
    const phase = statusPhase(status)
    if (phase && phase !== lastPhase) { lastPhase = phase; onProgress('worker.status.' + phase) }
    if (phase === 'connect-failed') {
      throw new Error(`Worker wallet connection failed after [${approvals.join(', ') || 'no approvals'}]: ${safeWorkerError(status)}`)
    }

    // Only an OPEN approval event warrants inspecting MetaMask. In particular,
    // addChain.done is a completed approval: the Worker must next switch the
    // chain, and probing an idle extension sidepanel at this point can stall
    // the harness even while the Worker proceeds to connected state.
    // A stale UI status must not override the Worker's authoritative events.
    const rpcProofWaiting = lastEvent === 'connect.wallet.rpcProof.backoffWait'
    const approvalRequestedByEvent = /^(?:connect\.wallet\.addChain\.start|connect\.ethers\.switchChain\.start|connect\.ethers\.requestAccounts\.start)$/.test(lastEvent)
    const approvalPending = eventTypes.length ? approvalRequestedByEvent
      : ['network-update-requested','account-requested','network-switch-requested'].includes(phase)
    const result = !rpcProofWaiting && approvalPending
      ? await boundedApprovalProbe(() => approve(context, extensionId, page), approvalProbeMs) : null
    if (result) {
      approvals.push(result.type)
      lastApproval = now()
      onProgress('metamask.' + result.type)
    }

    // Bounded transition watchdog: once the add-network request has completed,
    // the next Worker event should follow. Report a stalled Worker transition
    // explicitly instead of inspecting an approval that no longer exists.
    if (lastEvent === 'connect.wallet.addChain.done') {
      if (transitionStart === null) transitionStart = now()
      const elapsed = now() - transitionStart
      if (!transitionReported && elapsed >= 5_000) {
        onProgress('worker.connect.waiting-after-network-add')
        transitionReported = true
      }
      if (elapsed >= 45_000) {
        throw new Error('Worker did not advance after connect.wallet.addChain.done within 45s; '
          + 'network add was approved, but no next switch/RPC event was observed')
      }
    } else {
      transitionStart = null
      transitionReported = false
    }

    // A still-visible "Connect Wallet" button is NOT proof of failure while
    // an approval is pending. The old 3-second check was a false refusal.
    // Report unknown prompts only if the Worker's current event actually
    // requests approval, never because a stale sidepanel target still exists.
    if (now() - lastApproval >= unrecognizedMs && !rpcProofWaiting && phase !== 'rpc-backoff' && phase !== 'finalizing'
        && (eventTypes.length === 0 || approvalPending)) {
      const details = await diagnostics(context, extensionId, page)
      const pending = details.pages.filter(p =>
        !['home', 'other-extension'].includes(p.kind) ||
        p.networkAdd || p.networkSwitch || p.accountConnect)
      if (pending.length && !result) {
        throw new Error(`MetaMask approval not recognized or not enabled (last Worker event=${lastEvent}): ` + JSON.stringify(details))
      }
      // Some Chromium builds expose side panels outside Playwright pages.
      // Report that boundary explicitly rather than claiming Worker failure.
      if (!pending.length && approvalPending) {
        const sidepanel = details.cdpExtensionTargets?.some(t => t.kind === 'sidepanel')
        const explanation = sidepanel
          ? 'MetaMask side-panel approval is still pending; CDP access or matching approval controls were unavailable'
          : 'Worker is awaiting a MetaMask approval, but no accessible approval page was found'
        throw new Error(`${explanation} (last Worker event=${lastEvent}): ` + JSON.stringify(details))
      }
    }
    await sleep(pollMs)
  }
  const details = await diagnostics(context, extensionId, page)
  const pendingRpc = lastEvent === 'connect.wallet.rpcProof.backoffWait'
  throw new Error(`${pendingRpc ? 'Worker RPC proof did not finish before browser acceptance deadline' : 'Worker wallet connection timed out'} (last event=${lastEvent}, last status phase=${lastPhase || 'unknown'}, approvals=${approvals.join(',') || 'none'}): ${JSON.stringify(details)}`)
}
