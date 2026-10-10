// The exact browser live-provider and production captain/helm authority are
// exercised with an injected *adapter reply*, not a fake model claim.
const Live = globalThis.MainComputerBridgeLiveCaptainProvider;
const Runtime = globalThis.MainComputerBridgeEncounterRuntime;
const Policy = globalThis.MainComputerBridgeCaptainDecisionPolicy;
if (!Live || !Runtime || !Policy) throw new Error('BRIDGE_LIVE_PROVIDER_NOT_LOADED');
const delay = () => new Promise(resolve => setTimeout(resolve, 0));
const first = Runtime.create({physicalPlayerAuthority:true, requirePhysicalWeaponRange:true});
first.advance(1000,{active:true});
const source = [];
let requests=0;
const request = observation => {
  requests++;
  return Promise.resolve({ok:true,schema:'game.bridgeCaptainNanoJevDecision.v1',
    captainId:'captain.beta',shipId:'ship.beta',source:'nanojev-captain-v6',
    maneuver:'withdraw',rangeM:null,observationSeconds:observation.simulationSeconds,
    modelReceipt:{checkpointSha256:'model-sha',clientRequestId:'req-1'}});
};
const provider=Live.create({request,onDecision:selection=>{
  const time=first.simulationSeconds(1000),revision=first.enemyCaptainRevision+1;
  const order={captainId:'captain.beta',shipId:'ship.beta',decisionId:'nanojev-'+revision,revision,
    issuedAtSeconds:time,validThroughSeconds:time+60,maneuver:selection.maneuver,source:selection.source};
  const accepted=first.command({type:'captain-helm-order',order},1000);
  if(accepted.accepted) source.push(accepted.snapshot.helm['ship.beta'].activeOrder.source);
  return accepted.accepted;
}});
const obs={schema:'game.bridgeCaptainObservation.v1',captainId:'captain.beta',shipId:'ship.beta',
  simulationSeconds:0,rangeM:2667.8,radialVelocityMps:-85,targetHullPercent:100,transporterMaxRangeM:2500,
  relativePositionM:[2600,598],relativeVelocityMps:[-85,-12],mission:'pursue-main-ship-and-seek-boarding-range'};
const checks={};
checks.liveProviderCreatesNoOrderBeforeBridge=first.enemyCaptainRevision===-1;
checks.liveRequestStarted=provider.tick(obs)===true;
checks.singleFlight=provider.tick(obs)===false;
await delay();await delay();
checks.realReplyPublishesThroughHelm=source.length===1 && first.enemyCaptainOrder?.source==='nanojev-captain-v6';
checks.realReceiptCaptured=provider.status().lastReceipt?.checkpointSha256==='model-sha';
checks.oneLiveDecisionPublished=provider.status().liveDecisionCount===1;
checks.retryIsBounded=provider.tick({...obs,simulationSeconds:1})===false;
const failed = [];
const errors=[];
const failedProvider=Live.create({request:()=>Promise.reject(new Error('model-offline')),
  onDecision:()=>{throw new Error('offline must never publish');}});
failedProvider.tick(obs);await delay();await delay();
checks.unavailableModelIsExplicit=failedProvider.status().lastOutcome==='error' &&
  failedProvider.status().mode==='deterministic-fallback';
checks.offlineRetryWaits=failedProvider.tick({...obs,simulationSeconds:10})===false;
let slowResolve;
const waiting=Live.create({request:()=>new Promise(resolve=>{slowResolve=resolve;}),onDecision:()=>{throw new Error('late result accepted');}});
waiting.tick(obs);await delay();waiting.reset();
slowResolve({ok:true});await delay();await delay();
checks.resetInvalidatesLateDecision=waiting.status().lastOutcome==='not-requested';
const missingReceipt=Live.create({request:()=>Promise.resolve({ok:true,schema:'game.bridgeCaptainNanoJevDecision.v1',
 captainId:'captain.beta',shipId:'ship.beta',source:'nanojev-captain-v6',maneuver:'approach',
 observationSeconds:0,modelReceipt:{}}),onDecision:()=>{throw new Error('missing receipt accepted');}});
missingReceipt.tick(obs);await delay();await delay();
checks.modelReceiptCannotBeFabricated=missingReceipt.status().lastOutcome==='error';
const failedChecks=Object.entries(checks).filter(([,ok])=>!ok).map(([name])=>name);
return {ok:failedChecks.length===0,checks,failedChecks,
  metrics:{publishedSources:source,modelRequests:requests,offlineStatus:failedProvider.status()}};
