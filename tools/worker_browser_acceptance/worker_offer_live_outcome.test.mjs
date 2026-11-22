import test from 'node:test'
import assert from 'node:assert/strict'
import {offerFailureCause, offerApiRejectionCategory, offerApiRejectionDetail, safeHubRegistrationStatus,
  assessOfferHubAcceptance, offerDeferralCause} from './worker_offer_live_outcome.mjs'

test('disabled seller is named without assuming offer signing authorizes a setting change',()=>{
  assert.equal(offerFailureCause('OFFER_SELLER_DISABLED_SETTINGS_AUTH_REQUIRED'),
    'seller-disabled-settings-authorization-required')
})

test('MCEL confirmation and registration failures remain distinct',()=>{
  assert.equal(offerFailureCause('Expected MCEL confirmation absent or duplicate'),
    'confirmation-missing-or-duplicate')
  assert.equal(offerFailureCause('OFFER_HUB_REGISTRATION_FAILED'), 'hub-registration-failed')
  assert.equal(offerFailureCause('OFFER_POSTCONDITION_UNVERIFIED'), 'hub-acceptance-unverified')
  assert.equal(offerFailureCause('MCEL_WORKER:registration-not-acknowledged'),
    'registration-not-acknowledged')
  assert.equal(offerFailureCause('irrelevant error',{httpStatus:400}),
    'offer-sync-http-rejected')
})

test('backend categorization never returns secret-bearing raw error text',()=>{
  assert.equal(offerApiRejectionCategory({error:'Signature failed for 0x1234123412341234123412341234123412341234'}),
    'wallet-auth')
  assert.equal(offerApiRejectionCategory({error:'hub-http-404: not_found'}),
    'hub-route-not-found')
  assert.equal(offerApiRejectionCategory({error:'private 123-secret other'}),
    'other-rejection')
  assert.equal(safeHubRegistrationStatus('failed'),'failed')
  assert.equal(safeHubRegistrationStatus('token=secret'),'unknown')
})

test('MSK rejection diagnostics distinguish omitted key, absent cache and wrong chain without echoing identifiers',()=>{
  const code=offerApiRejectionDetail({error:'An active Hub-scoped multi-session key is required to register an offer.'})
  assert.equal(code,'browser-key-id-missing')
  assert.equal(offerApiRejectionDetail({error:'No active saved multi-session key was found for this wallet and Hub.'}),
    'private-cache-no-active-key')
  assert.equal(offerApiRejectionDetail({error:'No active saved multi-session key with that id was found for this wallet and Hub.'}),
    'specified-key-not-active')
  assert.equal(offerApiRejectionDetail({error:'Worker multi-session key is for a different chain.'}),
    'key-chain-mismatch')
  assert.equal(offerApiRejectionDetail({error:'Unexpected 0xabcdef123456 secret code'}),'not-msk-specific')
})


test('real saved signed-connection acceptance does not require hub receipt status',()=>{
  const sync={status:'accepted',worker_id:'worker-verified-42',assigned_ring:'3'}
  const session={selected_network:'testnet',worker_id:'worker-verified-42',
    profile:{hub_url:'https://testnet-hub.greatlibrary.io'},
    signed_connection:{network:'testnet',hub_url:'https://testnet-hub.greatlibrary.io',
      status:'hub-registered',hub_registration_status:'accepted',hub_registered:true,
      worker_id:'worker-verified-42',assigned_ring:'3'},
    // Actual Hub receipt has ok=true and worker ID, but no status field.
    hub_registration:{ok:true,worker_id:'worker-verified-42',assigned_ring:'3'}}
  const got=assessOfferHubAcceptance(session,sync)
  assert.equal(got.hubStatus,'accepted')
  assert.equal(got.receiptVerified,true)
  assert.equal(got.verified,true)
  assert.equal(safeHubRegistrationStatus(session.hub_registration.status),'unknown')
})

test('HTTP 200 deferred offer cannot pass from a previously cached draft worker id',()=>{
  const s={selected_network:'testnet',worker_id:'draft-node-id',
    profile:{hub_url:'https://testnet-hub.greatlibrary.io'},
    signed_connection:{network:'testnet',hub_url:'https://testnet-hub.greatlibrary.io',
      hub_registration_status:'not_submitted',hub_registered:false,worker:{node_id:'draft-node-id'}}}
  const got=assessOfferHubAcceptance(s,{status:'deferred',reason:'Waiting for computer to be idle'})
  assert.equal(got.verified,false)
  assert.equal(got.serverWorkerIdPresent,false)
  assert.equal(offerDeferralCause('Waiting for computer to be idle. Windows reports an active interactive user session.'),'windows-not-idle')
  assert.equal(offerFailureCause('OFFER_SYNC_DEFERRED'),'offer-deferred-by-local-policy')
})

test('an accepted HTTP response is insufficient with wrong or stale Hub proof',()=>{
  const sync={status:'accepted',worker_id:'id-1',assigned_ring:'3'}
  const s={selected_network:'testnet',worker_id:'id-1',
    profile:{hub_url:'https://testnet-hub.greatlibrary.io'},
    signed_connection:{network:'testnet',hub_url:'https://testnet-hub.greatlibrary.io',
      hub_registration_status:'accepted',hub_registered:true,worker_id:'id-1',assigned_ring:'3'},
    hub_registration:{ok:true,worker_id:'id-1'}}
  assert.equal(assessOfferHubAcceptance(s,sync).verified,true)
  assert.equal(assessOfferHubAcceptance({...s,hub_registration:{ok:false,worker_id:'id-1'}},sync).verified,false)
  assert.equal(assessOfferHubAcceptance({...s,worker_id:'stale-draft-id'},sync).verified,false)
  assert.equal(assessOfferHubAcceptance({...s,signed_connection:{...s.signed_connection,hub_url:'https://other-hub.example'}},sync).verified,false)
  assert.equal(assessOfferHubAcceptance({...s,signed_connection:{...s.signed_connection,hub_registered:false}},sync).verified,false)
  assert.equal(assessOfferHubAcceptance(s,{...sync,worker_id:'different'}).verified,false)
  assert.equal(assessOfferHubAcceptance(s,{...sync,status:'pending'}).verified,false)
})
