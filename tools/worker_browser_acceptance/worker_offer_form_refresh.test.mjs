import test from 'node:test'
import assert from 'node:assert/strict'
import {saveSellerOfferAfterRefresh} from './worker_offer_form_refresh.mjs'
const wanted={expectedRate:'0.00001',expectedTokens:512}
const expected={rate:'0.00001',tokens:512}
function callbacks(overrides={}){
  let current={rate:'0.001',tokens:512},writes=0,submissions=0
  return {
    ...wanted,
    writeFields:async next=>{writes++;current={...next}},
    readFields:async()=>({...current}),
    submit:async()=>{submissions++},
    mutationSubmitted:()=>false,
    delay:async()=>{},
    _read:()=>({...current}),
    _count:()=>({writes,submissions}),
    _set:value=>{current={...value}},
    ...overrides,
  }
}
test('late MCEL hydration resets first edit; bounded recovery verifies correct price through native save',async()=>{
  const f=callbacks()
  let phases=0
  f.delay=async()=>{if(++phases===1)f._set({rate:'0.001',tokens:512})}
  const result=await saveSellerOfferAfterRefresh(f)
  assert.deepEqual(result,{attempts:2,resets:1,staleConfirmations:0})
  assert.deepEqual(f._count(),{writes:2,submissions:1})
  assert.deepEqual(f._read(),expected)
})
test('valid edit overtaken before native confirmation: dismissed dialog is retried only before HTTP mutation',async()=>{
  let saves=0,submitted=false
  const f=callbacks({submit:async()=>{saves++;if(saves===1)throw Error('Unexpected MCEL confirmation refused');submitted=true},
    mutationSubmitted:()=>submitted})
  const result=await saveSellerOfferAfterRefresh(f)
  assert.deepEqual(result,{attempts:2,resets:0,staleConfirmations:1})
  assert.equal(saves,2)
})
test('continuous refresh never submits an incorrect offer',async()=>{
  const f=callbacks()
  const originalWrite=f.writeFields
  f.writeFields=async next=>{await originalWrite(next);f._set({rate:'0.001',tokens:512})}
  await assert.rejects(saveSellerOfferAfterRefresh({...f,attempts:3}),/OFFER_PRICE_FORM_REFRESH_DID_NOT_SETTLE/)
  assert.deepEqual(f._count(),{writes:3,submissions:0})
})
test('an unknown confirmation refusal is not treated as harmless input reset',async()=>{
  const f=callbacks({submit:async()=>{throw Error('unknown-confirmation')}})
  await assert.rejects(saveSellerOfferAfterRefresh(f),/unknown-confirmation/)
  assert.equal(f._count().writes,1)
})
test('once a request was sent, even a stale-dialog error cannot trigger a second write',async()=>{
  let submitted=false
  const f=callbacks({submit:async()=>{submitted=true;throw Error('Unexpected MCEL confirmation refused')},
    mutationSubmitted:()=>submitted})
  await assert.rejects(saveSellerOfferAfterRefresh(f),/Unexpected MCEL confirmation refused/)
  assert.equal(f._count().writes,1)
})
test('a backend error never causes duplicate submission',async()=>{
  const f=callbacks({submit:async()=>{throw Error('backend-postcondition-timeout')}})
  await assert.rejects(saveSellerOfferAfterRefresh(f),/backend-postcondition-timeout/)
  assert.equal(f._count().writes,1)
})
