// Observable, fail-closed result of the real Worker's multi-session-key request.
// Never log signed payloads, signature bytes, tokens, or raw API bodies.
const MSK_EVENTS = new Set([
  'request.start', 'request.blocked', 'request.ignored',
  'signature.created', 'hub.response', 'request.failed',
])

// The viewport embeds wallet.js once at Python module import. Applying a
// source patch without restarting that server leaves its old signer running.
// Probe the SERVED, executable builder before any wallet approval or signature.
export async function assertServedWorkerMskContract(page) {
  const observed = await page.evaluate(() => {
    const build = window.MainComputerWalletLibrary?.buildMultiSessionKeyMessage
    if (typeof build !== 'function') return {builderAvailable: false}
    try {
      const message = build({
        walletAddress: '0x1111111111111111111111111111111111111111',
        chainId: '0x28757b1',
        origin: window.location.origin,
      })
      // Report booleans only. Do not expose user entropy or any signed payload.
      return {
        builderAvailable: true,
        hasUserSlug: typeof message?.user_slug === 'string' && /^[0-9a-f]{64}$/.test(message.user_slug),
        correctChain: message?.chain_id === '0x28757b1',
        correctPurpose: message?.purpose === 'request_multi_session_key',
      }
    } catch {
      return {builderAvailable: true, builderFailed: true}
    }
  })
  if (observed?.builderAvailable && observed.hasUserSlug && observed.correctChain && observed.correctPurpose) {
    return true
  }
  throw new Error('Served Worker wallet signer lacks the required signed user_slug or is invalid. ' +
    'The viewport embeds wallet.js at server startup: restart the local Main Computer viewport server ' +
    'after applying the wallet.js patch, then rerun acceptance. No MSK signature was requested.')
}

export function workerMskEvent(text) {
  const m = /\[worker-msk\]\s+([a-z.]+)/.exec(String(text || ''))
  return m && MSK_EVENTS.has(m[1]) ? m[1] : null
}

export function isMskEndpointRequest(request) {
  if (!request || request.method() !== 'POST') return false
  try { return new URL(request.url()).pathname === '/api/applications/worker/multisession-key/request' }
  catch { return false }
}

