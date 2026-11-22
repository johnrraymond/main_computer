// Test-only active-URL verification. Only fixed, recognized extension routes
// are emitted: never copy arbitrary extension URLs, queries or page content.
import {screenLabel, verifyWalletHomeIdentity} from './metamask_onboarding.mjs'

const ID=/^[a-p]{32}$/
export function safeMetaMaskRoute(raw,extensionId) {
  if (!ID.test(extensionId||'')) return {route:'invalid-extension',url:'unverified'}
  const base=`chrome-extension://${extensionId}/home.html`
  if (raw===base || raw===`${base}#/`) return {route:'home-root',url:`${base}#/`}
  if (raw===`${base}#/unlock` || raw===`${base}#/onboarding/unlock`)
    return {route:'unlock',url:raw}
  if (typeof raw==='string' && raw.startsWith(`chrome-extension://${extensionId}/`))
    return {route:'other-extension-route',url:'unverified'}
  return {route:'not-verified-metamask-extension',url:'unverified'}
}

export async function inspectMetaMaskActiveUrl(context,extensionId,expectedAddress,{
  activate=false, verifyHome=verifyWalletHomeIdentity,
  classifyScreen=screenLabel,
}={}) {
  if (!ID.test(extensionId||'') || !context || typeof context.pages!=='function' ||
      !/^0x[0-9a-f]{40}$/i.test(expectedAddress||''))
    throw new Error('MetaMask URL inspection refused: invalid test identity')
  const base=`chrome-extension://${extensionId}/home.html#/`
  const candidates=[]
  const otherHomeTabs=[]
  const pages=context.pages()
  for(let i=0;i<pages.length;i++) {
    const page=pages[i]
    let raw=''
    try {raw=page.url()} catch {}
    const route=safeMetaMaskRoute(raw,extensionId)
    // A *different* extension home may be the tab visible in Chrome. Report
    // its exact URL only if it is one of the three whitelisted safe routes.
    // An unknown hash/query is never persisted or displayed.
    if(route.route==='not-verified-metamask-extension') {
      const match=typeof raw==='string' && raw.match(
        /^chrome-extension:\/\/([a-p]{32})\/home\.html(?:#\/|#\/unlock|#\/onboarding\/unlock)?$/)
      if(match && match[1]!==extensionId && otherHomeTabs.length<4) {
        let visible=false
        try{visible=await page.evaluate(()=>document.visibilityState==='visible')}catch{}
        otherHomeTabs.push({url:raw,visible})
      }
      continue
    }
    if(route.route==='invalid-extension') continue
    let screen='unavailable',identityMatched=false,visibility='unavailable',focused=false
    if(route.route==='home-root' || route.route==='unlock') {
      try{screen=await classifyScreen(page)}catch{}
      if(route.route==='home-root' && screen==='wallet-home') {
        try{await verifyHome(page,expectedAddress,extensionId);identityMatched=true}catch{}
      }
    }
    try {
      const state=await page.evaluate(()=>({visibility:document.visibilityState,
        focused:document.hasFocus()}))
      visibility=state?.visibility==='visible'?'visible':state?.visibility==='hidden'?'hidden':'unknown'
      focused=state?.focused===true
    }catch{}
    candidates.push({page,index:i,route:route.route,url:route.url,screen,identityMatched,visibility,focused})
  }
  // Never select an arbitrary open extension tab as an authorization target.
  // Prefer a verified home, then a real unlock route, then a home that still
  // needs its expected wallet identity established.
  const selected=candidates.find(c=>c.identityMatched) ||
    candidates.find(c=>c.route==='unlock' && c.screen==='wallet-unlock') ||
    candidates.find(c=>c.route==='home-root') || null
  if(activate && selected) {
    await selected.page.bringToFront()
    // Re-read after activation. The visible tab can navigate during React
    // hydration; the URL BEFORE bringToFront is not proof of the active URL.
    const observed=safeMetaMaskRoute(selected.page.url(),extensionId)
    selected.route=observed.route;selected.url=observed.url
    // Activation can finish hydration or navigate unlock -> wallet home.
    // Reclassify the current route and its public wallet identity rather than
    // certifying a stale pre-activation screen/identity observation.
    selected.screen='unavailable';selected.identityMatched=false
    if(selected.route==='home-root' || selected.route==='unlock') {
      try{selected.screen=await classifyScreen(selected.page)}catch{}
      if(selected.route==='home-root' && selected.screen==='wallet-home') {
        try{await verifyHome(selected.page,expectedAddress,extensionId);selected.identityMatched=true}catch{}
      }
    }
    try {
      const state=await selected.page.evaluate(()=>({visibility:document.visibilityState,
        focused:document.hasFocus()}))
      selected.visibility=state?.visibility==='visible'?'visible':state?.visibility==='hidden'?'hidden':'unknown'
      selected.focused=state?.focused===true
    }catch{selected.visibility='unavailable';selected.focused=false}
  }
  return {
    selectedPage:selected?.page||null,
    evidence:{expectedUrl:base,actualUrl:selected?.url||'no-recognized-home-tab',
      selectedRoute:selected?.route||'none',selectedScreen:selected?.screen||'none',
      selectedIndex:selected?.index??-1,tabCount:candidates.length,
      otherHomeTabs,
      walletIdentityMatched:selected?.identityMatched===true,
      visibility:selected?.visibility||'unavailable',focused:selected?.focused===true,
      tabs:candidates.slice(0,12).map(c=>({index:c.index,route:c.route,screen:c.screen,
        walletIdentityMatched:c.identityMatched,visibility:c.visibility,focused:c.focused}))}
  }
}

export function requireVerifiedMetaMaskUrl(evidence,{requireHome=false,requireForeground=false}={}) {
  const accepted=requireHome?['home-root']:['home-root','unlock']
  // Check the actual selected extension target's exact, allowlisted route.
  // document.visibilityState and hasFocus() describe OS/window scheduling,
  // not whether Playwright can operate a responsive MetaMask tab. They are
  // useful evidence, but requiring them BEFORE unlock causes false failures.
  if (!accepted.includes(evidence?.selectedRoute))
    throw new Error('METAMASK_ACTIVE_URL_UNVERIFIED')
  if (requireForeground && (evidence?.visibility!=='visible' || !evidence.focused))
    throw new Error('METAMASK_FOREGROUND_UNVERIFIED')
  if (requireHome && (!evidence.walletIdentityMatched || evidence.selectedScreen!=='wallet-home'))
    throw new Error('METAMASK_HOME_IDENTITY_UNVERIFIED')
}
