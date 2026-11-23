// Isolated, read-only reproduction of the MCEL Worker's Playwright price fill.
// No MetaMask, Hub, settings POST, Save Seller Offer, or Captain operations.
import {mkdirSync, writeFileSync, existsSync} from 'node:fs'
import {resolve, join} from 'node:path'
import {spawnSync} from 'node:child_process'
import {fileURLToPath} from 'node:url'

export const PRICE_SELECTOR = 'input[name="sellerCreditsPerToken"]'
export const TARGET_PRICE = '0.00001'
export const WORKER_PATH = '/applications/mcel-semantic-worker'

export function parseProbeArgs(argv) {
  const options = {hostUrl:'http://127.0.0.1:8765', observeMs:3500,
    headless:false, includeRefresh:true, outputDir:null}
  for (let i=0;i<argv.length;i++) {
    const arg=argv[i]
    if (arg==='--host-url' && argv[i+1]) options.hostUrl=argv[++i]
    else if (arg==='--observe-ms' && argv[i+1]) options.observeMs=Number(argv[++i])
    else if (arg==='--output-dir' && argv[i+1]) options.outputDir=argv[++i]
    else if (arg==='--headless') options.headless=true
    else if (arg==='--no-refresh-trial') options.includeRefresh=false
    else throw Error(`PRICE_PROBE_INVALID_ARGUMENT:${arg}`)
  }
  let u
  try {u=new URL(options.hostUrl)} catch {throw Error('PRICE_PROBE_INVALID_HOST_URL')}
  if (!['127.0.0.1','localhost'].includes(u.hostname) ||
      !['http:','https:'].includes(u.protocol) || u.username || u.password ||
      (u.pathname!=='/' && u.pathname!=='') || u.search || u.hash)
    throw Error('PRICE_PROBE_LOOPBACK_HOST_REQUIRED')
  if (!Number.isInteger(options.observeMs) || options.observeMs<500 || options.observeMs>15000)
    throw Error('PRICE_PROBE_OBSERVE_MS_OUT_OF_RANGE')
  options.url=u.origin+WORKER_PATH
  return options
}

// Match the already-verified native MCEL live-acceptance browser channel.
// 'chrome' requires a separate Google Chrome installation and is not needed.
export function probeBrowserLaunchOptions(options) {
  return {channel:'chromium',headless:options.headless}
}

export function probeTrials(includeRefresh=true) {
  const trials = [
    {id:'immediate-fill', settleMs:0, method:'fill', refresh:false},
    {id:'settled-fill', settleMs:2500, method:'fill', refresh:false},
    {id:'keyboard-type', settleMs:2500, method:'keyboard', refresh:false},
  ]
  if(includeRefresh)trials.push({id:'refresh-overlap-fill', settleMs:0, method:'fill', refresh:true})
  return trials
}

export function analyzeProbeTrial({samples=[], events=[], network=[]}={}) {
  const found=samples.some(x=>x?.value===TARGET_PRICE) ||
    events.some(x=>x?.event==='DOM_INPUT' && x?.value===TARGET_PRICE)
  const firstIndex=samples.findIndex(x=>x?.value===TARGET_PRICE)
  const sampleReset=firstIndex>=0 && samples.slice(firstIndex+1)
    .some(x=>x?.value!==TARGET_PRICE && typeof x?.value==='string')
  // A quick refresh can undo fill before the next Playwright sample. The
  // in-page event stream is the higher-resolution source for such races.
  let sawTarget=false, eventReset=false
  for(const ev of events){
    const value=ev?.event==='JS_SETTER_WRITE'?ev.after:
      ev?.event==='VALUE_CHANGED'?ev.after:
      ev?.event==='INPUT_REPLACED'?ev.nextValue:
      ev?.event==='DOM_INPUT'?ev.value:undefined
    if(typeof value!=='string')continue
    if(sawTarget && value!==TARGET_PRICE)eventReset=true
    if(value===TARGET_PRICE)sawTarget=true
  }
  const resetAfterFill=sampleReset||eventReset
  const jsResets=events.filter(x=>x?.event==='JS_SETTER_WRITE' &&
    x?.before===TARGET_PRICE && x?.after!==TARGET_PRICE)
  const replacements=events.filter(x=>x?.event==='INPUT_REPLACED')
  const submitted=network.some(x=>x?.method==='POST')
  return {targetObserved:found,resetAfterFill,jsResetCount:jsResets.length,
    replacementCount:replacements.length,
    settingsReads:network.filter(x=>x.method==='GET').length,
    unexpectedSettingsPost:submitted,
    suspectStack:jsResets.at(0)?.stack||null}
}

