// Live testnet work-cycle evidence. Never log subprocess stdout, authorizations,
// results or local worker settings: the only durable fields are correlation IDs.
import { spawn } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { safeMskApiRejection } from './worker_msk_outcome.mjs'

export const WORK_CYCLE_CHAIN = '0x28757b1'
// The Worker's live local-AI executor defaults to 300 seconds. Allow its
// legitimate execution timeout plus Hub settlement/polling margin before the
// browser harness considers an accepted request incomplete.
export const WORK_CYCLE_TIMEOUT_MS = 480_000
const MAX_PROCESS_OUTPUT = 3_000_000

export function captainCommand({ python = 'python', nonce = randomUUID(), model, retryTwoCredits = false, priceCeilingCredits = null, bridgeFundingRequired = null } = {}) {
  if (!/^[0-9a-f-]{36}$/i.test(nonce)) throw new Error('Invalid work-cycle nonce')
  const prompt = `Main Computer browser worker acceptance ${nonce}. Answer with the exact token: ACK-${nonce}`
  if (!model || typeof model !== 'string' || !/^[a-zA-Z0-9][a-zA-Z0-9:._/-]{0,127}$/.test(model)) {
    throw new Error('Worker model must be the advertised local model')
  }
  if (priceCeilingCredits !== null && ![1, 2].includes(priceCeilingCredits)) {
    throw new Error('Work-cycle price ceiling must be exactly 1 or 2 credits')
  }
  if (bridgeFundingRequired !== null && typeof bridgeFundingRequired !== 'boolean') {
    throw new Error('Bridge top-up decision must be explicit and boolean')
  }
  if (bridgeFundingRequired === true && priceCeilingCredits !== 2 && !retryTwoCredits) {
    throw new Error('Bounded bridge funding requires a two-credit price ceiling')
  }
  const twoCredits = priceCeilingCredits === 2 || retryTwoCredits
  // Legacy retry behavior is preserved for callers lacking a prior read-only
  // balance check. New callers explicitly allow bridging only if necessary.
  const bridge = twoCredits && (bridgeFundingRequired === true ||
    (bridgeFundingRequired === null))
  return {
    executable: python,
    args: ['-m', 'main_computer.captain_entrypoint', 'smoke', prompt,
      '--network', 'testnet', '--ring', '3', '--no-chain',
      // Price ceiling is derived from the registered Worker offer, not guessed.
      // Two-credit mode permits only the Captain's cumulative capped bridge
      // behavior; one-credit mode uses existing balance without any deposit.
      '--max-credits', twoCredits ? '2' : '1',
      ...(bridge ? ['--bridge-balance-target', '2', '--no-stipend'] : ['--no-bridge', '--no-stipend']),
      '--poll-seconds', '360', '--timeout-s', '30', '--model', model,
      '--smoke-id', `worker-browser-${nonce}`, '--yes', '--json'],
    nonce,
  }
}

// Only the exact, previously observed market rejection warrants one bounded
// price-ceiling retry. Authorization failures and ambiguous results do not.
export function shouldRetryCaptainAtTwoCredits(error) {
  const proof = error?.captainEvidence
  return proof?.hubHttpStatus === 409 && proof?.hubCode === 'worker_not_live' &&
    proof?.hubOperation === 'live-worker-request-submit'
}

export async function executeCaptainWithPriceRetry({
  python = 'python', model, cwd, execute = executeCaptain,
  makeCommand = captainCommand, onRetry = () => {}, priceCeilingCredits = null, bridgeFundingRequired = null,
} = {}) {
  const initial = makeCommand({python, model, priceCeilingCredits, bridgeFundingRequired})
  try {
    return await execute(initial, {cwd})
  } catch (error) {
    // When the live Worker's price is known, a 409 is not evidence that
    // bridging more money will make that Worker selectable. Never perform
    // another credit-bearing request without a second funding preflight.
    if (priceCeilingCredits !== null || !shouldRetryCaptainAtTwoCredits(error)) throw error
    onRetry()
    // A new nonce gives the second request an independent smoke identity.
    // The Captain CLI, not this browser script, enforces the balance cap.
    const retry = makeCommand({python, model, retryTwoCredits: true})
    return await execute(retry, {cwd})
  }
}

// Match the actual registered offer's input policy instead of guessing that
// the Worker is priced at one credit. Decimal arithmetic is exact to credit-wei.
export function priceOfWorkerOffer({targetTokens, creditPerToken}) {
  const tokens = String(targetTokens ?? '').trim()
  const price = String(creditPerToken ?? '').trim()
  if (!/^[1-9][0-9]{0,8}$/.test(tokens) || !/^(?:[0-9]+)(?:\.[0-9]{1,18})?$/.test(price)) {
    throw new Error('Worker price inputs unavailable or not exact decimal amounts')
  }
  const [whole, fraction = ''] = price.split('.')
  const rateWei = BigInt(whole) * 10n ** 18n + BigInt(fraction.padEnd(18, '0') || '0')
  const requiredWei = BigInt(tokens) * rateWei
  if (requiredWei <= 0n || requiredWei > 2n * 10n ** 18n) {
    throw new Error('Worker advertised price is outside the authorized 0-to-2-credit test range')
  }
  return {
    requiredCreditWei: requiredWei.toString(),
    priceCeilingCredits: requiredWei > 10n ** 18n ? 2 : 1,
  }
}

