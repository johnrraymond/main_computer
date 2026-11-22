import test from 'node:test'
import assert from 'node:assert/strict'
import {classifyNativeMskInspection} from './mcel_live_msk_inspection.mjs'

const base={panelPresent:true,panelVisible:true,stateCount:1,requestCount:1,revokeCount:1}
test('ready inactive state allows fresh MSK issuance',()=>{
  assert.equal(classifyNativeMskInspection({...base,stateText:'No active key'}),'NO_ACTIVE_KEY')
})
test('pre-existing active key is explicitly distinct from a fresh issue',()=>{
  for(const stateText of ['Active','Active server-side key','  Active  '])
    assert.equal(classifyNativeMskInspection({...base,stateText}),'PREEXISTING_ACTIVE')
})
test('missing and hidden wallet panels cannot certify MSK state',()=>{
  assert.equal(classifyNativeMskInspection({...base,panelPresent:false}),'PANEL_MISSING')
  assert.equal(classifyNativeMskInspection({...base,panelVisible:false}),'PANEL_HIDDEN')
})
test('missing or duplicated controls cannot certify MSK state',()=>{
  for(const field of ['stateCount','requestCount','revokeCount']){
    for(const value of [0,2])
      assert.equal(classifyNativeMskInspection({...base,[field]:value,stateText:'No active key'}),'CONTROLS_MISSING_OR_DUPLICATED')
  }
})
test('blank or unexpected status never becomes absence of an active key',()=>{
  assert.equal(classifyNativeMskInspection({...base,stateText:' '}),'STATE_EMPTY')
  assert.equal(classifyNativeMskInspection({...base,stateText:'Request pending'}),'STATE_UNRECOGNIZED')
})
test('classifier does not expose raw potentially sensitive state text',()=>{
  assert.equal(classifyNativeMskInspection({...base,stateText:'0xprivatekeyfixture'}),'STATE_UNRECOGNIZED')
})
