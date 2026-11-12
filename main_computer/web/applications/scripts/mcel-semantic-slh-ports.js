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
    "intent:refresh-worker-summary": ["worker-summary", "observeSlhWorkerSummary", "workerSummary"],
    "intent:refresh-identities": ["identities", "observeSlhIdentities", "identities"],
    "intent:refresh-identity-status": ["identity-status", "observeSlhIdentityStatus", "identityStatus"],
    "intent:refresh-lifecycle": ["lifecycle", "observeSlhLifecycle", "lifecycle"]
  });
  const notebookRead=Object.freeze({
    "read-organization-pending":"read-pending",
    "read-organization-enrolled":"read-enrolled",
    "read-organization-trash":"read-trash",
    "read-organization-retention":"read-retention"
  });
  const notebookAction=Object.freeze({
    "organization-accept":"accept", "organization-reject":"reject",
    "organization-delete":"delete", "organization-reactivate":"reactivate",
    "organization-remove":"remove", "set-organization-trash-retention":"set-retention",
    "empty-organization-trash":"empty-trash"
  });
  function notebookOperation(req){
    if(!plain(req)||req.appId!=="slh"||!plain(req.arguments))return null;
    const suffix=req.intentId?.startsWith("intent:")?req.intentId.slice(7):"";
    const read=notebookRead[suffix],action=notebookAction[suffix];
    const operation=read||action;
    if(!operation || req.capabilityId!==(read?"capability:organization-identity-observer":
      "capability:organization-identity-administration") ||
      req.risk!==(read?"external-read":"consequential") ||
      req.effectId!=="effect:"+suffix+".request" || req.operation!==operation)return null;
    const methods={"read-pending":"observeOrganizationPending","read-enrolled":"observeOrganizationEnrolled",
      "read-trash":"observeOrganizationTrash","read-retention":"observeOrganizationRetention",
      accept:"acceptOrganizationEnrollment",reject:"rejectOrganizationEnrollment",
      delete:"deleteOrganizationDevice",reactivate:"reactivateOrganizationDevice",
      remove:"removeOrganizationDevice","set-retention":"setOrganizationTrashRetention",
      "empty-trash":"emptyOrganizationTrash"};
    if(req.runtimeMethod!==methods[operation])return null;
    const keys=Object.keys(req.arguments).sort().join(",");
    if(["accept","reject","delete","reactivate","remove"].includes(operation)){
      if(keys!=="recordId" || typeof req.arguments.recordId!=="string" ||
         !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$/.test(req.arguments.recordId))return null;
    }else if(operation==="set-retention"){
      if(keys!=="enabled" || typeof req.arguments.enabled!=="boolean")return null;
    }else if(keys!=="")return null;
    return operation;
  }
  const rename = "intent:rename-worker";
  const firstRun = Object.freeze({
    "intent:start-local": ["start", "startSlhLocal", []],
    "intent:bootstrap-administrator": ["bootstrap", "bootstrapSlhAdministrator",
      ["adminId", "adminName", "deviceId", "acknowledgeP256Only"]],
    "intent:configure-organization": ["configure", "configureSlhOrganization",
      ["adminDeviceId", "host", "apiPort", "workerPort"]],
    "intent:configure-and-enable-organization": ["configure-and-enable", "configureAndEnableSlhOrganization",
      ["adminDeviceId", "bindAddress", "port"]],
    "intent:disable-organization": ["disable", "disableSlhOrganization", ["adminDeviceId"]],
    "intent:activate-organization": ["activate", "activateSlhOrganization", ["adminDeviceId"]],
    "intent:login-administrator": ["login", "loginSlhAdministrator", ["adminDeviceId"]],
    "intent:logout-administrator": ["logout", "logoutSlhAdministrator", []]
  });
  function plain(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
  function permitted(req) {
    if(!req || req.appId!=="slh" || !plain(req.arguments))return false;
    if(notebookOperation(req))return true;
    const spec=admitted[req.intentId];
    if(spec)return req.risk==="external-read" && req.effectId==="effect:"+req.intentId.slice(7)+".request" &&
      req.capabilityId==="capability:slh-observer" && req.operation===spec[0] &&
      req.runtimeMethod===spec[1] && Object.keys(req.arguments).length===0;
    const setup=firstRun[req.intentId];
    if(setup)return req.risk==="consequential" && req.effectId==="effect:"+req.intentId.slice(7)+".request" &&
      req.capabilityId==="capability:slh-first-run" && req.operation===setup[0] &&
      req.runtimeMethod===setup[1] && Object.keys(req.arguments).sort().join(",")===setup[2].slice().sort().join(",") &&
      Object.entries(req.arguments).every(([k,v])=> k==="acknowledgeP256Only" ? typeof v==="boolean" :
        k==="port" ? Number.isSafeInteger(v) && v>=1 && v<=65535 : typeof v==="string");
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
    let identitySnapshot=null;
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
    async function notebook(req){
      const operation=notebookOperation(req);
      if(!operation)throw Error("MCEL_SLH_NOTEBOOK:UNDECLARED_OPERATION");
      if(!accessToken)throw Error("MCEL_SLH_NOTEBOOK:ADMIN_SESSION_REQUIRED");
      const response=await fetcher("/api/applications/slh/identity-notebook",{
        method:"POST",credentials:"same-origin",headers:headers({"Content-Type":"application/json",
          "X-MCEL-SLH-Operation":"identity-notebook"}),
        body:JSON.stringify({target:"local",operation,arguments:req.arguments,sessionTicket:""})
      });
      const data=await response?.json?.().catch(()=>null);
      if(!response?.ok || data?.ok!==true){
        const code=data?.error?.code;
        throw Error("MCEL_SLH_NOTEBOOK:"+(typeof code==="string"&&/^[A-Z_]{3,64}$/.test(code)?code:"REQUEST_REFUSED"));
      }
      if(!plain(data) || data.ok!==true)throw Error("MCEL_SLH_NOTEBOOK:INVALID_RESPONSE");
      return data.result;
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
      preferredIdentity: ()=>identitySnapshot?.state==="AVAILABLE"?identitySnapshot.defaultDeviceId:"",
      authorize: async req=>permitted(req) && (notebookOperation(req)?!!accessToken:
        ((req.intentId!==rename && req.intentId!=="intent:logout-administrator") || !!accessToken)),
      invoke: async req=>{
        if(!permitted(req))throw Error("MCEL_SLH_PORT:undeclared-operation");
        if(notebookOperation(req))return notebook(req);
        if(firstRun[req.intentId]){
          const action=firstRun[req.intentId][0];
          if(action!=="start" && action!=="logout"){
            const selected=req.arguments.deviceId||req.arguments.adminDeviceId;
            if(!identitySnapshot || identitySnapshot.state!=="AVAILABLE" ||
                !identitySnapshot.identities.some(row=>row.deviceId===selected))
              throw Error("MCEL_SLH_PORT:identity-not-discovered");
          }
          if(action==="bootstrap" && req.arguments.acknowledgeP256Only!==true)
            throw Error("MCEL_SLH_PORT:p256-acknowledgement-required");
          // Signing in only authenticates; it is not a destructive operation.
          if(action!=="login" && (!global.confirm || !global.confirm(action==="bootstrap" ?
            "Enroll the selected LMS-SLH identity as the first administrator? Personal TOTP is not implemented. Continue?" :
            action==="start" ? "Start the local SLH control service on demand (not at Windows boot)?" :
            action==="configure" ? "Apply SLH configuration for "+req.arguments.host+
              "? Worker listener will require explicit activation; SEH remains off." :
            action==="configure-and-enable" ? "Configure or RESTART ORG-SLH on "+
              req.arguments.bindAddress+":"+req.arguments.port+
              " using the selected identity as administrator? The service will auto-start with LMS-SLH until disabled." :
            action==="disable" ? "Disable and stop ORG-SLH? Its configuration and administrator identity will be retained, and it will NOT auto-start until re-enabled." :
            action==="activate" ? "Enable the configured Organization Service and Worker listener?" :
            "Sign out and revoke this Organization Service session?")))
            throw Error("MCEL_SLH_PORT:administrator-canceled");
          const response=await fetcher("/api/applications/slh/setup/action", {
            method:"POST",credentials:"same-origin",headers:{Accept:"application/json",
              "Content-Type":"application/json","X-MCEL-SLH-Operation":action,
              ...(action==="logout" && accessToken ? {Authorization:"Bearer "+accessToken} : {})},
            body:JSON.stringify({action,arguments:req.arguments})
          });
          if(!response?.ok){
            const rejected=await response?.json?.().catch(()=>null);
            const code=typeof rejected?.error==="string" ? rejected.error : rejected?.error?.code;
            throw Error("MCEL_SLH_PORT:setup-"+action+"-refused"+
              (typeof code==="string" && /^[A-Z_]{3,64}$/.test(code) ? ":"+code : ""));
          }
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
            // Fresh-organization login can also initialize and activate ORG.
            // Invalidate stale pre-login Overview evidence immediately.
            workerViewOverview=null;
            setupChanged("login");
            return {outcome:"AUTHENTICATED",message:"Organization administrator session verified",deviceId:req.arguments.adminDeviceId};
          }
          if(!["STARTED","ALREADY_RUNNING","INITIALIZED","CONFIGURED","RUNNING","DISABLED"].includes(result.outcome) ||
             typeof result.message!=="string" || result.message.length>512)
            throw Error("MCEL_SLH_PORT:invalid-setup-receipt");
          workerViewOverview=null;
          if(action==="start")identitySnapshot=null;
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
        if(req.intentId==="intent:refresh-lifecycle"){
          const response=await fetcher("/api/applications/slh/observe?view=overview",
            {method:"GET",credentials:"same-origin",headers:{Accept:"application/json"}});
          if(!response?.ok)throw Error("MCEL_SLH_PORT:lifecycle-unavailable");
          const result=await response.json();
          if(!plain(result) || result.ok!==true || result.schema!=="mcel.slh.first-slice-observation.v1" ||
              result.view!=="overview" || !plain(result.overview))
            throw Error("MCEL_SLH_PORT:invalid-lifecycle-observation");
          const state=result.overview.organizationState;
          const endpoint=result.overview.organizationEndpoint;
          if(typeof state!=="string" || state.length>80 || typeof endpoint!=="string" || endpoint.length>120)
            throw Error("MCEL_SLH_PORT:invalid-lifecycle-state");
          const match=/^((?:[0-9]{1,3}\.){3}[0-9]{1,3}):([0-9]{1,5})$/.exec(endpoint);
          const bindAddress=match?match[1]:"0.0.0.0";
          const port=match?Number(match[2]):8899;
          if(!Number.isSafeInteger(port)||port<1||port>65535)
            throw Error("MCEL_SLH_PORT:invalid-lifecycle-port");
          return {state,bindAddress,port};
        }
        if(req.intentId==="intent:refresh-identities" || req.intentId==="intent:refresh-identity-status"){
          const response=await fetcher("/api/applications/slh/identities",
            {method:"GET",credentials:"same-origin",headers:{Accept:"application/json"}});
          if(!response?.ok)throw Error("MCEL_SLH_PORT:identities-unavailable");
          const result=await response.json();
          if(!plain(result) || result.schema!=="mcel.slh.identities.v4b" ||
              !["AVAILABLE","UNAVAILABLE","UNVERIFIED"].includes(result.state) ||
              !Array.isArray(result.identities) || result.identities.length>128 ||
              typeof result.defaultDeviceId!=="string" ||
              typeof result.explanation!=="string" || result.explanation.length>512 ||
              !result.identities.every(row=>plain(row) &&
                Object.keys(row).sort().join(",")==="defaultStatus,deviceId,fingerprint,label" &&
                typeof row.deviceId==="string" && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(row.deviceId) &&
                typeof row.label==="string" && row.label.length<=120 &&
                typeof row.fingerprint==="string" && /^[a-f0-9]{64}$/.test(row.fingerprint) &&
                ["DEFAULT","ADDITIONAL"].includes(row.defaultStatus)) ||
              (result.state==="AVAILABLE" && !result.identities.some(row=>
                row.deviceId===result.defaultDeviceId && row.defaultStatus==="DEFAULT")) ||
              (result.state!=="AVAILABLE" && (result.identities.length || result.defaultDeviceId)))
            throw Error("MCEL_SLH_PORT:invalid-identity-observation");
          identitySnapshot=result;
          if(req.intentId==="intent:refresh-identities")return result.identities;
          return {state:result.state,defaultDeviceId:result.defaultDeviceId,explanation:result.explanation};
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
