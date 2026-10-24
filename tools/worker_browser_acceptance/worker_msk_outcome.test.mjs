import {test} from 'node:test'
import assert from 'node:assert/strict'
import {classifyWorkerMsk, isMskEndpointRequest, waitForWorkerMsk, workerMskEvent, workerMskFailureCategory} from './worker_msk_outcome.mjs'

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
