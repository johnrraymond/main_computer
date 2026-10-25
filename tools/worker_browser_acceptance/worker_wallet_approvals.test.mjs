import test from 'node:test'
import assert from 'node:assert/strict'
import {advanceWorkerWalletApproval, completeWorkerWalletConnection, safeWalletApprovalDiagnostics, installWorkerWalletTrace, readWorkerWalletTrace, startWorkerWalletConnection} from './worker_wallet_approvals.mjs'
import vm from 'node:vm'

const EXT = 'a'.repeat(32)
const WALLET = '0x1234567890abcdef1234567890abcdef12345678'
const SITE = '127.0.0.1:8765'
const RPC = 'testnet-rpc.greatlibrary.io'
const CHAIN = 'Main Computer Testnet'
class FakeLocator {
  constructor({label = '', visible = false, enabled = true, action = () => {}} = {}) {
    this.label = label; this.visible = visible; this.enabled = enabled; this.action = action
  }
  first() { return this }
  async isVisible() { return this.visible }
  async isEnabled() { return this.enabled }
  async click() { this.action(this.label) }
}
function popup({extension = EXT, filename = 'notification.html', texts = [], buttons = [], pressed = [], enabled = true} = {}) {
  const matches = target => texts.some(text => target instanceof RegExp ? target.test(text) : text.includes(target))
  return {
    url: () => `chrome-extension://${extension}/${filename}`,
    isClosed: () => false,
    getByText: target => new FakeLocator({visible: matches(target)}),
    getByRole: (role, {name} = {}) => new FakeLocator({label: name,
      visible: role === 'button' && buttons.includes(name), enabled, action: label => pressed.push(label)}),
  }
}

// Reproduces the user's 13.53 screenshot, including the exact title and
// the Confirm button; neither "Add network" nor "Add a network" is present.
test('MetaMask 13.53 Add Main Computer Testnet: confirms only matching origin, RPC, and chain', async () => {
  const pressed = []
  const p = popup({filename: 'sidepanel.html', texts: [
    'Add Main Computer Testnet', 'A site is suggesting additional network details.',
    SITE, CHAIN, RPC,
  ], buttons: ['Cancel', 'Confirm'], pressed})
  assert.deepEqual(await advanceWorkerWalletApproval({pages: () => [p]}, EXT), {type:'network-add', action:'Confirm'})
  assert.deepEqual(pressed, ['Confirm'])
})

test('legacy Add a network still works with origin and RPC checks', async () => {
  const pressed = []
  const p = popup({texts: ['Add a network', CHAIN, SITE, RPC], buttons: ['Add network'], pressed})
  assert.equal((await advanceWorkerWalletApproval({pages: () => [p]}, EXT)).type, 'network-add')
  assert.deepEqual(pressed, ['Add network'])
})

test('supports matching network switch and account connection screens', async () => {
  const pressed = []
  const sw = popup({texts: ['Switch to Main Computer Testnet', CHAIN, SITE], buttons: ['Confirm'], pressed})
  assert.equal((await advanceWorkerWalletApproval({pages: () => [sw]}, EXT)).type, 'network-switch')
  const account = popup({filename: 'popup.html', texts: ['Connect with MetaMask', SITE], buttons: ['Next'], pressed})
  assert.equal((await advanceWorkerWalletApproval({pages: () => [account]}, EXT)).type, 'account-connect')
  assert.deepEqual(pressed, ['Confirm', 'Next'])
})

test('home.html allowed only for strict matching request; unrelated wallet home never clicked', async () => {
  const pressed = []
  const sidepanelAsHome = popup({filename:'home.html', texts:['Add Main Computer Testnet', CHAIN, SITE, RPC],
    buttons:['Confirm'], pressed})
  assert.equal((await advanceWorkerWalletApproval({pages: () => [sidepanelAsHome]}, EXT)).type, 'network-add')
  const normalWalletHome = popup({filename:'home.html', texts:['Fund your wallet', CHAIN, SITE, RPC],
    buttons:['Confirm'], pressed})
  assert.equal(await advanceWorkerWalletApproval({pages: () => [normalWalletHome]}, EXT), null)
  assert.deepEqual(pressed, ['Confirm'])
})

