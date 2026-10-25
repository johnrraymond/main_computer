// Chrome's extension side panel may be a CDP target but not a Playwright Page.
// Inspect ONLY boolean approval anchors and the location of one enabled button.
// Never return panel text, HTML, secret values, target identifiers or URLs.
const EXTENSION_ID = /^[a-p]{32}$/
const ELIGIBLE_FILES = new Set(['sidepanel.html', 'side-panel.html', 'notification.html', 'popup.html'])
const MESSAGE_TIMEOUT_MS = 2_500

// This function is serialized into the real extension's inspected renderer.
// It receives no secrets and returns only safe classifications / button bounds.
export function inspectApprovalSurface({trustedWorkerOrigin = false} = {}) {
  const isShown = element => {
    const rect = element.getBoundingClientRect()
    const style = window.getComputedStyle(element)
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' &&
      style.display !== 'none' && Number(style.opacity) !== 0
  }
  const body = document.body
  const content = body?.innerText || ''
  const headings = [...document.querySelectorAll('h1,h2,h3,[role="heading"]')]
    .filter(isShown).map(node => (node.innerText || node.textContent || '').trim())
  const matchesHeading = pattern => headings.some(label => pattern.test(label))
  const origin = /\b(?:127\.0\.0\.1|localhost):8765\b(?![.\w-])/i.test(content)
  const rpc = /\btestnet-rpc\.greatlibrary\.io\b(?![.\w-])/i.test(content)
  const chain = /\bMain Computer Testnet\b/i.test(content)
  const add = matchesHeading(/^Add Main Computer Testnet$/i) || matchesHeading(/^Add (?:a |this |the )?network$/i)
  const change = matchesHeading(/^Switch (?:to )?Main Computer Testnet$/i) ||
    matchesHeading(/^Switch (?:the |to |this )?network$/i)
  const connect = matchesHeading(/^Connect (?:with MetaMask|to this site|to site|account)/i) ||
    matchesHeading(/^Select (?:an? )?account/i)
  // v13.53 account approval has heading "127.0.0.1" and a separate
  // "Connect this website with MetaMask" caption, not a Connect heading.
  // A bare host is sufficient ONLY when the caller separately proves the
  // active test page has the exact expected local origin and Worker path.
  const modernConnect = trustedWorkerOrigin &&
    (matchesHeading(/^(?:127\.0\.0\.1|localhost)$/i) ||
      /(?:^|\n)\s*(?:127\.0\.0\.1|localhost)\s*(?:\n|$)/im.test(content)) &&
    /Connect this website with MetaMask/i.test(content) &&
    /See addresses, balances, and activity/i.test(content)
  // Unrelated requests never qualify, even if their body mentions an RPC or chain.
  const type = chain && origin && rpc && add ? 'network-add'
    : chain && origin && change ? 'network-switch'
      : (origin && connect) || modernConnect ? 'account-connect' : null
  const options = type === 'network-add' ? ['Confirm','Add network','Add this network','Approve']
    : type === 'network-switch' ? ['Switch network','Switch','Confirm','Approve']
      : type === 'account-connect' ? ['Next','Connect','Confirm','Connect to site'] : []
  let button = null
  const candidates = [...document.querySelectorAll('button,[role="button"]')]
  for (const label of options) {
    for (const element of candidates) {
      if ((element.innerText || element.textContent || '').trim() !== label || !isShown(element)) continue
      if (element.disabled || element.getAttribute('aria-disabled') === 'true') continue
      const rect = element.getBoundingClientRect()
      const x = rect.left + rect.width / 2
      const y = rect.top + rect.height / 2
      const hit = document.elementFromPoint(x, y)
      if (!hit || (!element.contains(hit) && !hit.contains(element))) continue
      button = {x: Math.round(x), y: Math.round(y), label}
      break
    }
    if (button) break
  }
  return {type, button, eligible: Boolean(type), origin: origin || modernConnect, rpc, chain,
    headingRecognized: add || change || connect || modernConnect}
}

function qualifiedTargets(targets, extensionId) {
  return (targets || []).filter(target => {
    if (!['page','other'].includes(target.type)) return false
    try {
      const url = new URL(target.url)
      return url.protocol === 'chrome-extension:' && url.host === extensionId &&
        ELIGIBLE_FILES.has(url.pathname.split('/').pop()?.toLowerCase())
    } catch { return false }
  })
}