// Only inspect the LOCAL MSK endpoint's failed response. The endpoint emits
// {ok:false,error:string} and may wrap the Hub's own JSON error in its text.
// Never read successful responses: they may contain newly issued key material.
export function safeMskApiRejection(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return 'no-structured-error'
  let reason = typeof body.error === 'string' ? body.error
    : typeof body.message === 'string' ? body.message : ''
  if (!reason) return 'no-structured-error'

  // The local proxy formats upstream failures as:
  // "Hub returned HTTP 400: {\"error\":\"...\"}".
  // Extract only the Hub's error field rather than logging the upstream body.
  const wrapped = /^Hub returned HTTP (\d{3}):\s*(\{[\s\S]*\})$/i.exec(reason.trim())
  if (wrapped) {
    try {
      const parsed = JSON.parse(wrapped[2])
      if (parsed && typeof parsed.error === 'string') {
        reason = `hub-http-${wrapped[1]}: ${parsed.error}`
      } else {
        reason = `hub-http-${wrapped[1]}: unclassified-error`
      }
    } catch {
      reason = `hub-http-${wrapped[1]}: unparseable-error`
    }
  }

  // Fail closed rather than disclose any field which may carry credentials or
  // signed requests. Validation labels such as "invalid signature" are safe.
  if (/(?:mnemonic|seed[ _-]?phrase|recovery[ _-]?phrase|private[ _-]?key|password|authorization|bearer|api[ _-]?key|access[ _-]?token|signed_request|signature|secret)\s*[:=]/i.test(reason)) {
    return 'sensitive-error-detail-redacted'
  }
  const safe = reason
    .replace(/0x[0-9a-f]{16,}/gi, '[hex-redacted]')
    .replace(/\b[A-Fa-f0-9]{64,}\b/g, '[hex-redacted]')
    .replace(/(?:https?:\/\/)[^\s"']+/gi, '[url-redacted]')
    .replace(/[\x00-\x1f\x7f]/g, ' ')
    .replace(/\s+/g, ' ').trim().slice(0, 300)
  return safe || 'empty-error'
}

export async function readMskApiRejection(response) {
  const status = response.status()
  if (status < 400) return null // Never inspect successful response bodies.
  try {
    const body = await response.json()
    return {httpStatus: status, reason: safeMskApiRejection(body)}
  } catch {
    return {httpStatus: status, reason: 'json-error-unavailable'}
  }
}

export function formatMskFailureWithApiReason(error, rejection) {
  const message = error instanceof Error ? error.message : 'Worker MSK issuance failed'
  if (!rejection || !Number.isInteger(rejection.httpStatus)) return message
  return `${message}; localApiRejection=${rejection.reason || 'unavailable'}`
}

// Error text shown in the local Worker UI is NOT a safe source for verbatim
// logs; it can contain backend details. Report the category and HTTP status.
export function workerMskFailureCategory(message) {
  const value = String(message || '').toLowerCase()
  if (/invalid signature|signature invalid|recover.*address|recovered.*address|signature mismatch/.test(value)) return 'signature-rejected'
  if (/active saved multi-session key already exists|revoke.*replacement/.test(value)) return 'key-already-exists'
  if (/hub.*(?:unreachable|unavailable|refused|timeout|timed out)|(?:timeout|timed out).*hub/.test(value)) return 'hub-unreachable'
  if (/connect|network|dns|proxy|tls/.test(value)) return 'network-or-hub-error'
  if (/http 5\d\d/.test(value)) return 'local-api-5xx'
  if (/http 4\d\d/.test(value)) return 'local-api-4xx'
  if (/request failed|key request failed/.test(value)) return 'worker-request-error'
  return 'unclassified'
}

export function classifyWorkerMsk({state = '', events = [], apiStatus = null, apiRequestFailed = false, uiStatus = '', elapsedMs = 0, timeoutMs = 60_000} = {}) {
  const found = new Set(events)
  const active = String(state).trim() === 'Active'
  const responseStatus = Number.isInteger(apiStatus) ? apiStatus : null
  const phase = found.has('hub.response') ? 'hub-response'
    : found.has('signature.created') ? 'signed-awaiting-hub'
    : found.has('request.start') ? 'awaiting-wallet-signature'
    : 'request-not-observed'
  if (active) {
    if (!found.has('signature.created') || !found.has('hub.response')) {
      return {kind:'error', phase, code:'active-key-without-signature-and-hub-proof', apiStatus:responseStatus}
    }
    return {kind:'success', phase:'active-key-verified', apiStatus:responseStatus}
  }
  if (found.has('request.failed') || found.has('request.blocked')) {
    return {kind:'error', phase, code: found.has('request.blocked') ? 'worker-request-blocked' : 'worker-request-failed',
      category:workerMskFailureCategory(uiStatus), apiStatus:responseStatus}
  }
  if (apiRequestFailed) return {kind:'error', phase, code:'local-api-transport-failed', apiStatus:responseStatus}
  if (responseStatus !== null && responseStatus >= 400) {
    return {kind:'error', phase, code:'local-api-http-error', apiStatus:responseStatus}
  }
  if (elapsedMs >= timeoutMs) {
    return {kind:'error', phase, code:found.has('hub.response') ? 'hub-response-did-not-activate-key'
      : found.has('signature.created') ? 'hub-issuance-timeout'
        : found.has('request.start') ? 'wallet-signature-not-completed'
          : 'key-request-not-started', apiStatus:responseStatus}
  }
  return {kind:'pending', phase, apiStatus:responseStatus}
}

export async function waitForWorkerMsk({
  readState, readStatus, readEvents, readApiStatus = () => null,
  readApiRequestFailed = () => false,
  sleep = ms => new Promise(resolve => setTimeout(resolve,ms)),
  now = () => Date.now(), timeoutMs = 60_000, pollMs = 350,
  onProgress = () => {},
} = {}) {
  const start = now()
  let lastPhase = ''
  while (true) {
    const [state, uiStatus] = await Promise.all([readState(), readStatus()])
    const result = classifyWorkerMsk({state, uiStatus, events:readEvents(), apiStatus:readApiStatus(),
      apiRequestFailed:readApiRequestFailed(), elapsedMs:now() - start, timeoutMs})
    if (result.phase !== lastPhase) { lastPhase = result.phase; onProgress(result.phase) }
    if (result.kind === 'success') return result
    if (result.kind === 'error') {
      const statusPart = result.apiStatus === null ? 'none' : String(result.apiStatus)
      throw new Error(`Worker MSK issuance failed: ${result.code} (phase=${result.phase}, category=${result.category || 'none'}, localApiHttp=${statusPart}); no active Hub key certified`)
    }
    await sleep(pollMs)
  }
}