for (const [name, texts] of [
  ['wrong chain', ['Add Main Computer Testnet', SITE, RPC.replace('testnet-', 'other-')]],
  ['wrong origin', ['Add Main Computer Testnet', 'malicious.example', RPC]],
  ['wrong RPC', ['Add Main Computer Testnet', SITE, 'evil-rpc.example']],
  ['RPC lookalike host', ['Add Main Computer Testnet', SITE, RPC + '.evil.example']],
  ['origin lookalike', ['Add Main Computer Testnet', '127.0.0.1:8765.evil.example', RPC]],
  ['only chain text', [CHAIN]],
  ['only generic Confirm', ['Unrelated signature request', CHAIN, SITE]],
]) {
  test(`does not click Confirm with ${name}`, async () => {
    const pressed = []
    const p = popup({texts, buttons:['Confirm'], pressed})
    assert.equal(await advanceWorkerWalletApproval({pages: () => [p]}, EXT), null)
    assert.deepEqual(pressed, [])
  })
}

test('refuses imitation extension and wrong app even with valid-looking text', async () => {
  const pressed = []
  const impostor = popup({extension: 'b'.repeat(32), texts:['Add Main Computer Testnet',SITE,RPC],buttons:['Confirm'],pressed})
  assert.equal(await advanceWorkerWalletApproval({pages: () => [impostor]}, EXT), null)
  assert.deepEqual(pressed, [])
  await assert.rejects(advanceWorkerWalletApproval({pages: () => []}, 'invalid'), /invalid extension identity/)
})

test('disabled approval does not bypass wallet UI', async () => {
  const pressed = []
  const p = popup({texts: ['Add Main Computer Testnet', SITE, RPC], buttons: ['Confirm'], enabled: false, pressed})
  assert.equal(await advanceWorkerWalletApproval({pages: () => [p]}, EXT), null)
  assert.deepEqual(pressed, [])
})

test('diagnostics contain only classifications, no extension route or sensitive text', async () => {
  const p = popup({filename:'notification.html?secret=should-not-log', texts:['Add Main Computer Testnet', SITE, RPC]})
  const result = await safeWalletApprovalDiagnostics({pages:()=>[p]}, EXT)
  assert.equal(result.extensionPages, 1)
  assert.equal(result.pages[0].kind, 'notification')
  assert.equal(result.pages[0].networkAdd, true)
  assert.doesNotMatch(JSON.stringify(result), /should-not-log/)
})

function workerPage({statuses = ['Not connected'], messages = [''], button = 'Wallet Open…'} = {}) {
  let i = 0
  return {
    locator: id => ({innerText: async () => {
      if (id === '#worker-primary-wallet-status') return statuses[Math.min(i++, statuses.length - 1)]
      if (id === '#worker-save-status') return messages[Math.min(Math.max(i - 1, 0), messages.length - 1)]
      if (id === '#worker-connect-wallet') return button
      return ''
    }}),
  }
}

test('pending MetaMask approval is not confused with failure after 3 seconds', async () => {
  let time = 0
  const phases = []
  const page = workerPage({statuses: ['Not connected','Not connected','Not connected','Not connected',
      `Connected ${WALLET.slice(0, 6)}...${WALLET.slice(-4)}`],
    messages:['Requesting MetaMask network update to https://testnet-rpc.greatlibrary.io']})
  let n = 0
  const got = await completeWorkerWalletConnection({context:{}, page, extensionId:EXT,
    expectedAddress:WALLET, deadlineMs:30_000, pollMs: 1_500,
    now:()=>time, sleep: async ms => {time += ms},
    approve:async () => ++n === 3 ? {type:'network-add'} : null,
    diagnostics:async () => ({extensionPages:0,pages:[]}), onProgress: x => phases.push(x)})
  assert.deepEqual(got, ['network-add'])
  assert.ok(time >= 4_500, 'the old 3-second check would have failed')
  assert.ok(phases.includes('metamask.network-add'))
})

test('real Worker connect failed status produces an actionable error', async () => {
  let time = 0
  const page = workerPage({messages:['Connect failed: RPC endpoint 403 Forbidden']})
  await assert.rejects(completeWorkerWalletConnection({context:{}, page, extensionId:EXT,
    expectedAddress:WALLET, now:()=>time, sleep: async ms=>{time+=ms}, approve:async()=>null}),
    /RPC endpoint 403 Forbidden/)
})