// The instrumentation executes at document creation, before the MCEL runtime.
// It records only the seller price field and request method/status metadata.
export function browserPriceInstrumentation() {
  (()=>{
    const KEY='__mcelPriceProbe';if(window[KEY])return
    const selector='input[name="sellerCreditsPerToken"]'
    const native=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')
    const start=performance.now(), events=[]
    const elapsed=()=>Math.round(performance.now()-start)
    const record=(event,values={})=>{
      events.push({ms:elapsed(),event,...values});if(events.length>1600)events.shift()
    }
    let element=null,lastValue=null
    function isPrice(el){return el instanceof HTMLInputElement && el.name==='sellerCreditsPerToken'}
    try {
      Object.defineProperty(HTMLInputElement.prototype,'value',{
        ...native,
        set(value){
          const watching=isPrice(this)
          const before=watching?native.get.call(this):null
          const stack=watching?String(new Error().stack||'').split('\n').slice(2,11).join('\n'):null
          native.set.call(this,value)
          if(watching){
            const after=native.get.call(this)
            if(before!==after)record('JS_SETTER_WRITE',{before,after,stack,
              connected:this.isConnected,focused:document.activeElement===this})
          }
        }
      })
    }catch(e){record('SETTER_HOOK_UNAVAILABLE',{name:String(e?.name||'Error')})}
    function sample(trigger='poll'){
      const next=document.querySelector(selector)
      if(next!==element){
        record('INPUT_REPLACED',{trigger,hadPrevious:!!element,
          previousValue:element?.value??null,nextValue:next?.value??null,
          previousConnected:element?.isConnected??null})
        element=next;lastValue=next?.value??null;return
      }
      if(next && next.value!==lastValue){
        record('VALUE_CHANGED',{trigger,before:lastValue,after:next.value,
          focused:document.activeElement===next})
        lastValue=next.value
      }
    }
    for(const type of ['beforeinput','input','change','focus','blur']){
      document.addEventListener(type,e=>{
        if(isPrice(e.target)){
          record('DOM_'+type.toUpperCase(),{value:e.target.value,
            isTrusted:e.isTrusted,focused:document.activeElement===e.target})
          sample('dom-'+type)
        }
      },true)
    }
    const attach=()=>{
      if(!document.documentElement)return
      const observer=new MutationObserver(()=>sample('mutation'))
      observer.observe(document.documentElement,{subtree:true,childList:true,
        attributes:true,attributeFilter:['value','name']})
    }
    if(document.documentElement)attach();else document.addEventListener('DOMContentLoaded',attach,{once:true})
    setInterval(()=>sample('poll-30ms'),30)
    window[KEY]={events,record,sample,read(){sample('read');
      const el=document.querySelector(selector)
      return {found:!!el,value:el?.value??null,connected:el?.isConnected??false,
        focused:document.activeElement===el,
        count:document.querySelectorAll(selector).length,
        path:location.pathname,
        visibility:document.visibilityState}}
    }
    record('PROBE_INSTALLED')
  })()
}

function safePath(url) {
  try {const u=new URL(url);return u.pathname} catch {return 'unavailable'}
}
function errorClass(e) {return String(e?.message||e||'').includes('Timeout')?'TimeoutError':String(e?.name||'Error').slice(0,64)}
function uniqueOutput(base){return join(base,`${new Date().toISOString().replace(/[:.]/g,'-')}-${process.pid}`)}

