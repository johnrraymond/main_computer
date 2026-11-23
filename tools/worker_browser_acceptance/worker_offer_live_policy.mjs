// Plan a strictly bounded live-acceptance policy change.  Never fake Windows
// idleness or bypass the server's real local-AI availability check.
export function planOfferAcceptancePolicy(settings, settingsAuthorized) {
  if(!settings || typeof settings.sellerEnabled!=='boolean')
    throw Error('OFFER_POLICY_UNAVAILABLE')
  const originalMode=settings.sellerAvailabilityMode
  if(!['totally_idle','ai_idle'].includes(originalMode))
    throw Error('OFFER_AVAILABILITY_MODE_UNVERIFIED')
  const modeForTest=settingsAuthorized && originalMode==='totally_idle'
    ? 'ai_idle' : originalMode
  return Object.freeze({
    originalEnabled:settings.sellerEnabled,
    originalMode,
    modeForTest,
    changeEnabled:settingsAuthorized && !settings.sellerEnabled,
    changeMode:modeForTest!==originalMode,
    // The gate may still legitimately defer if local AI capacity is busy.
    // This plan NEVER claims the backend will accept the registration.
    requiresChange:settingsAuthorized && (!settings.sellerEnabled || modeForTest!==originalMode),
  })
}
