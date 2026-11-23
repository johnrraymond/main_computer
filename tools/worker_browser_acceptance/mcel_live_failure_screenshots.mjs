// Failure-only visual evidence for the real MCEL Worker acceptance harness.
// Capture BEFORE browser cleanup. Files remain on this computer and are never
// embedded in the JSON receipt, printed as base64, or sent to the Hub.
// Screenshot pixels may contain private information: treat files as sensitive.
import {existsSync, mkdirSync, mkdtempSync, statSync, writeFileSync} from 'node:fs'
import {basename, dirname, join, posix} from 'node:path'
import {spawn, spawnSync} from 'node:child_process'

const EXTENSION_ID = /^[a-p]{32}$/
const PAGE_NAMES = new Map([
  ['/notification.html','notification'],
  ['/popup.html','popup'],
  ['/sidepanel.html','sidepanel'],
  ['/home.html','home'],
])
const VERSION = 'failure-screens-v1'
const MOMENT_VERSION = 'msk-moment-screens-v1'
const MOMENT_PHASES = new Set(['request-start','approval-target','request-failed','revoke-start','revoke-approval-target','revoke-failed',
  'offer-confirmed','offer-http-failed','offer-hub-failed','policy-before','policy-failed','offer-price-failed'])

function savedPng(path) {
  try { return existsSync(path) && statSync(path).isFile() && statSync(path).size > 64 }
  catch { return false }
}

// This command is shared by final and live-moment desktop captures.
const desktopCommand = String.raw`
$ErrorActionPreference='Stop'
try {
  Add-Type -AssemblyName System.Windows.Forms
  Add-Type -AssemblyName System.Drawing
  $bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
  if ($bounds.Width -lt 1 -or $bounds.Height -lt 1) { exit 2 }
  $bitmap = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  try {
    $graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bitmap.Size)
    $bitmap.Save($env:MC_WORKER_FAILURE_SCREENSHOT_PATH, [System.Drawing.Imaging.ImageFormat]::Png)
  } finally { $graphics.Dispose(); $bitmap.Dispose() }
  exit 0
} catch { exit 1 }
`
function captureWindowsDesktop(path) {
  if (process.platform !== 'win32') return 'unsupported'
  const outcome = spawnSync('powershell.exe', [
    '-NoProfile','-NonInteractive','-EncodedCommand',Buffer.from(desktopCommand,'utf16le').toString('base64'),
  ],{env:{...process.env,MC_WORKER_FAILURE_SCREENSHOT_PATH:path},
    windowsHide:true,stdio:'ignore',timeout:12000})
  return outcome.status===0 && savedPng(path) ? 'saved' : 'unavailable'
}

function eligibleExtensionPage(page, extensionId) {
  if (!EXTENSION_ID.test(extensionId||'') || !page || page.isClosed?.()) return null
  try {
    const url = new URL(page.url())
    if (url.protocol!=='chrome-extension:' || url.hostname!==extensionId) return null
    return PAGE_NAMES.get(url.pathname)||null
  } catch {return null}
}

function safeGate(gate) {
  return ['wallet-msk','offer-registration','work-cycle','requester-enforcement'].includes(gate)
    ?gate:'unknown'
}

/**
 * Capture an actual visible Windows desktop, the Worker page, and all reachable
 * known MetaMask UI pages (no arbitrary browser tabs). This is evidence only;
 * screenshot errors must never turn a failed gate into PASS or hide its cause.
 *
 * `desktopCapture` is injectable for an isolated test on non-Windows hosts.
 */
