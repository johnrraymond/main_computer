import test from 'node:test'
import assert from 'node:assert/strict'
import {waitForFreshOfferRequest} from './worker_offer_request_wait.mjs'

test('prior failed Hub state cannot terminate a still-pending fresh MCEL action',async()=>{
  let time=0,request=false
  const priorHubStatus='failed' // persisted from an earlier registration
  await waitForFreshOfferRequest({requestSeen:()=>request,transportFailed:()=>false,
    now:()=>time,sleep:async()=>{time+=300;if(time>=900)request=true},timeoutMs:2000})
  assert.equal(priorHubStatus,'failed')
  assert.equal(request,true)
  assert.equal(time,900)
})

test('a click without any offer-sync request refuses certification independently of stale Hub state',async()=>{
  let time=0
  await assert.rejects(waitForFreshOfferRequest({requestSeen:()=>false,
    transportFailed:()=>false,now:()=>time,sleep:async()=>{time+=300},
    timeoutMs:1200}),{message:'OFFER_SYNC_REQUEST_NOT_OBSERVED'})
})

test('transport failure takes precedence over a timeout',async()=>{
  let time=0
  await assert.rejects(waitForFreshOfferRequest({requestSeen:()=>false,
    transportFailed:()=>time>=600,now:()=>time,sleep:async()=>{time+=300},
    timeoutMs:1500}),{message:'OFFER_SYNC_TRANSPORT_FAILED'})
})

test('late request at the deadline is accepted',async()=>{
  let time=0
  assert.equal(await waitForFreshOfferRequest({requestSeen:()=>time>=600,
    transportFailed:()=>false,now:()=>time,sleep:async()=>{time+=300},
    timeoutMs:600}),true)
})

import {createOfferSyncTracker,waitForFreshOfferResponse} from './worker_offer_request_wait.mjs'

function request(path='/api/applications/worker/offer-sync',method='POST') {
  return {url:()=>`http://127.0.0.1:8765${path}`,method:()=>method}
}
function response(req,status=200) {return {request:()=>req,status:()=>status}}

test('only a request started after arming belongs to this offer attempt',()=>{
  const tracker=createOfferSyncTracker()
  const oldReq=request(), freshReq=request()
  assert.equal(tracker.observeRequest(oldReq),false)
  tracker.arm()
  assert.equal(tracker.observeResponse(response(oldReq)),false)
  assert.equal(tracker.hasResponse(),false)
  assert.equal(tracker.observeRequest(freshReq),true)
  assert.equal(tracker.hasRequest(),true)
  assert.equal(tracker.observeResponse(response(oldReq)),false)
  assert.equal(tracker.observeResponse(response(freshReq)),true)
  assert.equal(tracker.hasResponse(),true)
  assert.equal(tracker.lastResponse().status(),200)
})

test('unrelated requests and responses never certify an offer',()=>{
  const tracker=createOfferSyncTracker()
  tracker.arm()
  const read=request('/api/applications/worker/network-session','GET')
  const different=request('/api/applications/worker/settings','POST')
  assert.equal(tracker.observeRequest(read),false)
  assert.equal(tracker.observeRequest(different),false)
  assert.equal(tracker.observeResponse(response(different)),false)
  assert.equal(tracker.observeFailure(different),false)
  assert.equal(tracker.hasRequest(),false)
})

test('stale failed Hub status cannot defeat in-flight fresh offer-sync',async()=>{
  const priorHubStatus='failed'
  let time=0, responded=false
  const observed=await waitForFreshOfferResponse({
    responseSeen:()=>responded,transportFailed:()=>false,
    now:()=>time,sleep:async()=>{time+=300;if(time>=1500)responded=true},
    timeoutMs:3000,
  })
  assert.equal(observed,true)
  assert.equal(priorHubStatus,'failed')
  assert.equal(time,1500)
})

test('POST without a response receives a separate refusal, never stale Hub failure',async()=>{
  let time=0
  await assert.rejects(waitForFreshOfferResponse({responseSeen:()=>false,
    transportFailed:()=>false,now:()=>time,sleep:async()=>{time+=300},
    timeoutMs:1200}),{message:'OFFER_SYNC_RESPONSE_NOT_OBSERVED'})
})

test('transport failure after POST wins over response wait',async()=>{
  let time=0
  await assert.rejects(waitForFreshOfferResponse({responseSeen:()=>false,
    transportFailed:()=>time>=600,now:()=>time,sleep:async()=>{time+=300},
    timeoutMs:1800}),{message:'OFFER_SYNC_TRANSPORT_FAILED'})
})

test('late response at deadline may complete the wait',async()=>{
  let time=0
  assert.equal(await waitForFreshOfferResponse({responseSeen:()=>time>=600,
    transportFailed:()=>false,now:()=>time,sleep:async()=>{time+=300},
    timeoutMs:600}),true)
})