test('unrecognized popup has safe diagnostic and never clicks', async () => {
  let time = 0
  const suspicious = popup({texts:['Approve this signature', CHAIN, SITE], buttons:['Confirm']})
  const page = workerPage({messages:['Requesting MetaMask network update to https://testnet-rpc.greatlibrary.io']})
  await assert.rejects(completeWorkerWalletConnection({context:{pages:()=>[suspicious]}, page, extensionId:EXT,
    expectedAddress:WALLET, now:()=>time, sleep: async ms=>{time+=ms}, pollMs:3_000,
    unrecognizedMs:6_000}), /not recognized or not enabled/)
})

test('refuses different wallet and never claims success from unrelated approval', async () => {
  let time = 0
  const page = workerPage({statuses:['Connected 0xaaaaaaaa...aaaa'], messages:['RPC backoff retrying in 2s']})
  await assert.rejects(completeWorkerWalletConnection({context:{pages:()=>[]}, page, extensionId:EXT,
    expectedAddress:WALLET, deadlineMs:900, now:()=>time, sleep:async ms=>{time+=ms},
    approve:async()=>null}), /timed out/)
})

// The actual Worker emits these events through window.dispatchEvent. A status
// label can be overwritten by unrelated UI rendering and must not be used
// as the primary diagnostic signal.
function eventPage() {
  const listeners = new Map()
  const fakeWindow = {addEventListener: (name, fn) => listeners.set(name, fn)}
  const page = {evaluate: async fn => vm.runInNewContext(`(${fn.toString()})()`, {window: fakeWindow})}
  return {page, emit: (type, detail) => listeners.get('main-computer-worker-wallet')?.({detail: {type, detail}})}
}

test('records only allowlisted Worker event names, never event payload or wallet secrets', async () => {
  const {page, emit} = eventPage()
  await installWorkerWalletTrace(page)
  emit('connect.wallet.addChain.start', {mnemonic: 'super secret should never appear', signature: '0x00'})
  emit('other.unrelated', {password: 'should never appear'})
  emit('connect.wallet.rpcProof.done', {account: WALLET})
  assert.deepEqual(Array.from(await readWorkerWalletTrace(page)), ['connect.wallet.addChain.start', 'connect.wallet.rpcProof.done'])
  assert.doesNotMatch(JSON.stringify(await readWorkerWalletTrace(page)), /super secret|password|1234567890abcdef/)
})

test('reports a real Worker state event instead of spurious worker.other', async () => {
  const {page: eventSource, emit} = eventPage()
  await installWorkerWalletTrace(eventSource)
  const source = workerPage({statuses: ['Not connected', 'Not connected',
    `Connected ${WALLET.slice(0,6)}...${WALLET.slice(-4)}`], messages: [
    'Worker marketplace settings are local-only until their matching backend paths are connected.']})
  source.evaluate = eventSource.evaluate
  let clock = 0
  const lines = []
  const done = await completeWorkerWalletConnection({context:{}, page: source, extensionId:EXT,
    expectedAddress:WALLET, pollMs: 20, now:()=>clock, sleep:async ms => {clock+=ms; emit('connect.wallet.addChain.start', {secret:'not logged'})},
    approve:async()=>null, onProgress:line=>lines.push(line)})
  assert.deepEqual(done, [])
  assert.ok(lines.includes('worker.connect.wallet.addChain.start'))
  assert.ok(!lines.includes('worker.other'))
})

test('connect.failed event is terminal even when general-purpose status text is overwritten', async () => {
  const {page:eventSource, emit} = eventPage()
  await installWorkerWalletTrace(eventSource)
  const source = workerPage({messages:['Worker marketplace settings are local-only']})
  source.evaluate = eventSource.evaluate
  let clock = 0
  await assert.rejects(completeWorkerWalletConnection({context:{}, page:source, extensionId:EXT,
    expectedAddress:WALLET, now:()=>clock,
    sleep:async ms=>{clock+=ms; emit('connect.failed', {mnemonic:'never printed'})},
    approve:async()=>null}), /Worker emitted connect.failed/)
})

