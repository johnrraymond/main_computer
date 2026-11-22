import test from 'node:test'
import assert from 'node:assert/strict'
import {mkdtempSync,readFileSync,writeFileSync,existsSync,rmSync} from 'node:fs'
import {join} from 'node:path'
import {tmpdir} from 'node:os'
import {captureLiveFailureScreenshots} from './mcel_live_failure_screenshots.mjs'

const extensionId='dnamoknnfmliijgdpmkcjmgihhhidcgj'
const filePage=(url,log, {fail=false}={})=>({
  url:()=>url,isClosed:()=>false,
  async screenshot({path}) {log.push('page:'+url);if(fail)throw Error('private browser error')
    writeFileSync(path,Buffer.alloc(100,7))},
})

test('captures visible desktop FIRST, worker, and correct MetaMask extension pages',async()=>{
  const root=mkdtempSync(join(tmpdir(),'mcel-screen-'))
  try {
    const calls=[]
    const worker=filePage('http://127.0.0.1:8765/applications/mcel-semantic-worker',calls)
    const home=filePage(`chrome-extension://${extensionId}/home.html#/`,calls)
    const approval=filePage(`chrome-extension://${extensionId}/notification.html`,calls)
    const foreign=filePage('chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/home.html',calls)
    const outside=filePage('https://example.org/',calls)
    const data=await captureLiveFailureScreenshots({
      workerPage:worker,context:{pages:()=>[home,foreign,worker,outside,approval]},
      reportPath:join(root,'live-wallet-msk-evidence.json'),extensionId,gate:'wallet-msk',
      desktopCapture:path=>{calls.push('desktop');writeFileSync(path,Buffer.alloc(100,3));return 'saved'},
    })
    assert.equal(data.version,'failure-screens-v1')
    assert.equal(data.captures.filter(x=>x.status==='saved').length,4)
    assert.equal(calls[0],'desktop')
    assert.ok(calls[1].includes('127.0.0.1'))
    assert.ok(calls[2].includes('notification.html'))
    assert.ok(calls[3].includes('home.html'))
    assert.equal(calls.length,4) // never capture arbitrary sites or other extensions
    for(const shot of data.captures){
      assert.match(shot.file,/^live-wallet-msk-failure-[A-Za-z0-9_-]{6,}\//)
      assert.ok(existsSync(join(root,shot.file)))
    }
    assert.equal(readFileSync(join(root,data.captures[0].file)).length,100)
  }finally{rmSync(root,{recursive:true,force:true})}
})

test('a screenshot error is reported categorically and does not throw',async()=>{
  const root=mkdtempSync(join(tmpdir(),'mcel-screen-'))
  try {
    const logs=[]
    const worker=filePage('http://127.0.0.1:8765/applications/mcel-semantic-worker',logs,{fail:true})
    const data=await captureLiveFailureScreenshots({
      workerPage:worker,context:{pages:()=>[]},reportPath:join(root,'live.json'),
      gate:'wallet-msk',extensionId,
      desktopCapture:()=>{throw Error('secret text that must never appear')},
    })
    assert.deepEqual(data.captures.map(x=>[x.surface,x.status]),[
      ['desktop','unavailable'],['worker','unavailable'],['metamask','not-exposed']])
    assert.ok(!JSON.stringify(data).includes('secret'))
    assert.ok(data.captures.every(x=>!('file' in x)))
  }finally{rmSync(root,{recursive:true,force:true})}
})

test('visual capture happens in the real browser catch, before cleanup',()=>{
  // No approval button or signing behavior may be changed by diagnostics.
  const main=readFileSync(new URL('./mcel_live_acceptance.mjs',import.meta.url),'utf8')
  assert.ok(main.indexOf('proof.failureScreenshots=await captureLiveFailureScreenshots(')>0)
  assert.ok(main.indexOf('proof.failureScreenshots=await captureLiveFailureScreenshots(')<main.indexOf('await context?.close()'))
  assert.ok(main.includes('proof.errorClass=error instanceof Error'))
})

// The previous capture ran only in the catch handler, after request.failed.
// These snapshots must fire on the actual signing events while Chromium lives.
import {EventEmitter} from 'node:events'
import {captureLiveMskMoment} from './mcel_live_failure_screenshots.mjs'

test('live MSK moment captures an otherwise invisible CDP MetaMask approval target', async()=>{
  const root=mkdtempSync(join(tmpdir(),'mcel-moment-'))
  try{
    const calls=[]
    const worker=filePage('http://127.0.0.1:8765/applications/mcel-semantic-worker',calls)
    const cdp=new EventEmitter()
    const encoded=Buffer.alloc(160,8).toString('base64')
    cdp.detach=async()=>{}
    cdp.send=async(method,params={})=>{
      if(method==='Target.getTargets')return {targetInfos:[
        {type:'page',targetId:'good',url:`chrome-extension://${extensionId}/notification.html`},
        {type:'page',targetId:'foreign',url:'chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/notification.html'},
      ]}
      if(method==='Target.attachToTarget')return {sessionId:'one'}
      if(method==='Target.detachFromTarget')return {}
      if(method==='Target.sendMessageToTarget'){
        const {id,method:sub}=JSON.parse(params.message)
        calls.push('cdp:'+sub)
        queueMicrotask(()=>cdp.emit('Target.receivedMessageFromTarget',{
          sessionId:'one',message:JSON.stringify({id,result:sub==='Page.captureScreenshot'?{data:encoded}:{}}),
        }))
        return {}
      }
      throw Error('unexpected CDP call')
    }
    const data=await captureLiveMskMoment({
      workerPage:worker,
      context:{newCDPSession:async()=>cdp,pages:()=>[]},
      reportPath:join(root,'receipt.json'),extensionId,gate:'wallet-msk',phase:'approval-target',
      desktopCapture:async path=>{calls.push('desktop');writeFileSync(path,Buffer.alloc(128,4));return 'saved'},
    })
    assert.equal(data.version,'msk-moment-screens-v1')
    assert.equal(data.phase,'approval-target')
    assert.ok(data.captures.some(x=>x.surface==='metamask-cdp-notification'&&x.status==='saved'))
    assert.ok(data.captures.some(x=>x.surface==='desktop'&&x.status==='saved'))
    assert.ok(data.captures.some(x=>x.surface==='worker'&&x.status==='saved'))
    assert.ok(calls.includes('cdp:Page.captureScreenshot'))
    assert.ok(!JSON.stringify(data).includes('foreign'))
  }finally{rmSync(root,{recursive:true,force:true})}
})

test('MSK live screenshot phases are strictly allowlisted and never open arbitrary targets',async()=>{
  const root=mkdtempSync(join(tmpdir(),'mcel-moment-'))
  try{
    const data=await captureLiveMskMoment({phase:'password-leak',reportPath:join(root,'receipt.json')})
    assert.deepEqual(data.captures,[{surface:'storage',status:'unavailable'}])
    const harness=readFileSync(new URL('./mcel_live_acceptance.mjs',import.meta.url),'utf8')
    assert.match(harness,/event==='request\.start'\)snapshotMoment\('request-start'\)/)
    assert.match(harness,/event==='request\.failed'\)snapshotMoment\('request-failed'\)/)
    assert.match(harness,/snapshotMoment\('approval-target'\)/)
    assert.ok(harness.indexOf('await Promise.allSettled(proof.mskMomentTasks)') < harness.indexOf('await context?.close()'))
  }finally{rmSync(root,{recursive:true,force:true})}
})

