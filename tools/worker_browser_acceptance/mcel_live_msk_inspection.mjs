// Read-only interpretation of the native Worker's wallet/MSK disclosure.
// Do not read or report key IDs, signatures, provider payloads or key material.
export function classifyNativeMskInspection({
  panelPresent=false, panelVisible=false,
  stateCount=0, requestCount=0, revokeCount=0, stateText=''
}={}) {
  if (!panelPresent) return 'PANEL_MISSING'
  if (!panelVisible) return 'PANEL_HIDDEN'
  if (stateCount!==1 || requestCount!==1 || revokeCount!==1)
    return 'CONTROLS_MISSING_OR_DUPLICATED'
  const state=String(stateText).trim()
  if (state==='Active' || state==='Active server-side key')
    return 'PREEXISTING_ACTIVE'
  if (state==='No active key') return 'NO_ACTIVE_KEY'
  if (!state) return 'STATE_EMPTY'
  return 'STATE_UNRECOGNIZED'
}