async function probeOne(browser,options,trial,outDir) {
  const context=await browser.newContext({viewport:{width:1440,height:940},acceptDownloads:false})
  await context.addInitScript(browserPriceInstrumentation)
  const page=await context.newPage()
  const timeline=[],net=[],browserErrors=[]
  const watchReq=req=>{
    if(safePath(req.url())!=='/api/applications/worker/settings')return
    net.push({event:'request',ms:Date.now(),method:req.method(),path:'/api/applications/worker/settings'})
  }
  page.on('request',watchReq)
  page.on('response',res=>{
    if(safePath(res.url())==='/api/applications/worker/settings')
      net.push({event:'response',ms:Date.now(),method:res.request().method(),http:res.status()})
  })
  page.on('requestfailed',req=>{
    if(safePath(req.url())==='/api/applications/worker/settings')
      net.push({event:'failed',ms:Date.now(),method:req.method(),failure:'transport-failed'})
  })
  page.on('pageerror',err=>browserErrors.push({name:errorClass(err)}))
  const screenshot=async(name)=>{
    try{await page.screenshot({path:join(outDir,`${trial.id}-${name}.png`),timeout:3500})}
    catch(e){timeline.push({point:name+'-screenshot-unavailable',error:errorClass(e)})}
  }
  async function sample(point) {
    const data=await page.evaluate(()=>window.__mcelPriceProbe?.read()||{found:false}).catch(()=>({found:false}))
    const entry={point,ms:Date.now(),...data};timeline.push(entry);return entry
  }
  let error=null,observed=[],analysis=null,savedPrice=null
  try {
    await page.goto(options.url,{waitUntil:'domcontentloaded',timeout:20000})
    // Only navigate to Worker. Do not click marketplace, Save or Apply policy.
    const nav=page.locator('[data-mcel-purpose="navigation"] [data-value="worker"]')
    if(await nav.count()===1)await nav.click({timeout:12000})
    const field=page.locator(PRICE_SELECTOR)
    await field.waitFor({state:'visible',timeout:20000})
    if(await field.count()!==1)throw Error('PRICE_FIELD_NOT_UNIQUE')
    if(trial.settleMs)await page.waitForTimeout(trial.settleMs)
    await sample('before-fill')
    if(trial.refresh){
      const refresh=page.locator('button[data-intent="intent:refresh-seller"]')
      if(await refresh.count()!==1)throw Error('REFRESH_SELLER_READ_ONLY_BUTTON_MISSING')
      // A read-only refresh deliberately overlaps the edit to expose hydration races.
      await refresh.click({timeout:7000})
      await sample('after-refresh-click')
    }
    await page.evaluate(()=>window.__mcelPriceProbe?.record('PLAYWRIGHT_EDIT_BEGIN'))
    if(trial.method==='fill')await field.fill(TARGET_PRICE,{timeout:8000})
    else {
      await field.click();await field.press('ControlOrMeta+A')
      await field.pressSequentially(TARGET_PRICE,{delay:20})
    }
    await sample('immediately-after-fill')
    const sampleAt=[25,80,150,300,650,1200,2000,3500,5000,10000,15000]
      .filter(ms=>ms<=options.observeMs)
    let previous=0
    for(const ms of sampleAt){await page.waitForTimeout(ms-previous);previous=ms;await sample(`after-${ms}ms`)}
    if(previous<options.observeMs){await page.waitForTimeout(options.observeMs-previous);await sample('after-observe-ms')}
    // Also test loss on blur without submitting anything.
    await field.press('Tab').catch(()=>{})
    await sample('after-blur')
    await screenshot('final')
    // One read-only server check confirms the persisted offer was untouched.
    savedPrice=await page.evaluate(async()=>{
      try{const r=await fetch('/api/applications/worker/settings',{cache:'no-store',credentials:'same-origin'})
        const j=await r.json();return {http:r.status,
          rate:typeof j?.settings?.sellerCreditsPerToken==='string'?j.settings.sellerCreditsPerToken:'unknown'}}
      catch{return {http:null,rate:'unavailable'}}
    })
  }catch(e){error={class:errorClass(e),code:String(e?.message||'').slice(0,180)};await screenshot('failure')}
  finally{
    observed=await page.evaluate(()=>window.__mcelPriceProbe?.events||[]).catch(()=>[])
    analysis=analyzeProbeTrial({samples:timeline,events:observed,network:net})
    // Refuse false "read-only" claims if the app unexpectedly wrote settings.
    if(analysis.unexpectedSettingsPost)error=error||{class:'UnexpectedWrite',code:'SETTINGS_POST_DETECTED'}
    await context.close().catch(()=>{})
  }
  return {trial,analysis,error,samples:timeline,events:observed,network:net,
    backend:savedPrice,browserErrors}
}