export function captainProof(result) {
  if (!result || typeof result !== 'object' || result.ok !== true) {
    throw new Error('Captain did not report a successful testnet request')
  }
  if (result.network && result.network !== 'testnet') throw new Error('Captain network is not testnet')
  const hub = result.hub || {}
  if (hub.network !== 'testnet') throw new Error('Captain Hub did not report testnet')
  if (!/^https:\/\/testnet-hub\.greatlibrary\.io\/?$/i.test(hub.url || '')) {
    throw new Error('Captain Hub is not the canonical testnet Hub')
  }
  if (Number(hub.ring) !== 3) throw new Error('Captain request did not use ring 3')
  const submit = hub.submit || {}
  const requestId = String(submit.request_id || submit.request?.request_id || '').trim()
  const sessionId = String(submit.session_id || submit.accepted_session?.session_id || '').trim()
  if (!requestId || !sessionId) throw new Error('Captain submit lacks stable request/session identifiers')
  const poll = hub.poll || {}
  const state = String(poll.state || poll.status || poll.request?.state || '').trim().toLowerCase()
  if (String(poll.session_id || '') !== sessionId) throw new Error('Captain completion session does not match submit')
  if (poll.request_id && String(poll.request_id) !== requestId) throw new Error('Captain completion request does not match submit')
  if (!['completed', 'succeeded'].includes(state)) {
    const error = new Error('Captain Hub request did not complete (state=' + (state || 'unknown') + ')')
    // An accepted session has already reserved capacity and credit. Carry only
    // correlation identifiers to the existing harness: NEVER issue a third
    // Captain request or treat an accepted session as completed work.
    if (['accepted', 'running', 'pending'].includes(state)) {
      error.captainProgress = {requestId, sessionId, state, verdict: 'hub-accepted-worker-not-terminal'}
    }
    throw error
  }
  return {requestId, sessionId, state: 'completed'}
}

export function workerSessionProof(status, expectedWorkerId = '', expectedPriceWei = null) {
  if (!status || status.ok !== true) throw new Error('Worker runtime status is unavailable')
  const runtime = status.runtime || {}
  if (runtime.phase !== 'accepting' || runtime.allowed_to_accept !== true) {
    throw new Error('Browser Worker is not accepting Hub work yet')
  }
  const session = runtime.heartbeat_result?.live_session || {}
  if (!session.alive || !session.worker_session?.connected) {
    throw new Error('Browser Worker has no live Hub WebSocket session')
  }
  // The local viewport intentionally redacts workerId from verbose status.
  // Its live_session is already scoped to the registered worker by the
  // backend; compare any unredacted ID only if a future schema exposes it.
  const shownId = String(status.identity?.workerId || '').trim()
  if (!expectedWorkerId || (shownId && shownId !== expectedWorkerId)) {
    throw new Error('Live Worker identity does not match registered Worker')
  }
  if (String(session.hub_url || '').replace(/\/$/, '') !== 'https://testnet-hub.greatlibrary.io') {
    throw new Error('Worker live session is not connected to testnet Hub')
  }
  if (String(status.identity?.requestedRing || '').replace(/[^0-9]/g, '') !== '3') {
    throw new Error('Worker has not requested ring 3')
  }
  if (expectedPriceWei !== null) {
    const market = session.market_proof || {}
    if (market.hub_acknowledged !== true) {
      throw new Error('Hub has not acknowledged this Worker market offer')
    }
    if (market.ring_three !== true || market.chat_completions !== true) {
      throw new Error('Hub acknowledged a Worker with mismatched ring or capability')
    }
    if (market.capacity_available !== true) {
      throw new Error('Hub Worker market has no available capacity')
    }
    if (String(market.price_credit_wei || '') !== String(expectedPriceWei)) {
      throw new Error('Hub advertised Worker price differs from saved browser offer')
    }
  }
  return {workerId: expectedWorkerId, lastOffer: session.last_offer || {}, lastResult: session.last_result || {}}
}

