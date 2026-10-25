import test from 'node:test'
import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {advanceSidepanelApproval,inspectApprovalSurface,approveWorkerMskSignature,inspectWorkerMskSignatureSurface} from './metamask_sidepanel_cdp.mjs'
import {advanceWorkerWalletApproval} from './worker_wallet_approvals.mjs'
const EXT='a'.repeat(32)
const validState={type:'network-add', button:{label:'Confirm',x:40,y:520},
  origin:true, rpc:true,chain:true,headingRecognized:true}
function mockContext({file='sidepanel.html', extension=EXT, targetType='page', state=validState, failAttach=false}={}) {
  const events=[]
  let detached=false, attached=0, rootDetached=false
  class Root extends EventEmitter {
    async send(method,params={}) {
      events.push({method, params})
      if (method==='Target.getTargets') return {targetInfos:[
        {url:`chrome-extension://${extension}/${file}`,type:targetType,targetId:'hidden-identifier'}]}
      if (method==='Target.attachToTarget') { attached++; if (failAttach) throw new Error('unsupported'); return {sessionId:'nested'} }
      if (method==='Target.detachFromTarget') {detached=true;return {}}
      if (method==='Target.sendMessageToTarget') {
        const command=JSON.parse(params.message)
        const result=command.method==='Runtime.evaluate' ? {result:{value:state}} : {}
        queueMicrotask(()=>this.emit('Target.receivedMessageFromTarget',{
          sessionId:params.sessionId,message:JSON.stringify({id:command.id,result})}))
        return {}
      }
      throw new Error('unexpected '+method)
    }
    async detach() {rootDetached=true}
  }
  const root=new Root()
  return {context:{pages:()=>[],newCDPSession:async()=>root}, workerPage:{}, events,
    get detached(){return detached},get rootDetached(){return rootDetached},get attached(){return attached}}
}
const clicks = events=>events.filter(e=>e.method==='Target.sendMessageToTarget')
  .map(e=>JSON.parse(e.params.message)).filter(e=>e.method==='Input.dispatchMouseEvent')
test('sidepanel target not in context.pages receives trusted press/release and returns network-add',async()=>{
  const fixture=mockContext()
  const response=await advanceWorkerWalletApproval(fixture.context,EXT,fixture.workerPage)
  assert.deepEqual(response,{type:'network-add',action:'Confirm'})
  assert.deepEqual(clicks(fixture.events).map(e=>e.params.type),['mouseMoved','mousePressed','mouseReleased'])
  assert.equal(fixture.attached,1)
  assert.equal(fixture.detached,true)
  assert.equal(fixture.rootDetached,true)
  assert.deepEqual(fixture.events.filter(e=>e.method==='Target.attachToTarget').map(e=>e.params.flatten),[false])
})
test('same trust constraints in CDP: refuse mismatched origin, RPC, disabled and wrong label',async()=>{
  for (const overrides of [{origin:false},{rpc:false},{button:null},{button:{label:'Sign',x:30,y:40}},
    {button:{label:'Confirm',x:-50,y:500}},{headingRecognized:false}]) {
    const f=mockContext({state:{...validState,...overrides}})
    assert.equal(await advanceSidepanelApproval(f.context,f.workerPage,EXT),null)
    assert.deepEqual(clicks(f.events),[])
  }
})
test('sidepanel only; wrong extension, home, background and wrong URL do not attach',async()=>{
  for (const overrides of [{extension:'b'.repeat(32)},{file:'home.html'},
    {file:'background.js'},{targetType:'service_worker'}]) {
    const f=mockContext(overrides)
    assert.equal(await advanceSidepanelApproval(f.context,f.workerPage,EXT),null)
    assert.equal(f.attached,0)
  }
})
test('CDP transport refusal does not produce an unauthorized click',async()=>{
  const f=mockContext({failAttach:true})
  const statuses=[]
  assert.equal(await advanceSidepanelApproval(f.context,f.workerPage,EXT,{onStatus:status=>statuses.push(status)}),null)
  assert.deepEqual(statuses,['sidepanel-cdp-unavailable'])
  assert.deepEqual(clicks(f.events),[])
  assert.equal(f.rootDetached,true)
})
test('accounts and chain switching have separate allowlists',async()=>{
  for (const state of [
    {type:'account-connect',origin:true,chain:false,rpc:false, headingRecognized:true, button:{label:'Next',x:44,y:80}},
    {type:'network-switch',origin:true,chain:true,rpc:false, headingRecognized:true, button:{label:'Switch network',x:44,y:80}}]) {
    const f=mockContext({state})
    assert.equal((await advanceSidepanelApproval(f.context,f.workerPage,EXT)).type,state.type)
    assert.equal(clicks(f.events).length,3)
  }
})
test('visible browser DOM validates exact add-network anchors without returning text',()=>{
  function fakeDOM({title='Add Main Computer Testnet',text='Add Main Computer Testnet 127.0.0.1:8765 testnet-rpc.greatlibrary.io',
     buttonLabel='Confirm',disabled=false}={}) {
    const rect={left:0,top:0,width:200,height:80}
    const heading={innerText:title,getBoundingClientRect:()=>rect}
    const button={innerText:buttonLabel,disabled, getAttribute:()=>null,getBoundingClientRect:()=>rect,
      contains:()=>true}
    globalThis.document={body:{innerText:text}, querySelectorAll:sel=>sel==='h1,h2,h3,[role="heading"]'?[heading]:[button],
      elementFromPoint:()=>button}
    globalThis.window={getComputedStyle:()=>({visibility:'visible',display:'block',opacity:'1'})}
  }
  try {
    fakeDOM()
    assert.equal(inspectApprovalSurface().type,'network-add')
    assert.equal(inspectApprovalSurface().button.label,'Confirm')
    fakeDOM({text:'Add Main Computer Testnet 127.0.0.1:8765 evil.example'})
    assert.equal(inspectApprovalSurface().type,null)
    fakeDOM({disabled:true})
    assert.equal(inspectApprovalSurface().button,null)
    fakeDOM({title:'Sign transaction'})
    assert.equal(inspectApprovalSurface().type,null)
  } finally {delete globalThis.document;delete globalThis.window}
})

