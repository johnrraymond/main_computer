"use strict";
/* Scoped Network SLH connection: pin before P-256 login; never expose bearer. */
(function(global){
  const admitted=Object.freeze({
    intentId:"intent:connect-network-slh",capabilityId:"capability:network-slh-connection",
    effectId:"effect:connect-network-slh.request",operation:"connect",
    runtimeMethod:"connectNetworkSlh"
  });
  const disconnect=Object.freeze({
    intentId:"intent:disconnect-network-slh",capabilityId:"capability:network-slh-connection",
    effectId:"effect:disconnect-network-slh.request",operation:"disconnect",
    runtimeMethod:"disconnectNetworkSlh"
  });
  function plain(v){return v!==null&&typeof v==="object"&&!Array.isArray(v);}
  function validAddress(value){return typeof value==="string"&&value.length>0&&value.length<=60;}
  function validIdentity(value){return typeof value==="string"&&/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(value);}
  // Reuse the same declared record-notebook semantic operations; authority
  // differs only by the previously pinned, opaque remote ticket.
  const notebook=Object.freeze({
    "read-organization-pending":["read-pending","observeOrganizationPending"],
    "read-organization-enrolled":["read-enrolled","observeOrganizationEnrolled"],
    "read-organization-trash":["read-trash","observeOrganizationTrash"],
    "read-organization-retention":["read-retention","observeOrganizationRetention"],
    "organization-accept":["accept","acceptOrganizationEnrollment"],
    "organization-reject":["reject","rejectOrganizationEnrollment"],
    "organization-delete":["delete","deleteOrganizationDevice"],
    "organization-reactivate":["reactivate","reactivateOrganizationDevice"],
    "organization-remove":["remove","removeOrganizationDevice"],
    "set-organization-trash-retention":["set-retention","setOrganizationTrashRetention"],
    "empty-organization-trash":["empty-trash","emptyOrganizationTrash"]
  });
  function notebookOperation(req){
    const suffix=req?.intentId?.startsWith("intent:")?req.intentId.slice(7):"";
    const descriptor=notebook[suffix];if(!descriptor || !plain(req?.arguments))return null;
    const read=suffix.startsWith("read-");
    if(req.appId!=="slh" || req.capabilityId!==
      (read?"capability:organization-identity-observer":"capability:organization-identity-administration") ||
      req.risk!==(read?"external-read":"consequential") ||
      req.effectId!=="effect:"+suffix+".request" || req.operation!==descriptor[0] ||
      req.runtimeMethod!==descriptor[1])return null;
    const keys=Object.keys(req.arguments).sort().join(",");
    if(["accept","reject","delete","reactivate","remove"].includes(descriptor[0])){
      if(keys!=="recordId" || typeof req.arguments.recordId!=="string" ||
         !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$/.test(req.arguments.recordId))return null;
    }else if(descriptor[0]==="set-retention"){
      if(keys!=="enabled" || typeof req.arguments.enabled!=="boolean")return null;
    }else if(keys!=="")return null;
    return descriptor[0];
  }
  function permitted(req){
    if(notebookOperation(req))return true;
    if(!plain(req)||req.appId!=="slh"||req.risk!=="consequential"||!plain(req.arguments))return false;
    const identity=Object.keys(admitted).every(k=>req[k]===admitted[k]);
    const signingOut=Object.keys(disconnect).every(k=>req[k]===disconnect[k]);
    if(identity)return Object.keys(req.arguments).sort().join(",")==="address,adminDeviceId,port" &&
      validAddress(req.arguments.address)&&Number.isSafeInteger(req.arguments.port)&&
      req.arguments.port>=1&&req.arguments.port<=65535&&validIdentity(req.arguments.adminDeviceId);
    if(signingOut)return Object.keys(req.arguments).length===0;
    return false;
  }
  function create(options={}){
    if(options.appId!=="slh")throw Error("NETWORK_SLH_PORT_APP_REFUSED");
    const fetcher=options.fetcher||global.fetch?.bind(global);
    if(typeof fetcher!=="function")throw Error("NETWORK_SLH_PORT_FETCH_UNAVAILABLE");
    let ticket="";
    let administrator=false;
    let onConnectionChanged=()=>{};
    async function post(action,args){
      const response=await fetcher("/api/applications/slh/network/connect",{
        method:"POST",credentials:"same-origin",headers:{"Accept":"application/json",
          "Content-Type":"application/json","X-MCEL-SLH-Operation":"network-connect"},
        body:JSON.stringify({action,arguments:args})
      });
      const data=await response?.json?.().catch(()=>null);
      if(!response?.ok){
        const code=data?.error?.code;
        throw Error("NETWORK_SLH:"+(typeof code==="string"&&/^[A-Z_]{3,64}$/.test(code)?code:"REQUEST_REFUSED"));
      }
      if(!plain(data))throw Error("NETWORK_SLH:INVALID_RESPONSE");
      return data;
    }
    function connected(value){
      if(value.pairingRequired!==false||typeof value.sessionTicket!=="string"||
        value.sessionTicket.length<30||typeof value.endpoint!=="string"||
        typeof value.tlsSha256!=="string"||!/^[a-f0-9]{64}$/.test(value.tlsSha256)||
        value.authenticationState!=="AUTHENTICATED"||typeof value.role!=="string"||
        typeof value.deviceId!=="string"||typeof value.services!=="string"||
        typeof value.observedAt!=="string"||typeof value.explanation!=="string")
        throw Error("NETWORK_SLH:INVALID_AUTHENTICATED_RESPONSE");
      ticket=value.sessionTicket; // browser closure only, never semantic state
      administrator=value.role==="admin";
      onConnectionChanged(administrator);
      const {sessionTicket,pairingRequired,...observation}=value;
      return observation;
    }
    return Object.freeze({
      onConnectionChanged:callback=>{if(typeof callback==="function")onConnectionChanged=callback;},
      authorize:async req=>permitted(req) && (!notebookOperation(req)||(!!ticket&&administrator)),
      invoke:async req=>{
        if(!permitted(req))throw Error("NETWORK_SLH:OPERATION_REFUSED");
        const operation=notebookOperation(req);
        if(operation){
          if(!ticket||!administrator)throw Error("NETWORK_SLH:ADMIN_SESSION_REQUIRED");
          const response=await fetcher("/api/applications/slh/identity-notebook",{
            method:"POST",credentials:"same-origin",headers:{Accept:"application/json",
              "Content-Type":"application/json","X-MCEL-SLH-Operation":"identity-notebook"},
            body:JSON.stringify({target:"network",operation,arguments:req.arguments,sessionTicket:ticket})
          });
          const result=await response?.json?.().catch(()=>null);
          if(!response?.ok||result?.ok!==true){
            const code=result?.error?.code;
            if(["NETWORK_SESSION_EXPIRED","NETWORK_SESSION_NOT_FOUND","SESSION_INVALID"].includes(code)){
              ticket="";administrator=false;onConnectionChanged(false);
            }
            throw Error("NETWORK_SLH:"+(typeof code==="string"&&/^[A-Z_]{3,64}$/.test(code)?code:"REQUEST_REFUSED"));
          }
          return result.result;
        }
        if(req.intentId===disconnect.intentId){
          if(!ticket)throw Error("NETWORK_SLH:NOT_CONNECTED");
          const result=await post("disconnect",{sessionTicket:ticket});
          ticket="";administrator=false;onConnectionChanged(false);
          if(result.authenticationState!=="DISCONNECTED")throw Error("NETWORK_SLH:INVALID_LOGOUT");
          return {endpoint:"",tlsSha256:"",organizationState:"NOT_OBSERVED",
            authenticationState:"DISCONNECTED",role:"",deviceId:"",services:"",
            observedAt:new Date().toISOString(),explanation:result.explanation||"Remote session ended"};
        }
        if(ticket)throw Error("NETWORK_SLH:DISCONNECT_BEFORE_RECONNECT");
        const initial=await post("connect",req.arguments);
        if(initial.pairingRequired===false)return connected(initial);
        if(initial.pairingRequired!==true||typeof initial.endpoint!=="string"||
           typeof initial.tlsSha256!=="string"||!/^[a-f0-9]{64}$/.test(initial.tlsSha256))
          throw Error("NETWORK_SLH:INVALID_TRUST_CHALLENGE");
        // This is a first-time, self-signed service. Do not silently trust it.
        // An out-of-band confirmation of the fingerprint is required to defend
        // against an attacker impersonating the intended network destination.
        if(typeof global.confirm!=="function" || !global.confirm(
          "First connection to "+initial.endpoint+". Verify this ORG-SLH TLS certificate fingerprint with the server operator BEFORE continuing:\n\n"+
          initial.tlsSha256+"\n\nTrust this identity and sign in?"))
          throw Error("NETWORK_SLH:TRUST_NOT_CONFIRMED");
        return connected(await post("connect",{...req.arguments,confirmPin:initial.tlsSha256}));
      }
    });
  }
  global.McelSemanticSlhNetworkPorts=Object.freeze({create});
})(typeof globalThis!=="undefined"?globalThis:this);