export async function captureLiveFailureScreenshots({
  context, workerPage, reportPath, extensionId, gate, desktopCapture=captureWindowsDesktop,
}={}) {
  const result={version:VERSION, captures:[]}
  if (typeof reportPath!=='string' || !reportPath) {
    result.captures.push({surface:'storage',status:'unavailable'})
    return result
  }
  let directory
  try {
    mkdirSync(dirname(reportPath),{recursive:true})
    directory=mkdtempSync(join(dirname(reportPath),`live-${safeGate(gate)}-failure-`),{encoding:'utf8'})
  } catch {
    result.captures.push({surface:'storage',status:'unavailable'})
    return result
  }
  const name=basename(directory)
  const capture=async(surface,filename,fn)=>{
    const absolute=join(directory,filename)
    let status='unavailable'
    try {status=await fn(absolute)} catch {status='unavailable'}
    status=status==='saved' && savedPng(absolute)?'saved':'unavailable'
    result.captures.push({surface,status,...(status==='saved'
      ?{file:posix.join(name,filename)}:{})})
  }
  await capture('desktop','desktop.png',desktopCapture)
  if (workerPage && !workerPage.isClosed?.()) {
    await capture('worker','worker.png',async path=>{
      await workerPage.screenshot({path,timeout:6000,animations:'disabled',caret:'hide'})
      return 'saved'
    })
  } else result.captures.push({surface:'worker',status:'unavailable'})
  let pages=[]
  try {pages=context?.pages?.()||[]} catch {pages=[]}
  const extensionPages=pages.map(page=>({page,kind:eligibleExtensionPage(page,extensionId)}))
    .filter(item=>Boolean(item.kind))
    .sort((a,b)=>['notification','popup','sidepanel','home'].indexOf(a.kind)-
      ['notification','popup','sidepanel','home'].indexOf(b.kind)).slice(0,6)
  const counts={}
  for (const {page,kind} of extensionPages) {
    counts[kind]=(counts[kind]||0)+1
    const filename=`metamask-${kind}-${counts[kind]}.png`
    await capture('metamask-'+kind,filename,async path=>{
      // This does not click, approve, or change the selected MetaMask account.
      await page.screenshot({path,timeout:5000,animations:'disabled',caret:'hide'})
      return 'saved'
    })
  }
  if(!extensionPages.length)result.captures.push({surface:'metamask',status:'not-exposed'})
  return result
}


// Unlike the final catch-handler screenshots, these capture the live UI
// *while* the operation is still in progress. A signature prompt can disappear
// before the approval poll times out; the final catch snapshot is then too late.
async function captureWindowsDesktopWhileLive(path) {
  if (process.platform !== 'win32') return 'unsupported'
  return await new Promise(resolve => {
    let child, settled = false
    const done = status => {if (!settled) {settled = true;clearTimeout(timer);resolve(status)}}
    let timer
    try {
      child=spawn('powershell.exe',['-NoProfile','-NonInteractive','-EncodedCommand',
        Buffer.from(desktopCommand,'utf16le').toString('base64')],{
        env:{...process.env,MC_WORKER_FAILURE_SCREENSHOT_PATH:path},
        windowsHide:true,stdio:'ignore'})
      timer=setTimeout(()=>{try{child.kill()}catch{};done('unavailable')},9000)
      child.once('error',()=>done('unavailable'))
      child.once('close',code=>done(code===0 && savedPng(path)?'saved':'unavailable'))
    } catch {done('unavailable')}
  })
}

const ELIGIBLE_CDP_PATHS = new Map([
  ['/notification.html','notification'],['/popup.html','popup'],
  ['/sidepanel.html','sidepanel'],['/side-panel.html','sidepanel'],
  ['/home.html','home'],
])

// A MetaMask side panel may be a Chrome DevTools target but NOT a Playwright
// Page. Capture its pixels through CDP without changing focus or clicking.
async function captureCdpTargets(context,workerPage,extensionId,save) {
  if (!EXTENSION_ID.test(extensionId||'') || !context || !workerPage ||
      typeof context.newCDPSession !== 'function') return
  let root
  try {
    root=await context.newCDPSession(workerPage)
    const result=await root.send('Target.getTargets')
    const targets=(result?.targetInfos||[]).filter(target=>{
      if(!['page','other'].includes(target.type))return false
      try {
        const url=new URL(target.url)
        return url.protocol==='chrome-extension:' && url.hostname===extensionId &&
          ELIGIBLE_CDP_PATHS.has(url.pathname)
      }catch{return false}
    }).slice(0,5)
    for(let n=0;n<targets.length;n++){
      const target=targets[n]
      const kind=ELIGIBLE_CDP_PATHS.get(new URL(target.url).pathname)
      await save('metamask-cdp-'+kind,`metamask-cdp-${kind}-${n+1}.png`,async path=>{
        let sessionId
        try {
          sessionId=(await root.send('Target.attachToTarget',
            {targetId:target.targetId,flatten:false})).sessionId
          if(!sessionId) return 'unavailable'
          let seq=0
          const nestedSend=async(method,params={})=>await new Promise((resolve,reject)=>{
            const id=++seq
            let timeout
            const finish=()=>{clearTimeout(timeout);root.off('Target.receivedMessageFromTarget',receive)}
            const receive=event=>{
              if(event.sessionId!==sessionId)return
              let msg
              try{msg=JSON.parse(event.message)}catch{return}
              if(msg.id!==id)return
              finish()
              if(msg.error)reject(Error('cdp-rejected'))
              else resolve(msg.result||{})
            }
            root.on('Target.receivedMessageFromTarget',receive)
            timeout=setTimeout(()=>{finish();reject(Error('cdp-timeout'))},2500)
            root.send('Target.sendMessageToTarget',{
              sessionId,message:JSON.stringify({id,method,params}),
            }).catch(()=>{finish();reject(Error('cdp-unavailable'))})
          })
          await nestedSend('Page.enable')
          const shot=await nestedSend('Page.captureScreenshot',
            {format:'png',captureBeyondViewport:false,fromSurface:true})
          if(typeof shot.data!=='string' || shot.data.length<100)return 'unavailable'
          writeFileSync(path,Buffer.from(shot.data,'base64'),{mode:0o600})
          return 'saved'
        } finally {
          if(sessionId)await root.send('Target.detachFromTarget',{sessionId}).catch(()=>{})
        }
      })
    }
  } catch { // A vanished target must never mask the original signing failure.
  } finally {await root?.detach?.().catch(()=>{})}
}

