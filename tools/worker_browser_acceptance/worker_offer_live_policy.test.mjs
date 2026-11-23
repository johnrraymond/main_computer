import test from 'node:test'
import assert from 'node:assert/strict'
import {planOfferAcceptancePolicy} from './worker_offer_live_policy.mjs'

test('authorized live offer changes totally-idle to AI-idle without bypassing server AI checks',()=>{
  const got=planOfferAcceptancePolicy({sellerEnabled:true,sellerAvailabilityMode:'totally_idle'},true)
  assert.equal(got.originalMode,'totally_idle')
  assert.equal(got.modeForTest,'ai_idle')
  assert.equal(got.requiresChange,true)
  assert.equal(got.changeMode,true)
  assert.equal(got.changeEnabled,false)
})
test('without settings authorization the original policy is never changed',()=>{
  const got=planOfferAcceptancePolicy({sellerEnabled:true,sellerAvailabilityMode:'totally_idle'},false)
  assert.equal(got.modeForTest,'totally_idle')
  assert.equal(got.requiresChange,false)
  assert.equal(got.changeMode,false)
})
test('an already AI-idle and enabled seller is unchanged',()=>{
  const got=planOfferAcceptancePolicy({sellerEnabled:true,sellerAvailabilityMode:'ai_idle'},true)
  assert.equal(got.requiresChange,false)
})
test('disabled seller is only enabled with explicit settings authorization',()=>{
  const before={sellerEnabled:false,sellerAvailabilityMode:'totally_idle'}
  assert.equal(planOfferAcceptancePolicy(before,false).changeEnabled,false)
  const authorized=planOfferAcceptancePolicy(before,true)
  assert.equal(authorized.changeEnabled,true)
  assert.equal(authorized.changeMode,true)
})
test('invalid or unreadable policy refuses instead of guessing',()=>{
  assert.throws(()=>planOfferAcceptancePolicy({},true),/OFFER_POLICY_UNAVAILABLE/)
  assert.throws(()=>planOfferAcceptancePolicy({sellerEnabled:true,sellerAvailabilityMode:'always'},true),
    /OFFER_AVAILABILITY_MODE_UNVERIFIED/)
})
