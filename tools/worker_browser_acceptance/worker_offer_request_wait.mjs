// The MCEL confirmation dialog closes before its asynchronous marketplace
// operation finishes. A saved Hub `failed` status can belong to an earlier
// attempt; it cannot certify failure of the operation just confirmed.
// Wait for a request actually observed by the browser before considering
// Hub registration or its saved state. This module cannot issue a request.
export async function waitForFreshOfferRequest({requestSeen, transportFailed,
  sleep, now=Date.now, timeoutMs=30000, pollMs=300}) {
  const end=now()+timeoutMs
  while(now()<end){
    if(transportFailed())throw Error('OFFER_SYNC_TRANSPORT_FAILED')
    if(requestSeen())return true
    await sleep(pollMs)
  }
  if(transportFailed())throw Error('OFFER_SYNC_TRANSPORT_FAILED')
  if(requestSeen())return true
  throw Error('OFFER_SYNC_REQUEST_NOT_OBSERVED')
}

// A new registration attempt must own the POST before its response can be
// used as evidence. Ignore requests from settings saves or previous attempts,
// and ignore late responses to those requests even after the test is armed.
export function createOfferSyncTracker() {
  let armed=false
  const requests=new Set()
  const responses=[]
  const matches=request=>{
    try{return new URL(request.url()).pathname==='/api/applications/worker/offer-sync' &&
      request.method()==='POST'}catch{return false}
  }
  return {
    arm(){armed=true},
    observeRequest(request){
      if(!armed || !matches(request))return false
      requests.add(request)
      return true
    },
    observeResponse(response){
      try{
        if(!requests.has(response.request()))return false
        responses.push(response)
        return true
      }catch{return false}
    },
    observeFailure(request){return requests.has(request)},
    hasRequest(){return requests.size>0},
    hasResponse(){return responses.length>0},
    lastResponse(){return responses.at(-1)||null},
  }
}

// The POST-start event is not a registration result. The saved Hub status may
// still be a failure from an earlier attempt until this response arrives.
export async function waitForFreshOfferResponse({responseSeen,transportFailed,
  sleep,now=Date.now,timeoutMs=90000,pollMs=300}) {
  const end=now()+timeoutMs
  while(now()<end){
    if(transportFailed())throw Error('OFFER_SYNC_TRANSPORT_FAILED')
    if(responseSeen())return true
    await sleep(pollMs)
  }
  if(transportFailed())throw Error('OFFER_SYNC_TRANSPORT_FAILED')
  if(responseSeen())return true
  throw Error('OFFER_SYNC_RESPONSE_NOT_OBSERVED')
}
