"use strict";
/* Exact capability adapter for MCEL-authored Secure Local Hub.
 * In-app login uses the existing local P-256 identity to obtain a short-lived
 * Organization Service bearer. It lives only in this port closure; never URL,
 * localStorage, logs, derived state or DOM. Authentication and mutations are
 * still checked by the separately running Organization Service.
 */
(function(global) {
  const admitted = Object.freeze({
    "intent:refresh-overview": ["overview", "observeSlhOverview", "overview"],
    "intent:refresh-health": ["health", "observeSlhHealth", "health"],
    "intent:refresh-workers": ["workers", "observeSlhWorkers", "workers"],
    "intent:refresh-worker-summary": ["worker-summary", "observeSlhWorkerSummary", "workerSummary"]
  });
  const rename = "intent:rename-worker";
  const firstRun = Object.freeze({
    "intent:start-local": ["start", "startSlhLocal", []],
    "intent:bootstrap-administrator": ["bootstrap", "bootstrapSlhAdministrator",
      ["adminId", "adminName", "deviceId", "acknowledgeP256Only"]],
    "intent:configure-organization": ["configure", "configureSlhOrganization",
      ["adminDeviceId", "host", "apiPort", "workerPort"]],
    "intent:activate-organization": ["activate", "activateSlhOrganization", ["adminDeviceId"]],
    "intent:login-administrator": ["login", "loginSlhAdministrator", ["adminDeviceId"]],
    "intent:logout-administrator": ["logout", "logoutSlhAdministrator", []]
  });
  function plain(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
  function permitted(req) {
    if(!req || req.appId!=="slh" || !plain(req.arguments))return false;
    const spec=admitted[req.intentId];
    if(spec)return req.risk==="external-read" && req.effectId==="effect:"+req.intentId.slice(7)+".request" &&
      req.capabilityId==="capability:slh-observer" && req.operation===spec[0] &&
      req.runtimeMethod===spec[1] && Object.keys(req.arguments).length===0;
    const setup=firstRun[req.intentId];
    if(setup)return req.risk==="consequential" && req.effectId==="effect:"+req.intentId.slice(7)+".request" &&
      req.capabilityId==="capability:slh-first-run" && req.operation===setup[0] &&
      req.runtimeMethod===setup[1] && Object.keys(req.arguments).sort().join(",")===setup[2].slice().sort().join(",") &&
      Object.entries(req.arguments).every(([k,v])=> k==="acknowledgeP256Only" ? typeof v==="boolean" : typeof v==="string");
    if(req.intentId===rename)return req.risk==="consequential" && req.effectId==="effect:rename-worker.request" &&
      req.capabilityId==="capability:slh-worker-admin" && req.operation==="rename" &&
      req.runtimeMethod==="renameSlhWorker" && Object.keys(req.arguments).sort().join(",")==="deviceId,label" &&
      typeof req.arguments.deviceId==="string" && typeof req.arguments.label==="string";
    return false;
  }
  function create(options={}) {
    if(options.appId!=="slh")throw Error("MCEL_SLH_PORT:unknown-app");
    const fetcher=options.fetcher || global.fetch?.bind(global);
    if(typeof fetcher!=="function")throw Error("MCEL_SLH_PORT:missing-fetch");
    let accessToken="";
    let workerViewOverview=null;
    let authenticationChanged=()=>{};
    let setupChanged=()=>{};
    function headers(extra={}) {return {Accept:"application/json",...(accessToken?{Authorization:"Bearer "+accessToken}:{}),...extra};}
    async function readWorkers(token) {
      const response=await fetcher("/api/applications/slh/observe?view=workers",
        {method:"GET",credentials:"same-origin",headers:{Accept:"application/json",Authorization:"Bearer "+token}});
      if(!response?.ok)throw Error("MCEL_SLH_PORT:admin-authentication-refused");
      const data=await response.json();
      if(!plain(data) || data.schema!=="mcel.slh.first-slice-observation.v1" ||
        data.view!=="workers" || data.workerSummary?.observationState!=="AUTHENTICATED")
        throw Error("MCEL_SLH_PORT:admin-session-not-verified");
    }
    return Object.freeze({
      connect: async value=>{
        if(typeof value!=="string" || value.length<24 || value.length>256 || !/^[\x21-\x7e]+$/.test(value))
          throw Error("MCEL_SLH_PORT:invalid-session");
        accessToken="";
        await readWorkers(value);
        accessToken=value;
        authenticationChanged(true);
      },
      forget: ()=>{accessToken="";authenticationChanged(false);},
      onAuthenticationChanged: callback=>{if(typeof callback==="function")authenticationChanged=callback;},
      onSetupChanged: callback=>{if(typeof callback==="function")setupChanged=callback;},
      authorize: async req=>permitted(req) && ((req.intentId!==rename && req.intentId!=="intent:logout-administrator") || !!accessToken),
      invoke: async req=>{
        if(!permitted(req))throw Error("MCEL_SLH_PORT:undeclared-operation");
        if(firstRun[req.intentId]){
          const action=firstRun[req.intentId][0];
          if(action==="bootstrap" && req.arguments.acknowledgeP256Only!==true)
            throw Error("MCEL_SLH_PORT:p256-acknowledgement-required");
          if(!global.confirm || !global.confirm(action==="bootstrap" ?
            "Create the first SLH administrator using the local OS account's P-256 identity? Personal TOTP is not implemented. Continue?" :
            action==="start" ? "Start the local SLH control service on demand (not at Windows boot)?" :
            action==="configure" ? "Apply SLH configuration for "+req.arguments.host+
              "? Worker listener will require explicit activation; SEH remains off." :
            action==="activate" ? "Enable the configured Organization Service and Worker listener?" :
            action==="logout" ? "Sign out and revoke this Organization Service session?" :
            "Sign in with your locally installed administrator device identity?"))
            throw Error("MCEL_SLH_PORT:administrator-canceled");
          const response=await fetcher("/api/applications/slh/setup/action", {
            method:"POST",credentials:"same-origin",headers:{Accept:"application/json",
              "Content-Type":"application/json","X-MCEL-SLH-Operation":action,
              ...(action==="logout" && accessToken ? {Authorization:"Bearer "+accessToken} : {})},
            body:JSON.stringify({action,arguments:req.arguments})
          });
          if(!response?.ok)throw Error("MCEL_SLH_PORT:setup-"+action+"-refused");
          const result=await response.json();
          if(!plain(result))throw Error("MCEL_SLH_PORT:invalid-setup-receipt");
          if(action==="logout"){
            if(result.outcome!=="SIGNED_OUT")throw Error("MCEL_SLH_PORT:invalid-logout-receipt");
            accessToken="";
            authenticationChanged(false);
            return {outcome:"SIGNED_OUT",message:"Organization session revoked",deviceId:""};
          }
          if(action==="login"){
            if(typeof result.access_token!=="string")throw Error("MCEL_SLH_PORT:invalid-login-receipt");
            await readWorkers(result.access_token);
            accessToken=result.access_token;
            authenticationChanged(true);
            return {outcome:"AUTHENTICATED",message:"Organization administrator session verified",deviceId:req.arguments.adminDeviceId};
          }
          if(!["STARTED","ALREADY_RUNNING","INITIALIZED","CONFIGURED","RUNNING"].includes(result.outcome) ||
             typeof result.message!=="string" || result.message.length>512)
            throw Error("MCEL_SLH_PORT:invalid-setup-receipt");
          workerViewOverview=null;
          setupChanged(action);
          return {outcome:result.outcome,message:result.message,deviceId:result.deviceId||""};
        }
        if(req.intentId===rename){
          if(!accessToken)throw Error("MCEL_SLH_PORT:admin-session-required");
          if(!global.confirm || !global.confirm("Rename authenticated Worker "+req.arguments.deviceId+
              " to '"+req.arguments.label+"'? This changes the organization's audited device directory."))
            throw Error("MCEL_SLH_PORT:administrator-canceled");
          const result=await fetcher("/api/applications/slh/worker/rename",{
            method:"POST",credentials:"same-origin",headers:headers({"Content-Type":"application/json",
              "X-MCEL-SLH-Operation":"rename"}),body:JSON.stringify({device_id:req.arguments.deviceId,label:req.arguments.label})});
          if(!result?.ok)throw Error("MCEL_SLH_PORT:worker-mutation-refused");
          const data=await result.json();
          if(!plain(data) || data.outcome!=="UPDATED" || data.deviceId!==req.arguments.deviceId ||
             typeof data.message!=="string")throw Error("MCEL_SLH_PORT:invalid-mutation-receipt");
          return data;
        }
        // No bearer means the local application session cannot observe an
        // authenticated Worker Bank. This is a local auth prerequisite, not a
        // fabricated remote inventory. Avoid an extra network read on Overview;
        // Worker Bank itself still performs its ordinary bounded observation.
        if(req.intentId==="intent:refresh-worker-summary" && !accessToken &&
           options.initialView==="overview")return {
          observationState:"AUTH_REQUIRED",
          explanation:"No authenticated Organization Service session is established",
          observedAt:new Date().toISOString(),source:"Local MCEL session state",
          enrolledCount:"NOT_OBSERVED",connectedCount:"NOT_OBSERVED"
        };
        // A Worker Bank response includes the same authoritative Overview
        // observation. Reuse it for direct deep-link initialization, avoiding
        // a second request for the identical local status snapshot.
        if(req.intentId==="intent:refresh-overview" && options.initialView==="worker-bank" &&
            workerViewOverview && Date.now()-workerViewOverview.received<10000){
          return {...workerViewOverview.overview};
        }
        const view=req.intentId.includes("worker")?"workers":"overview";
        const response=await fetcher("/api/applications/slh/observe?view="+view,
          {method:"GET",credentials:"same-origin",headers:headers()});
        if(!response?.ok)throw Error("MCEL_SLH_PORT:observation-unavailable");
        const result=await response.json();
        if(!plain(result) || result.ok!==true ||
            result.schema!=="mcel.slh.first-slice-observation.v1" ||
            result.view!==view)throw Error("MCEL_SLH_PORT:invalid-observation");
        if(view==="workers" && plain(result.overview) &&
           Object.values(result.overview).every(v=>typeof v==="string" && v.length<=1024)){
          workerViewOverview={overview:{...result.overview},received:Date.now()};
        }
        const key=admitted[req.intentId][2];
        // A remote response never creates administrator authority. Without a
        // locally verified bearer, treat even a malformed/mocked AUTHENTICATED
        // Worker response as unauthenticated and publish no Worker identities.
        if(view==="workers" && !accessToken){
          if(key==="workers")return [];
          if(key==="workerSummary")return {
            observationState:"AUTH_REQUIRED",explanation:"Administrator session required",
            observedAt:new Date().toISOString(),source:"Local MCEL session state",
            enrolledCount:"NOT_OBSERVED",connectedCount:"NOT_OBSERVED"
          };
        }
        const value=result[key];
        if(key==="workers" || key==="health"){
          const fieldNames=key==="workers"?"assignment,id,label,marketplace,models,source,state":"id,source,state,subject";
          if(!Array.isArray(value) || value.length>128 ||
              !value.every(row=>plain(row) && Object.keys(row).sort().join(",")===fieldNames &&
                Object.values(row).every(v=>typeof v==="string" && v.length<=512)))
              throw Error("MCEL_SLH_PORT:invalid-collection");
        }else if(!plain(value) || Object.values(value).some(v=>typeof v!=="string" || v.length>1024)){
          throw Error("MCEL_SLH_PORT:invalid-summary");
        }
        return value;
      }
    });
  }
  global.McelSemanticSlhPorts=Object.freeze({create});
})(typeof globalThis!=="undefined"?globalThis:this);
