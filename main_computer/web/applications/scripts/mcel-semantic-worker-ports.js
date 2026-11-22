"use strict";
/* Bounded, typed Worker capabilities. No ambient wallet provider, private key,
 * Hub offer registration, SLH enrollment, or generic settings mutation.
 * The transport accepts only exact MCEL IR operation identities.
 */
(function(global){
  const requests=Object.freeze({
    "intent:refresh-destinations":["worker-observer","destinations","observeWorkerDestinations","external-read",[]],
    "intent:refresh-destination":["worker-observer","destination","observeWorkerDestination","external-read",[]],
    "intent:refresh-runtime":["worker-observer","runtime","observeWorkerRuntime","external-read",[]],
    "intent:refresh-policy":["worker-observer","policy","observeWorkerPolicy","external-read",[]],
    "intent:refresh-slh-target":["worker-observer","slh-target","observeWorkerSlhTarget","external-read",[]],
    "intent:refresh-identities":["worker-observer","identities","observeWorkerIdentities","external-read",[]],
    "intent:refresh-identity-status":["worker-observer","identity-status","observeWorkerIdentityStatus","external-read",[]],
    "intent:select-destination":["worker-destination-control","select","selectWorkerDestination","consequential",["network","requestedRing"]],
    "intent:commit-hub-choice":["worker-hub-choice-control","save","saveWorkerHubChoice","consequential",["network"]],
    "intent:disconnect":["worker-destination-control","disconnect","disconnectWorkerDestination","consequential",[]],
    "intent:save-policy":["worker-policy-control","save","saveWorkerPolicy","consequential",["availabilityMode","sellerEnabled"]],
    "intent:save-slh-target":["worker-slh-target-control","save","saveWorkerSlhTarget","consequential",["hubUrl"]],
    "intent:retry-hub":["worker-destination-control","retry","retryWorkerDestination","consequential",[]],
    "intent:refresh-seller":["worker-marketplace-observer","seller","observeMarketplaceSeller","external-read",[]],
    "intent:refresh-requester":["worker-marketplace-observer","requester","observeMarketplaceRequester","external-read",[]],
    "intent:refresh-marketplace-status":["worker-marketplace-observer","status","observeMarketplaceStatus","external-read",[]],
    "intent:save-seller":["worker-marketplace-settings","seller","saveMarketplaceSeller","consequential",
      ["sellerTargetTokens","sellerCreditsPerToken","maxConcurrency","models","capability","executionMode"]],
    "intent:save-requester":["worker-marketplace-settings","requester","saveMarketplaceRequester","consequential",
      ["remoteEnabled","remoteMode","remoteCreditsPerToken","remoteMaxOutputTokens","remoteDailyLimit","remoteAskBeforeSpend","remoteOnlyWhenBusy"]],
    "intent:register-offer":["worker-marketplace-actions","register","registerMarketplaceOffer","consequential",[]],
    "intent:work-now":["worker-marketplace-actions","work-now","startMarketplaceWorkNow","consequential",["minutes"]],
    "intent:finish-work-now":["worker-marketplace-actions","finish","finishMarketplaceWorkNow","consequential",[]],
    "intent:pause-selling":["worker-marketplace-actions","pause","pauseMarketplaceSelling","consequential",[]]
  });
  const networks=new Set(["none","mainnet","testnet","test","dev","slh"]);
  const modes=new Set(["totally_idle","ai_idle"]);
  const remoteModes=new Set(["never","ask-when-busy","auto-when-busy"]);
  const own=(obj,key)=>Object.prototype.hasOwnProperty.call(obj,key);
  const positiveDecimal=v=>typeof v==="string" && /^\d+(?:\.\d{1,18})?$/.test(v) &&
    v.length<=38 && /[1-9]/.test(v);
  const positiveInt=(v,max)=>Number.isSafeInteger(v)&&v>=1&&v<=max;
  const sellerKeys=["sellerTargetTokens","sellerCreditsPerToken","maxConcurrency","models","capability","executionMode"];
  const requesterKeys=["remoteEnabled","remoteMode","remoteCreditsPerToken","remoteMaxOutputTokens",
    "remoteDailyLimit","remoteAskBeforeSpend","remoteOnlyWhenBusy"];
  const verifySeller=v=>plain(v)&&keysEqual(v,sellerKeys)&&
    positiveInt(v.sellerTargetTokens,128000)&&positiveDecimal(v.sellerCreditsPerToken)&&
    positiveInt(v.maxConcurrency,128)&&short(v.models,500)&&v.models.length>0&&
    /^[a-zA-Z0-9_.,:\-/ ]+$/.test(v.models)&&v.models.split(",").length<=8&&
    v.capability==="chat.completions"&&v.executionMode==="worker_pull_v0";
  const verifyRequester=v=>plain(v)&&keysEqual(v,requesterKeys)&&
    typeof v.remoteEnabled==="boolean"&&remoteModes.has(v.remoteMode)&&
    positiveDecimal(v.remoteCreditsPerToken)&&positiveInt(v.remoteMaxOutputTokens,128000)&&
    Number.isSafeInteger(v.remoteDailyLimit)&&v.remoteDailyLimit>=0&&v.remoteDailyLimit<=1000000000&&
    typeof v.remoteAskBeforeSpend==="boolean"&&typeof v.remoteOnlyWhenBusy==="boolean";
  const marketplaceNetworks=new Set(["mainnet","testnet","test","dev"]);
  const plain=value=>value!==null&&typeof value==="object"&&!Array.isArray(value)&&
    Object.getPrototypeOf(value)===Object.prototype;
  const keysEqual=(value,expected)=>Object.keys(value).sort().join(",")===expected.slice().sort().join(",");
  const short=(value,max=1024)=>typeof value==="string"&&value.length<=max;
  const destinationFields=["selectedNetwork","connectionStatus","hub","chainId","requestedRing",
    "assignedRing","workerId","explanation","slhEnrollment"];
  const runtimeFields=["state","phase","reason","activeJobs","allowedToAccept"];
  const policyFields=["sellerEnabled","availabilityMode","remoteEnabled","remoteMode"];
  const slhTargetFields=["hubUrl","authenticationState"];
  const validSlhUrl=value=>typeof value==="string"&&value.length<=2048&&/^https:\/\/[^\s/?#]+(?::[0-9]{1,5})?\/?$/.test(value) && !value.includes("@");
  function validObservation(data){
    if(!plain(data)||data.ok!==true||data.schema!=="mcel.worker.observation.v1"||
       !Array.isArray(data.destinations)||data.destinations.length<2||data.destinations.length>8||
       !data.destinations.every(item=>plain(item)&&keysEqual(item,["network","label"])&&
         networks.has(item.network)&&short(item.label,80))||
       new Set(data.destinations.map(item=>item.network)).size!==data.destinations.length||
       !networks.has(data.hubChoice)||
       !data.destinations.some(row=>row.network===data.hubChoice)||
       !plain(data.destination)||!keysEqual(data.destination,destinationFields)||
       !destinationFields.filter(field=>field!=="requestedRing").every(field=>short(data.destination[field]))||
       !Number.isSafeInteger(data.destination.requestedRing)||
       data.destination.requestedRing<0||data.destination.requestedRing>3||
       !networks.has(data.destination.selectedNetwork)||
       !["NOT_VERIFIED","NOT_APPLICABLE"].includes(data.destination.slhEnrollment)||
       (data.destination.selectedNetwork==="slh")!==(data.destination.slhEnrollment==="NOT_VERIFIED")||
       !plain(data.slhTarget)||!keysEqual(data.slhTarget,slhTargetFields)||
       !short(data.slhTarget.hubUrl,2048)||
       data.slhTarget.authenticationState!=="NOT_VERIFIED"||
       !plain(data.runtime)||!keysEqual(data.runtime,runtimeFields)||
       !["state","phase","reason"].every(field=>short(data.runtime[field]))||
       !Number.isSafeInteger(data.runtime.activeJobs)||data.runtime.activeJobs<0||
       typeof data.runtime.allowedToAccept!=="boolean"||
       !plain(data.policy)||!keysEqual(data.policy,policyFields)||
       typeof data.policy.sellerEnabled!=="boolean"||
       typeof data.policy.remoteEnabled!=="boolean"||
       !modes.has(data.policy.availabilityMode)||!remoteModes.has(data.policy.remoteMode))return false;
    return true;
  }
  function specification(req){
    if(!plain(req)||req.appId!=="worker"||!plain(req.arguments))return null;
    const spec=requests[req.intentId];
    if(!spec||req.effectId!=="effect:"+req.intentId.slice(7)+".request"||
       req.capabilityId!=="capability:"+spec[0]||req.operation!==spec[1]||
       req.runtimeMethod!==spec[2]||req.risk!==spec[3]||!keysEqual(req.arguments,spec[4]))return null;
    const args=req.arguments;
    if(req.intentId==="intent:commit-hub-choice"){
      if(!networks.has(args.network))return null;
    }else if(req.intentId==="intent:select-destination"){
      if(!networks.has(args.network)||!Number.isSafeInteger(args.requestedRing)||
         args.requestedRing<0||args.requestedRing>3)return null;
    }else if(req.intentId==="intent:save-slh-target"){
      if(!validSlhUrl(args.hubUrl))return null;
    }else if(req.intentId==="intent:save-seller"){
      if(!verifySeller(args))return null;
    }else if(req.intentId==="intent:save-requester"){
      if(!verifyRequester(args))return null;
    }else if(req.intentId==="intent:work-now"){
      if(!positiveInt(args.minutes,10080))return null;
    }else if(req.intentId==="intent:save-policy"){
      if(typeof args.sellerEnabled!=="boolean"||!modes.has(args.availabilityMode))return null;
    }
    return spec;
  }
  function create(options={}){
    if(options.appId!=="worker")throw Error("MCEL_WORKER:app-not-admitted");
    const fetcher=options.fetcher||global.fetch?.bind(global);
    if(typeof fetcher!=="function")throw Error("MCEL_WORKER:fetch-unavailable");
    let lastObservation=null;
    let identitySnapshot=null;
    let identityIssued=0;
    let identityAccepted=0;
    let observationIssued=0;
    let observationAccepted=0;
    let selectedDestinationEpoch=0;
    let operationInFlight=false;
    let settingsIssued=0;
    let settingsAccepted=0;
    let lastMarketplaceSettings=null;
    function sellerProjection(s){
      return {sellerTargetTokens:Number(s.sellerTargetTokens||1024),
        sellerCreditsPerToken:String(s.sellerCreditsPerToken||"0.001"),
        maxConcurrency:Number(s.maxConcurrency||1),
        models:String(s.models||"gemma4:26b-a4b-it-q4_K_M"),
        capability:String(s.capability||"chat.completions"),
        executionMode:String(s.executionMode||"worker_pull_v0")};
    }
    function requesterProjection(s){
      return {remoteEnabled:s.remoteEnabled===true,
        remoteMode:remoteModes.has(s.remoteMode)?s.remoteMode:"ask-when-busy",
        remoteCreditsPerToken:String(s.remoteCreditsPerToken||"0.001"),
        remoteMaxOutputTokens:Number(s.remoteMaxOutputTokens||1024),
        remoteDailyLimit:Number(s.remoteDailyLimit??100000),
        remoteAskBeforeSpend:s.remoteAskBeforeSpend!==false,
        remoteOnlyWhenBusy:s.remoteOnlyWhenBusy!==false};
    }
    async function readSettings(){
      const issued=++settingsIssued;
      const data=await json("/api/applications/worker/settings");
      const settings=data.settings;
      if(!plain(settings))throw Error("MCEL_WORKER:settings-not-object");
      if(!verifySeller(sellerProjection(settings))||!verifyRequester(requesterProjection(settings)))
        throw Error("MCEL_WORKER:settings-invalid");
      if(issued>=settingsAccepted){settingsAccepted=issued;lastMarketplaceSettings=settings;}
      return settings;
    }
    function bridge(){
      const value=global.McelWorkerLegacyMarketplace;
      if(!value || typeof value.register!=="function" || typeof value.workNow!=="function")
        throw Error("MCEL_WORKER:legacy-marketplace-authority-not-initialized");
      return value;
    }
    function authorizedMarketplace(){
      const selected=global.__mcelWorker?.app?.readState("state:selected-network");
      if(!marketplaceNetworks.has(selected)||lastObservation?.hubChoice!==selected||
         lastObservation?.destination?.selectedNetwork!==selected||
         lastObservation?.destination?.connectionStatus!=="connected")
        throw Error("MCEL_WORKER:matching-connected-marketplace-session-required");
      return selected;
    }
    async function runExclusive(operation){
      if(operationInFlight)throw Error("MCEL_WORKER:marketplace-operation-in-flight");
      operationInFlight=true;
      try{return await operation();}finally{operationInFlight=false;}
    }
    async function runtimeProjection(){
      const [session,rt]=await Promise.all([
        json("/api/applications/worker/network-session"),
        json("/api/applications/worker/runtime-status")
      ]);
      const current=session.session||{};
      const runtime=rt.runtime||{};
      const override=rt.workNowOverride||runtime.workNowOverride||runtime.work_now_override||
        rt.localPolicy?.workNowOverride||rt.localPolicy?.work_now_override||{};
      const expires=String(override.expiresAt||override.expires_at||"");
      const remaining=Date.parse(expires);
      const status={network:String(current.selected_network||"none"),
        connectionStatus:String(current.connection_status||"disconnected"),
        registrationStatus:String(current.hub_registration?.status||current.signed_connection?.hub_registration_status||"not_submitted"),
        workerId:String(current.worker_id||""),assignedRing:String(current.assigned_ring||""),
        phase:String(runtime.phase||"not_accepting"),activeJobs:Number(runtime.active_jobs||0),
        lastHeartbeat:String(runtime.last_heartbeat_at||""),
        lastError:String(runtime.lastError||runtime.heartbeat_error||rt.reason||""),
        workNowState:Number.isFinite(remaining)&&remaining>Date.now()?"ACTIVE":"INACTIVE",
        workNowExpiresAt:expires,
        remainingSeconds:Number.isFinite(remaining)?Math.max(0,Math.ceil((remaining-Date.now())/1000)):0};
      if(!Number.isSafeInteger(status.activeJobs)||status.activeJobs<0)
        throw Error("MCEL_WORKER:runtime-invalid");
      return status;
    }
    async function json(url,method="GET",body){
      const response=await fetcher(url,{method,credentials:"same-origin",headers:{Accept:"application/json",
        ...(method==="POST"?{"Content-Type":"application/json"}:{})},
        ...(method==="POST"?{body:JSON.stringify(body)}:{})});
      if(!response||typeof response.ok!=="boolean")throw Error("MCEL_WORKER:transport-unverified");
      const result=await response.json().catch(()=>null);
      if(!response.ok||!plain(result)||result.ok!==true){
        const reason=short(result?.error,240)?result.error:"operation-failed";
        throw Error("MCEL_WORKER:"+method+":"+response.status+":"+reason);
      }
      return result;
    }
    async function observe(){
      const issued=++observationIssued;
      const data=await json("/api/applications/worker/mcel-observation");
      if(!validObservation(data))throw Error("MCEL_WORKER:observation-invalid");
      // A late response from an earlier read must not regress the Hub Choice
      // authority after a post-write verification observation has settled.
      if(issued>=observationAccepted){
        observationAccepted=issued;
        lastObservation=data;
      }
      return data;
    }
    // Consume the same guarded, local-only identity inventory as native SLH.
    // Never fabricate an empty inventory when LMS is unavailable; that is an
    // observation failure, not evidence that identities were deleted.
    async function observeIdentities(){
      const issued=++identityIssued;
      const response=await fetcher("/api/applications/slh/identities",{
        method:"GET",credentials:"same-origin",headers:{Accept:"application/json"}
      });
      if(!response?.ok)throw Error("MCEL_WORKER:identity-directory-unavailable");
      const result=await response.json().catch(()=>null);
      if(!plain(result)||result.schema!=="mcel.slh.identities.v4b"||
         !["AVAILABLE","UNAVAILABLE","UNVERIFIED"].includes(result.state)||
         !Array.isArray(result.identities)||result.identities.length>128||
         !short(result.defaultDeviceId,64)||!short(result.explanation,512)||
         !result.identities.every(row=>plain(row)&&
           keysEqual(row,["deviceId","label","fingerprint","defaultStatus"])&&
           /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(row.deviceId)&&
           short(row.label,120)&&/^[a-f0-9]{64}$/.test(row.fingerprint)&&
           ["DEFAULT","ADDITIONAL"].includes(row.defaultStatus))||
         (result.state==="AVAILABLE" && !result.identities.some(row=>
           row.deviceId===result.defaultDeviceId && row.defaultStatus==="DEFAULT"))||
         (result.state!=="AVAILABLE" && (result.identities.length || result.defaultDeviceId)))
        throw Error("MCEL_WORKER:invalid-identity-observation");
      if(issued>=identityAccepted){identityAccepted=issued;identitySnapshot=result;}
      return result;
    }
    function project(data,operation){
      return operation==="destinations"?data.destinations:operation==="destination"?data.destination:
        operation==="runtime"?data.runtime:operation==="slh-target"?data.slhTarget:data.policy;
    }
    return Object.freeze({
      authorize:req=>Boolean(specification(req)) && (
        req.risk==="external-read"||
        (lastObservation!==null && (!["intent:select-destination","intent:commit-hub-choice"].includes(req.intentId)||
          lastObservation.destinations.some(row=>row.network===req.arguments.network)))
      ),
      invoke:async req=>{
        const spec=specification(req);
        if(!spec)throw Error("MCEL_WORKER:operation-not-admitted");
        if(req.intentId==="intent:refresh-identities" || req.intentId==="intent:refresh-identity-status"){
          const result=await observeIdentities();
          if(req.intentId==="intent:refresh-identities"){
            if(result.state!=="AVAILABLE")throw Error("MCEL_WORKER:identity-directory-"+result.state.toLowerCase());
            // Do not publish fingerprints or credential material into the
            // Worker presentation state. The ID is a selection key only.
            return result.identities.map(row=>({
              deviceId:row.deviceId,label:row.label,
              defaultStatus:row.defaultStatus==="DEFAULT"?"Default identity":"Additional identity",
              accessStatus:"SLH/SEH access not verified"
            }));
          }
          return {state:result.state,defaultDeviceId:result.defaultDeviceId,explanation:result.explanation};
        }
        if(req.intentId==="intent:refresh-seller"||req.intentId==="intent:refresh-requester"){
          const settings=await readSettings();
          if(req.intentId==="intent:refresh-seller"){
            const value=sellerProjection(settings);
            try{bridge().setSeller(value,settings);}catch(_){}
            return value;
          }
          return requesterProjection(settings);
        }
        if(req.intentId==="intent:refresh-marketplace-status")return runtimeProjection();
        if(spec[3]==="external-read")return project(await observe(),spec[1]);
        if(!lastObservation)throw Error("MCEL_WORKER:observation-required-before-mutation");
        if(req.intentId==="intent:commit-hub-choice"){
          const choice=req.arguments.network;
          if(!lastObservation.destinations.some(row=>row.network===choice))
            throw Error("MCEL_WORKER:hub-choice-not-observed");
          await json("/api/applications/worker/settings","POST",{
            mcelHubChoice:choice,changed_fields:["mcelHubChoice"]
          });
          const observed=await observe();
          if(observed.hubChoice!==choice)
            throw Error("MCEL_WORKER:hub-choice-postcondition-unverified");
          return observed.hubChoice;
        }
        if(spec[1]==="select"){
          if(!lastObservation.destinations.some(row=>row.network===req.arguments.network))
            throw Error("MCEL_WORKER:destination-not-observed");
          const epoch=++selectedDestinationEpoch;
          await json("/api/applications/worker/network-session","POST",{
            network:req.arguments.network,requested_ring:String(req.arguments.requestedRing)});
          const result=await observe();
          if(epoch!==selectedDestinationEpoch)throw Error("MCEL_WORKER:destination-operation-superseded");
          if(result.destination.selectedNetwork!==req.arguments.network ||
             result.destination.requestedRing!==req.arguments.requestedRing)
            throw Error("MCEL_WORKER:destination-postcondition-unverified");
          return result.destination;
        }
        if(spec[1]==="disconnect"){
          const epoch=++selectedDestinationEpoch;
          await json("/api/applications/worker/network-session","POST",{
            network:"none",requested_ring:lastObservation.destination.requestedRing||"3"});
          const result=await observe();
          if(epoch!==selectedDestinationEpoch)throw Error("MCEL_WORKER:destination-operation-superseded");
          if(result.destination.selectedNetwork!=="none")
            throw Error("MCEL_WORKER:disconnect-postcondition-unverified");
          return result.destination;
        }
        if(req.intentId==="intent:retry-hub"){
          const selected=lastObservation.hubChoice;
          if(!marketplaceNetworks.has(selected))throw Error("MCEL_WORKER:marketplace-hub-required");
          const ring=lastObservation.destination.requestedRing;
          const epoch=++selectedDestinationEpoch;
          await json("/api/applications/worker/network-session","POST",{
            network:selected,requested_ring:String(ring)
          });
          const observed=await observe();
          if(epoch!==selectedDestinationEpoch||observed.destination.selectedNetwork!==selected)
            throw Error("MCEL_WORKER:retry-postcondition-unverified");
          return observed.destination;
        }
        if(req.intentId==="intent:save-seller"||req.intentId==="intent:save-requester"){
          return runExclusive(async()=>{
            const args=req.arguments;
            const fields=req.intentId==="intent:save-seller"?sellerKeys:requesterKeys;
            const before=await readSettings();
            const body={settings:{},changed_fields:fields};
            for(const key of fields)body.settings[key]=args[key];
            await json("/api/applications/worker/settings","POST",body);
            const next=await readSettings();
            const projected=req.intentId==="intent:save-seller"?sellerProjection(next):requesterProjection(next);
            if(fields.some(key=>projected[key]!==args[key]))
              throw Error("MCEL_WORKER:marketplace-settings-postcondition-unverified");
            if(req.intentId==="intent:save-seller"){
              try{bridge().setSeller(projected,next);}catch(_){}
            }
            return projected;
          });
        }
        if(["intent:register-offer","intent:work-now","intent:finish-work-now","intent:pause-selling"].includes(req.intentId)){
          return runExclusive(async()=>{
            if(req.intentId==="intent:pause-selling"){
              await json("/api/applications/worker/settings","POST",{
                settings:{sellerEnabled:false,rentalEnabled:false},
                changed_fields:["sellerEnabled","rentalEnabled"]
              });
              const saved=await readSettings();
              if(saved.sellerEnabled!==false)throw Error("MCEL_WORKER:pause-postcondition-unverified");
              return {status:"PAUSED",detail:"New paid jobs disabled; active work may drain"};
            }
            if(req.intentId==="intent:finish-work-now"){
              authorizedMarketplace();
              const result=await bridge().finish();
              return {status:"FINISH_REQUESTED",detail:String(result?.status||"Finish sent to Worker supervisor")};
            }
            authorizedMarketplace();
            const settings=await readSettings();
            if(settings.sellerEnabled!==true)throw Error("MCEL_WORKER:seller-not-enabled");
            const legacy=bridge();
            legacy.setSeller(sellerProjection(settings),settings);
            await legacy.refresh();
            if(req.intentId==="intent:register-offer"){
              const result=await legacy.register();
              if(!["accepted","deferred","pending"].includes(result?.status))
                throw Error("MCEL_WORKER:registration-not-acknowledged");
              return {status:String(result.status).toUpperCase(),
                detail:String(result.reason||result.worker_id||"Hub acknowledged offer sync")};
            }
            const result=await legacy.workNow(req.arguments.minutes*60);
            if(!result?.ok)throw Error("MCEL_WORKER:work-now-not-acknowledged");
            return {status:"WORK_NOW",detail:"Authorized temporary work for "+req.arguments.minutes+" minutes"};
          });
        }
        if(req.intentId==="intent:save-slh-target"){
          // Update only the endpoint; no device ID, fingerprint, private key,
          // enrollment, authentication or connection status may be claimed.
          const hubUrl=req.arguments.hubUrl.replace(/\/$/,"");
          await json("/api/applications/worker/settings","POST",{
            managedSLHUrl:hubUrl,changed_fields:["managedSLHUrl"]
          });
          const observed=(await observe()).slhTarget;
          if(observed.hubUrl.replace(/\/$/,"")!==hubUrl ||
             observed.authenticationState!=="NOT_VERIFIED")
            throw Error("MCEL_WORKER:slh-target-postcondition-unverified");
          return observed;
        }
        if(spec[1]==="save"){
          const args=req.arguments;
          // changed_fields prevents a policy edit from overwriting wallet,
          // signed connection, enrollment, price or server-owned offer state.
          await json("/api/applications/worker/settings","POST",{
            settings:{sellerEnabled:args.sellerEnabled,rentalEnabled:args.sellerEnabled,
              sellerAvailabilityMode:args.availabilityMode},
            changed_fields:["sellerEnabled","rentalEnabled","sellerAvailabilityMode"]});
          const policy=(await observe()).policy;
          if(policy.sellerEnabled!==args.sellerEnabled ||
             policy.availabilityMode!==args.availabilityMode)
            throw Error("MCEL_WORKER:policy-postcondition-unverified");
          return policy;
        }
        throw Error("MCEL_WORKER:operation-not-admitted");
      },
      lastObservation:()=>lastObservation,
      lastIdentityObservation:()=>identitySnapshot
    });
  }
  global.McelSemanticWorkerPorts=Object.freeze({create});
})(typeof globalThis!=="undefined"?globalThis:this);
