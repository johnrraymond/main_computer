// Keep the real canonical application shell open. The mounted Worker is an
// iframe; DOM operations belong to that exact frame, browser events and CDP
// belong to its real top-level page. Never silently use the Lab candidate.
const frameMethods = new Set(['evaluate','evaluateHandle','locator','getByRole',
  'getByText','getByLabel','getByPlaceholder','getByTestId','waitForFunction','waitForSelector'])

export async function mountCanonicalWorkerSurface(hostPage, expectedUrl) {
  const childUrl=expectedUrl+'?mcel-shell-child=1'
  if(hostPage.url()!==expectedUrl)throw Error('CANONICAL_WORKER_PARENT_URL_UNVERIFIED')
  await hostPage.locator('body[data-mcel-shell-canonical-promoted="true"]').waitFor({state:'attached',timeout:15000})
  const element=await hostPage.locator('#mcel-shell-frame').elementHandle({timeout:15000})
  if(!element)throw Error('CANONICAL_WORKER_FRAME_MISSING')
  const frame=await element.contentFrame()
  if(!frame)throw Error('CANONICAL_WORKER_FRAME_MISSING')
  await frame.waitForURL(childUrl,{timeout:45000})
  await frame.waitForFunction(()=>window.__mcelWorker?.app?.readState('state:destinations')?.length===6,null,{timeout:45000})
  const phase=await frame.locator('body').getAttribute('data-mcel-worker-phase')
  if(phase!=='promoted' || !(await frame.locator('[data-mcel-radio-choices]').count()))
    throw Error('CANONICAL_WORKER_CHILD_UNVERIFIED')
  const assertMounted=()=>{
    if(hostPage.url()!==expectedUrl || frame.isDetached() || frame.url()!==childUrl)
      throw Error('CANONICAL_WORKER_FRAME_CHANGED')
  }
  assertMounted()
  const page=new Proxy(hostPage,{get(target,key){
    if(key==='hostPage')return target
    if(frameMethods.has(key))return (...args)=>{assertMounted();return frame[key](...args)}
    const value=Reflect.get(target,key,target)
    return typeof value==='function'?value.bind(target):value
  }})
  return {page,proof:{url:expectedUrl,childUrl,phase,mounted:true}}
}
