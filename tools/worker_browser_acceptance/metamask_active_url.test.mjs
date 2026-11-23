import test from 'node:test'
import assert from 'node:assert/strict'
import {safeMetaMaskRoute,inspectMetaMaskActiveUrl,requireVerifiedMetaMaskUrl,waitForVerifiedMetaMaskPreconnect} from './metamask_active_url.mjs'

const ID='dnamoknnfmliijgdpmkcjmgihhhidcgj'
const ADDRESS='0x'+'1'.repeat(40)
const BASE=`chrome-extension://${ID}/home.html`
function tab(raw,{screen='wallet-home',match=true,shown='hidden'}={}) {
  let active=shown==='visible',visits=0
  return {
    url:()=>raw,
    bringToFront:async()=>{active=true;visits++},
    evaluate:async()=>({visibility:active?'visible':'hidden',focused:active}),
    get visits(){return visits},
    screen,match
  }
}
function context(...pages) {return {pages:()=>pages}}
const options={
  classifyScreen:async page=>page.screen,
  verifyHome:async(page)=>{if(!page.match)throw Error('mismatched public address')}
}
test('canonical screenshot URL is an exact wallet-home route',()=>{
  assert.deepEqual(safeMetaMaskRoute(`${BASE}#/`,ID),{route:'home-root',url:`${BASE}#/`})
  assert.deepEqual(safeMetaMaskRoute(`${BASE}#/onboarding/unlock`,ID),
    {route:'unlock',url:`${BASE}#/onboarding/unlock`})
})
test('route identity is checked separately from optional foreground evidence',async()=>{
  const p=tab(`${BASE}#/`)
  const before=await inspectMetaMaskActiveUrl(context(p),ID,ADDRESS,options)
  assert.equal(before.evidence.visibility,'hidden')
  requireVerifiedMetaMaskUrl(before.evidence,{requireHome:true})
  assert.throws(()=>requireVerifiedMetaMaskUrl(before.evidence,{requireForeground:true}),/FOREGROUND_UNVERIFIED/)
  const after=await inspectMetaMaskActiveUrl(context(p),ID,ADDRESS,{...options,activate:true})
  requireVerifiedMetaMaskUrl(after.evidence,{requireHome:true})
  assert.equal(after.evidence.actualUrl,`${BASE}#/`)
  assert.equal(after.evidence.focused,true)
  assert.equal(p.visits,1)
})
test('prefer the verified disposable home over a stale home tab',async()=>{
  const old=tab(`${BASE}#/`,{match:false}), current=tab(`${BASE}#/`,{match:true})
  const result=await inspectMetaMaskActiveUrl(context(old,current),ID,ADDRESS,{...options,activate:true})
  assert.equal(result.selectedPage,current)
  assert.equal(old.visits,0)
  assert.equal(current.visits,1)
  assert.equal(result.evidence.walletIdentityMatched,true)
  assert.equal(result.evidence.tabCount,2)
})
test('other extension, other route, and secret hash are never included in evidence',async()=>{
  const other=tab(`chrome-extension://${'a'.repeat(32)}/home.html#/`),
    bad=tab(`${BASE}#/signature?payload=secret`,{screen:'unrecognized-extension-screen'})
  const result=await inspectMetaMaskActiveUrl(context(other,bad),ID,ADDRESS,options)
  assert.equal(result.evidence.actualUrl,'no-recognized-home-tab')
  assert.equal(result.evidence.tabs[0].route,'other-extension-route')
  assert.doesNotMatch(JSON.stringify(result.evidence),/secret|signature|payload/)
  assert.throws(()=>requireVerifiedMetaMaskUrl(result.evidence),/ACTIVE_URL_UNVERIFIED/)
})
test('different-extension active home is explicitly identified without exposing unknown URLs',async()=>{
  const other=tab(`chrome-extension://${'a'.repeat(32)}/home.html#/`,{shown:'visible'})
  other.evaluate=async()=>true
  const result=await inspectMetaMaskActiveUrl(context(other),ID,ADDRESS,options)
  assert.deepEqual(result.evidence.otherHomeTabs,[{
    url:`chrome-extension://${'a'.repeat(32)}/home.html#/`,visible:true}])
  assert.equal(result.evidence.actualUrl,'no-recognized-home-tab')
})
test('unlock route is allowed before unlock but not after',async()=>{
  const p=tab(`${BASE}#/onboarding/unlock`,{screen:'wallet-unlock'})
  // Matches the real failure: valid lock URL but Chrome reports hidden and unfocused.
  const result=await inspectMetaMaskActiveUrl(context(p),ID,ADDRESS,options)
  assert.equal(result.evidence.visibility,'hidden')
  assert.equal(result.evidence.selectedRoute,'unlock')
  requireVerifiedMetaMaskUrl(result.evidence)
  assert.throws(()=>requireVerifiedMetaMaskUrl(result.evidence,{requireHome:true}),/ACTIVE_URL_UNVERIFIED/)
  assert.throws(()=>requireVerifiedMetaMaskUrl(result.evidence,{requireForeground:true}),/FOREGROUND_UNVERIFIED/)
})
test('wallet home with mismatching account cannot certify identity',async()=>{
  const p=tab(`${BASE}#/`,{match:false})
  const result=await inspectMetaMaskActiveUrl(context(p),ID,ADDRESS,{...options,activate:true})
  assert.equal(result.evidence.walletIdentityMatched,false)
  assert.throws(()=>requireVerifiedMetaMaskUrl(result.evidence,{requireHome:true}),/HOME_IDENTITY_UNVERIFIED/)
})

