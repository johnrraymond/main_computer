// Read-only differential JSON-RPC probe. Execute the same safe chain/block
// requests from three *real* Chromium origins: local Worker page, unpacked
// MetaMask renderer, and MetaMask service worker. Never enter a wallet secret,
// intercept/modify RPC, or substitute results for Worker acceptance.
export const TARGET_RPC = 'https://testnet-rpc.greatlibrary.io/'
export const EXPECTED_CHAIN = '0x28757b1'

const METHODS = ['eth_chainId', 'eth_blockNumber']
const EXTENSION_ID_RE = /^[a-p]{32}$/

export async function probeOneRpcMethod({url, method, timeoutMs}) {
  const safeMethod = method === 'eth_chainId' || method === 'eth_blockNumber'
  if (!safeMethod || url !== 'https://testnet-rpc.greatlibrary.io/') {
    return {kind: 'invalid-probe-parameters'}
  }
  // These breadcrumbs distinguish a probe-runtime defect (e.g. missing
  // AbortSignal.timeout) from a fetch rejected after dispatch. Do not expose
  // arbitrary exception messages, headers, or RPC response bodies.
  let stage = 'build-request'
  try {
    const request = {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({jsonrpc: '2.0', id: 1, method, params: []}),
      credentials: 'omit',
      redirect: 'error',
      cache: 'no-store',
      signal: AbortSignal.timeout(timeoutMs),
    }
    stage = 'invoke-fetch'
    const pending = fetch(url, request)
    stage = 'await-fetch'
    const response = await pending
    const status = response.status
    const info = {kind: response.ok ? 'http-ok' : 'http-rejected', status}
    if (!response.ok) return info
    stage = 'read-json'
    let payload
    try { payload = await response.json() }
    catch { return {kind: 'malformed-json', status} }
    if (payload?.jsonrpc !== '2.0' || payload?.id !== 1 ||
        typeof payload?.result !== 'string') {
      return {kind: 'invalid-rpc-result', status}
    }
    if (method === 'eth_chainId') {
      return {kind: payload.result.toLowerCase() === '0x28757b1' ? 'verified-chain' : 'wrong-chain',
        status, chain: /^0x[\da-f]+$/i.test(payload.result) ? payload.result.toLowerCase() : 'invalid'}
    }
    return {kind: /^0x[\da-f]+$/i.test(payload.result) ? 'valid-block' : 'invalid-block', status}
  } catch (error) {
    const name = typeof error?.name === 'string' ? error.name : ''
    const safeName = ['TypeError', 'AbortError', 'TimeoutError', 'NetworkError',
      'SecurityError', 'NotSupportedError', 'ReferenceError', 'SyntaxError',
      'RangeError', 'InvalidStateError', 'Error'].includes(name) ? name : 'other'
    return {
      kind: name === 'TimeoutError' || name === 'AbortError' ? 'timeout' : 'fetch-rejected',
      errorClass: safeName,
      errorStage: stage,
      // Feature booleans are safe; their absence explains errors which cannot
      // originate in the network, including ReferenceError during setup.
      runtimeFeatures: {
        fetch: typeof fetch === 'function',
        abortSignalTimeout: typeof AbortSignal !== 'undefined' &&
          typeof AbortSignal.timeout === 'function',
      },
    }
  }
}

// CDP is needed for Chromium MV3 service workers not exposed by
// Playwright.context.serviceWorkers(). Restrict attachment to the extension
// ID proven by Chromium, and issue only pinned, read-only RPC methods.
class CDPNestedSession {
  constructor(root, sessionId) {
    this.root = root
    this.sessionId = sessionId
    this.nextId = 1
  }
  async send(method, params = {}) {
    const id = this.nextId++
    return await new Promise((resolve, reject) => {
      let timer
      const finish = () => {
        clearTimeout(timer)
        this.root.off('Target.receivedMessageFromTarget', onMessage)
      }
      const onMessage = event => {
        if (event.sessionId !== this.sessionId) return
        let data
        try { data = JSON.parse(event.message) } catch { return }
        if (data.id !== id) return
        finish()
        if (data.error) reject(new Error('cdp-command-rejected'))
        else resolve(data.result || {})
      }
      this.root.on('Target.receivedMessageFromTarget', onMessage)
      timer = setTimeout(() => { finish(); reject(new Error('cdp-command-timeout')) }, 9_000)
      this.root.send('Target.sendMessageToTarget', {
        sessionId: this.sessionId,
        message: JSON.stringify({id, method, params}),
      }).catch(() => { finish(); reject(new Error('cdp-transport-unavailable')) })
    })
  }
}