/**
 * Three bounded, event-time checkpoints. No DOM text, wallet payload,
 * signature or address is serialized to the evidence JSON or console.
 * The local PNGs are sensitive and should be reviewed before sharing.
 */
export async function captureLiveMskMoment({context,workerPage,reportPath,extensionId,
  gate,phase,desktopCapture=captureWindowsDesktopWhileLive}={}) {
  const result={version:MOMENT_VERSION,phase,captures:[]}
  if (!MOMENT_PHASES.has(phase) || typeof reportPath!=='string' || !reportPath)
    return {...result,captures:[{surface:'storage',status:'unavailable'}]}
  let directory
  try{
    mkdirSync(dirname(reportPath),{recursive:true})
    directory=mkdtempSync(join(dirname(reportPath),`live-${safeGate(gate)}-moment-${phase}-`))
  }catch{return {...result,captures:[{surface:'storage',status:'unavailable'}]}}
  const folder=basename(directory)
  const save=async(surface,filename,fn)=>{
    const path=join(directory,filename)
    let status='unavailable'
    try{status=await fn(path)}catch{}
    status=status==='saved' && savedPng(path)?'saved':'unavailable'
    result.captures.push({surface,status,...(status==='saved'?{file:posix.join(folder,filename)}:{})})
  }
  // Launch desktop capture immediately, without blocking the event loop.
  // CDP screenshots start while the Windows child captures the visible screen.
  const desktop=save('desktop','desktop.png',desktopCapture)
  if(workerPage && !workerPage.isClosed?.()){
    let pages=[]
    try {pages=context?.pages?.()||[]} catch {}
    const known=pages.map(page=>({page,kind:eligibleExtensionPage(page,extensionId)}))
      .filter(x=>x.kind).slice(0,4)
    const counts={}
    const exposed=known.map(({page,kind})=>{
      counts[kind]=(counts[kind]||0)+1
      return save('metamask-'+kind,`metamask-${kind}-${counts[kind]}.png`,async path=>{
        await page.screenshot({path,timeout:2500,animations:'disabled',caret:'hide'})
        return 'saved'
      })
    })
    await Promise.allSettled([
      save('worker','worker.png',async path=>{
        await workerPage.screenshot({path,timeout:2500,animations:'disabled',caret:'hide'})
        return 'saved'
      }),
      captureCdpTargets(context,workerPage,extensionId,save),
      ...exposed,
    ])
  }
  await desktop
  return result
}


// Apply the same safe event-time screenshot mechanism to offer registration.
// Keep its evidence distinct from signing; no exception or payload text is read.
export async function captureLiveOfferMoment(args={}) {
  const phase=args.phase
  if(!['offer-confirmed','offer-http-failed','offer-hub-failed','policy-before','policy-failed','offer-price-failed'].includes(phase))
    return {version:'offer-moment-screens-v1',phase,captures:[{surface:'storage',status:'unavailable'}]}
  const evidence=await captureLiveMskMoment(args)
  return {...evidence,version:'offer-moment-screens-v1'}
}