test('CDP target classification never reports target URLs or identifiers', async () => {
  let detached = false
  const fakeContext = {pages:()=>[], newCDPSession:async()=>({
    send:async()=>({targetInfos:[
      {type:'other', url:`chrome-extension://${EXT}/sidepanel.html?token=secret`, targetId:'sensitive'},
      {type:'service_worker', url:`chrome-extension://${EXT}/background.js`,targetId:'also-sensitive'},
      {type:'page', url:'https://example.com/private?token=secret'},
    ]}), detach:async()=>{detached=true}
  })}
  const result = await safeWalletApprovalDiagnostics(fakeContext, EXT, {})
  assert.deepEqual(result.cdpExtensionTargets, [{kind:'sidepanel',type:'other'}, {kind:'other-extension',type:'service_worker'}])
  assert.equal(detached, true)
  assert.doesNotMatch(JSON.stringify(result), /secret|sensitive|example.com/)
})

// Reproduces the fresh-network run: MetaMask's sidepanel target remains
// registered after network-add, while the Worker emits rpcProof.backoffWait.
// The generic status can still say 'network update requested'. That is NOT
// evidence of an approval waiting to be clicked.
test('RPC backoff event outranks stale network-update status and idle sidepanel target', async () => {
  const {page: eventSource, emit} = eventPage()
  await installWorkerWalletTrace(eventSource)
  emit('connect.wallet.addChain.start')
  const source = workerPage({
    statuses: Array(8).fill('Not connected').concat([`Connected ${WALLET.slice(0, 6)}...${WALLET.slice(-4)}`]),
    messages: ['Requesting MetaMask network update to https://testnet-rpc.greatlibrary.io'],
  })
  source.evaluate = eventSource.evaluate
  let clock = 0, ticks = 0, diagnosticCalls = 0, approvalCalls = 0
  const events = []
  const approvals = await completeWorkerWalletConnection({
    context: {}, page: source, extensionId: EXT, expectedAddress: WALLET,
    pollMs: 3000, unrecognizedMs: 6000, now: () => clock,
    sleep: async ms => {
      clock += ms
      ticks += 1
      if (ticks >= 1 && ticks <= 6) emit('connect.wallet.rpcProof.backoffWait')
      if (ticks === 7) emit('connect.wallet.rpcProof.done')
    },
    approve: async () => { approvalCalls++; return null },
    diagnostics: async () => {
      diagnosticCalls++
      return {extensionPages: 2, pages: [
        {kind: 'home', networkAdd: false, networkSwitch: false, accountConnect: false},
        {kind: 'home', networkAdd: false, networkSwitch: false, accountConnect: false},
      ], cdpExtensionTargets: [{kind: 'sidepanel', type: 'page'}]}
    },
    onProgress: event => events.push(event),
  })
  assert.deepEqual(approvals, [])
  assert.ok(clock >= 21000, 'must outlive the old 15-second popup refusal')
  assert.ok(events.includes('worker.connect.wallet.rpcProof.backoffWait'))
  assert.equal(diagnosticCalls, 0, 'idle sidepanel is not an approval failure during RPC proof')
  assert.ok(approvalCalls < ticks, 'do not probe sidepanel on each RPC backoff tick')
})

test('RPC backoff surfaces the actual Worker failure rather than an approval error', async () => {
  const {page: eventSource, emit} = eventPage()
  await installWorkerWalletTrace(eventSource)
  emit('connect.wallet.addChain.done')
  const source = workerPage({messages: [
    'Requesting MetaMask network update to https://testnet-rpc.greatlibrary.io',
  ]})
  source.evaluate = eventSource.evaluate
  let clock = 0, ticks = 0
  await assert.rejects(completeWorkerWalletConnection({
    context: {}, page: source, extensionId: EXT, expectedAddress: WALLET,
    pollMs: 3000, unrecognizedMs: 6000, now: () => clock,
    sleep: async ms => {
      clock += ms
      ticks++
      emit(ticks < 9 ? 'connect.wallet.rpcProof.backoffWait' : 'connect.failed')
    },
    approve: async () => null,
    diagnostics: async () => ({extensionPages: 0, pages: [],
      cdpExtensionTargets: [{kind: 'sidepanel', type: 'page'}]}),
  }), /Worker emitted connect.failed/)
  assert.ok(clock >= 24000)
})