// CDP Target.attachToTarget(flatten:false) bridges an otherwise-unmapped
// extension target. The deprecated nested transport remains fail-closed: if a
// Chromium revision removes it, this harness reports an explicit refusal.
class NestedTargetSession {
  constructor(root, sessionId) { this.root = root; this.sessionId = sessionId; this.nextId = 1 }
  async send(method, params = {}) {
    const id = this.nextId++
    return await new Promise((resolve, reject) => {
      let timer
      const receive = event => {
        if (event.sessionId !== this.sessionId) return
        let msg
        try { msg = JSON.parse(event.message) } catch { return }
        if (msg.id !== id) return
        finish()
        if (msg.error) reject(new Error('nested-command-rejected'))
        else resolve(msg.result || {})
      }
      const finish = () => {
        clearTimeout(timer)
        this.root.off('Target.receivedMessageFromTarget', receive)
      }
      this.root.on('Target.receivedMessageFromTarget', receive)
      timer = setTimeout(() => {finish(); reject(new Error('nested-command-timed-out'))}, MESSAGE_TIMEOUT_MS)
      this.root.send('Target.sendMessageToTarget', {sessionId: this.sessionId,
        message: JSON.stringify({id, method, params})}).catch(() => {
        finish(); reject(new Error('nested-transport-unavailable'))
      })
    })
  }
}

async function inspectAttachedTarget(root, target, trustedWorkerOrigin) {
  let sessionId
  try {
    const result = await root.send('Target.attachToTarget', {targetId: target.targetId, flatten: false})
    sessionId = result.sessionId
    const nested = new NestedTargetSession(root, sessionId)
    await nested.send('Runtime.enable')
    const inspected = await nested.send('Runtime.evaluate', {
      expression: `(${inspectApprovalSurface.toString()})({trustedWorkerOrigin:${trustedWorkerOrigin ? 'true' : 'false'}})`, returnByValue: true,
      awaitPromise: false, silent: true,
    })
    const state = inspected?.result?.value
    if (!state || typeof state !== 'object' || !['network-add','network-switch','account-connect',null].includes(state.type)) {
      return {result:null, status:'inspection-unavailable'}
    }
    const trustedAnchors = state.origin && state.headingRecognized && (
      (state.type === 'network-add' && state.chain && state.rpc) ||
      (state.type === 'network-switch' && state.chain) ||
      state.type === 'account-connect')
    const allowedNames = state.type === 'network-add' ? ['Confirm','Add network','Add this network','Approve']
      : state.type === 'network-switch' ? ['Switch network','Switch','Confirm','Approve']
        : state.type === 'account-connect' ? ['Next','Connect','Confirm','Connect to site'] : []
    if (trustedAnchors && state.button && allowedNames.includes(state.button.label) &&
        Number.isInteger(state.button.x) && Number.isInteger(state.button.y) &&
        state.button.x >= 0 && state.button.y >= 0 &&
        state.button.x < 4096 && state.button.y < 4096) {
      // Trusted Chromium mouse input, not DOM.click or an extension provider stub.
      // Never click a button until origin, network, RPC and heading matched.
      const mouse = {x:state.button.x, y:state.button.y, button:'left', clickCount:1}
      await nested.send('Input.dispatchMouseEvent',{...mouse,type:'mouseMoved'})
      await nested.send('Input.dispatchMouseEvent',{...mouse,type:'mousePressed'})
      await nested.send('Input.dispatchMouseEvent',{...mouse,type:'mouseReleased'})
      return {result:{type:state.type,action:state.button.label}, status:'approved'}
    }
    return {result:null, status:state.type ? 'matching-approval-disabled' : 'unrecognized-panel'}
  } finally {
    if (sessionId) await root.send('Target.detachFromTarget',{sessionId}).catch(() => undefined)
  }
}

export async function advanceSidepanelApproval(context, workerPage, extensionId, {onStatus = () => {}} = {}) {
  if (!EXTENSION_ID.test(extensionId)) throw new Error('Invalid extension identity for side-panel approval')
  if (!workerPage || typeof context.newCDPSession !== 'function') return null
  let trustedWorkerOrigin = false
  try {
    const url = new URL(workerPage.url())
    trustedWorkerOrigin = url.protocol === 'http:' &&
      ['127.0.0.1', 'localhost'].includes(url.hostname) &&
      url.port === '8765' && url.pathname === '/applications/worker'
  } catch { /* Older, fully qualified extension-origin prompts still work. */ }
  const root = await context.newCDPSession(workerPage)
  try {
    const targets = qualifiedTargets((await root.send('Target.getTargets')).targetInfos, extensionId)
    for (const target of targets) {
      let outcome
      try { outcome = await inspectAttachedTarget(root, target, trustedWorkerOrigin) }
      catch { onStatus('sidepanel-cdp-unavailable'); continue }
      if (outcome.status !== 'unrecognized-panel') onStatus(outcome.status)
      if (outcome.result) return outcome.result
    }
    return null
  } finally { await root.detach().catch(() => undefined) }
}


