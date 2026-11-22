// Non-secret, bounded failure telemetry for the native live wallet gate.
// No extension DOM, addresses, key IDs, URLs, raw provider events, exception
// bodies, console text or signature payloads are copied into evidence.
const events = new Set([
  'connect.ethers.requestAccounts.resolved','connect.ethers.requestAccounts.start',
  'connect.ethers.switchChain.done','connect.ethers.switchChain.start',
  'connect.ethers.switchChain.unknownChain','connect.failed',
  'connect.failed.multiple-metamask-providers','connect.failed.multiple-wallet-providers',
  'connect.finalized.connected','connect.ignored.busy',
  'connect.wallet.addChain.done','connect.wallet.addChain.start',
  'connect.wallet.addChain.symbolMismatchFallback',
  'connect.wallet.networkPreflight.start',
  'connect.wallet.postAccountPreflight.recheck',
  'connect.wallet.postAccountPreflight.reused',
  'connect.wallet.postAccountPreflight.wait',
  'connect.wallet.rpcProof.backoffCleared',
  'connect.wallet.rpcProof.backoffWait',
  'connect.wallet.rpcProof.done',
  'provider.accountsChanged.refresh','provider.chainChanged.refresh',
  'provider.hydrate.connected','provider.hydrate.failed',
  'provider.hydrate.ignored.busy','provider.hydrate.networkPreflight.done',
  'provider.hydrate.networkPreflight.start','provider.hydrate.no-account',
  'provider.hydrate.rpc-needs-repair','provider.hydrate.start',
  'provider.hydrate.wrong-chain','provider.refresh','provider.refresh.failed',
  'provider.refresh.rpc-needs-repair',
])
const phases = new Set([
  'control-ready','click-dispatched','activity-observed','already-connected',
  'network-preflight','network-update-requested','rpc-ready','rpc-backoff',
  'network-switch-requested','account-requested','account-approved',
  'finalizing','connected','connect-failed',
  'waiting-after-network-add','approval-observed',
])
const statuses = new Set(['none','network-preflight','network-update-requested',
  'rpc-ready','rpc-backoff','network-switch-requested','account-requested',
  'account-approved','finalizing','connected','connect-failed','other'])
const choices = new Set(['none','mainnet','testnet','test','dev','slh'])
const buttonStates = new Set(['idle','hydrating','requesting','finalizing','connected','other'])
const targetTypes = new Set(['page','other','background_page','service_worker'])
const targetKinds = new Set(['notification','sidepanel','popup','home','other-extension'])
const kinds = new Set(['start','mutation','event','milestone','end'])
const yes = value => value === true

export function safeWalletLifecycleEvent(value) {
  return typeof value === 'string' && events.has(value) ? value : null
}
export function safeWalletProgress(value) {
  if(typeof value !== 'string' || !value.startsWith('worker.') && !value.startsWith('metamask.')) return null
  if(value.startsWith('worker.connect.')) {
    const phase=value.slice('worker.connect.'.length)
    if(phases.has(phase))return 'worker.connect.'+phase
  }
  if(value.startsWith('worker.status.')) {
    const phase=value.slice('worker.status.'.length)
    if(phases.has(phase))return 'worker.status.'+phase
  }
  if(value.startsWith('worker.') && safeWalletLifecycleEvent(value.slice('worker.'.length)))return value
  // Approved request category only, not extension labels or DOM text.
  if(/^metamask\.(?:network-add|network-switch|account-connect|account-select)$/i.test(value)) return value.toLowerCase()
  return null
}
export function safeWalletDiagnosticSnapshot(row={}) {
  const x=row&&typeof row==='object'?row:{}
  return {
    button:buttonStates.has(x.button)?x.button:'other',
    disabled:yes(x.disabled),busy:yes(x.busy),handlerBound:yes(x.handlerBound),
    panelVisible:yes(x.panelVisible),contextVerified:yes(x.contextVerified),
    hubChoice:choices.has(x.hubChoice)?x.hubChoice:'unverified',
    status:statuses.has(x.status)?x.status:'other',
  }
}
export function safeWalletDiagnosticTimeline(rows) {
  if(!Array.isArray(rows))return []
  return rows.slice(-60).flatMap(raw=>{
    if(!raw||typeof raw!=='object'||!kinds.has(raw.kind))return []
    const ms=Number.isInteger(raw.elapsedMs)&&raw.elapsedMs>=0&&raw.elapsedMs<=600000?raw.elapsedMs:0
    const value=raw.kind==='event'?safeWalletLifecycleEvent(raw.value):
      raw.kind==='milestone'?safeWalletProgress(raw.value):null
    if((raw.kind==='event'||raw.kind==='milestone')&&!value)return []
    return [{kind:raw.kind,elapsedMs:ms,
      ...(value?{value}:{}),
      ...(raw.kind==='mutation'||raw.kind==='start'||raw.kind==='end'
        ?{state:safeWalletDiagnosticSnapshot(raw.state)}:{})}]
  })
}
export function safeWalletExtensionEvidence(row={}) {
  const pages=Array.isArray(row?.pages)?row.pages:[]
  const targets=Array.isArray(row?.cdpExtensionTargets)?row.cdpExtensionTargets:[]
  return {
    pages:pages.length,
    pageStates:pages.slice(0,12).map(x=>({
      kind:targetKinds.has(x?.kind)?x.kind:'other-extension',
      networkAdd:yes(x?.networkAdd),networkSwitch:yes(x?.networkSwitch),
      accountConnect:yes(x?.accountConnect),originMatched:yes(x?.originMatched),rpcMatched:yes(x?.rpcMatched)
    })),
    targets:targets.slice(0,12).map(x=>({
      kind:targetKinds.has(x?.kind)?x.kind:'other-extension',
      type:targetTypes.has(x?.type)?x.type:'other'
    }))
  }
}
export function safeWalletBackendSession(row={}) {
  return {
    httpOk:yes(row?.httpOk),
    selectedNetwork:choices.has(row?.selectedNetwork)?row.selectedNetwork:'unverified',
    status:new Set(['connected','disconnected','connecting','failed']).has(row?.status)?row.status:'unverified',
    ring:new Set(['1','2','3']).has(String(row?.ring))?String(row.ring):'unverified'
  }
}