function zipReport(outDir){
  const zipPath=join(outDir,'price-fill-evidence.zip')
  // zipfile from Python stdlib; never package browser profiles or site credentials.
  const code='import os,sys,zipfile\np=sys.argv[1];dst=os.path.join(p,"price-fill-evidence.zip")\nwith zipfile.ZipFile(dst,"w",zipfile.ZIP_DEFLATED) as z:\n for f in os.listdir(p):\n  q=os.path.join(p,f)\n  if os.path.isfile(q) and f!=os.path.basename(dst) and (f.endswith(".json") or f.endswith(".png")):\n   z.write(q,f)\nprint(dst)'
  for(const py of [process.env.PYTHON,'python','py'].filter(Boolean)){
    const p=spawnSync(py,['-c',code,outDir],{encoding:'utf8',timeout:15000})
    if(p.status===0 && existsSync(zipPath))return zipPath
  }
  return null
}

export async function runProbe(options) {
  const outDir=options.outputDir?resolve(options.outputDir):uniqueOutput(resolve('runtime/reports/mcel-worker-price-fill-probe'))
  mkdirSync(outDir,{recursive:true})
  const report={schema:'mcel.worker.price-playwright-isolation.v1',url:options.url,
    target:TARGET_PRICE,observeMs:options.observeMs,noSettingsWritesIntended:true,
    noWallet:true,noHubRegistration:true,noCaptainSpending:true,
    results:[],error:null}
  let browser
  try{
    const {chromium}=await import('@playwright/test')
    browser=await chromium.launch(probeBrowserLaunchOptions(options))
    for(const trial of probeTrials(options.includeRefresh)){
      const r=await probeOne(browser,options,trial,outDir)
      report.results.push(r)
      console.log('PRICE_FILL_PROBE_TRIAL',`name=${trial.id}`,
        `target-observed=${r.analysis.targetObserved}`,
        `reset=${r.analysis.resetAfterFill}`,
        `setter-resets=${r.analysis.jsResetCount}`,
        `replacements=${r.analysis.replacementCount}`,
        `settings-POST=${r.analysis.unexpectedSettingsPost}`,
        `error=${r.error?.class||'none'}`)
    }
  }catch(e){report.error={class:errorClass(e),code:String(e?.message||'').slice(0,350)}}
  finally{await browser?.close().catch(()=>{})}
  const reportPath=join(outDir,'price-fill-report.json')
  writeFileSync(reportPath,JSON.stringify(report,null,2)+'\n','utf8')
  const zip=zipReport(outDir)
  console.log('PRICE_FILL_PROBE_REPORT',reportPath)
  console.log('PRICE_FILL_PROBE_BUNDLE',zip||'unavailable (see report directory)')
  if(report.error)console.log('PRICE_FILL_PROBE_ERROR',report.error)
  const failed=!!report.error||report.results.some(r=>r.error||r.analysis?.unexpectedSettingsPost)
  return failed?1:0
}

if(process.argv[1] && resolve(process.argv[1])===resolve(fileURLToPath(import.meta.url))){
  try {const options=parseProbeArgs(process.argv.slice(2));process.exitCode=await runProbe(options)}
  catch(e){console.error('PRICE_FILL_PROBE_USAGE_ERROR',String(e.message||e));process.exitCode=2}
}
