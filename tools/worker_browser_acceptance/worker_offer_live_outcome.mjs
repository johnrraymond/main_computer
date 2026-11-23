// Bounded diagnostic vocabulary for native Worker offer-registration failures.
// Never return raw backend, provider, wallet, or browser exception text.
export function offerFailureCause(message, offer={}) {
  const text=typeof message==='string'?message:''
  const status=offer.hubRegistrationStatus
  if(text==='OFFER_SELLER_DISABLED_SETTINGS_AUTH_REQUIRED')return 'seller-disabled-settings-authorization-required'
  if(text==='OFFER_REGISTRATION_BUTTON_NOT_READY')return 'register-button-not-ready'
  if(text==='OFFER_HUB_REGISTRATION_FAILED' || status==='failed')return 'hub-registration-failed'
  if(text==='OFFER_POSTCONDITION_UNVERIFIED')return 'hub-acceptance-unverified'
  if(text==='OFFER_SYNC_DEFERRED')return 'offer-deferred-by-local-policy'
  if(text==='OFFER_SYNC_NOT_ACCEPTED')return 'offer-sync-not-accepted'
  if(text==='OFFER_SYNC_HTTP_REJECTED')return 'offer-sync-http-rejected'
  if(text==='OFFER_SYNC_TRANSPORT_FAILED')return 'offer-sync-transport-failed'
  if(text==='OFFER_SYNC_REQUEST_NOT_OBSERVED')return 'offer-sync-request-not-observed'
  if(text==='OFFER_SYNC_RESPONSE_NOT_OBSERVED')return 'offer-sync-response-not-observed'
  if(text==='Expected MCEL confirmation absent or duplicate')return 'confirmation-missing-or-duplicate'
  if(text==='Unexpected MCEL confirmation refused')return 'unexpected-confirmation'
  if(text.startsWith('MCEL_WORKER:seller-not-enabled'))return 'seller-not-enabled'
  if(text.startsWith('MCEL_WORKER:matching-connected-marketplace-session-required'))return 'marketplace-session-mismatch'
  if(text.startsWith('MCEL_WORKER:legacy-marketplace-authority-not-initialized'))return 'legacy-registration-bridge-missing'
  if(text.startsWith('MCEL_WORKER:marketplace-operation-in-flight'))return 'marketplace-operation-busy'
  if(text.startsWith('MCEL_WORKER:registration-not-acknowledged'))return 'registration-not-acknowledged'
  if(text.startsWith('Wallet/Hub session mismatch'))return 'wallet-hub-session-mismatch'
  if(text.startsWith('MCEL_WORKER:POST:'))return 'local-worker-post-rejected'
  if(text.includes('Testnet Hub did not accept signed registration'))return 'hub-acceptance-unverified'
  if(Number.isInteger(offer.httpStatus) && offer.httpStatus>=400)return 'offer-sync-http-rejected'
  if(offer.transportFailed===true)return 'offer-sync-transport-failed'
  return 'offer-operation-unclassified'
}

export function offerApiRejectionCategory(body) {
  const value=String(body?.error||body?.reason||'').toLowerCase()
  if(!value)return 'none'
  if(/multisession|multi.session|\bmsk\b|key.inactive/.test(value))return 'msk-authorization'
  if(/network|hub.url|cluster|chain/.test(value))return 'hub-network-context'
  if(/ring/.test(value))return 'ring-mismatch'
  if(/wallet|signature|signer/.test(value))return 'wallet-auth'
  if(/seller|availability|policy/.test(value))return 'seller-policy'
  if(/timeout|unreachable|connection|websocket|refused/.test(value))return 'hub-connection'
  if(/price|credit|funding/.test(value))return 'pricing-or-credit'
  if(/validation|missing|required|invalid/.test(value))return 'invalid-registration-payload'
  if(/not_found|404/.test(value))return 'hub-route-not-found'
  return 'other-rejection'
}

