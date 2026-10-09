// Runs the real production captain policy and encounter authority on a deterministic clock.
const R=globalThis.MainComputerBridgeEncounterRuntime;
const P=globalThis.MainComputerBridgeCaptainDecisionPolicy;
if(!R||!P) throw new Error('BRIDGE_CAPTAIN_POLICY_UNAVAILABLE');
const runtime=R.create({physicalPlayerAuthority:true,requirePhysicalWeaponRange:true});
const startMs=3500;
const now=t=>startMs+t*1000;
const premature={startedAtMs:runtime.startedAtMs,revision:runtime.enemyCaptainRevision,acceleration:runtime.acceleration['ship.beta'].slice()};
const a=runtime.ships['ship.alpha'],b=runtime.ships['ship.beta'];
const obs=t=>{
  const dx=b.xM-a.xM,dy=b.yM-a.yM,r=Math.hypot(dx,dy);
  return {schema:'game.bridgeCaptainObservation.v1',captainId:'captain.beta',shipId:'ship.beta',
    simulationSeconds:t,rangeM:r,
    radialVelocityMps:((b.vxMps-a.vxMps)*dx+(b.vyMps-a.vyMps)*dy)/Math.max(1,r),
    mission:'pursue-main-ship-and-seek-boarding-range'};
};
const copy=v=>JSON.parse(JSON.stringify(v));
const sample=[];
const decisions=[];
for(let i=0;i<=300;i++) {
  const t=i*3;
  runtime.advance(now(t),{active:true});
  const order=P.decide({observation:obs(t),activeOrder:runtime.enemyCaptainOrder,
                         nextRevision:Math.max(1,runtime.enemyCaptainRevision+1)});
  if(order) {
    const result=runtime.command({type:'captain-helm-order',order},now(t));
    if(!result?.accepted) throw new Error('BRIDGE_CAPTAIN_ORDER_FAILED '+JSON.stringify(result));
    decisions.push(copy(order));
  }
  if(i%10===0 || i===300) {
    sample.push({t,rangeM:obs(t).rangeM,relativeVelocityMps:obs(t).radialVelocityMps,
      orderId:runtime.enemyCaptainOrder?.decisionId??null,
      activeRevision:runtime.enemyCaptainRevision});
  }
}
const cap=P.SHIP_CAPABILITIES;
const eligibleEarthOrbit=P.availableActions({rangeM:400},'ship.mother');
const moon=P.availableActions({rangeM:384000000},'ship.mother');
const jupiter=P.availableActions({rangeM:600000000000},'ship.mother');
const betaNear=P.availableActions({rangeM:2499},'ship.beta');
const betaFar=P.availableActions({rangeM:2501},'ship.beta');
const pred=runtime.readAuthorityState();
const checks={
  noPreBridgeCaptainClock:premature.startedAtMs===null&&premature.revision===-1,
  noPreBridgeThrust:Math.hypot(...premature.acceleration)===0,
  firstDecisionAtBridgeTimeZero:decisions[0]?.issuedAtSeconds===0 && decisions[0]?.captainId==='captain.beta' &&
    decisions[0]?.shipId==='ship.beta' && decisions[0]?.source==='deterministic-opening-captain-v1',
  noSecondCaptainAuthority:pred.helm?.['ship.beta']?.lastRevision===decisions.at(-1)?.revision,
  captainRenewsOrdersAcross15Minutes:decisions.length>=5 && sample.at(-1).activeRevision>=5,
  validOrderNeverStopsJustBecauseCaptainIsNotWatching:sample.every(s=>s.orderId!==null),
  fifteenMinuteWorldRangeBounded:sample.every(s=>s.rangeM>500&&s.rangeM<4000),
  captainSpecifiedBoardingDistance:decisions.every(d=>d.rangeM===2050),
  distinctShipCapabilities:cap['ship.beta'].transporter.maxRangeM!==cap['ship.mother'].transporter.maxRangeM,
  earthOrbitWithinMotherRange:eligibleEarthOrbit.transportBoarders,
  moonAndJupiterOutOfMotherRange:!moon.transportBoarders&&!jupiter.transportBoarders,
  raiderRangeBoundaries:betaNear.transportBoarders&&!betaFar.transportBoarders,
  teleportUnavailableIsNotAutomaticOrder:decisions.every(d=>['approach','hold'].includes(d.maneuver)),
  noUnauthorizedEnemyPhysicalThrust:pred.ships['ship.alpha'].axMps2===0&&pred.ships['ship.alpha'].ayMps2===0
};
const failedChecks=Object.entries(checks).filter(([,x])=>!x).map(([k])=>k);
return {ok:failedChecks.length===0,checks,failedChecks,
 schema:'game.spaceCaptainBridgeEntryDeterministicProbe.v1',
 metrics:{decisions,samples:sample,finalRangeM:obs(900).rangeM}};
