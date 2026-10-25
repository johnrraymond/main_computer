import {test} from 'node:test'
import assert from 'node:assert/strict'
import {classifyWorkerMsk, isMskEndpointRequest, waitForWorkerMsk, workerMskEvent, workerMskFailureCategory, safeMskApiRejection, readMskApiRejection, formatMskFailureWithApiReason, assertServedWorkerMskContract} from './worker_msk_outcome.mjs'

const base = {events:['request.start','signature.created','hub.response']}

test('extracts only allowed worker MSK event names', () => {
  assert.equal(workerMskEvent('[worker-msk] signature.created { signature:"0xSECRET" }'), 'signature.created')
  assert.equal(workerMskEvent('[worker-msk] hub.response'), 'hub.response')
  assert.equal(workerMskEvent('[worker-msk] request.failed'), 'request.failed')
  assert.equal(workerMskEvent('[worker-msk] signed_request.payload'), null)
})

test('filters to the exact local MSK POST endpoint', () => {
  const request = (url,method='POST') => ({url:()=>url,method:()=>method})
  assert.equal(isMskEndpointRequest(request('http://127.0.0.1:8765/api/applications/worker/multisession-key/request')),true)
  assert.equal(isMskEndpointRequest(request('http://127.0.0.1:8765/api/applications/worker/multisession-key/request','GET')),false)
  assert.equal(isMskEndpointRequest(request('http://127.0.0.1:8765/api/applications/worker/multisession-key/revoke')),false)
  assert.equal(isMskEndpointRequest(request('not-a-url')),false)
})

test('does not certify active key without both signature and hub events', () => {
  assert.equal(classifyWorkerMsk({state:'Active',events:['request.start'], apiStatus:200}).code,
    'active-key-without-signature-and-hub-proof')
  assert.equal(classifyWorkerMsk({state:'Active',events:base.events,apiStatus:200}).kind, 'success')
})

test('Worker request.failed preempts the 60-second visual timeout', () => {
  const result = classifyWorkerMsk({state:'No active key', events:[...base.events,'request.failed'],
    uiStatus:'Multi-session key request failed: Hub unavailable',elapsedMs:1000})
  assert.deepEqual([result.kind,result.code,result.category],['error','worker-request-failed','hub-unreachable'])
})

test('HTTP rejection identified without reading the response body', () => {
  const result = classifyWorkerMsk({state:'No active key',events:['request.start','signature.created'],apiStatus:400})
  assert.deepEqual([result.code,result.apiStatus],['local-api-http-error',400])
  assert.equal(classifyWorkerMsk({apiRequestFailed:true}).code,'local-api-transport-failed')
})

test('distinguishes missed signature, missing Hub response, and missing active state', () => {
  assert.equal(classifyWorkerMsk({events:['request.start'], elapsedMs:60000}).code,'wallet-signature-not-completed')
  assert.equal(classifyWorkerMsk({events:['request.start','signature.created'],elapsedMs:60000}).code,'hub-issuance-timeout')
  assert.equal(classifyWorkerMsk({events:base.events, elapsedMs:60000}).code,'hub-response-did-not-activate-key')
  assert.equal(classifyWorkerMsk({elapsedMs:60000}).code,'key-request-not-started')
})

test('backend classifications do not expose request or wallet contents', () => {
  assert.equal(workerMskFailureCategory('Multi-session key request failed: HTTP 502'), 'local-api-5xx')
  assert.equal(workerMskFailureCategory('invalid signature recovered address does not match'), 'signature-rejected')
  assert.equal(workerMskFailureCategory('other error with 0xdeadbeef'), 'unclassified')
})

test('wait returns on verified active key and emits phase transitions only', async () => {
  let t=0
  const phases=[]
  const result=await waitForWorkerMsk({
    now:()=>t, sleep:async()=>{t+=350},
    readState:async()=>t>=700?'Active':'No active key',
    readStatus:async()=>'',
    readEvents:()=>t>=700?base.events:['request.start'],
    readApiStatus:()=>t>=700?200:null,
    onProgress:p=>phases.push(p),
  })
  assert.equal(result.kind,'success')
  assert.deepEqual(phases,['awaiting-wallet-signature','active-key-verified'])
})

