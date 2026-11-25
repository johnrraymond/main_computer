import {test} from 'node:test'
import assert from 'node:assert/strict'
import {mountCanonicalWorkerSurface} from './canonical_worker_surface.mjs'
const URL='http://127.0.0.1:8765/applications/worker'
const CHILD=URL+'?mcel-shell-child=1'
function fixture({phase='promoted',parent=URL,child=CHILD}={}){
  const state={parent,child,detached:false,events:[],phase}
  const frame={url:()=>state.child,isDetached:()=>state.detached,
    waitForURL:async url=>assert.equal(url,state.child),waitForFunction:async()=>{},
    locator:selector=>({getAttribute:async()=>state.phase,count:async()=>1,selector}),
    evaluate:async()=>42,getByRole:role=>({role})}
  const host={url:()=>state.parent,
    locator:selector=>({waitFor:async()=>assert.equal(selector,'body[data-mcel-shell-canonical-promoted="true"]'),
      elementHandle:async()=>({contentFrame:async()=>frame})}),
    on(event){assert.equal(this,host);state.events.push(event)},
    screenshot:async()=> 'whole-shell',bringToFront:async()=>{}}
  return {host,state,frame}
}
test('mounts exact promoted child and preserves host ownership of events and screenshots',async()=>{
  const {host,state}=fixture()
  const {page,proof}=await mountCanonicalWorkerSurface(host,URL)
  assert.deepEqual(proof,{url:URL,childUrl:CHILD,phase:'promoted',mounted:true})
  assert.equal(page.hostPage,host)
  assert.equal(page.url(),URL)
  assert.equal(await page.evaluate(()=>0),42)
  assert.equal(page.locator('#inside').selector,'#inside')
  page.on('response',()=>{})
  assert.deepEqual(state.events,['response'])
  assert.equal(await page.screenshot(),'whole-shell')
  state.child=URL+'?other=1'
  assert.throws(()=>page.locator('#inside'),/FRAME_CHANGED/)
})
test('refuses candidate, foreign parent and stale/detached frame',async()=>{
  await assert.rejects(mountCanonicalWorkerSurface(fixture({phase:'candidate'}).host,URL),/CHILD_UNVERIFIED/)
  await assert.rejects(mountCanonicalWorkerSurface(fixture({parent:URL+'?preview=1'}).host,URL),/PARENT_URL_UNVERIFIED/)
  const {host,state}=fixture()
  const {page}=await mountCanonicalWorkerSurface(host,URL)
  state.detached=true
  assert.throws(()=>page.evaluate(()=>0),/FRAME_CHANGED/)
})