test('revocation moment phases capture the second signed operation before cleanup',async()=>{
  const root=mkdtempSync(join(tmpdir(),'mcel-revoke-moment-'))
  try{
    const calls=[]
    const worker=filePage('http://127.0.0.1:8765/applications/mcel-semantic-worker',calls)
    const page=filePage(`chrome-extension://${extensionId}/sidepanel.html`,calls)
    const evidence=await captureLiveMskMoment({
      workerPage:worker, context:{pages:()=>[page]},
      reportPath:join(root,'receipt.json'),extensionId,gate:'wallet-msk',phase:'revoke-failed',
      desktopCapture:async path=>{writeFileSync(path,Buffer.alloc(128,8));return 'saved'},
    })
    assert.equal(evidence.phase,'revoke-failed')
    assert.ok(evidence.captures.some(row=>row.surface==='worker'&&row.status==='saved'))
    assert.ok(evidence.captures.some(row=>row.surface==='metamask-sidepanel'&&row.status==='saved'))
    const harness=readFileSync(new URL('./mcel_live_acceptance.mjs',import.meta.url),'utf8')
    assert.match(harness,/event==='revoke\.start'\)snapshotMoment\('revoke-start'\)/)
    assert.match(harness,/event==='revoke\.failed'\)snapshotMoment\('revoke-failed'\)/)
    assert.match(harness,/snapshotMoment\('revoke-approval-target'\)/)
  }finally{rmSync(root,{recursive:true,force:true})}
})


import {captureLiveOfferMoment} from './mcel_live_failure_screenshots.mjs'
test('offer snapshots capture the Worker at an actual failure event without leaking browser text',async()=>{
  const root=mkdtempSync(join(tmpdir(),'mcel-offer-moment-'))
  try{
    const calls=[]
    const worker=filePage('http://127.0.0.1:8765/applications/mcel-semantic-worker',calls)
    const result=await captureLiveOfferMoment({workerPage:worker,context:{pages:()=>[]},
      reportPath:join(root,'evidence.json'),extensionId,gate:'offer-registration',phase:'offer-http-failed',
      desktopCapture:async path=>{writeFileSync(path,Buffer.alloc(100,4));return 'saved'},
    })
    assert.equal(result.version,'offer-moment-screens-v1')
    assert.ok(result.captures.some(row=>row.surface==='worker'&&row.status==='saved'))
    assert.ok(result.captures.every(row=>!JSON.stringify(row).includes('wallet-address')))
    const harness=readFileSync(new URL('./mcel_live_acceptance.mjs',import.meta.url),'utf8')
    assert.match(harness,/snapshotOfferMoment\('offer-http-failed'\)/)
    assert.ok(harness.indexOf('await Promise.allSettled(proof.offerMomentTasks)') < harness.indexOf('await context?.close()'))
  }finally{rmSync(root,{recursive:true,force:true})}
})
