import test from 'node:test'
import assert from 'node:assert/strict'
import {ensureDisposableMetaMaskUnlocked, visibleUnlockForm} from './metamask_cached_unlock.mjs'

const ID = 'dnamoknnfmliijgdpmkcjmgihhhidcgj'
const ADDRESS = '0x' + 'a'.repeat(40)
const PASSWORD = 'disposable-only-secret'
function page({id = ID,route = '#/onboarding/unlock',state = 'locked',inputType = 'password',expectedPassword = PASSWORD} = {}) {
  let currentState = state,entered = '',clicks = 0
  const input = {
    isVisible:async()=>currentState==='locked',
    fill:async value=>{entered=value}
  }
  const button = {
    isVisible:async()=>currentState==='locked',
    isEnabled:async()=>entered.length>0,
    click:async()=>{if(entered!==expectedPassword)throw new Error('incorrect test password');clicks++;currentState='home'}
  }
  const empty = {isVisible:async()=>false,isEnabled:async()=>false}
  const result = {
    url:()=>`chrome-extension://${id}/home.html${currentState==='locked'?route:'#/'}`,
    getByRole:(role,options)=>({first:()=>role==='button'&&options?.name?.test?.('Unlock')?button:empty}),
    getByPlaceholder:()=>({first:()=>input}),
    locator:sel=>({first:()=>sel==='input[type="password"]' && inputType==='password'?input:empty}),
    isClosed:()=>false,
    bringToFront:async()=>{},
    isHome:()=>currentState==='home',
    get clicks(){return clicks},
    get entered(){return entered}
  }
  return result
}
function fakeContext(pages){return {pages:()=>pages}}
const verify = async (p,a,id)=>{
  if (!p.url().startsWith(`chrome-extension://${id}/home.html`) || !p.isHome() || a!==ADDRESS) {
    throw new Error('wallet home not matching disposable identity')
  }
  return true
}
test('reused MetaMask v13.53 unlock page is filled, clicked and identity-verified',async()=>{
  const p=page(),events=[]
  const result=await ensureDisposableMetaMaskUnlocked({context:fakeContext([p]),extensionId:ID,expectedAddress:ADDRESS,password:PASSWORD,verifyHome:verify,
    sleep:async()=>{},onProgress:state=>events.push(state)})
  assert.equal(result,p)
  assert.equal(p.clicks,1)
  assert.deepEqual(events,['metamask.disposable-profile-unlock-submitted','metamask.disposable-profile-identity-verified'])
})
test('already unlocked cached wallet is verified without a password submission',async()=>{
  const p=page({state:'home'})
  await ensureDisposableMetaMaskUnlocked({context:fakeContext([p]),extensionId:ID,expectedAddress:ADDRESS,password:PASSWORD,verifyHome:verify})
  assert.equal(p.clicks,0)
  assert.equal(p.entered,'')
})
test('old unlock route remains compatible',async()=>{
  const p=page({route:'#/unlock'})
  await ensureDisposableMetaMaskUnlocked({context:fakeContext([p]),extensionId:ID,expectedAddress:ADDRESS,password:PASSWORD,verifyHome:verify,sleep:async()=>{}})
  assert.equal(p.clicks,1)
})
test('will never fill untrusted extension password field',async()=>{
  const untrusted=page({id:'a'.repeat(32)})
  assert.equal(await visibleUnlockForm(untrusted,ID),null)
  await assert.rejects(ensureDisposableMetaMaskUnlocked({context:fakeContext([untrusted]),extensionId:ID,expectedAddress:ADDRESS,password:PASSWORD,verifyHome:verify,timeoutMs:0}),/unlock incomplete/)
  assert.equal(untrusted.entered,'')
})
test('will never fill a password outside exact unlock route',async()=>{
  const p=page({route:'#/foo'})
  assert.equal(await visibleUnlockForm(p,ID),null)
  await assert.rejects(ensureDisposableMetaMaskUnlocked({context:fakeContext([p]),extensionId:ID,expectedAddress:ADDRESS,password:PASSWORD,verifyHome:verify,timeoutMs:0}),/unlock incomplete/)
  assert.equal(p.entered,'')
})
test('non-matching cached address must not certify wallet home',async()=>{
  const p=page({state:'home'})
  await assert.rejects(ensureDisposableMetaMaskUnlocked({context:fakeContext([p]),extensionId:ID,expectedAddress:'0x'+'b'.repeat(40),password:PASSWORD,verifyHome:verify,timeoutMs:0}),/unlock incomplete/)
})
test('a locked tab is unlocked even if another cached home tab exists',async()=>{
  const locked=page();const home=page({state:'home'})
  await ensureDisposableMetaMaskUnlocked({context:fakeContext([home,locked]),extensionId:ID,expectedAddress:ADDRESS,password:PASSWORD,verifyHome:verify,sleep:async()=>{}})
  assert.equal(locked.clicks,1)
})
test('refuse invalid extension ID or wallet before touching secrets',async()=>{
  const p=page()
  await assert.rejects(ensureDisposableMetaMaskUnlocked({context:fakeContext([p]),extensionId:'invalid',expectedAddress:ADDRESS,password:PASSWORD}),/invalid test identity/)
  assert.equal(p.entered,'')
})
test('incorrect test password does not disclose secret in thrown error',async()=>{
  const p=page()
  await assert.rejects(ensureDisposableMetaMaskUnlocked({context:fakeContext([p]),extensionId:ID,expectedAddress:ADDRESS,password:'wrong',verifyHome:verify,timeoutMs:100}),/incorrect test password/)
})
test('cached unlocked profile selector sees unlock route as responsive instead of waiting 30 seconds',async()=>{
  const {selectMetaMaskPage, screenLabel} = await import('./metamask_onboarding.mjs')
  const p=page()
  p.getByText=()=>({first:()=>({isVisible:async()=>false})})
  assert.equal(await screenLabel(p),'wallet-unlock')
  // no navigation or second onboarding tab is permitted for a cached profile
  const context={pages:()=>[p],newPage:async()=>{throw new Error('unexpected second page')}}
  assert.equal(await selectMetaMaskPage(context,ID,{fresh:false,probeAttempts:1}),p)
})


