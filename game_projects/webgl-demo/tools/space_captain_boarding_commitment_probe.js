// A complete *abstract* first-encounter boarding commitment, no boarding combat.
// Runs the production captain policy + encounter physics against fixed simulated time.
const R=globalThis.MainComputerBridgeEncounterRuntime;
const P=globalThis.MainComputerBridgeCaptainDecisionPolicy;
if (!R||!P) throw new Error('CAPTAIN_BOARDING_MODULES_REQUIRED');
// The replay uses the same authoritative per-ship capabilities as live gameplay.
// Do not introduce another (potentially drifting) transporter-range constant.
const enemyTransporterRangeM=P.SHIP_CAPABILITIES?.['ship.beta']?.transporter?.maxRangeM;
if(!Number.isFinite(enemyTransporterRangeM)||enemyTransporterRangeM<=0)
  throw new Error('BOARDING_REPLAY_ENEMY_TRANSPORTER_CAPABILITY_REQUIRED');
function scenario(evacuationStrategy) {
 const runtime=R.create({physicalPlayerAuthority:true});
 const base=12345,now=t=>base+t*1000;
 const captainOrders=[],records=[],decisionObservations=[];
 const observe=t=>{
   const a=runtime.ships['ship.alpha'],b=runtime.ships['ship.beta'];
   const dx=b.xM-a.xM,dy=b.yM-a.yM,dvx=b.vxMps-a.vxMps,dvy=b.vyMps-a.vyMps;
   const d=Math.hypot(dx,dy);
   return {schema:'game.bridgeCaptainObservation.v1',captainId:'captain.beta',shipId:'ship.beta',
     simulationSeconds:t,rangeM:d,radialVelocityMps:(dx*dvx+dy*dvy)/Math.max(d,1e-9),
     relativeSpeedMps:Math.hypot(dvx,dvy),targetHullPercent:runtime.targetHullPercent,
     relativePositionM:[dx,dy],relativeVelocityMps:[dvx,dvy],
     transporterMaxRangeM:enemyTransporterRangeM,boarding:{...runtime.boarding},
     mission:'pursue-main-ship-and-seek-boarding-range'};
 };
 const before={startedAtMs:runtime.startedAtMs,boarding:{...runtime.boarding}};
 let playerFired=false;
 for(let i=0;i<=150;i++){
   const t=i*3;
   runtime.advance(now(t),{active:true});
   const observation=observe(t),activeOrder=runtime.enemyCaptainOrder;
   let order=P.decide({observation,activeOrder,nextRevision:Math.max(1,runtime.enemyCaptainRevision+1)});
   if(order){
      if(order.maneuver==='withdraw' && !decisionObservations.some(o=>o.stage==='ready-to-withdraw'))
        decisionObservations.push({stage:'ready-to-withdraw',expectedAction:'withdraw',observation:{...observation}});
      const accepted=runtime.command({type:'captain-helm-order',order},now(t));
      if(!accepted.accepted)throw new Error('HELM '+JSON.stringify(accepted));captainOrders.push({type:'helm',...order});}
   const boarding=P.decideBoarding({observation:observe(t),nextRevision:Math.max(1,runtime.enemyCaptainRevision+1),evacuationStrategy});
   if(boarding){
     decisionObservations.push({stage:boarding.action==='initiate'?'boarding-eligible':'boarders-committed',
       expectedAction:boarding.action,observation:observe(t)});
     const accepted=runtime.command({type:'captain-boarding-order',order:boarding},now(t));
      if(!accepted.accepted)throw new Error('BOARDING '+JSON.stringify(accepted));captainOrders.push({type:'boarding',...boarding});}
   if(runtime.boarding.phase==='deployed' && !playerFired && t>=60){
     const fire=runtime.command({type:'fire-primary-weapon'},now(t));
     if(!fire.accepted)throw new Error('FIRE '+JSON.stringify(fire));playerFired=true;
   }
   if(i%5===0||boarding||runtime.boarding.phase==='deployed') records.push({t,rangeM:observe(t).rangeM,
     relativeSpeedMps:observe(t).relativeSpeedMps,phase:runtime.boarding.phase,
     hull:runtime.targetHullPercent,order:runtime.enemyCaptainOrder?.maneuver||null});
 }
 return {before,orders:captainOrders,decisionObservations,events:runtime.eventsSince(0),boarding:{...runtime.boarding},
   ranges:records,finalRange:observe(450).rangeM,shots:runtime.shots.length,playerFired};
}
const recall=scenario('recall'),abandon=scenario('abandon');
const results={recall,abandon};
const kinds=s=>s.events.filter(e=>e.kind==='boarding-complete');
const has=s=>s.orders.filter(e=>e.type==='boarding').map(e=>e.action);
const checks={
 preBridgeBoardingInactive:recall.before.startedAtMs===null&&recall.before.boarding.phase==='idle',
 bothCaptainsInitiate:has(recall)[0]==='initiate'&&has(abandon)[0]==='initiate',
 bothDeployBeforeRetreat:recall.events.some(e=>e.kind==='boarding-complete'&&e.action==='initiate'&&e.success)&&
   abandon.events.some(e=>e.kind==='boarding-complete'&&e.action==='initiate'&&e.success),
 playerThreatCausesChoice:recall.playerFired&&abandon.playerFired&&has(recall)[1]==='recall'&&has(abandon)[1]==='abandon',
 recoveryIsTimeTaking:kinds(recall).some(e=>e.action==='recall'&&e.success&&e.phase==='recovered'),
 abandonmentIsTimeTaking:kinds(abandon).some(e=>e.action==='abandon'&&e.phase==='abandoned'),
 recoveredOrAbandonedAreDistinct:recall.boarding.boarders==='aboard'&&abandon.boarding.boarders==='abandoned',
 bothCaptainsEventuallyWithdraw:recall.orders.some(o=>o.maneuver==='withdraw')&&abandon.orders.some(o=>o.maneuver==='withdraw'),
 boardingNeverAutomatic:recall.events.filter(e=>e.kind==='captain-boarding-order').every(e=>e.source==='deterministic-opening-captain-v1'),
 noBoardingCombatCreated:recall.events.every(e=>!['boarding-attack','boarding-combat'].includes(e.kind)),
 exactDeploymentDuration:kinds(recall).some(e=>e.action==='initiate'&&
   Math.abs(e.atSeconds-recall.events.find(o=>o.kind==='captain-boarding-order'&&o.action==='initiate').atSeconds-30)<1e-6),
 exactRecallDuration:kinds(recall).some(e=>e.action==='recall'&&
   Math.abs(e.atSeconds-recall.events.find(o=>o.kind==='captain-boarding-order'&&o.action==='recall').atSeconds-24)<1e-6),
 exactAbandonDuration:kinds(abandon).some(e=>e.action==='abandon'&&
   Math.abs(e.atSeconds-abandon.events.find(o=>o.kind==='captain-boarding-order'&&o.action==='abandon').atSeconds-12)<1e-6),
};
const failedChecks=Object.entries(checks).filter(([,v])=>!v).map(([k])=>k);
return {ok:failedChecks.length===0,schema:'game.spaceCaptainBoardingCommitmentProbe.v1',checks,failedChecks,
 metrics:{recall:{orders:recall.orders,decisionObservations:recall.decisionObservations,
 boarding:recall.boarding,boardingEvents:recall.events.filter(e=>e.kind.includes('boarding')),range:recall.finalRange},
 abandon:{orders:abandon.orders,decisionObservations:abandon.decisionObservations,
 boarding:abandon.boarding,boardingEvents:abandon.events.filter(e=>e.kind.includes('boarding')),range:abandon.finalRange}}};