// Observe only the pinned public JSON-RPC request, without capturing bodies,
// headers, cookies, arbitrary URLs, or unrelated MetaMask network traffic.
// The CDP events distinguish HTTP denial from a browser-level CORS/preflight
// block even when fetch() exposes only an unhelpful TypeError.
export function observePinnedRpcNetwork(root, sessionId) {
  const requestIds = new Set()
  const observations = []
  const pinned = url => {
    try {
      const parsed = new URL(url)
      return parsed.protocol === 'https:' &&
        parsed.hostname === 'testnet-rpc.greatlibrary.io' &&
        parsed.port === '' && parsed.pathname === '/' && parsed.search === ''
    } catch { return false }
  }
  const listen = event => {
    if (event.sessionId !== sessionId) return
    let data
    try { data = JSON.parse(event.message) } catch { return }
    if (!data.method?.startsWith('Network.')) return
    const {method, params = {}} = data
    if (method === 'Network.requestWillBeSent' && pinned(params.request?.url)) {
      requestIds.add(params.requestId)
      return
    }
    if (!requestIds.has(params.requestId)) return
    if (method === 'Network.responseReceived' && Number.isInteger(params.response?.status)) {
      observations.push({type: 'response', status: params.response.status})
    } else if (method === 'Network.responseReceivedExtraInfo' && Number.isInteger(params.statusCode)) {
      observations.push({type: 'response', status: params.statusCode})
    } else if (method === 'Network.loadingFailed') {
      const reason = params.corsErrorStatus?.corsError
      if (typeof reason === 'string' && /^[A-Za-z0-9_]+$/.test(reason)) {
        observations.push({type: 'cors', corsError: reason})
      } else if (typeof params.blockedReason === 'string' && /^[A-Za-z0-9_]+$/.test(params.blockedReason)) {
        observations.push({type: 'blocked', blockedReason: params.blockedReason})
      } else if (typeof params.errorText === 'string' && /^net::ERR_[A-Z0-9_]+$/.test(params.errorText)) {
        observations.push({type: 'network-error', netError: params.errorText})
      } else {
        observations.push({type: 'loading-failed'})
      }
    }
  }
  root.on('Target.receivedMessageFromTarget', listen)
  return {
    observation: () => {
      const statuses = [...new Set(observations.filter(item => item.type === 'response').map(item => item.status))]
      // Prefer a diagnostic from the browser over guessing from fetch's TypeError.
      const final = [...observations].reverse().find(item => item.type === 'cors') ||
        [...observations].reverse().find(item => item.type === 'blocked') ||
        [...observations].reverse().find(item => item.type === 'network-error') ||
        [...observations].reverse().find(item => item.type === 'loading-failed')
      if (!final && statuses.length === 0) return {kind: 'no-network-events'}
      return {kind: final?.type || 'http-response-observed',
        ...(final?.corsError ? {corsError: final.corsError} : {}),
        ...(final?.blockedReason ? {blockedReason: final.blockedReason} : {}),
        ...(final?.netError ? {netError: final.netError} : {}),
        ...(statuses.length ? {httpStatuses: statuses} : {})}
    },
    close: () => root.off('Target.receivedMessageFromTarget', listen),
  }
}

function isExactExtensionTarget(target, extensionId) {
  if (target?.type !== 'service_worker') return false
  try {
    const url = new URL(target.url)
    return url.protocol === 'chrome-extension:' && url.host === extensionId
  } catch { return false }
}

export async function probeExtensionServiceWorkerCDP(context, workerPage, extensionId) {
  if (!EXTENSION_ID_RE.test(extensionId)) throw new Error('Invalid MetaMask extension ID')
  if (typeof context.newCDPSession !== 'function' || !workerPage) {
    return {available: false, reason: 'cdp-not-supported'}
  }
  let root
  try {
    root = await context.newCDPSession(workerPage)
    const targets = (await root.send('Target.getTargets')).targetInfos || []
    const target = targets.find(info => isExactExtensionTarget(info, extensionId))
    if (!target) return {available: false, reason: 'no-extension-service-worker-target'}
    let sessionId
    try {
      const attachment = await root.send('Target.attachToTarget', {
        targetId: target.targetId, flatten: false,
      })
      sessionId = attachment.sessionId
      const nested = new CDPNestedSession(root, sessionId)
      await nested.send('Runtime.enable')
      const methods = {}
      // Failure to enable the Network domain is diagnostic, not permission
      // to fail the RPC probe. Unsupported targets still return fetch results.
      let networkEventsAvailable = false
      try {
        await nested.send('Network.enable')
        networkEventsAvailable = true
      } catch { /* target lacks Network domain */ }
      for (const method of METHODS) {
        const networkObserver = networkEventsAvailable
          ? observePinnedRpcNetwork(root, sessionId) : null
        try {
        // Evaluate a fixed, self-contained read-only fetch in the *worker's*
        // own execution context; no wallet secrets, provider requests, or DOM.
        const expression = `(${probeOneRpcMethod.toString()})(${JSON.stringify({
          url: TARGET_RPC, method, timeoutMs: 6_000,
        })})`
        const output = await nested.send('Runtime.evaluate', {
          expression, awaitPromise: true, returnByValue: true, silent: true,
        })
        const value = output?.result?.value
        if (output.exceptionDetails || !value || typeof value.kind !== 'string') {
          return {available: false, reason: 'cdp-worker-evaluation-unavailable'}
        }
        if (value.kind === 'fetch-rejected' || value.kind === 'timeout') {
          // Allow already-in-flight Network.loadingFailed to arrive.
          if (networkObserver) await new Promise(resolve => setTimeout(resolve, 150))
          methods[method] = {...value, networkEvidence: networkObserver
            ? networkObserver.observation() : {kind: 'network-domain-unavailable'}}
        } else {
          methods[method] = value
        }
        } finally { networkObserver?.close() }
      }
      return {available: true, via: 'cdp-service-worker', methods}
    } catch {
      return {available: false, reason: 'cdp-service-worker-attach-or-evaluation-failed'}
    } finally {
      if (sessionId) await root.send('Target.detachFromTarget', {sessionId}).catch(() => undefined)
    }
  } catch {
    return {available: false, reason: 'cdp-target-discovery-failed'}
  } finally {
    if (root) await root.detach().catch(() => undefined)
  }
}