test('unlock field disappears during fill: verify already-unlocked wallet instead of leaking password', async()=>{
  const p=page()
  let fills=0
  p.locator=()=>({first:()=>({
    isVisible:async()=>p.url().includes('/unlock'),
    fill:async(_password,options)=>{
      fills++
      assert.equal(options.timeout,1500)
      // React removes the input while Playwright is in the fill operation.
      // The default Playwright exception also includes the raw password.
      p.transitioned=true
      throw new Error('locator.fill: element was detached; fill('+PASSWORD+')')
    },
  })})
  const originalUrl=p.url.bind(p)
  p.url=()=>p.transitioned?`chrome-extension://${ID}/home.html#/`:originalUrl()
  p.isHome=()=>!!p.transitioned
  let success=false
  const value=await ensureDisposableMetaMaskUnlocked({context:fakeContext([p]),extensionId:ID,
    expectedAddress:ADDRESS,password:PASSWORD,verifyHome:verify,timeoutMs:1_000,
    sleep:async()=>{},onProgress:event=>{if(event.endsWith('identity-verified'))success=true}})
  assert.equal(value,p)
  assert.equal(fills,1)
  assert.equal(success,true)
})

test('after unlock submitted, stale password field is never refilled while home is verified',async()=>{
  const locked=page(), home=page({state:'home'})
  let fills=0, verifyAttempts=0
  // Simulate a stale, still-visible unlock route even after submission.
  const originalLocator=locked.locator.bind(locked)
  const originalGetByRole=locked.getByRole.bind(locked)
  locked.url=()=>`chrome-extension://${ID}/home.html#/onboarding/unlock`
  locked.getByRole=(role,options)=>{
    const original=originalGetByRole(role,options).first()
    return {first:()=>({...original,isVisible:async()=>true})}
  }
  locked.locator=selector=>{
    if(selector!=='input[type="password"]')return originalLocator(selector)
    const base=originalLocator(selector).first()
    return {first:()=>({isVisible:async()=>true, fill:async value=>{
      fills++
      if(fills>1)throw new Error('SECOND-FILL-SHOULD-NOT-HAPPEN')
      return base.fill(value)
    }})}
  }
  const verifyAfterSecondPass=async (p,a,id)=>{
    verifyAttempts++
    if(verifyAttempts<3)throw new Error('wallet home is still rendering')
    return verify(p,a,id)
  }
  const result=await ensureDisposableMetaMaskUnlocked({context:fakeContext([locked,home]),
    extensionId:ID,expectedAddress:ADDRESS,password:PASSWORD,verifyHome:verifyAfterSecondPass,
    timeoutMs:1000,sleep:async()=>{}})
  assert.ok(result===home || result===locked)
  assert.equal(fills,1)
  assert.equal(locked.clicks,1)
})

test('if fill keeps failing, bounded retries never expose the disposable secret',async()=>{
  const locked=page()
  let trials=0
  locked.locator=()=>({first:()=>({
    isVisible:async()=>true,
    fill:async (_value,options)=>{
      assert.equal(options.timeout,1_500)
      trials++
      throw new Error('locator.fill: timeout '+PASSWORD)
    },
  })})
  await assert.rejects(ensureDisposableMetaMaskUnlocked({context:fakeContext([locked]),
    extensionId:ID,expectedAddress:ADDRESS,password:PASSWORD,verifyHome:verify,
    timeoutMs:40,pollMs:2,sleep:async()=>new Promise(r=>setTimeout(r,2))}),error=>{
      assert.match(error.message,/unlock incomplete/)
      assert.ok(!error.message.includes(PASSWORD))
      return true
    })
  assert.ok(trials>=1)
})

test('identity mismatch remains refusal after unlock even if provider might be accessible',async()=>{
  const p=page()
  await assert.rejects(ensureDisposableMetaMaskUnlocked({context:fakeContext([p]),
    extensionId:ID,expectedAddress:'0x'+'b'.repeat(40),password:PASSWORD,
    verifyHome:verify,timeoutMs:30,pollMs:2,sleep:async()=>new Promise(r=>setTimeout(r,2))}),
    /unlock incomplete/)
  assert.equal(p.clicks,1)
})
