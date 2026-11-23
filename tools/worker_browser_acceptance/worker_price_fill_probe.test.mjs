import test from 'node:test'
import assert from 'node:assert/strict'
import {parseProbeArgs, probeTrials, analyzeProbeTrial, TARGET_PRICE,
  PRICE_SELECTOR,WORKER_PATH,probeBrowserLaunchOptions} from './worker_price_fill_probe.mjs'

test('uses local Worker URL, no login or mutation authorization',()=>{
  const c=parseProbeArgs([])
  assert.equal(c.url,'http://127.0.0.1:8765'+WORKER_PATH)
  assert.equal(c.headless,false)
  assert.equal(PRICE_SELECTOR,'input[name="sellerCreditsPerToken"]')
})
test('CLI allows local host and observation controls',()=>{
  const c=parseProbeArgs(['--host-url','http://localhost:8765','--headless',
    '--observe-ms','800','--no-refresh-trial'])
  assert.equal(c.url,'http://localhost:8765'+WORKER_PATH)
  assert.equal(c.includeRefresh,false)
  assert.equal(c.observeMs,800)
  assert.equal(c.headless,true)
})
test('rejects remote hosts, credentials and invalid timing',()=>{
  for(const args of [
    ['--host-url','https://example.com'],
    ['--host-url','http://user:secret@localhost:8765/'],
    ['--host-url','http://localhost:8765/wrong'],
    ['--observe-ms','0'], ['--observe-ms','20000'], ['--unrecognized']
  ])assert.throws(()=>parseProbeArgs(args))
})
test('four trials include early, settled, keyboard, explicit read-only refresh',()=>{
  assert.deepEqual(probeTrials().map(x=>x.id),[
    'immediate-fill','settled-fill','keyboard-type','refresh-overlap-fill'])
  assert.equal(probeTrials(false).length,3)
})
test('successful unsaved fill is not a reset',()=>{
  const x=analyzeProbeTrial({samples:[{value:'0.001'},{value:TARGET_PRICE},
    {value:TARGET_PRICE}],events:[{event:'DOM_INPUT',value:TARGET_PRICE}],network:[]})
  assert.equal(x.targetObserved,true)
  assert.equal(x.resetAfterFill,false)
})
test('detects reset between Playwright samples by DOM event sequence',()=>{
  const x=analyzeProbeTrial({samples:[{value:'0.001'},{value:'0.001'}],
    events:[{event:'DOM_INPUT',value:TARGET_PRICE},
      {event:'JS_SETTER_WRITE',before:TARGET_PRICE,after:'0.001',stack:'at refreshSeller'}]})
  assert.equal(x.resetAfterFill,true)
  assert.equal(x.jsResetCount,1)
  assert.equal(x.suspectStack,'at refreshSeller')
})
test('detects replacement of edited input',()=>{
  const x=analyzeProbeTrial({events:[{event:'DOM_INPUT',value:TARGET_PRICE},
    {event:'INPUT_REPLACED',nextValue:'0.001'}]})
  assert.equal(x.resetAfterFill,true)
  assert.equal(x.replacementCount,1)
})
test('declares a settings POST as an unexpected write even without bodies',()=>{
  const x=analyzeProbeTrial({network:[{event:'request',method:'GET'},
    {event:'request',method:'POST'}]})
  assert.equal(x.settingsReads,1)
  assert.equal(x.unexpectedSettingsPost,true)
})
test('never claims target observed without a sample or event',()=>{
  const x=analyzeProbeTrial({samples:[{value:'0.001'}]})
  assert.equal(x.targetObserved,false)
  assert.equal(x.resetAfterFill,false)
})

test('uses the working MCEL Playwright chromium channel without external Chrome',()=>{
  assert.deepEqual(probeBrowserLaunchOptions(parseProbeArgs([])),{
    channel:'chromium',headless:false
  })
  assert.deepEqual(probeBrowserLaunchOptions(parseProbeArgs(['--headless'])),{
    channel:'chromium',headless:true
  })
})
