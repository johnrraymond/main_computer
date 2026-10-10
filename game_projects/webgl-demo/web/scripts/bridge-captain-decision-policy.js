;(function () {
  "use strict";
  // Deterministic cognition for the opening pursuit. This adapter can later be
  // replaced by a NanoJev decision provider; the helm contract is identical.
  const SHIP_CAPABILITIES=Object.freeze({
    'ship.beta':Object.freeze({transporter:Object.freeze({maxRangeM:2500,maxPayloadKg:1000,cooldownSeconds:15,operational:true}), shipTeleport:Object.freeze({supported:false})}),
    'ship.mother':Object.freeze({transporter:Object.freeze({maxRangeM:1000000,maxPayloadKg:1000,cooldownSeconds:15,operational:true}),shipTeleport:Object.freeze({supported:false})})
  });
  function availableActions(snapshot, shipId='ship.beta') {
    const cap=SHIP_CAPABILITIES[shipId];
    if(!cap) throw new Error('CAPTAIN_SHIP_CAPABILITY_NOT_FOUND');
    const distance=Number(snapshot?.rangeM);
    if (!Number.isFinite(distance)||distance<0) throw new Error('CAPTAIN_WORLD_RANGE_REQUIRED');
    return Object.freeze({
      approach:true,hold:true,withdraw:true,coast:true,
      transportBoarders:Boolean(cap.transporter.operational&&distance<=cap.transporter.maxRangeM),
      recallBoarders:snapshot?.boarding?.phase==='deployed',
      abandonBoarders:snapshot?.boarding?.phase==='deployed',
      transporterMaxRangeM:cap.transporter.maxRangeM, measuredRangeM:distance
    });
  }
  function decide({observation,activeOrder=null,nextRevision=1}={}) {
    const actions=availableActions(observation);
    if (!Number.isSafeInteger(nextRevision)||nextRevision<1) throw new Error('CAPTAIN_REVISION_REQUIRED');
    // Only the captain decides pursuit or holding. There is no phase-to-thrust code.
    const targetRangeM=Math.max(200,actions.transporterMaxRangeM-450);
    const shouldHold=observation.rangeM<=targetRangeM+75 && Math.abs(Number(observation.radialVelocityMps)||0)<3;
    const committed=['deploying','deployed','recalling','abandoning'].includes(observation.boarding?.phase);
    const retreat=observation.targetHullPercent<=65;
    const resolved=['recovered','abandoned'].includes(observation.boarding?.phase);
    const maneuver=retreat&&!committed&&resolved?'withdraw':shouldHold||committed?'hold':'approach';
    const now=Number(observation.simulationSeconds);
    if(!Number.isFinite(now)||now<0) throw new Error('CAPTAIN_SIMULATION_TIME_REQUIRED');
    // Do not issue needless orders. Renew deliberately before expiry, or switch
    // when the captain's chosen maneuver changes.
    if(activeOrder?.maneuver===maneuver && activeOrder?.validThroughSeconds>now+15) return null;
    return Object.freeze({
      captainId:'captain.beta', shipId:'ship.beta',
      decisionId:`opening-beta-${nextRevision}`, revision:nextRevision,
      issuedAtSeconds:now, validThroughSeconds:now+90,
      maneuver,rangeM:targetRangeM,source:'deterministic-opening-captain-v1'
    });
  }
  function decideBoarding({observation,nextRevision=1,evacuationStrategy='recall'}={}) {
    if(!['recall','abandon'].includes(evacuationStrategy)) throw new Error('CAPTAIN_EVACUATION_CHOICE_REQUIRED');
    const actions=availableActions(observation);
    if(!Number.isSafeInteger(nextRevision)||nextRevision<1) throw new Error('CAPTAIN_REVISION_REQUIRED');
    const now=Number(observation.simulationSeconds);
    if(!Number.isFinite(now)||now<0) throw new Error('CAPTAIN_SIMULATION_TIME_REQUIRED');
    const phase=observation.boarding?.phase || 'idle';
    let action=null;
    if(phase==='idle' && actions.transportBoarders &&
       Math.abs(Number(observation.relativeSpeedMps))<=5 &&
       Number(observation.targetHullPercent)>65) action='initiate';
    else if(phase==='deployed' && Number(observation.targetHullPercent)<=65) action=evacuationStrategy;
    if(!action) return null;
    return Object.freeze({
      captainId:'captain.beta',shipId:'ship.beta',decisionId:`opening-boarding-beta-${nextRevision}`,
      revision:nextRevision,issuedAtSeconds:now,validThroughSeconds:now+60,
      action,source:'deterministic-opening-captain-v1'
    });
  }

  globalThis.MainComputerBridgeCaptainDecisionPolicy=Object.freeze({
    SCHEMA:'game.bridgeCaptainDecisionPolicy.v1',SHIP_CAPABILITIES,availableActions,decide,decideBoarding
  });
})();