test('a truly pending network addition is still refused when no UI can be approved', async () => {
  const {page: eventSource, emit} = eventPage()
  await installWorkerWalletTrace(eventSource)
  emit('connect.wallet.addChain.start')
  const source = workerPage({messages: [
    'Requesting MetaMask network update to https://testnet-rpc.greatlibrary.io',
  ]})
  source.evaluate = eventSource.evaluate
  let clock = 0
  await assert.rejects(completeWorkerWalletConnection({
    context: {}, page: source, extensionId: EXT, expectedAddress: WALLET,
    pollMs: 3000, unrecognizedMs: 6000, now: () => clock,
    sleep: async ms => { clock += ms }, approve: async () => null,
    diagnostics: async () => ({extensionPages: 0, pages: [],
      cdpExtensionTargets: [{kind: 'sidepanel', type: 'page'}]}),
  }), /MetaMask side-panel approval is still pending/)
})

test('RPC proof timeout reports RPC rather than an approval issue', async () => {
  const {page: eventSource, emit} = eventPage()
  await installWorkerWalletTrace(eventSource)
  emit('connect.wallet.rpcProof.backoffWait')
  const source = workerPage({messages: ['Requesting MetaMask network update']})
  source.evaluate = eventSource.evaluate
  let clock = 0
  await assert.rejects(completeWorkerWalletConnection({
    context: {}, page: source, extensionId: EXT, expectedAddress: WALLET,
    deadlineMs: 7000, pollMs: 3000, unrecognizedMs: 3000,
    now: () => clock, sleep: async ms => { clock += ms },
    approve: async () => { throw new Error('approval probing during RPC proof') },
    diagnostics: async () => ({extensionPages: 0, pages: [],
      cdpExtensionTargets: [{kind: 'sidepanel', type: 'page'}]}),
  }), /RPC proof did not finish before browser acceptance deadline/)
})

// A visible Worker panel is not sufficient: its click binding may still be
// installing or passive hydration may have disabled Connect Wallet.
function connectStartFixture({bound = true, disabled = false, activity = 'trace', connected = false} = {}) {
  let clicks = 0
  const trace = []
  const button = {
    dataset: {workerBound: bound ? 'true' : undefined},
    disabled,
    textContent: connected ? 'Connected' : disabled ? 'Checking Wallet…' : 'Connect Wallet',
    busy: false,
    getAttribute(name) {return name === 'aria-busy' && this.busy ? 'true' : null},
  }
  const globals = {
    document: {querySelector: selector => selector === '#worker-connect-wallet' ? button : null},
    window: {__mcWorkerWalletTrace: trace}
  }
  const run = (fn, arg) => vm.runInNewContext(`(${fn.toString()})(__arg)`, {...globals, __arg: arg})
  const page = {
    waitForFunction: async (fn, arg) => {if (!run(fn, arg)) throw new Error('timeout')},
    evaluate: async fn => run(fn),
    locator: selector => {
      assert.equal(selector, '#worker-connect-wallet')
      return {click: async () => {
        clicks++
        if (activity === 'trace') trace.push('connect.wallet.networkPreflight.start')
        if (activity === 'busy') button.busy = true
        if (activity === 'ignored') trace.push('connect.ignored.busy')
      }}
    }
  }
  return {page, trace, button, getClicks: () => clicks}
}

test('Connect Wallet waits for actual Worker handler and enabled control', async () => {
  for (const initial of [{bound:false}, {disabled:true}]) {
    const f = connectStartFixture(initial)
    await assert.rejects(startWorkerWalletConnection(f.page), /never became handler-bound and enabled/)
    assert.equal(f.getClicks(), 0)
  }
})

test('work-cycle reuses the already-connected disposable wallet without clicking again', async () => {
  const f = connectStartFixture({connected:true, disabled:true})
  const events = []
  const result = await startWorkerWalletConnection(f.page, {
    allowAlreadyConnected: true, onProgress: e => events.push(e),
  })
  assert.deepEqual(result, {alreadyConnected:true})
  assert.equal(f.getClicks(), 0)
  assert.deepEqual(events, ['worker.connect.already-connected'])
})

test('wallet-only acceptance does not silently bypass Connect Wallet with a reused profile', async () => {
  const f = connectStartFixture({connected:true, disabled:true})
  await assert.rejects(startWorkerWalletConnection(f.page), /never became handler-bound and enabled/)
  assert.equal(f.getClicks(), 0)
})