async function probeClient(client, url, timeoutMs = 6000) {
  if (!client || typeof client.evaluate !== 'function') {
    return {available: false, reason: 'target-not-present'}
  }
  try {
    const methods = {}
    for (const method of METHODS) {
      methods[method] = await client.evaluate(probeOneRpcMethod,
        {url, method, timeoutMs})
    }
    return {available: true, methods}
  } catch (error) {
    // A closed/stale renderer, destroyed execution context, or CDP failure
    // must not be misrepresented as an RPC response.
    const detail = String(error?.message || '')
    const reason = /Target.*closed|Page.*closed|Browser.*closed/i.test(detail)
      ? 'renderer-closed' : /context.*destroyed|Cannot find context/i.test(detail)
      ? 'renderer-navigated-during-probe' : /Timeout/i.test(detail)
      ? 'renderer-evaluation-timeout' : 'renderer-evaluation-failed'
    return {available: false, reason}
  }
}

export async function probeRpcClients(context, workerPage, extensionId, options = {}) {
  if (!EXTENSION_ID_RE.test(extensionId)) throw new Error('Invalid MetaMask extension ID')
  const result = {schema: 'main-computer.rpc-origin-probe.v1',
    target: 'testnet-rpc.greatlibrary.io',
    extensionOrigin: `chrome-extension://${extensionId}`}
  result.workerPage = await probeClient(workerPage, TARGET_RPC)

  const extensionPages = context.pages().filter(page => {
    try { return !page.isClosed() && page.url().startsWith(result.extensionOrigin + '/') }
    catch { return false }
  })
  result.extensionPage = {available: false, reason: 'no-extension-renderer'}
  let temporaryPage = null
  // In standalone rpc-probe mode, explicitly open one page at the known,
  // verified extension ID. Merely observing a fresh profile normally leaves
  // context.pages() empty for this origin. This performs no wallet actions.
  if (extensionPages.length === 0 && options.openExtensionPage === true &&
      typeof context.newPage === 'function') {
    try {
      temporaryPage = await context.newPage()
      await temporaryPage.goto(`${result.extensionOrigin}/home.html`, {
        waitUntil: 'domcontentloaded', timeout: 15_000,
      })
      if (temporaryPage.url().startsWith(result.extensionOrigin + '/')) {
        extensionPages.push(temporaryPage)
      } else {
        result.extensionPage.reason = 'renderer-navigated-away'
      }
    } catch {
      result.extensionPage.reason = 'extension-renderer-navigation-failed'
    }
  }
  try {
    for (const extensionPage of extensionPages) {
      const probe = await probeClient(extensionPage, TARGET_RPC)
      if (probe.available) { result.extensionPage = probe; break }
      result.extensionPage = probe
    }

    const extensionWorkers = context.serviceWorkers().filter(worker => {
      try { return worker.url().startsWith(result.extensionOrigin + '/') }
      catch { return false }
    })
    result.extensionServiceWorker = {available: false, reason: 'no-playwright-extension-worker'}
    for (const worker of extensionWorkers) {
      const probe = await probeClient(worker, TARGET_RPC)
      if (probe.available) { result.extensionServiceWorker = probe; break }
      result.extensionServiceWorker = probe
    }
    if (!result.extensionServiceWorker.available && options.probeCDPServiceWorker === true) {
      // Chromium may expose an MV3 worker via CDP even if the Playwright
      // worker wrapper has been suspended, detached, or never surfaced.
      result.extensionServiceWorker = await probeExtensionServiceWorkerCDP(
        context, workerPage, extensionId)
    }
  } finally {
    if (temporaryPage) await temporaryPage.close().catch(() => undefined)
  }
  return result
}

export function rpcProbeSummary(result) {
  const verdict = client => {
    if (!client?.available) return client?.reason || 'unavailable'
    const chain = client.methods?.eth_chainId?.kind
    const block = client.methods?.eth_blockNumber?.kind
    return chain === 'verified-chain' && block === 'valid-block'
      ? 'PASS' : `chain:${chain || 'unknown'},block:${block || 'unknown'}`
  }
  return {workerPage: verdict(result.workerPage), extensionPage: verdict(result.extensionPage),
    extensionServiceWorker: verdict(result.extensionServiceWorker)}
}
