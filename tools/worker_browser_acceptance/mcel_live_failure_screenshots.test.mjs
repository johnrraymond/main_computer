import test from 'node:test'
import assert from 'node:assert/strict'
import {mkdtempSync,readFileSync,writeFileSync,existsSync,rmSync} from 'node:fs'
import {join} from 'node:path'
import {tmpdir} from 'node:os'
import {captureLiveFailureScreenshots,captureLiveOfferMoment} from './mcel_live_failure_screenshots.mjs'

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


test('policy failure screenshot is captured at the event, before finally cleanup',async()=>{
  const root=mkdtempSync(join(tmpdir(),'mcel-policy-screen-'))
  try{
    const calls=[]
    const page=filePage('http://127.0.0.1:8765/applications/mcel-semantic-worker',calls)
    const result=await captureLiveOfferMoment({workerPage:page,
      context:{pages:()=>[]},reportPath:join(root,'live-work-cycle-evidence.json'),
      gate:'work-cycle',extensionId,phase:'policy-failed',
      desktopCapture:path=>{writeFileSync(path,Buffer.alloc(100,3));return 'saved'}})
    assert.equal(result.phase,'policy-failed')
    assert.equal(result.version,'offer-moment-screens-v1')
    assert.ok(result.captures.some(c=>c.surface==='worker' && c.status==='saved'))
    assert.ok(result.captures.some(c=>c.surface==='desktop' && c.status==='saved'))
    assert.ok(result.captures.some(c=>c.file?.includes('moment-policy-failed-')))
    const main=readFileSync(new URL('./mcel_live_acceptance.mjs',import.meta.url),'utf8')
    assert.ok(main.includes("await snapshotOfferMoment('policy-failed')"))
    assert.ok(main.indexOf("await snapshotOfferMoment('policy-failed')") < main.indexOf('}finally{',main.indexOf('async function testMarketplace')))
  }finally{rmSync(root,{recursive:true,force:true})}
})

test('offer price save failure has an event-time capture phase',async()=>{
  const root=mkdtempSync(join(tmpdir(),'mcel-price-screen-'))
  try{
    const calls=[]
    const page=filePage('http://127.0.0.1:8765/applications/mcel-semantic-worker',calls)
    const result=await captureLiveOfferMoment({workerPage:page,context:{pages:()=>[]},
      gate:'work-cycle',extensionId,phase:'offer-price-failed',
      reportPath:join(root,'live-work-cycle-evidence.json'),
      desktopCapture:path=>{writeFileSync(path,Buffer.alloc(100,3));return 'saved'}})
    assert.equal(result.phase,'offer-price-failed')
    assert.ok(result.captures.some(c=>c.file?.includes('moment-offer-price-failed-')))
  }finally{rmSync(root,{recursive:true,force:true})}
})
