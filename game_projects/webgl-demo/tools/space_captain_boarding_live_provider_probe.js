// Simulates the exact NanoJev response contract against the actual browser
// live-provider and authoritative boarding orders. The reply is INJECTED;
// use space_captain_boarding_ai_calls_smoke.py to call the real checkpoint.
const R=globalThis.MainComputerBridgeEncounterRuntime;
const P=globalThis.MainComputerBridgeCaptainDecisionPolicy;
const Live=globalThis.MainComputerBridgeLiveCaptainProvider;
if(!R||!P||!Live)throw new Error('BOARDING_PROVIDER_MODULES_REQUIRED');
const delay=()=>new Promise(resolve=>setTimeout(resolve,0));
const makeObs=(runtime,time)=>{
 const a=runtime.ships['ship.alpha'],b=runtime.ships['ship.beta'];
 const dx=b.xM-a.xM,dy=b.yM-a.yM,dvx=b.vxMps-a.vxMps,dvy=b.vyMps-a.vyMps;
 const r=Math.hypot(dx,dy);
 return {schema:'game.bridgeCaptainObservation.v1',captainId:'captain.beta',shipId:'ship.beta',
 simulationSeconds:time,rangeM:r,radialVelocityMps:(dx*dvx+dy*dvy)/Math.max(1,r),
 relativePositionM:[dx,dy],relativeVelocityMps:[dvx,dvy],
 relativeSpeedMps:Math.hypot(dvx,dvy),
 transporterMaxRangeM:2500,boarding:{...runtime.boarding},
 targetHullPercent:runtime.targetHullPercent,mission:'pursue-main-ship-and-seek-boarding-range'};
};
async function runBranch(strategy) {
 const rt=R.create({physicalPlayerAuthority:true});const now=t=>9000+1000*t;
 let selected=null,currentTime=0,requests=0;
 const accepted=[];
 const provider=Live.create({request:obs=>{
  requests++;
  return Promise.resolve({ok:true,schema:'game.bridgeCaptainNanoJevDecision.v1',
    captainId:'captain.beta',shipId:'ship.beta',source:'nanojev-captain-v6',
    actionType:'boarding',boardingAction:selected,observationSeconds:obs.simulationSeconds,
    modelReceipt:{clientRequestId:`call-${requests}`,checkpointId:'test-real',checkpointSha256:'sha-of-model'}});
 },onDecision:result=>{
  const revision=Math.max(1,rt.enemyCaptainRevision+1);
  const order={captainId:'captain.beta',shipId:'ship.beta',
    decisionId:`nanojev-boarding-${revision}`,revision,
    issuedAtSeconds:currentTime,validThroughSeconds:currentTime+50,
    action:result.boardingAction,modelReceipt:result.modelReceipt,source:result.source};
  const response=rt.command({type:'captain-boarding-order',order},now(currentTime));
  if(response.accepted)accepted.push({action:result.boardingAction,receipt:response.snapshot.boarding.modelReceipt});
  return response.accepted;
 }});
 rt.advance(now(0),{active:true});
 const approach=P.decide({observation:makeObs(rt,0),nextRevision:1});
 rt.command({type:'captain-helm-order',order:approach},now(0));
 for(let t=3;t<=21;t+=3)rt.advance(now(t),{active:true});
 currentTime=21;selected='initiate';
 if(!provider.tick(makeObs(rt,21))) throw new Error('FIRST_NANOJEV_NOT_SENT');
 await delay();await delay();
 const began=rt.boarding.phase==='deploying';
 const unauthorizedWithdraw=rt.command({type:'captain-helm-order',order:{
   captainId:'captain.beta',shipId:'ship.beta',revision:rt.enemyCaptainRevision+1,
   decisionId:'premature-escape',maneuver:'withdraw',issuedAtSeconds:21,validThroughSeconds:70}},now(21));
 for(let t=24;t<=51;t+=3){rt.advance(now(t),{active:true});
    if(t===24){const hold=P.decide({observation:makeObs(rt,t),activeOrder:rt.enemyCaptainOrder,
      nextRevision:rt.enemyCaptainRevision+1});if(hold)rt.command({type:'captain-helm-order',order:hold},now(t));}}
 const deployed=rt.boarding.phase==='deployed';
 rt.advance(now(60),{active:true});rt.command({type:'fire-primary-weapon'},now(60));rt.advance(now(63),{active:true});
 currentTime=63;selected=strategy;
 provider.tick(makeObs(rt,63));await delay();await delay();
 const inProgress=rt.boarding.phase===(strategy==='recall'?'recalling':'abandoning');
 const resolvedTime=63+(strategy==='recall'?24:12);
 rt.advance(now(resolvedTime),{active:true});
 const final=rt.boarding.phase;
 const lastReceipt=provider.status().lastReceipt;
 const complete=rt.eventsSince(0).filter(e=>e.kind==='boarding-complete');
 return {began,deployed,inProgress,final,lastReceipt,accepted,complete,
  unauthorizedWithdraw:unauthorizedWithdraw.reason,requests};
}
const recall=await runBranch('recall'),abandon=await runBranch('abandon');
const checks={
 modelCanIssueBoardingInitiation:recall.began&&abandon.began,
 modelIssuedOrdersUseActualAuthority:recall.accepted.length===2&&abandon.accepted.length===2,
 mustResolveBoardersBeforeWithdrawal:recall.unauthorizedWithdraw==='boarders-committed-choose-recall-or-abandon'&&
  abandon.unauthorizedWithdraw==='boarders-committed-choose-recall-or-abandon',
 modelCanRecall:recall.inProgress&&recall.final==='recovered',
 modelCanAbandon:abandon.inProgress&&abandon.final==='abandoned',
 modelCheckpointSurvivesAllEvents:[recall,abandon].every(x=>
  x.accepted.every(a=>a.receipt?.checkpointSha256==='sha-of-model')&&
  x.complete.every(e=>e.checkpointSha256==='sha-of-model')),
 liveProviderCountsEachDecision:recall.requests===2&&abandon.requests===2,
};
const failedChecks=Object.entries(checks).filter(([,v])=>!v).map(([k])=>k);
return {ok:failedChecks.length===0,schema:'game.spaceCaptainBoardingLiveProviderProbe.v1',checks,failedChecks,
  metrics:{recall,abandon}};
