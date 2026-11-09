"use strict";
/* Bounded, read-only, target-scoped network SLH observation port. */
(function(global){
  const admitted=Object.freeze({
    intentId:"intent:inspect-network-slh",capabilityId:"capability:network-slh-identity",
    effectId:"effect:inspect-network-slh.request",operation:"inspect",
    runtimeMethod:"inspectPinnedNetworkSlh"
  });
  function plain(v){return v!==null&&typeof v==="object"&&!Array.isArray(v);}
  function permitted(req){return plain(req)&&req.appId==="slh-network"&&req.risk==="external-read"&&
    Object.keys(admitted).every(k=>req[k]===admitted[k])&&plain(req.arguments)&&
    Object.keys(req.arguments).sort().join(",")==="address,port,tlsSha256"&&
    typeof req.arguments.address==="string"&&req.arguments.address.length<=60&&
    Number.isSafeInteger(req.arguments.port)&&typeof req.arguments.tlsSha256==="string"&&
    /^[a-fA-F0-9]{64}$/.test(req.arguments.tlsSha256);
  }
  function create(options={}){
    if(options.appId!=="slh-network")throw Error("NETWORK_SLH_PORT_APP_REFUSED");
    const fetcher=options.fetcher||global.fetch?.bind(global);
    if(typeof fetcher!=="function")throw Error("NETWORK_SLH_PORT_FETCH_UNAVAILABLE");
    return Object.freeze({
      authorize:async req=>permitted(req),
      invoke:async req=>{
        if(!permitted(req))throw Error("NETWORK_SLH_PORT_OPERATION_REFUSED");
        const response=await fetcher("/api/applications/slh/network/inspect",{
          method:"POST",credentials:"same-origin",
          headers:{"Content-Type":"application/json","Accept":"application/json"},
          body:JSON.stringify(req.arguments)
        });
        if(!response?.ok)throw Error("NETWORK_SLH_INSPECTION_REFUSED");
        const value=await response.json();
        if(!plain(value)||Object.keys(value).sort().join(",")!==
            "authenticationState,endpoint,explanation,observedAt,organizationState,tlsSha256"||
           !Object.values(value).every(v=>typeof v==="string"&&v.length<=1024)||
           value.authenticationState!=="NOT_AUTHENTICATED"||
           value.tlsSha256.toLowerCase()!==req.arguments.tlsSha256.toLowerCase())
          throw Error("NETWORK_SLH_INVALID_OBSERVATION");
        return [{id:"verified-network-target",...value}];
      }
    });
  }
  global.McelSemanticSlhNetworkPorts=Object.freeze({create});
})(typeof globalThis!=="undefined"?globalThis:this);