// Exclusive to the immediately preceding Worker "Request Multi-Session Key"
// click. The wallet signs a JSON message, not a transaction. Validate the
// rendered request against the disposable wallet before issuing mouse input.
// NEVER return or log the rendered message or any signature to the harness.
export function inspectWorkerMskSignatureSurface({expectedWallet = '', trustedWorkerOrigin = false} = {}) {
  const invalid = reason => ({kind:'msk-signature', verified:false, reason, button:null})
  if (!trustedWorkerOrigin || !/^0x[0-9a-f]{40}$/i.test(expectedWallet)) return invalid('worker-or-wallet-untrusted')
  const body = document.body?.innerText || ''
  if (!body || body.length > 32768) return invalid('surface-unavailable')
  const visible = element => {
    const r = element.getBoundingClientRect()
    const style = window.getComputedStyle(element)
    return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' &&
      style.display !== 'none' && Number(style.opacity) !== 0
  }
  const headings = [...document.querySelectorAll('h1,h2,h3,[role="heading"]')]
    .filter(visible).map(n => (n.innerText || n.textContent || '').trim())
  if (!headings.some(h => /^Signature request$/i.test(h))) return invalid('not-signature-request')
  if (!/\bNetwork\b[\s\S]{0,100}\bMain Computer Testnet\b/i.test(body)) return invalid('network-mismatch')
  // Inspect the displayed request origin, independently of the JSON message.
  const from = /\bRequest from\b([\s\S]{0,120})/i.exec(body)?.[1] || ''
  if (!/(?:^|[^a-z0-9.])(?:127\.0\.0\.1|localhost):8765\b/i.test(from)) return invalid('origin-mismatch')
  // The text must contain precisely one displayed JSON request of our kind.
  // Parse locally inside the extension renderer; expose only an approval bit.
  const matches = [...body.matchAll(/\{\s*"purpose"\s*:\s*"request_multi_session_key"/g)]
  if (matches.length !== 1) return invalid('message-not-unique')
  const start = matches[0].index
  let depth = 0, quoted = false, slash = false, finish = -1
  for (let i = start; i < body.length; i++) {
    const c = body[i]
    if (quoted) {
      if (slash) slash = false
      else if (c === '\\') slash = true
      else if (c === '"') quoted = false
    } else {
      if (c === '"') quoted = true
      else if (c === '{') depth++
      else if (c === '}' && --depth === 0) { finish = i + 1; break }
    }
  }
  if (finish < 0) return invalid('message-not-parsable')
  let message
  try { message = JSON.parse(body.slice(start,finish)) }
  catch { return invalid('message-not-parsable') }
  if (!message || Array.isArray(message) || typeof message !== 'object') return invalid('message-shape')
  const keys = ['purpose','request_id','user_slug','wallet_address','chain_id','origin','issued_at','expires_at','version']
  if (Object.keys(message).sort().join(',') !== keys.sort().join(',')) return invalid('message-keys')
  if (message.purpose !== 'request_multi_session_key' ||
      typeof message.user_slug !== 'string' || !/^[0-9a-f]{64}$/.test(message.user_slug) ||
      message.version !== 'main-computer-multisession-key-request-v1' ||
      typeof message.wallet_address !== 'string' ||
      message.wallet_address.toLowerCase() !== expectedWallet.toLowerCase() ||
      message.chain_id !== '0x28757b1' ||
      message.origin !== 'http://127.0.0.1:8765' ||
      typeof message.request_id !== 'string' || !/^msk_req_[a-z0-9-]{12,80}$/i.test(message.request_id)) {
    return invalid('message-identity-mismatch')
  }
  if (typeof message.issued_at !== 'string' || typeof message.expires_at !== 'string' ||
      !/^\d{4}-\d\d-\d\dT/.test(message.issued_at) ||
      !/^\d{4}-\d\d-\d\dT/.test(message.expires_at)) return invalid('message-time-invalid')
  const issued = Date.parse(message.issued_at), expires = Date.parse(message.expires_at), now = Date.now()
  if (!Number.isFinite(issued) || !Number.isFinite(expires) ||
      issued < now - 5 * 60_000 || issued > now + 30_000 ||
      Math.abs((expires - issued) - 10 * 60_000) > 5_000 ||
      expires <= now) return invalid('message-time-invalid')
  const candidates = [...document.querySelectorAll('button,[role="button"]')]
  const confirm = candidates.find(element => (element.innerText || element.textContent || '').trim() === 'Confirm' &&
    visible(element) && !element.disabled && element.getAttribute('aria-disabled') !== 'true')
  if (!confirm) return invalid('confirm-disabled')
  const rect = confirm.getBoundingClientRect()
  const x = Math.round(rect.left + rect.width / 2), y = Math.round(rect.top + rect.height / 2)
  const hit = document.elementFromPoint(x,y)
  if (!hit || (!confirm.contains(hit) && !hit.contains(confirm))) return invalid('confirm-obstructed')
  return {kind:'msk-signature', verified:true, reason:'validated', button:{label:'Confirm',x,y}}
}

function isTrustedWorkerPage(page) {
  try {
    const url = new URL(page.url())
    return url.protocol === 'http:' && url.hostname === '127.0.0.1' &&
      url.port === '8765' && url.pathname === '/applications/worker'
  } catch { return false }
}

// Do not reuse the general wallet-approval handler for signatures. This method
// is reachable only after the test has explicitly clicked Request Key, and
// is authorized for one matching message and one matching account only.
export async function approveWorkerMskSignature(context, workerPage, extensionId, expectedWallet, {
  timeoutMs = 30_000, pollMs = 350,
  now = () => Date.now(), sleep = ms => new Promise(resolve => setTimeout(resolve,ms)),
  onProgress = () => {}
} = {}) {
  if (!EXTENSION_ID.test(extensionId) || !isTrustedWorkerPage(workerPage) ||
      !/^0x[0-9a-f]{40}$/i.test(expectedWallet)) {
    throw new Error('MSK signature refused: untrusted test wallet, extension, or Worker origin')
  }
  const start = now()
  let lastReason = 'panel-not-found'
  while (now() - start < timeoutMs) {
    let root
    try {
      root = await context.newCDPSession(workerPage)
      const targets = qualifiedTargets((await root.send('Target.getTargets')).targetInfos,extensionId)
      for (const target of targets) {
        let sessionId
        try {
          sessionId = (await root.send('Target.attachToTarget',{targetId:target.targetId, flatten:false})).sessionId
          const nested = new NestedTargetSession(root,sessionId)
          await nested.send('Runtime.enable')
          const expression = `(${inspectWorkerMskSignatureSurface.toString()})(${JSON.stringify({expectedWallet, trustedWorkerOrigin:true})})`
          const data = await nested.send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:false,silent:true})
          const state = data?.result?.value
          if (!state || state.kind !== 'msk-signature') continue
          lastReason = typeof state.reason === 'string' && /^[a-z-]{1,60}$/.test(state.reason)
            ? state.reason : 'inspection-unavailable'
          if (!state.verified) continue
          const b = state.button
          if (!b || b.label !== 'Confirm' || !Number.isInteger(b.x) || !Number.isInteger(b.y) ||
              b.x < 0 || b.y < 0 || b.x > 4096 || b.y > 4096) continue
          const mouse = {x:b.x,y:b.y,button:'left',clickCount:1}
          await nested.send('Input.dispatchMouseEvent',{...mouse,type:'mouseMoved'})
          await nested.send('Input.dispatchMouseEvent',{...mouse,type:'mousePressed'})
          await nested.send('Input.dispatchMouseEvent',{...mouse,type:'mouseReleased'})
          onProgress('metamask.msk-signature')
          return {type:'msk-signature', action:'Confirm'}
        } catch { lastReason = 'cdp-target-unavailable' }
        finally { if (sessionId) await root.send('Target.detachFromTarget',{sessionId}).catch(()=>undefined) }
      }
    } catch { lastReason = 'cdp-unavailable' }
    finally { await root?.detach()?.catch(()=>undefined) }
    await sleep(pollMs)
  }
  throw new Error('MetaMask MSK signature approval refused or unavailable (' + lastReason + '); no arbitrary signature approved')
}