// Diagnostic only: describe what this specific Worker has done with an already
// accepted Captain session. Do not expose prompts, model output, raw errors,
// wallets, signed authorizations, or other workers' records.
export function captainWorkerExecutionAtFailure(status, captain) {
  const runtime = status?.runtime || {}
  const session = runtime.heartbeat_result?.live_session || {}
  const offer = session.last_offer || {}
  const result = session.last_result || {}
  const offerMatches = offer.type === 'hub.work.offer' &&
    offer.request_id === captain?.requestId && offer.session_id === captain?.sessionId
  const resultMatches = ['worker.work.result', 'worker.work.failed',
    'hub.work.result.accepted', 'hub.work.failed.accepted', 'hub.work.terminal.accepted'].includes(result.type) &&
    result.request_id === captain?.requestId && result.session_id === captain?.sessionId
  const active = Number(session.active_work_count)
  const resultType = resultMatches ? result.type : 'none'
  const statusValue = resultMatches ? String(result.status || result.result?.status || '') : ''
  const resultStatus = ['success', 'succeeded', 'failed', 'cancelled'].includes(statusValue) ? statusValue : 'unknown'
  let verdict = 'no-matching-work-offer-observed'
  if (resultMatches) verdict = resultType === 'worker.work.failed' || resultStatus === 'failed'
    ? 'worker-reported-execution-failure'
    : resultType === 'hub.work.result.accepted' || resultType === 'hub.work.terminal.accepted'
      ? 'worker-received-terminal-hub-ack-but-captain-poll-not-terminal'
      : 'worker-result-sent-but-hub-termination-unverified'
  else if (offerMatches && active > 0) verdict = 'matching-offer-still-executing'
  else if (offerMatches) verdict = 'matching-offer-observed-without-terminal-result'
  return {
    statusReadable: status?.ok === true,
    workerWebsocketAlive: session.alive === true,
    workerWebsocketConnected: session.worker_session?.connected === true,
    matchingOfferObserved: offerMatches,
    matchingResultObserved: resultMatches,
    resultType, resultStatus,
    activeWorkCount: Number.isInteger(active) && active >= 0 ? Math.min(active, 9999) : null,
    verdict,
  }
}

export function verifyDeliveredWork(status, expectedWorkerId, captain) {
  const session = workerSessionProof(status, expectedWorkerId)
  const offer = session.lastOffer
  const result = session.lastResult
  if (offer.type !== 'hub.work.offer' || String(offer.request_id || '') !== captain.requestId ||
      String(offer.session_id || '') !== captain.sessionId) {
    throw new Error('Testnet Captain request was not observed by this Worker')
  }
  if (!['worker.work.result', 'hub.work.result.accepted'].includes(result.type) ||
      String(result.request_id || '') !== captain.requestId ||
      String(result.session_id || '') !== captain.sessionId) {
    throw new Error('No matching Worker execution result was observed')
  }
  if ((result.type === 'worker.work.result' && result.result?.status !== 'success') ||
      (result.type === 'hub.work.result.accepted' && result.status !== 'succeeded')) {
    throw new Error('Worker produced no successful execution result')
  }
  return {workerId: session.workerId, requestId: captain.requestId, sessionId: captain.sessionId,
          offerObserved: true, resultObserved: true, resultType: result.type}
}

// Only emit whitelisted Hub operation names. URLs can contain request IDs,
// wallet addresses and query parameters and must NEVER enter diagnostics.
export function safeCaptainHubOperation(urlText) {
  let url
  try { url = new URL(urlText) } catch { return 'unknown' }
  if (url.protocol !== 'https:' || url.hostname !== 'testnet-hub.greatlibrary.io') return 'unknown'
  const path = url.pathname
  const operations = new Map([
    ['/api/hub/v1/credits/wallet-funding/completion', 'bridge-readiness'],
    ['/api/hub/v1/credits/wallet-funding/complete', 'bridge-completion'],
    ['/api/hub/v1/credits/wallet-funding/import', 'bridge-import'],
    ['/api/hub/v1/credits/balance', 'credit-balance'],
    ['/api/hub/v1/credits/charges', 'credit-charges'],
    ['/api/hub/v1/requests/quote', 'request-quote'],
    ['/api/hub/v1/requests/submit', 'request-submit'],
    ['/api/hub/v1/requests', 'request-submit'],
    ['/api/hub/v1/work/requests', 'live-worker-request-submit'],
    ['/api/hub/v1/credits/bridge-reconciliation/record', 'bridge-reconciliation-record'],
    ['/api/hub/v1/credits/bridge-reconciliation/execute', 'bridge-reconciliation-execute'],
    ['/api/hub/v1/credits/bridge-deposit', 'bridge-deposit-lookup'],
  ])
  if (operations.has(path)) return operations.get(path)
  if (/^\/api\/hub\/v1\/requests\/[^/]+\/(?:result|pickup)$/.test(path)) return 'request-result'
  if (/^\/api\/hub\/v1\/requests\/[^/]+$/.test(path)) return 'request-status'
  if (/^\/api\/hub\/v1\/work\/sessions\/[^/]+\/stream$/.test(path)) return 'session-stream'
  return 'unknown'
}

