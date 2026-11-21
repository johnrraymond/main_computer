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
  function sehLifecycleOperation(req){
    if(!plain(req) || req.appId!=="slh" || !plain(req.arguments) ||
       Object.keys(req.arguments).length!==0)return null;
    const name=req.intentId?.startsWith("intent:")?req.intentId.slice(7):"";
    const read=name==="read-seh-connection";
    const action=name.startsWith("seh-connection-")?name.slice(15):"";
    if(!read && !["start","stop","restart"].includes(action))return null;
    if(req.capabilityId!==(read?"capability:seh-connection-observer":"capability:seh-connection-control") ||
       req.risk!==(read?"external-read":"consequential") ||
       req.effectId!=="effect:"+name+".request" ||
       req.operation!==(read?"status":action) ||
       req.runtimeMethod!==(read?"observeSehConnection":"controlSehConnection"))return null;
    return read?"status":action;
  }
  const softwareAuthenticator=Object.freeze({
    "intent:read-software-authenticator-status":["status","observeSlhSoftwareAuthenticator","external-read","capability:slh-software-authenticator-observer"],
    "intent:software-authenticator-pair":["pair","pairSlhSoftwareAuthenticator","consequential","capability:slh-software-authenticator-control"],
    "intent:software-authenticator-reveal":["reveal","revealSlhSoftwareAuthenticator","consequential","capability:slh-software-authenticator-control"],
    "intent:software-authenticator-revoke":["revoke","revokeSlhSoftwareAuthenticator","consequential","capability:slh-software-authenticator-control"]
  });
  const rename = "intent:rename-worker";
  const firstRun = Object.freeze({
    "intent:start-local": ["start", "startSlhLocal", []],
    "intent:bootstrap-administrator": ["bootstrap", "bootstrapSlhAdministrator",
      ["adminId", "adminName", "deviceId", "acknowledgeP256Only"]],
    "intent:configure-organization": ["configure", "configureSlhOrganization",
      ["adminDeviceId", "host", "apiPort", "workerPort"]],
    "intent:configure-and-enable-organization": ["configure-and-enable", "configureAndEnableSlhOrganization",
      ["adminDeviceId", "bindAddress", "port"]],
    "intent:toggle-organization-enablement": ["toggle-enablement", "toggleSlhOrganizationEnablement", ["adminDeviceId"]],
    "intent:toggle-organization": ["toggle", "toggleSlhOrganization", ["adminDeviceId"]],
    "intent:activate-organization": ["activate", "activateSlhOrganization", ["adminDeviceId"]]
  });
  function plain(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
  function permitted(req) {
    if(!req || req.appId!=="slh" || !plain(req.arguments))return false;
    if(notebookOperation(req)||sehLifecycleOperation(req)||
       softwareAuthenticatorOperation(req)||
       global.McelSemanticSehDeploymentPort?.operation(req))return true;
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
  function softwareAuthenticatorOperation(req){
    if(!plain(req) || req.appId!=="slh" || !plain(req.arguments))return null;
    const spec=softwareAuthenticator[req.intentId];
    if(!spec || req.operation!==spec[0] || req.runtimeMethod!==spec[1] ||
      req.risk!==spec[2] || req.capabilityId!==spec[3] ||
      req.effectId!=="effect:"+req.intentId.slice(7)+".request" ||
      Object.keys(req.arguments).join(",")!=="adminDeviceId" ||
      typeof req.arguments.adminDeviceId!=="string" ||
      !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(req.arguments.adminDeviceId))return null;
    return spec[0];
  }
  function create(options={}) {
    if(options.appId!=="slh")throw Error("MCEL_SLH_PORT:unknown-app");
    const fetcher=options.fetcher || global.fetch?.bind(global);
    if(typeof fetcher!=="function")throw Error("MCEL_SLH_PORT:missing-fetch");
    let otpClose=()=>{};
    // Ephemeral credential presentation, owned by the identity disclosure.
    // The code stays in this closure and the live output element only: never
    // in MCEL state, receipts, activity, localStorage, or a standalone modal.
    function renderCurrentCode(result){
      const valid=result && typeof result.deviceId==="string" &&
        Number.isSafeInteger(result.expiresAt) &&
        result.expiresAt*1000>Date.now() &&
        result.expiresAt*1000<=Date.now()+61000 &&
        result.periodSeconds===30 && typeof result.usable==="boolean" &&
        (result.usable ? typeof result.code==="string" && /^[0-9]{8}$/.test(result.code) : result.code===null);
      if(!valid)throw Error("MCEL_SLH_OTP:INVALID_CURRENT_CODE");
      const doc=global.document;
      const host=[...(doc?.querySelectorAll("[data-mcel-credential-detail]")||[])].find(
        el=>el.getAttribute("data-mcel-credential-detail")===result.deviceId);
      if(!host || !host.closest("details")?.open)
        throw Error("MCEL_SLH_OTP:IDENTITY_DETAIL_CLOSED");
      otpClose();
      const status=host.querySelector("[data-mcel-passkey-status]");
      const display=host.querySelector("[data-mcel-passkey-code]");
      const remaining=host.querySelector("[data-mcel-passkey-remaining]");
      const copy=host.querySelector("[data-mcel-passkey-copy]");
      if(!status||!display||!remaining||!copy)
        throw Error("MCEL_SLH_OTP:IDENTITY_DETAIL_INCOMPLETE");
      const deviceId=result.deviceId,epoch=identityEpoch;
      let secret=result.usable?result.code:"";
      let expiresAt=result.expiresAt;
      let closed=false,refreshing=false;
      let lastChecked=0;
      const clearSecret=()=>{secret="";display.textContent="•••• ••••";
        copy.disabled=true;};
      const paint=()=>{
        if(closed)return;
        const seconds=Math.max(0,Math.ceil((expiresAt*1000-Date.now())/1000));
        if(seconds===0)clearSecret();
        const usable=!!secret && seconds>0;
        status.textContent=usable?"Usable":"Old / expired";
        status.setAttribute("data-mcel-passkey-usable",String(usable));
        display.textContent=usable?secret:"•••• ••••";
        remaining.textContent=seconds+"s remaining";
        copy.disabled=!usable;
      };
      const refresh=async()=>{
        if(closed||refreshing||!host.closest("details")?.open ||
          epoch!==identityEpoch||activeIdentity!==deviceId||!accessToken)return;
        refreshing=true;
        try{
          const {response,data}=await authorizedJsonRequest("/api/applications/slh/software-authenticator",{
            method:"POST",headers:{"Content-Type":"application/json",
              "X-MCEL-SLH-Operation":"software-authenticator"},
            body:JSON.stringify({operation:"current",deviceId}),replayOnInvalidSession:false
          });
          if(closed||epoch!==identityEpoch||!host.closest("details")?.open)return;
          const next=data?.result;
          if(!response?.ok||data?.ok!==true || !next||next.deviceId!==deviceId ||
             next.periodSeconds!==30||typeof next.usable!=="boolean" ||
             !Number.isSafeInteger(next.expiresAt) ||
             next.expiresAt*1000<=Date.now()||next.expiresAt*1000>Date.now()+61000 ||
             (next.usable?!/^[0-9]{8}$/.test(next.code):next.code!==null)){
            clearSecret();status.textContent="Unavailable";remaining.textContent="Authorization unavailable";
            return;
          }
          secret=next.usable?next.code:"";
          expiresAt=next.expiresAt;
          paint();
        }catch{
          if(!closed){clearSecret();status.textContent="Unavailable";remaining.textContent="Authorization unavailable";}
        }finally{refreshing=false;}
      };
      const onCopy=async()=>{
        if(closed||!secret||Date.now()>=expiresAt*1000)return;
        try{
          if(typeof global.navigator?.clipboard?.writeText!=="function")throw Error("CLIPBOARD_UNAVAILABLE");
          await global.navigator.clipboard.writeText(secret);
        }catch{remaining.textContent="Clipboard unavailable";}
      };
      const onDisclosure=()=>{if(!host.closest("details")?.open)otpClose();};
      const onVisibility=()=>{if(doc.visibilityState==="hidden")otpClose();};
      const owner=host.closest("details");
      const timer=global.setInterval(()=>{
        if(closed)return;
        if(!host.isConnected||!owner?.open||epoch!==identityEpoch||activeIdentity!==deviceId){otpClose();return;}
        paint();
        const now=Date.now();
        if((now>=expiresAt*1000 || now-lastChecked>=5000) && !refreshing){
          lastChecked=now;
          void refresh();
        }
      },1000);
      otpClose=()=>{
        if(closed)return;
        closed=true;global.clearInterval(timer);
        owner?.removeEventListener("toggle",onDisclosure);
        doc.removeEventListener("visibilitychange",onVisibility);
        copy.removeEventListener("click",onCopy);
        clearSecret();status.textContent="Not displayed";remaining.textContent="—";
        otpClose=()=>{};
      };
      copy.addEventListener("click",onCopy);
      owner?.addEventListener("toggle",onDisclosure);
      doc.addEventListener("visibilitychange",onVisibility);
      paint();
    }
    let accessToken="";
    let tokenExpiresAt=0; // Ephemeral, server-issued Unix expiry; never browser persistence.
    let activeIdentity="";
    let identityEpoch=0;
    let pendingAuthentication=null;
    let workerViewOverview=null;
    let identitySnapshot=null;
    let lifecycleObservation=null;
    let organizationFailure=null;
    let authenticationChanged=()=>{};
    let setupChanged=()=>{};
    const operationIds=new Map();
    function durableOperationId(operation,args){
      if(!["deploy","undeploy"].includes(operation))return undefined;
      const key=operation+":"+JSON.stringify(args);
      if(!operationIds.has(key)){
        const id=global.crypto?.randomUUID?.().replaceAll("-","") ||
          (Date.now().toString(16)+Math.random().toString(16).slice(2)+Math.random().toString(16).slice(2));
        operationIds.set(key,"mcel-seh-"+id);
      }
      return operationIds.get(key);
    }
    // Reuse an operation ID while its acknowledgement or outcome is uncertain.
    // Once LMS confirms a terminal receipt, the same action is a NEW request.
    function releaseRecordedOperation(receipt){
      if(!receipt || !["RECORDED","CANCELED"].includes(receipt.phase) ||
         typeof receipt.operationId!=="string")return;
      for(const [key,id] of operationIds){
        if(id===receipt.operationId)operationIds.delete(key);
      }
    }
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
    const validSigner=value=>typeof value==="string" && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(value);
    function discardIdentity(next=""){
      if(next===activeIdentity)return;
      const oldToken=accessToken;
      otpClose();activeIdentity=next;accessToken="";tokenExpiresAt=0;pendingAuthentication=null;
      ++identityEpoch;workerViewOverview=null;
      authenticationChanged(false);
      if(oldToken){
        // This is session cleanup, not an interactive Logout operation.
        void fetcher("/api/applications/slh/setup/action",{method:"POST",credentials:"same-origin",
          headers:{Accept:"application/json","Content-Type":"application/json",
            "X-MCEL-SLH-Operation":"logout",Authorization:"Bearer "+oldToken},
          body:JSON.stringify({action:"logout",arguments:{}})}).catch(()=>{});
      }
    }
    function invalidateToken(token,notify=true){
      // Do not revoke a newer login just because an older request settled late.
      if(!token || accessToken!==token)return;
      otpClose();accessToken="";tokenExpiresAt=0;
      // During an immediate re-login, defer the UI authorization transition:
      // resetting the record notebook would discard its in-flight action.
      // The backend remains the authority; a failed re-login revokes the UI.
      if(notify)authenticationChanged(false);
    }
    async function ensureAuthorization(){
      if(!validSigner(activeIdentity))throw Error("MCEL_SLH_PORT:IDENTITY_NOT_SELECTED");
      // ORG-SLH sessions expire after 900s. Refresh before crossing the
      // authority's expiry boundary, without introducing a periodic timer.
      if(accessToken && (!tokenExpiresAt || Date.now()+30000<tokenExpiresAt))return accessToken;
      if(accessToken)invalidateToken(accessToken,false);
      if(pendingAuthentication)return pendingAuthentication;
      const signer=activeIdentity, epoch=identityEpoch;
      const pending=(async()=>{
        let response;
        try{
          response=await fetcher("/api/applications/slh/setup/action",{
            method:"POST",credentials:"same-origin",headers:{Accept:"application/json",
              "Content-Type":"application/json","X-MCEL-SLH-Operation":"authenticate"},
            body:JSON.stringify({action:"authenticate",arguments:{adminDeviceId:signer}})});
        }catch{
          // Do not leak transport exception text or credentials into UI receipts.
          throw Error("MCEL_SLH_PORT:AUTHENTICATION_TRANSPORT_UNAVAILABLE");
        }
        const result=await response?.json?.().catch(()=>null);
        if(!response || typeof response.ok!=="boolean")
          throw Error("MCEL_SLH_PORT:AUTHENTICATION_INVALID_RESPONSE");
        if(!response.ok){
          if(Number(response.status)>=500)
            throw Error("MCEL_SLH_PORT:AUTHENTICATION_SERVICE_UNAVAILABLE");
          const code=typeof result?.error==="string" ? result.error : result?.error?.code;
          throw Error("MCEL_SLH_PORT:AUTHORIZATION_REFUSED"+
            (typeof code==="string" && /^[A-Z_]{3,64}$/.test(code) ? ":"+code : ""));
        }
        if(typeof result?.access_token!=="string" || result.access_token.length<24 ||
           (result.device_id||result.deviceId)!==signer)
          throw Error("MCEL_SLH_PORT:AUTHENTICATION_INVALID_RESPONSE");
        if(epoch!==identityEpoch || signer!==activeIdentity)
          throw Error("MCEL_SLH_PORT:IDENTITY_CONTEXT_CHANGED");
        // Authentication was already verified by ORG-SLH's signed challenge.
        // Worker inventory is not an authority prerequisite for other operations.
        if(epoch!==identityEpoch || signer!==activeIdentity)
          throw Error("MCEL_SLH_PORT:IDENTITY_CONTEXT_CHANGED");
        const expires=Number(result.expires_at);
        // Legacy supplied sessions may not expose expiry. Such sessions are
        // still revalidated by the strictly typed 401 recovery path below.
        tokenExpiresAt=Number.isSafeInteger(expires) && expires>0?expires*1000:0;
        accessToken=result.access_token;
        authenticationChanged(true);
        return accessToken;
      })();
      pendingAuthentication=pending;
      try{return await pending;}
      catch(error){
        if(epoch===identityEpoch && signer===activeIdentity && !accessToken)
          authenticationChanged(false);
        throw error;
      }
      finally{if(pendingAuthentication===pending)pendingAuthentication=null;}
    }
    async function authorizedJsonRequest(url, request){
      const signer=activeIdentity, epoch=identityEpoch;
      for(let attempt=0;attempt<2;attempt++){
        const token=await ensureAuthorization();
        if(epoch!==identityEpoch || signer!==activeIdentity)
          throw Error("MCEL_SLH_PORT:IDENTITY_CONTEXT_CHANGED");
        let response;
        try{
          response=await fetcher(url,{
            method:request.method,credentials:"same-origin",
            headers:{Accept:"application/json",Authorization:"Bearer "+token,
              ...request.headers},
            ...(request.body===undefined?{}:{body:request.body})
          });
        }catch{
          throw Error("MCEL_SLH_PORT:AUTHENTICATED_TRANSPORT_UNAVAILABLE");
        }
        const data=await response?.json?.().catch(()=>null);
        if(epoch!==identityEpoch || signer!==activeIdentity)
          throw Error("MCEL_SLH_PORT:IDENTITY_CONTEXT_CHANGED");
        const code=data?.error?.code;
        // Retry only an authenticated read or a direct single-step notebook
        // operation: its ORG-SLH authority check precedes the mutation.
        // Multi-step mutations (rename, SEH lifecycle) may have committed before
        // returning a late 401 and must never be automatically replayed.
        if(Number(response?.status)===401 && code==="SESSION_INVALID"){
          const canRetry=attempt===0 && request.replayOnInvalidSession===true;
          invalidateToken(token,!canRetry);
          if(canRetry)continue;
        }else if(Number(response?.status)===401 && code==="SESSION_REVOKED"){
          invalidateToken(token); // do not attempt to bypass revocation
        }
        return {response,data};
      }
      throw Error("MCEL_SLH_PORT:AUTHORIZATION_RECOVERY_REFUSED");
    }
    async function notebook(req){
      const operation=notebookOperation(req);
      if(!operation)throw Error("MCEL_SLH_NOTEBOOK:UNDECLARED_OPERATION");
      await ensureAuthorization();
      let response,data;
      try{
        ({response,data}=await authorizedJsonRequest("/api/applications/slh/identity-notebook",{
          method:"POST",replayOnInvalidSession:true,headers:{"Content-Type":"application/json",
            "X-MCEL-SLH-Operation":"identity-notebook"},
          body:JSON.stringify({target:"local",operation,arguments:req.arguments,sessionTicket:""})
        }));
      }catch(error){
        if(error?.message!=="MCEL_SLH_PORT:AUTHENTICATED_TRANSPORT_UNAVAILABLE")throw error;
        throw Error("MCEL_SLH_NOTEBOOK:TRANSPORT_UNAVAILABLE");
      }
      if(Number(response?.status)>=500)
        throw Error("MCEL_SLH_NOTEBOOK:SERVICE_UNAVAILABLE");
      if(!response?.ok || data?.ok!==true){
        const code=data?.error?.code;
        throw Error("MCEL_SLH_NOTEBOOK:"+(typeof code==="string"&&/^[A-Z_]{3,64}$/.test(code)?code:"REQUEST_REFUSED"));
      }
      if(!plain(data) || data.ok!==true)throw Error("MCEL_SLH_NOTEBOOK:INVALID_RESPONSE");
      return data.result;
    }
    let managementChanged=()=>{};
    async function managementRequest(operation,args={}){
      if(!["operation-state","operation-status","cancel-operation"].includes(operation))
        throw Error("MCEL_LMS_MANAGEMENT:OPERATION_REFUSED");
      const response=await fetcher("/api/applications/slh/seh/deployment",{
        method:"POST",credentials:"same-origin",headers:{Accept:"application/json",
          "Content-Type":"application/json","X-MCEL-SLH-Operation":"seh-deployment"},
        body:JSON.stringify({target:"local",operation,arguments:args,sessionTicket:""})
      });
      const value=await response?.json?.().catch(()=>null);
      if(!response?.ok||value?.ok!==true)
        throw Error("MCEL_LMS_MANAGEMENT:"+(value?.error?.code||"UNAVAILABLE"));
      return value.result;
    }
    return Object.freeze({
      managementRequest,releaseRecordedOperation,
      onManagementChanged:callback=>{if(typeof callback==="function")managementChanged=callback;},
      setIdentity: value=>{
        if(value!==""&&!validSigner(value))throw Error("MCEL_SLH_PORT:INVALID_IDENTITY");
        discardIdentity(value);
      },
      connect: async value=>{
        if(typeof value!=="string" || value.length<24 || value.length>256 || !/^[\x21-\x7e]+$/.test(value))
          throw Error("MCEL_SLH_PORT:invalid-session");
        otpClose();accessToken="";tokenExpiresAt=0;
        await readWorkers(value);
        accessToken=value;
        authenticationChanged(true);
      },
      forget: ()=>discardIdentity(""),
      onAuthenticationChanged: callback=>{if(typeof callback==="function")authenticationChanged=callback;},
      onSetupChanged: callback=>{if(typeof callback==="function")setupChanged=callback;},
      preferredIdentity: ()=>identitySnapshot?.state==="AVAILABLE"?identitySnapshot.defaultDeviceId:"",
      // Public, read-only observation provenance for SLH E/W/G diagnostics.
      lifecycleObservation: ()=>lifecycleObservation?{...lifecycleObservation}:null,
      organizationFailure: ()=>organizationFailure?{...organizationFailure}:null,
      authorize: async req=>{
        if(!permitted(req))return false;
        if(notebookOperation(req)||sehLifecycleOperation(req)||
           (softwareAuthenticatorOperation(req)&&softwareAuthenticatorOperation(req)!=="status")||
           req.intentId===rename ||
           (req.intentId==="intent:refresh-worker-summary" && options.initialView!=="overview") ||
           req.intentId==="intent:refresh-workers"){
          // Only an undeclared operation is denied here. Authentication
          // failures retain their typed cause through MCEL's generic receipt.
          await ensureAuthorization();
        }
        // Config/start continue to be verified separately by the SLH backend.
        return true;
      },
      invoke: async req=>{
        if(!permitted(req))throw Error("MCEL_SLH_PORT:undeclared-operation");
        if(notebookOperation(req)||sehLifecycleOperation(req)||
           (softwareAuthenticatorOperation(req)&&softwareAuthenticatorOperation(req)!=="status")||
           req.intentId===rename)
          await ensureAuthorization();
        if(notebookOperation(req))return notebook(req);
        const otpOperation=softwareAuthenticatorOperation(req);
        if(otpOperation){
          const deviceId=req.arguments.adminDeviceId;
          if(!activeIdentity || activeIdentity!==deviceId)
            throw Error("MCEL_SLH_OTP:SELECTED_IDENTITY_REQUIRED");
          // MCEL notebooks observe their active tab on mount. An unlogged
          // observer must NEVER silently invoke P-256 signing as a side effect
          // of opening this application or selecting an identity.
          if(otpOperation==="status" && !accessToken)return {
            active:false,localFactor:false,status:"UNOBSERVED",deviceId,
            periodSeconds:30,message:"Administrator login required before authenticator status can be verified"
          };
          const {response,data}=await authorizedJsonRequest("/api/applications/slh/software-authenticator",{
            method:"POST",headers:{"Content-Type":"application/json",
              "X-MCEL-SLH-Operation":"software-authenticator"},
            body:JSON.stringify({operation:otpOperation==="reveal"?"current":otpOperation,deviceId})
          });
          if(!response?.ok||data?.ok!==true){
            const code=data?.error?.code;
            throw Error("MCEL_SLH_OTP:"+(typeof code==="string"&&/^[A-Z_]{3,64}$/.test(code)?code:"REQUEST_REFUSED"));
          }
          if(otpOperation==="reveal"){
            if(data.result?.deviceId!==deviceId ||
               Object.keys(data.result).sort().join(",")!=="code,deviceId,expiresAt,periodSeconds,usable")
              throw Error("MCEL_SLH_OTP:IDENTITY_RESULT_MISMATCH");
            renderCurrentCode(data.result);
            return {outcome:"DISPLAYED",message:"Identity passkey detail updated",deviceId};
          }
          const result=data.result;
          if(!plain(result) || result.deviceId!==deviceId)
            throw Error("MCEL_SLH_OTP:INVALID_RESULT");
          if(otpOperation==="status"){
            if(Object.keys(result).sort().join(",")!=="active,deviceId,localFactor,message,periodSeconds,status" ||
               typeof result.active!=="boolean" || typeof result.localFactor!=="boolean" ||
               result.periodSeconds!==30 || typeof result.message!=="string" ||
               result.message.length>256 || !["PAIRED","LOCAL_PAIRING_REQUIRED","REGISTRATION_REQUIRED","NOT_PAIRED","UNOBSERVED"].includes(result.status))
              throw Error("MCEL_SLH_OTP:INVALID_STATUS");
          }else if(Object.keys(result).sort().join(",")!=="deviceId,message,outcome" ||
                   result.outcome!==(otpOperation==="pair"?"PAIRED":"REVOKED") ||
                   typeof result.message!=="string" || result.message.length>256)
            throw Error("MCEL_SLH_OTP:INVALID_RECEIPT");
          return result;
        }

        const deployment=global.McelSemanticSehDeploymentPort?.operation(req);
        if(deployment){
          // This Computer goes straight to resident LMS. No selected signer,
          // ORG-SLH session, or Worker inventory is involved in LMS management.
          const response=await fetcher("/api/applications/slh/seh/deployment",{
            method:"POST",credentials:"same-origin",headers:{Accept:"application/json",
              "Content-Type":"application/json","X-MCEL-SLH-Operation":"seh-deployment"},
            body:JSON.stringify({target:"local",operation:deployment,arguments:req.arguments,sessionTicket:"",
              ...(durableOperationId(deployment,req.arguments)?{operationId:durableOperationId(deployment,req.arguments)}:{})})
          });
          const value=await response?.json?.().catch(()=>null);
          if(!response?.ok||value?.ok!==true){
            const code=value?.error?.code;
            throw Error("MCEL_SEH_DEPLOYMENT:"+(typeof code==="string"&&/^[A-Z_]{3,64}$/.test(code)?code:"REQUEST_REFUSED"));
          }
          if(["deploy","undeploy"].includes(deployment))managementChanged(value.result);
          return value.result;
        }
        const sehOperation=sehLifecycleOperation(req);
        if(sehOperation){
          await ensureAuthorization();
          if(sehOperation!=="status" &&
             (typeof global.confirm!=="function" || !global.confirm("Manage this ORG-SLH outbound SEH connection: "+sehOperation+"?")))
            throw Error("MCEL_SEH_LIFECYCLE:CANCELED");
          const {response,data:value}=await authorizedJsonRequest("/api/applications/slh/seh/lifecycle",{
            method:"POST",headers:{"Content-Type":"application/json",
              "X-MCEL-SLH-Operation":"seh-lifecycle"},
            body:JSON.stringify({target:"local",operation:sehOperation,sessionTicket:""})
          });
          if(!response?.ok||value?.ok!==true)throw Error("MCEL_SEH_LIFECYCLE:"+(value?.error?.code||"REQUEST_REFUSED"));
          return value.result;
        }
        if(firstRun[req.intentId]){
          const action=firstRun[req.intentId][0];
          if(action!=="start"){
            const selected=req.arguments.deviceId||req.arguments.adminDeviceId;
            if(!identitySnapshot || identitySnapshot.state!=="AVAILABLE" ||
                !identitySnapshot.identities.some(row=>row.deviceId===selected))
              throw Error("MCEL_SLH_PORT:identity-not-discovered");
          }
          if(action==="bootstrap" && req.arguments.acknowledgeP256Only!==true)
            throw Error("MCEL_SLH_PORT:p256-acknowledgement-required");
          if(!global.confirm || !global.confirm(action==="bootstrap" ?
            "Enroll the selected LMS-SLH identity as the first administrator? Personal TOTP is not implemented. Continue?" :
            action==="start" ? "Start the local SLH control service on demand (not at Windows boot)?" :
            action==="configure" ? "Apply SLH configuration for "+req.arguments.host+
              "? Worker listener will require explicit activation; SEH remains off." :
            action==="configure-and-enable" ? "Configure or RESTART ORG-SLH on "+
              req.arguments.bindAddress+":"+req.arguments.port+
              " using the selected identity as administrator? The service will auto-start with LMS-SLH until disabled." :
            action==="toggle-enablement" ? "Change the persistent ORG-SLH enablement policy? Enabling starts the configured service; disabling stops it and prevents automatic startup." :
            action==="toggle" ? "Start or temporarily stop ORG-SLH according to its current verified state? Stop retains the configured auto-start policy." :
            action==="activate" ? "Enable the configured Organization Service and Worker listener?" :
            "Confirm the authorized operation?"))
            throw Error("MCEL_SLH_PORT:administrator-canceled");
          const response=await fetcher("/api/applications/slh/setup/action", {
            method:"POST",credentials:"same-origin",headers:{Accept:"application/json",
              "Content-Type":"application/json","X-MCEL-SLH-Operation":action,
              ...(accessToken ? {Authorization:"Bearer "+accessToken} : {})},
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
          if(!["STARTED","ALREADY_RUNNING","INITIALIZED","CONFIGURED","RUNNING","ENABLED","STOPPED","DISABLED"].includes(result.outcome) ||
             typeof result.message!=="string" || result.message.length>512)
            throw Error("MCEL_SLH_PORT:invalid-setup-receipt");
          workerViewOverview=null;
          if(action==="start")identitySnapshot=null;
          setupChanged(action);
          return {outcome:result.outcome,message:result.message,deviceId:result.deviceId||""};
        }
        if(req.intentId===rename){
          await ensureAuthorization();
          if(!global.confirm || !global.confirm("Rename authenticated Worker "+req.arguments.deviceId+
              " to '"+req.arguments.label+"'? This changes the organization's audited device directory."))
            throw Error("MCEL_SLH_PORT:administrator-canceled");
          const {response:result,data}=await authorizedJsonRequest("/api/applications/slh/worker/rename",{
            method:"POST",headers:{"Content-Type":"application/json",
              "X-MCEL-SLH-Operation":"rename"},body:JSON.stringify({device_id:req.arguments.deviceId,label:req.arguments.label})});
          if(!result?.ok)throw Error("MCEL_SLH_PORT:worker-mutation-refused");
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
          const failure=result.organizationFailure;
          // Only fixed, sanitized LMS status evidence can enter the display
          // and operational diagnostics; never render exception strings.
          organizationFailure=null;
          if(plain(failure) && (state==="failed" || state==="stopped_unexpectedly")){
            const code=failure.errorCode;
            if(["ORG_SLH_BIND_ADDRESS_UNAVAILABLE","ORG_SLH_LISTENER_BIND_FAILED"].includes(code) &&
               failure.stage==="LISTENER_BIND" &&
               typeof failure.bindAddress==="string" &&
               /^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$/.test(failure.bindAddress) &&
               failure.bindAddress.split(".").every(n=>Number(n)<=255) &&
               Number.isSafeInteger(failure.port) && failure.port>=1 && failure.port<=65535 &&
               (failure.osError===null || Number.isInteger(failure.osError) && failure.osError>=0 && failure.osError<=65535) &&
               (failure.restartCount===null || Number.isInteger(failure.restartCount) && failure.restartCount>=0 && failure.restartCount<=10000) &&
               typeof failure.restartLimitReached==="boolean"){
              organizationFailure={errorCode:code,stage:"LISTENER_BIND",
                bindAddress:failure.bindAddress,port:failure.port,osError:failure.osError,
                restartCount:failure.restartCount,restartLimitReached:failure.restartLimitReached,
                explanation:code==="ORG_SLH_BIND_ADDRESS_UNAVAILABLE"?
                  "The configured bind address is not assigned to this computer.":
                  "The configured listener could not bind.",
                observedAt:typeof failure.observedAt==="string"?failure.observedAt.slice(0,40):""};
            }
          }
          if(typeof state!=="string" || state.length>80 || typeof endpoint!=="string" || endpoint.length>120)
            throw Error("MCEL_SLH_PORT:invalid-lifecycle-state");
          // The overview can succeed while its underlying 1.5-second LMS
          // probe has timed out. Never manufacture a configuration from that
          // successful HTTP response. A wildcard bind is valid *only* when
          // explicitly present in the observed endpoint.
          const match=/^((?:[0-9]{1,3}\.){3}[0-9]{1,3}):([0-9]{1,5})$/.exec(endpoint);
          const host=match?.[1]||"";
          const port=match?Number(match[2]):0;
          const validEndpoint=!!match && host.split(".").every(part=>Number(part)<=255) &&
            Number.isSafeInteger(port) && port>=1 && port<=65535;
          const serviceState=String(result.overview.serviceState||"UNKNOWN");
          const observationState=String(result.overview.observationState||"UNKNOWN");
          const stateVerified=!(["NOT_OBSERVED","UNVERIFIED","UNAVAILABLE"].includes(state) ||
            ["NOT_OBSERVED","UNREACHABLE","UNVERIFIED"].includes(serviceState) ||
            ["UNAVAILABLE","NOT_OBSERVED","UNVERIFIED"].includes(observationState));
          const classification=validEndpoint && stateVerified?"observed":
            !validEndpoint?"endpoint-unverified":"service-unverified";
          lifecycleObservation={serviceState,observationState,organizationState:state,
            endpoint,classification,observedAt:new Date().toISOString()};
          if(classification!=="observed")
            throw Error("MCEL_SLH_PORT:lifecycle-"+classification);
          return {state,bindAddress:host,port};
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
          // UNAVAILABLE and UNVERIFIED are failed observations, not verified
          // empty directories. Preserve the separate status projection while
          // refusing to publish an empty list as an authoritative result.
          if(req.intentId==="intent:refresh-identities"){
            if(result.state!=="AVAILABLE")throw Error("MCEL_SLH_PORT:identity-directory-"+result.state.toLowerCase());
            return result.identities;
          }
          return {state:result.state,defaultDeviceId:result.defaultDeviceId,explanation:result.explanation};
        }
        const view=req.intentId.includes("worker")?"workers":"overview";
        const {response,data:result}=view==="workers"
          ? await authorizedJsonRequest("/api/applications/slh/observe?view=workers",{method:"GET",replayOnInvalidSession:true})
          : await (async()=>{
              const response=await fetcher("/api/applications/slh/observe?view=overview",
                {method:"GET",credentials:"same-origin",headers:headers()});
              return {response,data:await response?.json?.().catch(()=>null)};
            })();
        if(!response?.ok)throw Error("MCEL_SLH_PORT:observation-unavailable");
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