test('actual MetaMask 13.53 site-connection screen recognized with local Worker origin proof',()=>{
  const rect={left:30,top:900,width:140,height:50}
  const h={innerText:'127.0.0.1',getBoundingClientRect:()=>rect}
  const button={innerText:'Connect',disabled:false,getAttribute:()=>null,
    getBoundingClientRect:()=>rect,contains:()=>true}
  globalThis.document={body:{innerText:'127.0.0.1\nConnect this website with MetaMask\nAccount 1\nSee addresses, balances, and activity'},
    querySelectorAll:sel=>sel==='h1,h2,h3,[role="heading"]'?[h]:[button], elementFromPoint:()=>button}
  globalThis.window={getComputedStyle:()=>({visibility:'visible',display:'block',opacity:'1'})}
  try {
    assert.equal(inspectApprovalSurface().type,null, 'bare host alone cannot authorize a click')
    const state=inspectApprovalSurface({trustedWorkerOrigin:true})
    assert.equal(state.type,'account-connect')
    assert.equal(state.button.label,'Connect')
    assert.equal(state.origin,true)
    document.body.innerText='127.0.0.1\nSign this transaction\nAccount 1\nSee addresses, balances, and activity'
    assert.equal(inspectApprovalSurface({trustedWorkerOrigin:true}).type,null)
  } finally {delete globalThis.document;delete globalThis.window}
})

test('CDP side-panel approval requires active Worker page before bare-host permission click',async()=>{
  const state={type:'account-connect', origin:true,chain:false,rpc:false,headingRecognized:true,
    button:{label:'Connect',x:120,y:200}}
  // The Chrome renderer itself only produces the above state after it receives
  // trustedWorkerOrigin. Assert the CDP expression does not pass that authority
  // when the browser is on an unexpected site or path.
  for (const [workerUrl,expected] of [
    ['http://127.0.0.1:8765/applications/worker','trustedWorkerOrigin:true'],
    ['http://127.0.0.1:8766/applications/worker','trustedWorkerOrigin:false'],
    ['http://malicious.example:8765/applications/worker','trustedWorkerOrigin:false'],
  ]) {
    const fixture=mockContext({state})
    fixture.workerPage.url=()=>workerUrl
    await advanceSidepanelApproval(fixture.context,fixture.workerPage,EXT)
    const sent=fixture.events.filter(x=>x.method==='Target.sendMessageToTarget')
      .map(x=>JSON.parse(x.params.message)).find(x=>x.method==='Runtime.evaluate')
    assert.ok(sent.params.expression.includes(expected))
  }
})

