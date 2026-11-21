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
    "intent:select-destination":["worker-destination-control","select","selectWorkerDestination","consequential",["network","requestedRing"]],
    "intent:commit-hub-choice":["worker-hub-choice-control","save","saveWorkerHubChoice","consequential",["network"]],
    "intent:disconnect":["worker-destination-control","disconnect","disconnectWorkerDestination","consequential",[]],
    "intent:save-policy":["worker-policy-control","save","saveWorkerPolicy","consequential",["availabilityMode","remoteEnabled","remoteMode","sellerEnabled"]],
    "intent:save-slh-target":["worker-slh-target-control","save","saveWorkerSlhTarget","consequential",["hubUrl"]]
  });
  const networks=new Set(["none","mainnet","testnet","test","dev","slh"]);
  const modes=new Set(["totally_idle","ai_idle"]);
  const remoteModes=new Set(["never","ask-when-busy","auto-when-busy"]);
  const own=(obj,key)=>Object.prototype.hasOwnProperty.call(obj,key);
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
    }else if(req.intentId==="intent:save-policy"){
      if(typeof args.sellerEnabled!=="boolean"||typeof args.remoteEnabled!=="boolean"||
         !modes.has(args.availabilityMode)||!remoteModes.has(args.remoteMode))return null;
    }
    return spec;
  }
  function create(options={}){
    if(options.appId!=="worker")throw Error("MCEL_WORKER:app-not-admitted");
    const fetcher=options.fetcher||global.fetch?.bind(global);
    if(typeof fetcher!=="function")throw Error("MCEL_WORKER:fetch-unavailable");
    let lastObservation=null;
    let observationIssued=0;
    let observationAccepted=0;
    let selectedDestinationEpoch=0;
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
              sellerAvailabilityMode:args.availabilityMode,remoteEnabled:args.remoteEnabled,
              remoteMode:args.remoteMode},
            changed_fields:["sellerEnabled","rentalEnabled","sellerAvailabilityMode",
              "remoteEnabled","remoteMode"]});
          const policy=(await observe()).policy;
          if(policy.sellerEnabled!==args.sellerEnabled ||
             policy.availabilityMode!==args.availabilityMode ||
             policy.remoteEnabled!==args.remoteEnabled ||
             policy.remoteMode!==args.remoteMode)
            throw Error("MCEL_WORKER:policy-postcondition-unverified");
          return policy;
        }
        throw Error("MCEL_WORKER:operation-not-admitted");
      },
      lastObservation:()=>lastObservation
    });
  }
  global.McelSemanticWorkerPorts=Object.freeze({create});
})(typeof globalThis!=="undefined"?globalThis:this);
