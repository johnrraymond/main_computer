import test from 'node:test'
import assert from 'node:assert/strict'
import {probeOneRpcMethod, probeRpcClients, rpcProbeSummary, probeExtensionServiceWorkerCDP, observePinnedRpcNetwork} from './rpc_client_probe.mjs'

const VALID = {
  eth_chainId: {kind:'verified-chain',status:200,chain:'0x28757b1'},
  eth_blockNumber: {kind:'valid-block',status:200},
}

function client(result, url = 'chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/home.html') {
  return {url: () => url, isClosed: () => false,
    evaluate: async (fn, args) => {
      assert.equal(fn, probeOneRpcMethod)
      assert.equal(args.url, 'https://testnet-rpc.greatlibrary.io/')
      assert.ok(['eth_chainId','eth_blockNumber'].includes(args.method))
      return result[args.method]
    }}
}

test('compares Worker browser page, extension renderer and extension service worker', async () => {
  const web = client(VALID, 'http://127.0.0.1:8765/applications/worker')
  const ext = client({eth_chainId:{kind:'http-rejected',status:403}, eth_blockNumber:{kind:'http-rejected',status:403}})
  const sw = client({eth_chainId:{kind:'fetch-rejected'}, eth_blockNumber:{kind:'fetch-rejected'}},
    'chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/background.js')
  const ctx = {pages:()=>[web,ext], serviceWorkers:()=>[sw]}
  const result = await probeRpcClients(ctx,web,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
  assert.equal(result.workerPage.methods.eth_chainId.kind,'verified-chain')
  assert.equal(result.extensionPage.methods.eth_chainId.status,403)
  assert.equal(result.extensionServiceWorker.methods.eth_blockNumber.kind,'fetch-rejected')
  assert.deepEqual(rpcProbeSummary(result),{
    workerPage:'PASS', extensionPage:'chain:http-rejected,block:http-rejected',
    extensionServiceWorker:'chain:fetch-rejected,block:fetch-rejected',
  })
})

test('refuses malformed extension identity', async () => {
  await assert.rejects(probeRpcClients({pages:()=>[],serviceWorkers:()=>[]}, null, 'evil.test'),
    /Invalid MetaMask/)
})

test('does not certify missing or closed extension inspection targets', async () => {
  const web = client(VALID, 'http://127.0.0.1:8765/applications/worker')
  const closed = {...client(VALID), isClosed:()=>true}
  const result = await probeRpcClients({pages:()=>[closed], serviceWorkers:()=>[]}, web,
    'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
  assert.equal(result.extensionPage.available,false)
  assert.equal(result.extensionServiceWorker.available,false)
  assert.deepEqual(rpcProbeSummary(result),{
    workerPage:'PASS',extensionPage:'no-extension-renderer',extensionServiceWorker:'no-playwright-extension-worker',
  })
})

test('fetch does not follow redirects or use credentials and sends read-only JSON-RPC', async () => {
  const old = globalThis.fetch
  globalThis.fetch = async (url, options) => {
    assert.equal(url,'https://testnet-rpc.greatlibrary.io/')
    assert.equal(options.method,'POST')
    assert.equal(options.credentials,'omit')
    assert.equal(options.redirect,'error')
    assert.equal(options.cache,'no-store')
    assert.deepEqual(JSON.parse(options.body),{jsonrpc:'2.0',id:1,method:'eth_chainId',params:[]})
    return {status:200,ok:true,json:async()=>({jsonrpc:'2.0',id:1,result:'0x28757b1'})}
  }
  try { assert.equal((await probeOneRpcMethod({url:'https://testnet-rpc.greatlibrary.io/',method:'eth_chainId',timeoutMs:1000})).kind,'verified-chain') }
  finally {globalThis.fetch=old}
})

test('HTTP denial stays distinct from CORS/transport rejection', async () => {
  const old=globalThis.fetch
  try {
    globalThis.fetch=async()=>({status:403,ok:false})
    assert.deepEqual(await probeOneRpcMethod({url:'https://testnet-rpc.greatlibrary.io/',method:'eth_blockNumber',timeoutMs:1000}),
      {kind:'http-rejected',status:403})
    globalThis.fetch=async()=>{throw new TypeError('private network error')}
    assert.deepEqual(await probeOneRpcMethod({url:'https://testnet-rpc.greatlibrary.io/',method:'eth_blockNumber',timeoutMs:1000}),
      {kind:'fetch-rejected',errorClass:'TypeError',errorStage:'await-fetch',
        runtimeFeatures:{fetch:true,abortSignalTimeout:true}})
  } finally {globalThis.fetch=old}
})

test('rejects bad proof even if endpoint returns HTTP 200', async () => {
  const old=globalThis.fetch
  try {
    globalThis.fetch=async()=>({status:200,ok:true,json:async()=>({jsonrpc:'2.0',id:1,result:'0x28757b2'})})
    const bad=await probeOneRpcMethod({url:'https://testnet-rpc.greatlibrary.io/',method:'eth_chainId',timeoutMs:1000})
    assert.equal(bad.kind,'wrong-chain')
    globalThis.fetch=async()=>({status:200,ok:true,json:async()=>({jsonrpc:'2.0',id:1,result:'not-a-block'})})
    assert.equal((await probeOneRpcMethod({url:'https://testnet-rpc.greatlibrary.io/',method:'eth_blockNumber',timeoutMs:1000})).kind,
      'invalid-block')
  } finally {globalThis.fetch=old}
})

test('refuses to send a mutation or nonpinned endpoint', async () => {
  assert.deepEqual(await probeOneRpcMethod({url:'https://invalid.example/',method:'eth_blockNumber',timeoutMs:1000}),
    {kind:'invalid-probe-parameters'})
  assert.deepEqual(await probeOneRpcMethod({url:'https://testnet-rpc.greatlibrary.io/',method:'eth_sendRawTransaction',timeoutMs:1000}),
    {kind:'invalid-probe-parameters'})
})


test('standalone rpc-probe opens verified extension renderer once and closes it after inspection', async () => {
  const ID='a'.repeat(32)
  const url=`chrome-extension://${ID}/home.html`
  const web=client(VALID,'http://127.0.0.1:8765/applications/worker')
  let opened=0, closed=0, navigated=[]
  const temp={...client({eth_chainId:{kind:'http-rejected',status:403},
    eth_blockNumber:{kind:'http-rejected',status:403}},url),
    goto:async (u,config)=>{navigated.push(u);assert.equal(config.waitUntil,'domcontentloaded')},
    close:async()=>{closed++}}
  const ctx={pages:()=>[web],serviceWorkers:()=>[],newPage:async()=>{opened++;return temp}}
  const result=await probeRpcClients(ctx,web,ID,{openExtensionPage:true,probeCDPServiceWorker:true})
  assert.equal(opened,1)
  assert.equal(closed,1)
  assert.deepEqual(navigated,[url])
  assert.equal(result.extensionPage.available,true)
  assert.equal(result.extensionPage.methods.eth_chainId.status,403)
  assert.equal(result.extensionServiceWorker.reason,'cdp-not-supported')
})

test('normal acceptance probe does not open a new extension tab', async () => {
  const web=client(VALID,'http://127.0.0.1:8765/applications/worker')
  const ctx={pages:()=>[web],serviceWorkers:()=>[],newPage:async()=>{throw Error('unexpected new tab')}}
  const result=await probeRpcClients(ctx,web,'a'.repeat(32))
  assert.equal(result.extensionPage.reason,'no-extension-renderer')
})

test('CDP finds only the exact extension service worker, never attaches to another extension', async () => {
  const other='b'.repeat(32)
  let attached=false, detached=false
  const root={send:async method=>{
    if (method==='Target.getTargets')return {targetInfos:[
      {type:'service_worker',url:`chrome-extension://${other}/background.js`,targetId:'wrong'},
      {type:'page',url:`chrome-extension://${'a'.repeat(32)}/home.html`,targetId:'wrong-page'},
    ]}
    if (method==='Target.attachToTarget')attached=true
    throw Error('unknown method')
  },detach:async()=>{detached=true}}
  const result=await probeExtensionServiceWorkerCDP({newCDPSession:async()=>root},{},'a'.repeat(32))
  assert.deepEqual(result,{available:false,reason:'no-extension-service-worker-target'})
  assert.equal(attached,false)
  assert.equal(detached,true)
})

test('CDP probes extension service worker through its target with no wallet mutation', async () => {
  const {EventEmitter}=await import('node:events')
  const ID='a'.repeat(32)
  const requests=[]
  let outerDetached=false
  const root=new EventEmitter()
  root.send=async (method,params={})=>{
    requests.push({method,params})
    if (method==='Target.getTargets')return {targetInfos:[
      {type:'service_worker',url:`chrome-extension://${ID}/background.js`,targetId:'my-extension'},
    ]}
    if (method==='Target.attachToTarget')return {sessionId:'nested'}
    if (method==='Target.sendMessageToTarget') {
      const message=JSON.parse(params.message)
      let result={}
      if(message.method==='Runtime.evaluate') {
        assert.match(message.params.expression,/eth_chainId|eth_blockNumber/)
        assert.doesNotMatch(message.params.expression,/eth_sendTransaction|personal_sign/)
        const isChain=message.params.expression.includes('\"method\":\"eth_chainId\"')
        result={result:{value:isChain ? VALID.eth_chainId : VALID.eth_blockNumber}}
      }
      queueMicrotask(()=>root.emit('Target.receivedMessageFromTarget',{
        sessionId:'nested',message:JSON.stringify({id:message.id,result})
      }))
      return {}
    }
    if (method==='Target.detachFromTarget')return {}
    throw Error('unexpected '+method)
  }
  root.detach=async()=>{outerDetached=true}
  const output=await probeExtensionServiceWorkerCDP({newCDPSession:async()=>root},{},ID)
  assert.equal(output.available,true)
  assert.equal(output.via,'cdp-service-worker')
  assert.equal(output.methods.eth_chainId.kind,'verified-chain')
  assert.equal(output.methods.eth_blockNumber.kind,'valid-block')
  assert.equal(requests.filter(x=>x.method==='Target.attachToTarget').length,1)
  assert.equal(requests.filter(x=>x.method==='Target.detachFromTarget').length,1)
  assert.equal(outerDetached,true)
})

test('no read-only RPC conclusion is drawn from an inaccessible CDP worker', async () => {
  let detached=false
  const root={send:async(method)=>{
    if(method==='Target.getTargets')return {targetInfos:[{type:'service_worker',
      url:'chrome-extension://'+'a'.repeat(32)+'/background.js',targetId:'my-extension'}]}
    if(method==='Target.attachToTarget')throw Error('target inaccessible')
    throw Error('unexpected command')
  },detach:async()=>{detached=true}}
  const output=await probeExtensionServiceWorkerCDP({newCDPSession:async()=>root},{},'a'.repeat(32))
  assert.deepEqual(output,{available:false,reason:'cdp-service-worker-attach-or-evaluation-failed'})
  assert.equal(detached,true)
})


test('CDP observer distinguishes CORS preflight, HTTP status, and unrelated extension traffic', async () => {
  const {EventEmitter} = await import('node:events')
  const root = new EventEmitter()
  const observer = observePinnedRpcNetwork(root, 'trusted-session')
  const emit = (method, params, sessionId='trusted-session') => root.emit('Target.receivedMessageFromTarget', {
    sessionId, message: JSON.stringify({method, params}),
  })
  emit('Network.requestWillBeSent', {requestId:'private',request:{url:'https://wallet.example/secret'}})
  emit('Network.responseReceivedExtraInfo', {requestId:'private',statusCode:403})
  emit('Network.requestWillBeSent', {requestId:'different',request:{url:'https://testnet-rpc.greatlibrary.io/'}} ,'wrong-session')
  emit('Network.requestWillBeSent', {requestId:'rpc',request:{url:'https://testnet-rpc.greatlibrary.io/'}})
  emit('Network.responseReceivedExtraInfo', {requestId:'rpc',statusCode:403})
  emit('Network.loadingFailed', {requestId:'rpc',corsErrorStatus:{
    corsError:'PreflightMissingAllowOriginHeader',failedParameter:'PRIVATE_NOT_LOGGED'}})
  assert.deepEqual(observer.observation(), {
    kind:'cors',corsError:'PreflightMissingAllowOriginHeader',httpStatuses:[403],
  })
  observer.close()
  assert.equal(root.listenerCount('Target.receivedMessageFromTarget'),0)
})

test('CDP observer reports network errors without inventing CORS or HTTP statuses', async () => {
  const {EventEmitter} = await import('node:events')
  const root=new EventEmitter()
  const observer=observePinnedRpcNetwork(root,'nested')
  const emit=(method,params)=>root.emit('Target.receivedMessageFromTarget',{
    sessionId:'nested',message:JSON.stringify({method,params})})
  assert.deepEqual(observer.observation(),{kind:'no-network-events'})
  emit('Network.requestWillBeSent', {requestId:'req',request:{url:'https://testnet-rpc.greatlibrary.io/'}})
  emit('Network.loadingFailed', {requestId:'req',errorText:'net::ERR_CONNECTION_RESET'})
  assert.deepEqual(observer.observation(),{kind:'network-error',netError:'net::ERR_CONNECTION_RESET'})
  observer.close()
})

test('CDP probe retains sanitized network error details from the real extension worker session', async () => {
  const {EventEmitter}=await import('node:events')
  const ID='a'.repeat(32)
  const root=new EventEmitter()
  root.send=async (method,params={})=>{
    if(method==='Target.getTargets')return {targetInfos:[{type:'service_worker',
      url:`chrome-extension://${ID}/background.js`,targetId:'exact'}]}
    if(method==='Target.attachToTarget')return {sessionId:'nested'}
    if(method==='Target.sendMessageToTarget') {
      const command=JSON.parse(params.message)
      let result={}
      if (command.method==='Runtime.evaluate') {
        result={result:{value:{kind:'fetch-rejected',errorClass:'TypeError'}}}
        queueMicrotask(()=>{
          const send=(eventMethod,eventParams)=>root.emit('Target.receivedMessageFromTarget',{
            sessionId:'nested',message:JSON.stringify({method:eventMethod,params:eventParams})})
          send('Network.requestWillBeSent',{requestId:'rpc',request:{url:'https://testnet-rpc.greatlibrary.io/'}})
          send('Network.loadingFailed',{requestId:'rpc',corsErrorStatus:{corsError:'MissingAllowOriginHeader'}})
        })
      }
      queueMicrotask(()=>root.emit('Target.receivedMessageFromTarget',{
        sessionId:'nested',message:JSON.stringify({id:command.id,result})}))
      return {}
    }
    if (method==='Target.detachFromTarget')return {}
    throw Error('Unexpected command: '+method)
  }
  root.detach=async()=>{}
  const result=await probeExtensionServiceWorkerCDP({newCDPSession:async()=>root},{},ID)
  assert.equal(result.available,true)
  assert.equal(result.methods.eth_chainId.kind,'fetch-rejected')
  assert.deepEqual(result.methods.eth_chainId.networkEvidence,{
    kind:'cors',corsError:'MissingAllowOriginHeader',
  })
  assert.equal(result.methods.eth_blockNumber.networkEvidence.kind,'cors')
  assert.equal(root.listenerCount('Target.receivedMessageFromTarget'),0)
})

test('extension renderer evaluation errors expose only classified causes, never raw errors', async () => {
  const ID='a'.repeat(32)
  const web=client(VALID,'http://127.0.0.1:8765/applications/worker')
  const renderer={url:()=>`chrome-extension://${ID}/home.html`,isClosed:()=>false,
    evaluate:async()=>{throw new Error('Execution context was destroyed; secret-not-for-logs')}}
  const result=await probeRpcClients({pages:()=>[web,renderer],serviceWorkers:()=>[]},web,ID)
  assert.equal(result.extensionPage.reason,'renderer-navigated-during-probe')
  assert.ok(!JSON.stringify(result).includes('secret-not-for-logs'))
})


test('probe distinguishes an unsupported runtime feature from an actual fetch attempt', async () => {
  const oldSignal = globalThis.AbortSignal
  const oldFetch = globalThis.fetch
  let fetchCalled = false
  try {
    globalThis.AbortSignal = {timeout: undefined}
    globalThis.fetch = async () => {fetchCalled = true; throw Error('should not call fetch')}
    const output = await probeOneRpcMethod({url:'https://testnet-rpc.greatlibrary.io/',
      method:'eth_chainId',timeoutMs:500})
    assert.equal(fetchCalled,false)
    assert.equal(output.errorStage,'build-request')
    assert.equal(output.errorClass,'TypeError')
    assert.deepEqual(output.runtimeFeatures,{fetch:true,abortSignalTimeout:false})
  } finally { globalThis.AbortSignal=oldSignal; globalThis.fetch=oldFetch }
})

test('probe classifies synchronous and asynchronous fetch failure stages without exposing exceptions', async () => {
  const old = globalThis.fetch
  try {
    globalThis.fetch = () => {throw Error('SECRET_RESPONSE_HEADER_TOKEN')}
    const syncResult = await probeOneRpcMethod({url:'https://testnet-rpc.greatlibrary.io/',
      method:'eth_chainId',timeoutMs:200})
    assert.equal(syncResult.errorStage,'invoke-fetch')
    assert.equal(syncResult.errorClass,'Error')
    assert.ok(!JSON.stringify(syncResult).includes('SECRET_RESPONSE_HEADER_TOKEN'))
    globalThis.fetch = async () => {throw new TypeError('SECRET_URL_QUERY')}
    const asyncResult = await probeOneRpcMethod({url:'https://testnet-rpc.greatlibrary.io/',
      method:'eth_blockNumber',timeoutMs:200})
    assert.equal(asyncResult.errorStage,'await-fetch')
    assert.equal(asyncResult.errorClass,'TypeError')
    assert.ok(!JSON.stringify(asyncResult).includes('SECRET_URL_QUERY'))
  } finally {globalThis.fetch=old}
})
