// Observable, fail-closed result of the real Worker's multi-session-key request.
// Never inspect or log signed payloads, signature bytes, tokens, or API bodies.
const MSK_EVENTS = new Set([
  'request.start', 'request.blocked', 'request.ignored',
  'signature.created', 'hub.response', 'request.failed',
])

export function workerMskEvent(text) {
  const m = /\[worker-msk\]\s+([a-z.]+)/.exec(String(text || ''))
  return m && MSK_EVENTS.has(m[1]) ? m[1] : null
}

export function isMskEndpointRequest(request) {
  if (!request || request.method() !== 'POST') return false
  try { return new URL(request.url()).pathname === '/api/applications/worker/multisession-key/request' }
  catch { return false }
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
