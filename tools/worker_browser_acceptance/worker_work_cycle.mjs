// Live testnet work-cycle evidence. Never log subprocess stdout, authorizations,
// results or local worker settings: the only durable fields are correlation IDs.
import { spawn } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { safeMskApiRejection } from './worker_msk_outcome.mjs'

export const WORK_CYCLE_CHAIN = '0x28757b1'
export const WORK_CYCLE_TIMEOUT_MS = 240_000
const MAX_PROCESS_OUTPUT = 3_000_000

export function captainCommand({ python = 'python', nonce = randomUUID(), model } = {}) {
  if (!/^[0-9a-f-]{36}$/i.test(nonce)) throw new Error('Invalid work-cycle nonce')
  const prompt = `Main Computer browser worker acceptance ${nonce}. Answer with the exact token: ACK-${nonce}`
  if (!model || typeof model !== 'string' || !/^[a-zA-Z0-9][a-zA-Z0-9:._/-]{0,127}$/.test(model)) {
    throw new Error('Worker model must be the advertised local model')
  }
  return {
    executable: python,
    args: ['-m', 'main_computer.captain_entrypoint', 'smoke', prompt,
      '--network', 'testnet', '--ring', '3', '--no-chain',
      '--max-credits', '1', '--bridge-credits', '1',
      '--poll-seconds', '120', '--timeout-s', '30', '--model', model,
      '--smoke-id', `worker-browser-${nonce}`, '--yes', '--json'],
    nonce,
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
  if (!['completed', 'succeeded'].includes(state)) {
    throw new Error('Captain Hub request did not complete (state=' + (state || 'unknown') + ')')
  }
  if (String(poll.session_id || '') !== sessionId) throw new Error('Captain completion session does not match submit')
  if (poll.request_id && String(poll.request_id) !== requestId) throw new Error('Captain completion request does not match submit')
  return {requestId, sessionId, state: 'completed'}
}

export function workerSessionProof(status, expectedWorkerId = '') {
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
  return {workerId: expectedWorkerId, lastOffer: session.last_offer || {}, lastResult: session.last_result || {}}
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

// Captain writes operator-facing refusals to stdout (`ERROR: ...`) and argparse
// writes them to stderr (`...: error: ...`). Capture that *specific diagnostic*,
// not the complete process output: it may include wallet funding records,
// signed authorizations, transaction details or credentials.
export function captainFailureEvidence({stdout = '', stderr = '', exitCode = null} = {}) {
  const lines = (String(stderr).slice(-32_000) + '\n' + String(stdout).slice(-32_000)).split(/\r?\n/)
  let raw = ''
  let pythonException = ''
  for (const line of lines) {
    const operator = /^\s*ERROR:\s*(.{1,1500})\s*$/i.exec(line)
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

export function executeCaptain(command, {cwd, timeoutMs = WORK_CYCLE_TIMEOUT_MS, spawnImpl = spawn} = {}) {
  return new Promise((resolve, reject) => {
    const child = spawnImpl(command.executable, command.args, {cwd, shell: false, windowsHide: true, stdio: ['ignore','pipe','pipe']})
    let stdout = ''
    let stderr = ''
    let finished = false
    const fail = message => { if (!finished) { finished = true; clearTimeout(timer); reject(new Error(message)) } }
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
        return fail('Captain testnet request refused (exit=' + code + ', category=' + failure.category +
          ', reason=' + failure.reason + '); no browser Worker execution certified')
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
      } catch (error) { fail('Captain response validation failed: ' + error.message) }
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

export async function waitForLiveWorker({page, workerId, timeoutMs = 45_000, readStatus = readWorkerLiveStatus,
                                         sleep = ms => new Promise(r => setTimeout(r,ms))}) {
  const deadline = Date.now() + timeoutMs
  let last = 'no-live-session'
  while (Date.now() < deadline) {
    try {
      return workerSessionProof(await readStatus(page), workerId)
    } catch (error) {
      last = error.message
      await sleep(1000)
    }
  }
  throw new Error('Browser Worker never reached a live testnet WebSocket session: ' + last)
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