test('activation transition from locked route to verified home refreshes account evidence',async()=>{
  let raw=`${BASE}#/onboarding/unlock`
  const p=tab(raw,{screen:'wallet-unlock',shown:'hidden'})
  p.url=()=>raw
  p.bringToFront=async()=>{raw=`${BASE}#/`;p.screen='wallet-home'}
  p.evaluate=async()=>({visibility:'hidden',focused:false})
  const result=await inspectMetaMaskActiveUrl(context(p),ID,ADDRESS,{...options,activate:true})
  assert.equal(result.evidence.actualUrl,`${BASE}#/`)
  assert.equal(result.evidence.selectedRoute,'home-root')
  assert.equal(result.evidence.selectedScreen,'wallet-home')
  assert.equal(result.evidence.walletIdentityMatched,true)
  assert.equal(result.evidence.visibility,'hidden')
  requireVerifiedMetaMaskUrl(result.evidence,{requireHome:true})
})
test('wrong account never passes unlocked-home verification even if foreground',async()=>{
  const p=tab(`${BASE}#/`,{screen:'wallet-home',match:false,shown:'visible'})
  const result=await inspectMetaMaskActiveUrl(context(p),ID,ADDRESS,options)
  assert.equal(result.evidence.visibility,'visible')
  assert.throws(()=>requireVerifiedMetaMaskUrl(result.evidence,{requireHome:true}),/HOME_IDENTITY_UNVERIFIED/)
})

test('a verified unlock URL is selected before its password form finishes rendering',async()=>{
  const p=tab(`${BASE}#/onboarding/unlock`,{screen:'unrecognized-extension-screen',shown:'hidden'})
  const result=await inspectMetaMaskActiveUrl(context(p),ID,ADDRESS,{...options,activate:true})
  assert.equal(result.evidence.selectedRoute,'unlock')
  assert.equal(result.evidence.actualUrl,`${BASE}#/onboarding/unlock`)
  assert.equal(result.evidence.selectedScreen,'unrecognized-extension-screen')
  requireVerifiedMetaMaskUrl(result.evidence)
  assert.throws(()=>requireVerifiedMetaMaskUrl(result.evidence,{requireHome:true}),/ACTIVE_URL_UNVERIFIED/)
})
test('preconnect retries until the recognized extension URL appears',async()=>{
  let url='about:blank'
  const p=tab(url,{screen:'unrecognized-extension-screen'})
  p.url=()=>url
  let waits=0
  const observations=[]
  const result=await waitForVerifiedMetaMaskPreconnect(context(p),ID,ADDRESS,{
    ...options,timeoutMs:1000, pollMs:1,
    inspect:(ctx,id,address,opts)=>inspectMetaMaskActiveUrl(ctx,id,address,{...options,...opts}),
    sleep:async()=>{waits++;url=`${BASE}#/unlock`},
    onObservation:(evidence,attempt)=>observations.push({route:evidence.selectedRoute,attempt}),
  })
  assert.equal(waits,1)
  assert.equal(result.checks,2)
  assert.deepEqual(observations.map(x=>x.route),['none','unlock'])
  requireVerifiedMetaMaskUrl(result.evidence)
})
test('preconnect never accepts another extension or an unrecognized route',async()=>{
  const p=tab(`chrome-extension://${'a'.repeat(32)}/home.html#/unlock`,{screen:'wallet-unlock'})
  let observations=[]
  await assert.rejects(waitForVerifiedMetaMaskPreconnect(context(p),ID,ADDRESS,{
    ...options,timeoutMs:0,
    inspect:(ctx,id,address,opts)=>inspectMetaMaskActiveUrl(ctx,id,address,{...options,...opts}),
    onObservation:ev=>observations.push(ev),
  }),/METAMASK_ACTIVE_URL_UNVERIFIED/)
  assert.equal(observations[0].selectedRoute,'none')
})