const SIGN_WALLET = '0x1F857b314a19c6969a903529bdFA852bA73A84C2'
const SIGN_ORIGIN = 'http://127.0.0.1:8765'
const SIGN_CHAIN = '0x28757b1'
function fakeMskDOM({messageOverrides={}, surfaceOverrides={}, buttonLabel='Confirm', disabled=false, hidden=false, extraMessage=''}={}) {
  const issued = new Date(Date.now() - 10_000).toISOString()
  const expiry = new Date(Date.parse(issued) + 600_000).toISOString()
  const message={purpose:'request_multi_session_key',request_id:'msk_req_80cecc9-2679-4b76-a2fe-76908959dbf5',
    user_slug:'a'.repeat(64),wallet_address:SIGN_WALLET,chain_id:SIGN_CHAIN,origin:SIGN_ORIGIN,
    issued_at:issued,expires_at:expiry,version:'main-computer-multisession-key-request-v1',...messageOverrides}
  const messageText=JSON.stringify(message)
  const text=surfaceOverrides.text ??
    `Signature request\nNetwork\nMain Computer Testnet\nRequest from\nHTTP 127.0.0.1:8765\nMessage\n${messageText}${extraMessage}`
  const rect={left:20,top:500,width:150,height:50}
  const heading={innerText:surfaceOverrides.heading ?? 'Signature request',getBoundingClientRect:()=>rect}
  const button={innerText:buttonLabel,disabled,getAttribute:()=>null,getBoundingClientRect:()=>rect,contains:()=>true}
  globalThis.document={body:{innerText:text}, querySelectorAll:sel=>sel==='h1,h2,h3,[role="heading"]'?[heading]:[button],
    elementFromPoint:()=>button}
  globalThis.window={getComputedStyle:()=>({visibility:hidden?'hidden':'visible',display:'block',opacity:'1'})}
  return message
}
test('recognizes only Worker-generated, valid ten-minute multisession key signature',()=>{
  try {
    fakeMskDOM()
    const approved=inspectWorkerMskSignatureSurface({expectedWallet:SIGN_WALLET,trustedWorkerOrigin:true})
    assert.equal(approved.verified,true)
    assert.deepEqual(approved.button,{label:'Confirm',x:95,y:525})
    assert.ok(!('message' in approved),'inspector must not return request payload')
    assert.equal(inspectWorkerMskSignatureSurface({expectedWallet:SIGN_WALLET,trustedWorkerOrigin:false}).verified,false)
  } finally {delete globalThis.document;delete globalThis.window}
})
test('MSK signature refuses any mismatch in address, chain, origin, purpose, version and request shape',()=>{
  const bad=[
    {messageOverrides:{wallet_address:'0x'+'a'.repeat(40)}},
    {messageOverrides:{chain_id:'0x28757b2'}},
    {messageOverrides:{origin:'http://evil.example'}},
    {messageOverrides:{purpose:'transfer_eth'}},
    {messageOverrides:{version:'future-v2'}},
    {messageOverrides:{request_id:'msk_req_bad'}},
    {messageOverrides:{user_slug:''}},
    {messageOverrides:{user_slug:'b'.repeat(31)}},
    {messageOverrides:{user_slug:'A'.repeat(64)}},
    {messageOverrides:{user_slug:'z'.repeat(64)}},
    {messageOverrides:{user_slug:1}},
    {messageOverrides:{value:'100'}},
    {surfaceOverrides:{heading:'Transaction request'}},
    {surfaceOverrides:{text:'Signature request\nNetwork Main Computer Testnet\nRequest from evil.example\nMessage {"purpose":"request_multi_session_key"}'}},
    {buttonLabel:'Sign'},
    {disabled:true},
    {hidden:true},
    {extraMessage:'\n{"purpose":"request_multi_session_key"}'},
  ]
  try {
    for (const scenario of bad) {
      fakeMskDOM(scenario)
      const result=inspectWorkerMskSignatureSurface({expectedWallet:SIGN_WALLET,trustedWorkerOrigin:true})
      assert.equal(result.verified,false,JSON.stringify(scenario).slice(0,100))
      assert.equal(result.button,null)
    }
  } finally {delete globalThis.document;delete globalThis.window}
})
test('MSK signature requires exactly nine signed message fields including secure user_slug',()=>{
  try {
    const valid = fakeMskDOM()
    assert.deepEqual(Object.keys(valid).sort(), [
      'purpose','request_id','user_slug','wallet_address','chain_id','origin','issued_at','expires_at','version'
    ].sort())
    assert.equal(inspectWorkerMskSignatureSurface({expectedWallet:SIGN_WALLET,trustedWorkerOrigin:true}).verified,true)
    const missing = {...valid}
    delete missing.user_slug
    fakeMskDOM({messageOverrides:missing, surfaceOverrides:{text:`Signature request\nNetwork Main Computer Testnet\nRequest from HTTP 127.0.0.1:8765\nMessage\n${JSON.stringify(missing)}`}})
    assert.equal(inspectWorkerMskSignatureSurface({expectedWallet:SIGN_WALLET,trustedWorkerOrigin:true}).reason,'message-keys')
    const extra = {...valid,transfer:'1'}
    fakeMskDOM({surfaceOverrides:{text:`Signature request\nNetwork Main Computer Testnet\nRequest from HTTP 127.0.0.1:8765\nMessage\n${JSON.stringify(extra)}`}})
    assert.equal(inspectWorkerMskSignatureSurface({expectedWallet:SIGN_WALLET,trustedWorkerOrigin:true}).reason,'message-keys')
  } finally {delete globalThis.document;delete globalThis.window}
})
test('MSK signature rejects expired, future, and non-ten-minute signing messages',()=>{
  const now=Date.now()
  const invalid=[
    {issued_at:new Date(now-15*60_000).toISOString(),expires_at:new Date(now-5*60_000).toISOString()},
    {issued_at:new Date(now+120_000).toISOString(),expires_at:new Date(now+720_000).toISOString()},
    {issued_at:new Date(now-10_000).toISOString(),expires_at:new Date(now+30_000).toISOString()},
  ]
  try {
    for (const dates of invalid) {
      fakeMskDOM({messageOverrides:dates})
      assert.equal(inspectWorkerMskSignatureSurface({expectedWallet:SIGN_WALLET,trustedWorkerOrigin:true}).verified,false)
    }
  } finally {delete globalThis.document;delete globalThis.window}
})
test('MSK CDP adapter approves one verified signature with real mouse events',async()=>{
  const fixture=mockContext({state:{kind:'msk-signature',verified:true,reason:'validated',
    button:{label:'Confirm',x:80,y:410}}})
  fixture.workerPage.url=()=>SIGN_ORIGIN+'/applications/worker'
  const progress=[]
  const result=await approveWorkerMskSignature(fixture.context,fixture.workerPage,EXT,SIGN_WALLET,
    {onProgress:s=>progress.push(s)})
  assert.deepEqual(result,{type:'msk-signature',action:'Confirm'})
  assert.deepEqual(progress,['metamask.msk-signature'])
  assert.deepEqual(clicks(fixture.events).map(e=>e.params.type),['mouseMoved','mousePressed','mouseReleased'])
  assert.equal(fixture.detached,true)
  assert.equal(fixture.rootDetached,true)
  const evaluate=fixture.events.filter(e=>e.method==='Target.sendMessageToTarget')
    .map(e=>JSON.parse(e.params.message)).find(e=>e.method==='Runtime.evaluate')
  assert.ok(evaluate.params.expression.includes(SIGN_WALLET))
  assert.ok(!JSON.stringify(fixture.events).includes('mnemonic'))
})
test('MSK CDP adapter refuses different Worker origin and wallet before target inspection',async()=>{
  for (const [url,wallet] of [
    ['http://127.0.0.1:8766/applications/worker',SIGN_WALLET],
    ['http://127.0.0.1:8765/applications/other',SIGN_WALLET],
    ['http://evil.example:8765/applications/worker',SIGN_WALLET],
    [SIGN_ORIGIN+'/applications/worker','0xgarbage']
  ]) {
    const fixture=mockContext()
    fixture.workerPage.url=()=>url
    await assert.rejects(approveWorkerMskSignature(fixture.context,fixture.workerPage,EXT,wallet),/untrusted/)
    assert.equal(fixture.attached,0)
  }
})
test('MSK CDP adapter never clicks rejected requests or unrelated confirmation labels',async()=>{
  const refused=[
    {kind:'msk-signature',verified:false,reason:'message-identity-mismatch',button:null},
    {kind:'msk-signature',verified:true,reason:'validated',button:{label:'Sign',x:80,y:410}},
    {kind:'msk-signature',verified:true,reason:'validated',button:{label:'Confirm',x:-1,y:410}},
    {type:'network-add',button:{label:'Confirm',x:80,y:410},origin:true,rpc:true,chain:true,headingRecognized:true}
  ]
  for (const state of refused) {
    const fixture=mockContext({state})
    fixture.workerPage.url=()=>SIGN_ORIGIN+'/applications/worker'
    let tick=0
    await assert.rejects(approveWorkerMskSignature(fixture.context,fixture.workerPage,EXT,SIGN_WALLET,
      {timeoutMs:1,now:()=>tick++,sleep:async()=>{}}),/approval refused or unavailable/)
    assert.deepEqual(clicks(fixture.events),[])
  }
})

test('native acceptance invokes scoped CDP MSK approval after requesting the real key', async()=>{
  const {readFileSync}=await import('node:fs')
  const {fileURLToPath}=await import('node:url')
  const {dirname,join}=await import('node:path')
  const runner=readFileSync(join(dirname(fileURLToPath(import.meta.url)),'native_acceptance.mjs'),'utf8')
  assert.ok(runner.includes('await approveWorkerMskSignature(context, page, extensionId, walletSecret.address'))
  assert.ok(runner.indexOf("await page.locator('#worker-request-multisession-key').click()") <
    runner.indexOf('await approveWorkerMskSignature('))
  assert.equal(runner.includes('metamask.confirmSignature()'),false)
  assert.ok(runner.includes('await waitForWorkerMsk({'), 'Hub issuance still required')
  assert.ok(runner.includes("logs.filter(v => v.startsWith('msk.'))"), 'real signing events still required')
})