test('connected UI without installed handler is never accepted as a reusable session', async () => {
  const f = connectStartFixture({connected:true, disabled:true, bound:false})
  await assert.rejects(startWorkerWalletConnection(f.page, {allowAlreadyConnected:true}),
    /never became handler-bound and enabled/)
  assert.equal(f.getClicks(), 0)
})

test('hydrating/disabled is not accepted just because reuse was authorized', async () => {
  const f = connectStartFixture({disabled:true})
  await assert.rejects(startWorkerWalletConnection(f.page, {allowAlreadyConnected:true}),
    /never became handler-bound and enabled/)
  assert.equal(f.getClicks(), 0)
})

test('Connect Wallet confirms a single dispatched click and a real Worker event', async () => {
  const f = connectStartFixture({activity:'trace'})
  const events = []
  await startWorkerWalletConnection(f.page, {onProgress: event=>events.push(event)})
  assert.equal(f.getClicks(), 1)
  assert.deepEqual(events, ['worker.connect.control-ready', 'worker.connect.click-dispatched', 'worker.connect.activity-observed'])
})

test('Connect Wallet may start by entering busy state before emitting a connect event', async () => {
  const f = connectStartFixture({activity:'busy'})
  await startWorkerWalletConnection(f.page)
  assert.equal(f.getClicks(), 1)
})

test('Connect Wallet fails with a safe diagnostic when click did not start connection', async () => {
  const f = connectStartFixture({activity:'none'})
  await assert.rejects(startWorkerWalletConnection(f.page), /no connection event or busy state/)
  assert.equal(f.getClicks(), 1)
})

test('Connect Wallet does not accept a click ignored because hydration became busy', async () => {
  const f = connectStartFixture({activity:'ignored'})
  await assert.rejects(startWorkerWalletConnection(f.page), /ignored Connect Wallet/)
  assert.equal(f.getClicks(), 1)
})

// Screenshot: MetaMask 13.53 has title "127.0.0.1" (no :8765), the
// subtitle "Connect this website with MetaMask", and a Connect button.
test('MetaMask 13.53 account permission approval uses verified Worker URL and exact panel anchors', async () => {
  const pressed = []
  const permission = popup({filename:'sidepanel.html', texts:[
    '127.0.0.1', 'Connect this website with MetaMask',
    'Account 1', 'See addresses, balances, and activity'
  ], buttons:['Cancel','Connect'],pressed})
  const localWorker = {url: () => 'http://127.0.0.1:8765/applications/worker'}
  assert.deepEqual(await advanceWorkerWalletApproval({pages:()=>[permission]}, EXT, localWorker),
    {type:'account-connect',action:'Connect'})
  assert.deepEqual(pressed,['Connect'])
})

test('never approve bare-host account consent without exact trusted Worker page', async () => {
  const pressed=[]
  const permission=popup({filename:'sidepanel.html', texts:[
    '127.0.0.1','Connect this website with MetaMask',
    'See addresses, balances, and activity'
  ], buttons:['Connect'],pressed})
  for (const url of [null, 'http://127.0.0.1:8766/applications/worker',
    'https://127.0.0.1:8765/applications/worker',
    'http://127.0.0.1:8765/applications/other',
    'http://127.0.0.1.evil.example:8765/applications/worker',
    'http://attacker.example:8765/applications/worker']) {
    const workerPage=url===null?null:{url:()=>url}
    assert.equal(await advanceWorkerWalletApproval({pages:()=>[permission]}, EXT,workerPage),null)
  }
  assert.deepEqual(pressed,[])
})

test('never approve host-only panel without matching consent caption and permission', async () => {
  const pressed=[]
  const workerPage={url:()=> 'http://127.0.0.1:8765/applications/worker'}
  for (const texts of [
    ['127.0.0.1','See addresses, balances, and activity'],
    ['127.0.0.1','Connect this website with MetaMask'],
    ['attacker.example','Connect this website with MetaMask','See addresses, balances, and activity'],
    ['127.0.0.1.evil.example','Connect this website with MetaMask','See addresses, balances, and activity'],
  ]) {
    const p=popup({filename:'sidepanel.html',texts,buttons:['Connect'],pressed})
    assert.equal(await advanceWorkerWalletApproval({pages:()=>[p]},EXT,workerPage),null)
  }
  assert.deepEqual(pressed,[])
})