// Report why a backend MSK rejection occurred without exposing IDs or raw
// wallet-signed payloads. This is an allowlisted vocabulary, not error echoing.
export function offerApiRejectionDetail(body) {
  const text=String(body?.error||body?.reason||'').toLowerCase()
  // Stable Hub's live WebSocket sends a bounded reason_code when it rejects
  // worker.auth. Preserve ONLY exact known codes, never the associated raw
  // user_message, wallet, key ID, or socket data.
  const hubCode=/\breason_code\s*=\s*([a-z0-9_-]{1,80})\b/.exec(text)?.[1]
  const codes={
    key_not_active:'hub-key-not-active',key_expired:'hub-key-expired',
    key_lifetime_invalid:'hub-key-lifetime-invalid',
    missing_multisession_authorization:'hub-key-authorization-missing',
    missing_multisession_key_id:'hub-key-id-missing',
    unsupported_chain_id:'hub-unsupported-chain',
    record_chain_id_mismatch:'hub-record-chain-mismatch',
    chain_id_mismatch:'hub-authorization-chain-mismatch',
    key_wallet_mismatch:'hub-key-wallet-mismatch',
    stored_key_wallet_invalid:'hub-key-record-invalid',
    bad_wallet_address:'hub-wallet-invalid',
  }
  if(hubCode && Object.hasOwn(codes,hubCode))return codes[hubCode]
  if(text.includes('hub rejected worker.auth'))return 'hub-auth-rejected-unclassified'
  if(text.includes('active hub-scoped multi-session key is required'))return 'browser-key-id-missing'
  if(text.includes('no active saved multi-session key with that id'))return 'specified-key-not-active'
  if(text.includes('no active saved multi-session key'))return 'private-cache-no-active-key'
  if(text.includes('multi-session key is for a different chain'))return 'key-chain-mismatch'
  if(/multisession|multi.session|\bmsk\b/.test(text))return 'other-msk-rejection'
  return 'not-msk-specific'
}

export function safeHubRegistrationStatus(status) {
  return ['not_submitted','submitting','accepted','failed','stale'].includes(status)?status:'unknown'
}

// The Hub registration receipt returned by /offer-sync is not a Hub registration
// *status*. It typically has {ok:true,worker_id,assigned_ring} but no status.
// The authoritative accepted status is saved on signed_connection by the
// backend after a real Hub acknowledgement. Never infer success from HTTP 200,
// a draft node_id, or a stale worker_id alone.
export function assessOfferHubAcceptance(session, offerSync) {
  const s=session && typeof session==='object' ? session : {}
  const signed=s.signed_connection && typeof s.signed_connection==='object' ? s.signed_connection : {}
  const receipt=s.hub_registration && typeof s.hub_registration==='object' ? s.hub_registration : {}
  const returned=offerSync && typeof offerSync==='object' ? offerSync : {}
  const syncStatus=['accepted','deferred','failed','pending','unchanged'].includes(returned.status)
    ? returned.status : 'unknown'
  const hubStatus=safeHubRegistrationStatus(signed.hub_registration_status || receipt.status)
  const issuedId=typeof returned.worker_id==='string' ? returned.worker_id.trim() : ''
  const signedId=typeof signed.worker_id==='string' ? signed.worker_id.trim() : ''
  const networkMatch=s.selected_network==='testnet' && signed.network==='testnet'
  const hubUrl=typeof s.profile?.hub_url==='string' ? s.profile.hub_url.replace(/\/+$/,'') : ''
  const signedUrl=typeof signed.hub_url==='string' ? signed.hub_url.replace(/\/+$/,'') : ''
  const hubMatch=Boolean(hubUrl && signedUrl && hubUrl===signedUrl)
  const ringMatch=typeof returned.assigned_ring==='string' &&
    returned.assigned_ring!=='' && returned.assigned_ring===signed.assigned_ring
  const workerMatch=Boolean(issuedId && signedId && issuedId===signedId && s.worker_id===signedId)
  const receiptVerified=receipt.ok===true &&
    (typeof receipt.worker_id!=='string' || receipt.worker_id===issuedId)
  const verified=syncStatus==='accepted' && hubStatus==='accepted' &&
    signed.hub_registered===true && receiptVerified &&
    networkMatch && hubMatch && ringMatch && workerMatch
  return {syncStatus,hubStatus,registered:signed.hub_registered===true,
    networkMatch,hubMatch,workerMatch,ringMatch,receiptVerified,
    serverWorkerIdPresent:Boolean(signedId),verified}
}

// Classify deferral reasons without printing raw runtime policy text.
export function offerDeferralCause(reason) {
  const value=String(reason||'').toLowerCase()
  if(/windows|interactive user|quser|computer.*idle/.test(value))return 'windows-not-idle'
  if(/ai.*busy|ai.*idle|capacity/.test(value))return 'local-ai-not-idle'
  if(/jobs.*finish|active.*job/.test(value))return 'active-worker-job'
  if(/availability.*changed/.test(value))return 'availability-changed'
  if(!value)return 'unspecified'
  return 'other-policy'
}
