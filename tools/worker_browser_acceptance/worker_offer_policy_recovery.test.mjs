import test from 'node:test'
import assert from 'node:assert/strict'
import {sellerPolicyReceipt,sellerPolicyMatches,parseSellerPolicyReceipt,
  enforceSellerPolicy} from './worker_offer_policy_recovery.mjs'

const baseline={sellerEnabled:true,sellerAvailabilityMode:'totally_idle',
 sellerTargetTokens:1024,sellerCreditsPerToken:'0.001'}
test('crash recovery receipt preserves exact original price',()=>{
 const receipt=sellerPolicyReceipt(baseline)
 assert.equal(receipt.sellerCreditsPerToken,'0.001')
 assert.deepEqual(parseSellerPolicyReceipt(JSON.parse(JSON.stringify(receipt))),receipt)
 assert.equal(sellerPolicyMatches({...baseline,sellerCreditsPerToken:'0.00001'},receipt),false)
 assert.equal(sellerPolicyMatches(baseline,receipt),true)
})
test('recovery verifier requires price restoration as well as token and availability policy',async()=>{
 const baselineReceipt=sellerPolicyReceipt(baseline)
 let current={...baseline,sellerAvailabilityMode:'ai_idle',sellerTargetTokens:512,sellerCreditsPerToken:'0.00001'}
 let writes=0
 const result=await enforceSellerPolicy({
  expected:baselineReceipt,read:async()=>current,
  write:async val=>{writes++;current={...current,...val}},
  delay:async()=>{},
 })
 assert.equal(result.verified,true)
 assert.equal(writes,1)
 assert.equal(current.sellerCreditsPerToken,'0.001')
})
test('recovery receipt rejects malformed prices and preserves legacy receipts',()=>{
 assert.throws(()=>sellerPolicyReceipt({...baseline,sellerCreditsPerToken:'0'}))
 assert.throws(()=>parseSellerPolicyReceipt({...sellerPolicyReceipt(baseline),sellerCreditsPerToken:'0.abc'}))
 const legacy=sellerPolicyReceipt({sellerEnabled:true,sellerAvailabilityMode:'totally_idle'})
 assert.equal(parseSellerPolicyReceipt(legacy).sellerCreditsPerToken,undefined)
})