test('wait rejects promptly on worker-side failure without leaking response text', async () => {
  let t=0
  await assert.rejects(waitForWorkerMsk({
    now:()=>t, sleep:async()=>{t+=350},
    readState:async()=> 'No active key',
    readStatus:async()=> 'Multi-session key request failed: HTTP 400 wallet_secret=supersecret',
    readEvents:()=>t>0?['request.start','signature.created','request.failed']:['request.start'],
    readApiStatus:()=>400,
  }), e=>e.message.includes('localApiHttp=400') && !e.message.includes('supersecret'))
  assert.ok(t < 60000)
})

test('wait expires with stage-specific error for pending Hub issuance', async () => {
  let t=0
  await assert.rejects(waitForWorkerMsk({
    now:()=>t,sleep:async ms=>{t+=ms},timeoutMs:1400,
    readState:async()=> 'No active key',readStatus:async()=>'',
    readEvents:()=>['request.start','signature.created'],
  }),/hub-issuance-timeout/)
  assert.ok(t>=1400)
})

test('reports local validation and upstream Hub errors without signed request contents', async () => {
  const local = {ok:false,error:'signed_request.message.user_slug must contain at least 32 characters.'}
  assert.equal(safeMskApiRejection(local), local.error)
  const hub = {ok:false,error:'Hub returned HTTP 400: {"error":"signed_request.message.chain_id mismatch: expected 42424241"}'}
  assert.equal(safeMskApiRejection(hub), 'hub-http-400: signed_request.message.chain_id mismatch: expected 42424241')
  const response = {status:()=>400, json:async()=>hub}
  const result = await readMskApiRejection(response)
  assert.deepEqual(result,{httpStatus:400,reason:'hub-http-400: signed_request.message.chain_id mismatch: expected 42424241'})
  const failure = formatMskFailureWithApiReason(new Error('Worker MSK issuance failed: local-api-http-error'),result)
  assert.match(failure,/localApiRejection=hub-http-400/)
})

test('does not inspect successful responses that can contain new key material', async () => {
  let bodyRead = false
  const res = await readMskApiRejection({status:()=>200,json:async()=>{bodyRead=true;return {key:'very-secret'}}})
  assert.equal(res,null)
  assert.equal(bodyRead,false)
})

test('redacts sensitive values and refuses arbitrary request material in error messages', async () => {
  assert.equal(safeMskApiRejection({error:'invalid wallet 0x'+'a'.repeat(40)}),'invalid wallet [hex-redacted]')
  assert.equal(safeMskApiRejection({error:'signature: 0x'+'f'.repeat(130)}),'sensitive-error-detail-redacted')
  assert.equal(safeMskApiRejection({error:'signed_request: {"private_key":"oops"}'}),'sensitive-error-detail-redacted')
  assert.equal(safeMskApiRejection({error:'Authorization: Bearer abc'}),'sensitive-error-detail-redacted')
  assert.equal(safeMskApiRejection({error:'hub URL https://example.com/secret?token=abc'}),'hub URL [url-redacted]')
  assert.equal(safeMskApiRejection({error:'\n'+'x'.repeat(900)}).length,300)
  assert.equal(safeMskApiRejection({error:{something:'no'}}),'no-structured-error')
  assert.deepEqual(await readMskApiRejection({status:()=>400,json:async()=>{throw new Error('malformed')}}),
    {httpStatus:400,reason:'json-error-unavailable'})
})

// The earlier user_slug source patch was correct, but the local viewport server
// retained a pre-patch in-memory HTML snapshot. Reject that SERVED version.
test('served signer schema is validated before MetaMask signing', async () => {
  const valid = {builderAvailable:true,hasUserSlug:true,correctChain:true,correctPurpose:true}
  assert.equal(await assertServedWorkerMskContract({evaluate:async()=>valid}),true)
})

test('stale served signer missing user_slug refuses before a wallet signature', async () => {
  const stale = {builderAvailable:true,hasUserSlug:false,correctChain:true,correctPurpose:true}
  await assert.rejects(assertServedWorkerMskContract({evaluate:async()=>stale}), error =>
    /restart the local Main Computer viewport server/.test(error.message) &&
    /No MSK signature was requested/.test(error.message) &&
    !error.message.includes('0x1111111111111111111111111111111111111111'))
})

test('missing, throwing, or wrong-chain served builder fails closed', async () => {
  for (const state of [
    {builderAvailable:false},
    {builderAvailable:true,builderFailed:true},
    {builderAvailable:true,hasUserSlug:true,correctChain:false,correctPurpose:true},
    {builderAvailable:true,hasUserSlug:true,correctChain:true,correctPurpose:false},
  ]) {
    await assert.rejects(assertServedWorkerMskContract({evaluate:async()=>state}), /Served Worker wallet signer/)
  }
})