// Live regression 2026-10-23: the final event was addChain.done, MetaMask had
// returned to its wallet home, and the Worker UI continued to advance. No
// approval surface should be inspected until a new *.start event arrives.
test('completed network addition never triggers an idle side-panel approval probe', async () => {
  const {page: eventSource, emit} = eventPage()
  await installWorkerWalletTrace(eventSource)
  emit('connect.wallet.addChain.done')
  const source = workerPage({
    statuses: Array(6).fill('Not connected').concat([`Connected ${WALLET.slice(0, 6)}...${WALLET.slice(-4)}`]),
    messages: ['Requesting MetaMask network update to https://testnet-rpc.greatlibrary.io'],
  })
  source.evaluate = eventSource.evaluate
  let clock = 0, approvalCalls = 0
  const progress = []
  const result = await completeWorkerWalletConnection({
    context: {}, page: source, extensionId: EXT, expectedAddress: WALLET,
    now: () => clock, pollMs: 3_000, sleep: async ms => {clock += ms},
    approve: async () => {approvalCalls++; throw new Error('stale sidepanel must not be inspected')},
    diagnostics: async () => {throw new Error('stale sidepanel must not be diagnosed')},
    onProgress: event => progress.push(event),
  })
  assert.deepEqual(result, [])
  assert.equal(approvalCalls, 0)
  assert.ok(progress.includes('worker.connect.wallet.addChain.done'))
  assert.ok(progress.includes('worker.connect.waiting-after-network-add'))
})

test('completed network addition without a next Worker event fails with a bounded transition error', async () => {
  const {page: eventSource, emit} = eventPage()
  await installWorkerWalletTrace(eventSource)
  emit('connect.wallet.addChain.done')
  const source = workerPage({statuses:['Not connected'], messages:['Requesting MetaMask network update']})
  source.evaluate = eventSource.evaluate
  let clock = 0, approvalCalls = 0
  await assert.rejects(completeWorkerWalletConnection({
    context: {}, page: source, extensionId: EXT, expectedAddress: WALLET,
    now: () => clock, deadlineMs: 60_000, pollMs: 5_000,
    sleep: async ms => {clock += ms},
    approve: async () => {approvalCalls++; return null},
    diagnostics: async () => ({extensionPages:0,pages:[]}),
  }), /did not advance after connect\.wallet\.addChain\.done within 45s/)
  assert.equal(approvalCalls, 0)
})

test('a later account request resumes approval probing after completed network addition', async () => {
  const {page: eventSource, emit} = eventPage()
  await installWorkerWalletTrace(eventSource)
  emit('connect.wallet.addChain.done')
  const source = workerPage({statuses:['Not connected','Not connected','Not connected',
    `Connected ${WALLET.slice(0,6)}...${WALLET.slice(-4)}`], messages:['Requesting network update']})
  source.evaluate = eventSource.evaluate
  let clock = 0, approvalCalls = 0
  const approvals = await completeWorkerWalletConnection({
    context:{}, page: source, extensionId: EXT, expectedAddress: WALLET,
    now:()=>clock, pollMs:100, sleep:async ms => {clock += ms; emit('connect.ethers.requestAccounts.start')},
    approve:async () => {approvalCalls++; return {type:'account-connect'}},
    diagnostics:async () => ({extensionPages:0,pages:[]}),
  })
  assert.ok(approvalCalls > 0)
  assert.ok(approvals.includes('account-connect'))
})

test('an actually pending approval inspection is bounded and fails closed if CDP stalls', async () => {
  const {page: eventSource, emit} = eventPage()
  await installWorkerWalletTrace(eventSource)
  emit('connect.ethers.requestAccounts.start')
  const source = workerPage({statuses:['Not connected'], messages:['Opening browser wallet account request']})
  source.evaluate = eventSource.evaluate
  await assert.rejects(completeWorkerWalletConnection({
    context:{}, page: source, extensionId: EXT, expectedAddress: WALLET,
    approvalProbeMs: 10, approve: () => new Promise(() => {}),
  }), /approval inspection stalled/)
})