// Extract only bounded, schema-like rejection metadata. The full Hub response
// may include owner records, wallets, signatures and request bodies; those never
// enter console output or the acceptance evidence JSON.
function safeHubPublicField(value, {humanMessage = false} = {}) {
  if (typeof value !== 'string' || value.length < 1 || value.length > 220) return ''
  if (/[{}<>\r\n\[\]"'\\]/.test(value)) return ''
  if (/(?:mnemonic|seed|recovery|password|private|secret|authorization|bearer|credential|api.key|access.token|signed.request|signature|\btoken\b)/i.test(value)) return ''
  if (!/^[A-Za-z0-9_ ,.:;()\-]+$/.test(value)) return ''
  if (/\b[A-Za-z0-9_-]{30,}\b/.test(value) && !/\b0x[0-9a-f]{40,}\b/i.test(value)) return ''
  const sanitized = value
    .replace(/\b(?:0x)?[a-f0-9]{32,}\b/gi, '[hex-redacted]')
    .replace(/\b[A-Za-z0-9_/-]{70,}\b/g, '[long-value-redacted]')
    .replace(/\s+/g, ' ').trim()
  return sanitized.slice(0, humanMessage ? 170 : 95)
}

const KNOWN_HUB_REJECTION_CODES = new Set([
  'requester_msk_invalid', 'worker_not_live', 'worker_owner_changed',
  'worker_owner_not_local', 'worker_capacity_unavailable', 'worker_selection_unstable',
  'worker_offer_timeout', 'worker_acceptance_rejected', 'duplicate_request_id_work_mismatch',
  'temporal_unavailable', 'insufficient_credits', 'insufficient_available_credit',
  'owner_hub_handoff_failed',
])

function safeCaptainHubBodyMetadata(bodyText) {
  if (!bodyText || !bodyText.trimStart().startsWith('{')) return {}
  // Fully parse small bounded responses. Never return arbitrary JSON fields.
  if (bodyText.length <= 16_384) {
    try {
      const body = JSON.parse(bodyText)
      if (body && !Array.isArray(body) && typeof body === 'object') {
        const code = safeHubPublicField(body.error)
        const message = safeHubPublicField(body.message, {humanMessage: true})
        const reasonCode = safeHubPublicField(body.reason_code)
        return {
          ...(code ? {hubCode: code} : {}),
          ...(message ? {hubMessage: message} : {}),
          ...(reasonCode ? {hubReasonCode: reasonCode} : {}),
        }
      }
    } catch { /* A bounded Captain ERROR line may truncate nested owner JSON. */ }
  }
  // Stable Hub emits {"ok": false, "error": "worker_not_live", ...}.
  // When following nested owner records are large or truncated, recognize
  // ONLY a known enum in that exact prefix. Never include unparsed text.
  const match = /^\s*\{\s*"ok"\s*:\s*false\s*,\s*"error"\s*:\s*"([a-z_]{3,80})"/i.exec(bodyText)
  const code = match?.[1] || ''
  return KNOWN_HUB_REJECTION_CODES.has(code) ? {hubCode: code} : {}
}

// An observed HTTP rejection identifies the failing contract boundary, not
// automatically which software component contains the underlying defect.
export function diagnoseCaptainHubRejection({hubHttpStatus, hubOperation, hubCode = ''}) {
  const code = String(hubCode).trim().toLowerCase()
  if (hubOperation !== 'live-worker-request-submit' && hubOperation !== 'request-submit') {
    if (hubOperation.startsWith('bridge-')) return 'bridge-operation-rejected'
    return 'hub-api-operation-rejected'
  }
  if (code === 'requester_msk_invalid') return 'requester-multisession-authorization-rejected'
  if (code === 'worker_not_live') return 'no-eligible-live-worker-in-hub-market'
  if (code === 'worker_owner_changed' || code === 'worker_owner_not_local') return 'hub-worker-owner-or-socket-mismatch'
  if (code === 'worker_capacity_unavailable') return 'hub-worker-capacity-unavailable'
  if (code === 'worker_selection_unstable') return 'hub-worker-market-changed-during-selection'
  if (code === 'worker_offer_timeout' || code === 'worker_acceptance_rejected') return 'worker-offer-not-accepted'
  if (code === 'duplicate_request_id_work_mismatch') return 'captain-request-id-conflicts-with-existing-work'
  if (code === 'temporal_unavailable') return 'hub-temporal-service-unavailable'
  if (code === 'insufficient_credits' || code === 'insufficient_available_credit') return 'captain-hub-credit-insufficient'
  if (hubHttpStatus === 401) return 'hub-request-authorization-rejected'
  if (hubHttpStatus === 403) return 'hub-access-rejected-authorization-or-edge-policy-unresolved'
  if (hubHttpStatus === 409) return 'hub-work-selection-or-conflict-rejected-unclassified'
  if (hubHttpStatus === 400 || hubHttpStatus === 422) return 'hub-work-payload-rejected-unclassified'
  if (hubHttpStatus >= 500) return 'hub-service-unavailable-or-internal-error'
  return 'hub-work-request-rejected-unclassified'
}

export function captainHubFailureMetadata(rawError) {
  const raw = String(rawError || '')
  const m = /\bHub request failed for (https:\/\/[^\s]+) with HTTP ([45]\d\d):\s*([\s\S]*)/i.exec(raw)
  if (!m) return null
  const httpStatus = Number(m[2])
  const hubOperation = safeCaptainHubOperation(m[1])
  const metadata = safeCaptainHubBodyMetadata(m[3])
  return {
    hubHttpStatus: httpStatus,
    hubOperation,
    ...metadata,
    verdict: diagnoseCaptainHubRejection({hubHttpStatus: httpStatus, hubOperation, hubCode: metadata.hubCode}),
  }
}

// Recheck the browser-side session on the *same failed run*. A live browser
// socket is useful evidence, but cannot prove the Hub market record is valid.
export function captainWorkerAtFailure(status) {
  const runtime = status?.runtime || {}
  const session = runtime.heartbeat_result?.live_session || {}
  return {
    statusReadable: status?.ok === true,
    accepting: runtime.phase === 'accepting' && runtime.allowed_to_accept === true,
    websocketAlive: session.alive === true,
    websocketConnected: session.worker_session?.connected === true,
    testnetHub: String(session.hub_url || '').replace(/\/$/, '') === 'https://testnet-hub.greatlibrary.io',
    ring3Selected: String(status?.identity?.requestedRing || '').replace(/[^0-9]/g, '') === '3',
  }
}

// Captain writes operator-facing refusals to stdout (`ERROR: ...`) and argparse
// writes them to stderr (`...: error: ...`). Capture that *specific diagnostic*,
// not the complete process output: it may include wallet funding records,
// signed authorizations, transaction details or credentials.
export function captainFailureEvidence({stdout = '', stderr = '', exitCode = null} = {}) {
  // executeCaptain already bounds each stream to MAX_PROCESS_OUTPUT. Scan the
  // entire bounded output for explicit error markers: a diagnostic at the
  // beginning of a verbose Captain response must not disappear simply because
  // the final 32K contains unrelated output.
  const lines = (String(stderr).slice(0, MAX_PROCESS_OUTPUT) + '\n' + String(stdout).slice(0, MAX_PROCESS_OUTPUT)).split(/\r?\n/)
  let raw = ''
  let pythonException = ''
  for (const line of lines) {
    const operator = /^\s*ERROR:\s*(.{1,24000})/i.exec(line.slice(0, 24_012))
    const argparse = /^\s*[^\r\n]{0,120}?: error:\s*(.{1,1500})\s*$/i.exec(line)
    if (operator || argparse) raw = (operator || argparse)[1]
    // Exit 1 is normally an uncaught Python exception. We deliberately
    // ignore traceback frames (source code, file paths and locals) and take
    // only its final exception line. Do not copy stderr into the report.
    const py = /^\s*((?:[A-Za-z_][\w.]*)(?:Error|Exception)):\s*(.{0,1200})\s*$/.exec(line)
    if (py) pythonException = py[1] + ': ' + py[2]
  }
  if (!raw && pythonException && /Traceback \(most recent call last\):/.test(stderr)) raw = pythonException
  let category = pythonException && raw === pythonException ? 'captain-python-exception' : 'captain-unclassified'
  if (category === 'captain-python-exception') {
    // Preserve unhandled exception identity; its redacted text supplies detail.
  } else if (/unrecognized arguments|invalid choice|argument .*required|too few arguments|invalid .* value/i.test(raw)) {
    category = 'captain-arguments'
  } else if (/bridge|deposit|escrow|refund|funding|credits?|balance|reconcil/i.test(raw)) {
    category = 'captain-funding-or-bridge'
  } else if (/wallet|officer|private.key|mother.state|captain identity|account identity/i.test(raw)) {
    category = 'captain-wallet-or-state'
  } else if (/hub|websocket|authentication|authorization|HTTP [45]\d\d|multisession/i.test(raw)) {
    category = 'captain-hub-or-authorization'
  } else if (/worker|model|ring|capacity|offer|dispatch/i.test(raw)) {
    category = 'captain-work-dispatch'
  } else if (/chain|rpc|gas|nonce|transaction/i.test(raw)) {
    category = 'captain-chain-or-rpc'
  }
  if (!raw) return {exitCode, category, reason: 'no-classified-captain-error-line'}
  const hub = captainHubFailureMetadata(raw)
  if (hub) {
    // Hub errors are untrusted; only validated scalar metadata escapes.
    return {exitCode, category: 'captain-hub-http',
      reason: hub.hubCode || hub.hubMessage || 'hub-error-body-redacted', ...hub}
  }
  // Arbitrary stderr/stdout text cannot be trusted. Redact possible values,
  // and suppress whole lines which name secret-bearing fields.
  if (/[{}]/.test(raw) || /(?:mnemonic|seed[ _-]?phrase|recovery[ _-]?phrase|password|(?:private|secret)[ _-]?key|authorization|bearer|api[ _-]?key|access[ _-]?token|signed_request|signature)\s*(?:[:=]|\s+bearer)/i.test(raw)) {
    return {exitCode, category, reason: 'sensitive-error-detail-redacted'}
  }
  const reason = raw
    .replace(/0x[0-9a-f]{16,}/gi, '[hex-redacted]')
    .replace(/\b[A-Fa-f0-9]{64,}\b/g, '[hex-redacted]')
    .replace(/\b[A-Za-z0-9_\/+=-]{75,}\b/g, '[long-value-redacted]')
    .replace(/\b(?:https?:\/\/)[^\s"']+/gi, '[url-redacted]')
    .replace(/\b[A-Za-z]:\\[^\s"']+/g, '[path-redacted]')
    .replace(/\b[^\s@]+@[^\s@]+\.[^\s@]+\b/g, '[email-redacted]')
    .replace(/[\x00-\x1f\x7f]/g, ' ')
    .replace(/\s+/g, ' ').trim().slice(0, 240)
  return {exitCode, category, reason: reason || 'empty-captain-error'}
}

// Report only stream SHAPES. Raw subprocess text may contain signed
// authorizations, private keys or Hub response bodies. In particular an
// unclassified exit must no longer masquerade as proof of an empty stream.
export function captainStdioEvidence({stdout = '', stderr = ''} = {}) {
  const summarize = value => {
    const output = String(value)
    const trimmed = output.trim()
    let shape = 'empty'
    if (trimmed) {
      if (/Traceback \(most recent call last\):/.test(output)) shape = 'python-traceback'
      else if (/^\s*usage:/m.test(output)) shape = 'argument-usage'
      else if (/^\s*ERROR:/mi.test(output)) shape = 'operator-error'
      else if (/^[\[{]/.test(trimmed)) shape = 'json-like'
      else shape = 'other-text'
    }
    return {bytes: Buffer.byteLength(output, 'utf8'), shape}
  }
  return {stdout: summarize(stdout), stderr: summarize(stderr)}
}

// A read-only *second process* is invoked ONLY after a failed Captain
// execution, never as a retry. Construct its arguments by removing every
// execution authorization and asserting --prepare-only. It must not submit
// a Hub request, broadcast bridge funding or sign a wallet transaction.
export function captainPrepareOnlyCommand(command) {
  const args = Array.isArray(command?.args) ? command.args : []
  if (!args.includes('--yes') || !args.includes('--network') ||
      args[args.indexOf('--network') + 1] !== 'testnet' ||
      !args.includes('--no-chain') || !args.includes('--smoke-id')) {
    throw new Error('Refusing unrecognized Captain command for read-only preparation')
  }
  const safeArgs = args.filter(value => !['--yes', '--execute'].includes(value))
  if (safeArgs.includes('--private-key') || safeArgs.includes('--bridge-controller-private-key')) {
    throw new Error('Read-only diagnosis refuses command-line signing overrides')
  }
  return {executable: command.executable, args: [...safeArgs, '--prepare-only']}
}

export function probeCaptainPreparation(command, {cwd, spawnImpl = spawn, timeoutMs = 35_000} = {}) {
  let prepare
  try { prepare = captainPrepareOnlyCommand(command) }
  catch { return Promise.resolve({result: 'refused-unsafe-command'}) }
  return new Promise(resolve => {
    let child
    try { child = spawnImpl(prepare.executable, prepare.args, {
      cwd, shell: false, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
    }) } catch { return resolve({result: 'process-launch-failed'}) }
    let stdout = '', stderr = '', complete = false
    const finish = info => { if (!complete) { complete = true; clearTimeout(timer); resolve(info) } }
    const timer = setTimeout(() => {
      try { child.kill() } catch {}
      finish({result: 'prepare-only-timeout'})
    }, timeoutMs)
    child.on('error', () => finish({result: 'process-launch-failed'}))
    child.stdout.on('data', chunk => {
      stdout += String(chunk)
      if (stdout.length > MAX_PROCESS_OUTPUT) {
        try { child.kill() } catch {}
        finish({result: 'output-limit-exceeded'})
      }
    })
    child.stderr.on('data', chunk => {
      stderr += String(chunk)
      if (stderr.length > MAX_PROCESS_OUTPUT) {
        try { child.kill() } catch {}
        finish({result: 'output-limit-exceeded'})
      }
    })
    child.on('close', code => {
      if (complete) return
      if (code !== 0) {
        return finish({result: 'prepare-only-refused', exitCode: code,
                       error: captainFailureEvidence({stdout, stderr, exitCode: code}),
                       stdio: captainStdioEvidence({stdout, stderr})})
      }
      let payload
      try { payload = JSON.parse(stdout.trim()) }
      catch { return finish({result: 'unparseable-prepare-only-result',
                             stdio: captainStdioEvidence({stdout, stderr})}) }
      if (payload?.ok === true && payload.execute === false &&
          payload.hub?.network === 'testnet' && Number(payload.chain?.chain_id) === 42424241) {
        finish({result: 'prepare-only-pass', network: 'testnet', chainId: 42424241})
      } else {
        finish({result: 'prepare-only-proof-incomplete'})
      }
    })
  })
}

export function executeCaptain(command, {cwd, timeoutMs = WORK_CYCLE_TIMEOUT_MS, spawnImpl = spawn} = {}) {
  return new Promise((resolve, reject) => {
    const child = spawnImpl(command.executable, command.args, {cwd, shell: false, windowsHide: true, stdio: ['ignore','pipe','pipe']})
    let stdout = ''
    let stderr = ''
    let finished = false
    const fail = message => { if (!finished) { finished = true; clearTimeout(timer); reject(message instanceof Error ? message : new Error(message)) } }
    const timer = setTimeout(() => { child.kill(); fail('Captain testnet transaction exceeded its deadline') }, timeoutMs)
    child.on('error', () => fail('Captain CLI could not be launched'))
    child.stdout.on('data', chunk => {
      stdout += String(chunk)
      if (stdout.length > MAX_PROCESS_OUTPUT) { child.kill(); fail('Captain response exceeded size limit') }
    })
    child.stderr.on('data', chunk => {
      stderr += String(chunk)
      if (stderr.length > MAX_PROCESS_OUTPUT) { child.kill(); fail('Captain stderr exceeded size limit') }
    })
    child.on('close', code => {
      if (finished) return
      if (code !== 0) {
        const failure = captainFailureEvidence({stdout, stderr, exitCode: code})
        const transport = captainStdioEvidence({stdout, stderr})
        const message = 'Captain testnet request refused (exit=' + code + ', category=' + failure.category +
          ', reason=' + failure.reason +
          (failure.hubHttpStatus ? ', hubHttp=' + failure.hubHttpStatus : '') +
          (failure.hubOperation ? ', hubOperation=' + failure.hubOperation : '') +
          (failure.verdict ? ', verdict=' + failure.verdict : '') +
          ', stdout=' + transport.stdout.shape + ':' + transport.stdout.bytes +
          ', stderr=' + transport.stderr.shape + ':' + transport.stderr.bytes +
          '); no browser Worker execution certified'
        const error = new Error(message)
        error.captainEvidence = failure // validated, non-sensitive fields only
        finished = true
        clearTimeout(timer)
        return reject(error)
      }
      let output
      try {
        // JSON parser errors can quote arbitrary fragments of the input,
        // which may contain wallet authorization or credit details.
        output = JSON.parse(stdout.trim())
      } catch {
        return fail('Captain returned malformed JSON; no Worker execution certified')
      }
      try {
        const proof = captainProof(output)
        finished = true; clearTimeout(timer); resolve(proof)
      } catch (error) {
        const reported = new Error('Captain response validation failed: ' + error.message)
        if (error.captainProgress) reported.captainProgress = error.captainProgress
        fail(reported)
      }
    })
  })
}

export async function readWorkerLiveStatus(page) {
  return page.evaluate(async () => {
    const response = await fetch('/api/applications/worker/runtime-status?verbose=1', {cache: 'no-store'})
    if (!response.ok) throw new Error('Worker runtime status HTTP ' + response.status)
    // The response is used only inside Chromium and never printed.
    return await response.json()
  })
}

export async function waitForDeliveredWork({page, workerId, captain, timeoutMs = 45_000, readStatus = readWorkerLiveStatus, sleep = ms => new Promise(r => setTimeout(r,ms))}) {
  const deadline = Date.now() + timeoutMs
  let last = 'no-worker-evidence'
  while (Date.now() < deadline) {
    try {
      const status = await readStatus(page)
      return verifyDeliveredWork(status, workerId, captain)
    } catch (error) {
      last = error.message
      await sleep(1000)
    }
  }
  throw new Error('No identity-correlated Worker delivery and result proof: ' + last)
}

export async function waitForLiveWorker({page, workerId, expectedPriceWei = null, timeoutMs = 45_000, readStatus = readWorkerLiveStatus,
                                         sleep = ms => new Promise(r => setTimeout(r,ms))}) {
  const deadline = Date.now() + timeoutMs
  let last = 'no-live-session'
  while (Date.now() < deadline) {
    try {
      return workerSessionProof(await readStatus(page), workerId, expectedPriceWei)
    } catch (error) {
      last = error.message
      await sleep(1000)
    }
  }
  throw new Error('Browser Worker never reached a live testnet WebSocket session: ' + last)
}

// The functional test has a tiny ACK payload, so it need not reserve the
// Worker's normal 1024-token request budget. This is a temporary TEST-only
// seller offer, not a Hub funding or ledger override; the original offer
// must be restored after the Work-now session is closed.
export const WORK_CYCLE_SMOKE_TOKEN_LIMIT = 512

export function boundedSmokeOfferTokens(value) {
  const text = String(value ?? '').trim()
  if (!/^[1-9][0-9]{0,5}$/.test(text)) throw new Error('Worker token budget is unavailable or invalid')
  const current = Number(text)
  if (!Number.isSafeInteger(current) || current > 128_000) throw new Error('Worker token budget is out of bounds')
  return {originalTokens: current, testTokens: Math.min(current, WORK_CYCLE_SMOKE_TOKEN_LIMIT)}
}

// Uses the Worker's own form/autosave path, never an unreviewed direct Hub
// registration or privileged local-file edit. Require positive persistence
// evidence from the settings API before proceeding to Work-now registration.
export async function setWorkerOfferTargetTokens(page, tokens) {
  if (!Number.isSafeInteger(tokens) || tokens < 1 || tokens > 128_000) {
    throw new Error('Invalid Worker token budget for test setting')
  }
  const field = page.locator('#worker-offer-target-tokens')
  const pending = page.waitForResponse(
    r => isLocalWorkerPost(r, '/api/applications/worker/settings'), {timeout: 30_000})
  await field.fill(String(tokens))
  const response = await pending
  if (!response.ok()) throw new Error('Worker token budget save failed (HTTP ' + response.status() + ')')
  let body
  try { body = await response.json() } catch { throw new Error('Worker token budget save result unreadable') }
  if (body?.ok !== true || Number(body?.settings?.sellerTargetTokens) !== tokens) {
    throw new Error('Worker token budget save was not confirmed by local backend')
  }
  if (String(await field.inputValue()) !== String(tokens)) {
    throw new Error('Worker token budget UI changed during persistence')
  }
}

// Work now is a two-step UI: opening its duration dialog does not register a
// Worker. Wait for its *own* POST and make any rejection visible before Captain
// can spend credits. This never approves a wallet request or signs anything.
function isLocalWorkerPost(response, pathname) {
  try {
    return response.request().method() === 'POST' && new URL(response.url()).pathname === pathname
  } catch { return false }
}

export async function prepareWorkerForWorkNow(page, {onProgress = () => {}, onPaidPolicyChanged = () => {}} = {}) {
  let changedPaidPolicy = false
  const enabled = page.locator('#worker-rental-enabled')
  if (!(await enabled.isChecked())) {
    const saved = page.waitForResponse(r => isLocalWorkerPost(r, '/api/applications/worker/settings'), {timeout: 30_000})
    await enabled.check()
    changedPaidPolicy = true
    onPaidPolicyChanged()
    onProgress('worker.work-now.paid-work-selected')
    const response = await saved
    if (!response.ok()) throw new Error('Worker paid-work policy save failed (HTTP ' + response.status() + ')')
    onProgress('worker.work-now.paid-work-saved')
  }
  const ring = page.locator('#worker-network-ring')
  if ((await ring.inputValue()) !== '3') {
    const saved = page.waitForResponse(r => isLocalWorkerPost(r, '/api/applications/worker/network-session'), {timeout: 30_000})
    await ring.selectOption('3')
    const response = await saved
    if (!response.ok()) throw new Error('Worker ring selection failed (HTTP ' + response.status() + ')')
    onProgress('worker.work-now.ring-three-selected')
  } else {
    // Selecting an already-selected ring still fires change, and the Worker
    // change handler unnecessarily resets signed/network state while it POSTs.
    onProgress('worker.work-now.ring-three-preserved')
  }
  return {changedPaidPolicy}
}

export async function postWorkNowDuration(page, {onProgress = () => {}} = {}) {
  const responsePromise = page.waitForResponse(r => isLocalWorkerPost(r, '/api/applications/worker/work-now'), {timeout: 60_000})
  await page.locator('#worker-work-now-15').click()
  onProgress('worker.work-now.duration-submitted')
  const response = await responsePromise
  onProgress('worker.work-now.local-api-http.' + response.status())
  return response
}

// The local Work-now API returns {ok:false,error:string} for validation/Hub
// refusals. Consume only failed response bodies, never registration success
// payloads, which can carry authentication data.
export async function readWorkNowRejection(response) {
  if (!response || response.status() < 400) return null
  let reason = 'json-error-unavailable'
  try { reason = safeMskApiRejection(await response.json()) } catch {}
  return {httpStatus: response.status(), reason}
}
