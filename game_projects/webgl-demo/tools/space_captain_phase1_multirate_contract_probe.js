const contract = globalThis.MainComputerSpaceCaptainMultirateContract;
const runtimeFactory = globalThis.MainComputerBridgeViewscreenEncounterRuntime;
if (!contract) return {ok:false, error:'multirate-contract-missing'};
if (!runtimeFactory) return {ok:false, error:'bridge-runtime-missing'};

const EPS = 1e-8;
const magnitude = (x, y) => Math.hypot(Number(x) || 0, Number(y) || 0);
const shipDistance = (a, b) => Math.hypot(
  Number(a?.xM || 0) - Number(b?.xM || 0),
  Number(a?.yM || 0) - Number(b?.yM || 0)
);
const velocityDistance = (a, b) => Math.hypot(
  Number(a?.vxMps || 0) - Number(b?.vxMps || 0),
  Number(a?.vyMps || 0) - Number(b?.vyMps || 0)
);
const authorityFingerprint = (snapshot) => JSON.stringify({
  atSeconds: snapshot?.authority?.atSeconds,
  updateCount: snapshot?.authority?.updateCount,
  eventAnchorCount: snapshot?.authority?.eventAnchorCount,
  nextPhysicsAtSeconds: snapshot?.authority?.nextPhysicsAtSeconds,
  nextTacticalBoundaryAtSeconds: snapshot?.authority?.nextTacticalBoundaryAtSeconds,
});

const runtime = runtimeFactory.create({tacticalSliceSeconds:3, physicsStepSeconds:0.2});
const START_MS = 1000;
// Explicit ship-mounted observation fixture; a projection cannot invent a camera.
const OBSERVER = Object.freeze({bodyId:'ship.mother',positionM:[1000,2000,3000],velocityMps:[0,0,0],
  forwardWorld:[1,0,0],upWorld:[0,0,1],validThroughSeconds:20});
const viewAt = nowMs => runtime.snapshot(nowMs, {active:true,observerPose:OBSERVER,
  targetWorldPositionM:[3600,2598,3000]});
runtime.advance(START_MS, {active:true});
const initial = viewAt(START_MS);
const initialAuthority = authorityFingerprint(initial);

// A presentation read before the next authority boundary may predict, but must not mutate authority.
const predictedOnly = viewAt(START_MS + 100);
const predictionAuthorityUnchanged = authorityFingerprint(predictedOnly) === initialAuthority;

// A read that crosses an unprocessed authority boundary must fail rather than catch authority up.
let staleSnapshotRejected = false;
let staleSnapshotError = '';
try {
  viewAt(START_MS + 250);
} catch (error) {
  staleSnapshotError = String(error?.message || error);
  staleSnapshotRejected = staleSnapshotError.includes('BRIDGE_VIEWSCREEN_AUTHORITY_STALE');
}
const authorityStillUnchangedAfterRejectedRead = runtime.authorityAtSeconds === 0 && runtime.authorityUpdateCount === 0;

runtime.advance(START_MS + 250, {active:true});
const afterPhysicsAdvance = viewAt(START_MS + 250);
const firstPhysicsAnchorIsConfiguredGrid = Math.abs(Number(afterPhysicsAdvance.authority.atSeconds) - 0.2) <= EPS;
const onePhysicsUpdateProcessed = Number(afterPhysicsAdvance.authority.updateCount) === 1;

// Move into the deterministic encounter and fire at a non-grid timestamp.
const FIRE_SECONDS = 7.02;
runtime.authority.playerFire(START_MS + FIRE_SECONDS * 1000);
const fired = viewAt(START_MS + FIRE_SECONDS * 1000);
const playerFireIsExactSemanticEvent =
  Math.abs(Number(fired.playerFireAtSeconds) - FIRE_SECONDS) <= EPS &&
  fired.authority?.lastExactEvent?.kind === 'player-fire' &&
  Math.abs(Number(fired.authority?.lastExactEvent?.atSeconds) - FIRE_SECONDS) <= EPS;
const combatBoundaryIsNextConfiguredGrid = Math.abs(Number(fired.combatBoundarySeconds) - 9.0) <= EPS;
// The captain, not the combat phase, must request a maneuver. Verify that
// the next tactical boundary changes acceleration under that active order.
const captainOrder=runtime.authority.command({type:'captain-helm-order',order:{
  captainId:'captain.beta',shipId:'ship.beta',decisionId:'phase1-captain-001',revision:1,
  issuedAtSeconds:FIRE_SECONDS,validThroughSeconds:30,maneuver:'hold',rangeM:2050,
  source:'deterministic-phase1-test'
}},START_MS+FIRE_SECONDS*1000);
if (!captainOrder.accepted) throw new Error('PHASE1_CAPTAIN_ORDER_REJECTED');


const impactSeconds = Number(fired.impactAtSeconds);
runtime.advance(START_MS + impactSeconds * 1000, {active:true});
const impacted = viewAt(START_MS + impactSeconds * 1000);
const impactAnchorsAtExactTimestamp =
  impacted.authority?.lastExactEvent?.kind === 'Impact' &&
  Math.abs(Number(impacted.authority?.lastExactEvent?.atSeconds) - impactSeconds) <= EPS &&
  Math.abs(Number(impacted.authority?.atSeconds) - impactSeconds) <= EPS;

// Characterize the 9s tactical boundary: position and velocity remain continuous while acceleration changes.
runtime.advance(START_MS + 8999, {active:true});
const beforeBoundary = viewAt(START_MS + 8999);
const targetBefore = beforeBoundary.ships['ship.beta'];
const dt = 0.001;
const predictedAtBoundary = {
  xM: Number(targetBefore.xM) + Number(targetBefore.vxMps) * dt + 0.5 * Number(targetBefore.axMps2) * dt * dt,
  yM: Number(targetBefore.yM) + Number(targetBefore.vyMps) * dt + 0.5 * Number(targetBefore.ayMps2) * dt * dt,
  vxMps: Number(targetBefore.vxMps) + Number(targetBefore.axMps2) * dt,
  vyMps: Number(targetBefore.vyMps) + Number(targetBefore.ayMps2) * dt,
};
runtime.advance(START_MS + 9000, {active:true});
const atBoundary = viewAt(START_MS + 9000);
const targetAfter = atBoundary.ships['ship.beta'];
const tacticalBoundaryPositionErrorM = shipDistance(predictedAtBoundary, targetAfter);
const tacticalBoundaryVelocityErrorMps = velocityDistance(predictedAtBoundary, targetAfter);
const accelerationChangeMps2 = magnitude(
  Number(targetAfter.axMps2) - Number(targetBefore.axMps2),
  Number(targetAfter.ayMps2) - Number(targetBefore.ayMps2)
);

const checks = {
  contractSchemaIsFrozen: contract.SCHEMA === 'game.spaceCaptainMultirateContract.v1',
  testedEnvelopeIncludesConfiguredValues: contract.insideTestedEnvelope({tacticalSliceSeconds:3, physicsStepSeconds:0.2}),
  timingValuesRemainParameters: Number(initial.tacticalSliceSeconds) === 3 && Number(initial.physicsStepSeconds) === 0.2,
  renderCadenceIsRequestAnimationFrame: initial.renderCadence === 'requestAnimationFrame' && contract.RULES.renderCadence === 'requestAnimationFrame',
  renderMayNotMutateAuthority: contract.RULES.renderMayMutateAuthority === false,
  predictionMayNotMutateAuthority: contract.RULES.predictionMayMutateAuthority === false,
  predictionReadLeavesAuthorityUnchanged: predictionAuthorityUnchanged,
  staleRenderReadIsRejected: staleSnapshotRejected,
  rejectedRenderReadLeavesAuthorityUnchanged: authorityStillUnchangedAfterRejectedRead,
  physicsAdvancesOnlyOnConfiguredGrid: firstPhysicsAnchorIsConfiguredGrid && onePhysicsUpdateProcessed,
  playerFireAnchorsAtExactTimestamp: playerFireIsExactSemanticEvent,
  hostileReactionUsesImmutableTacticalGrid: combatBoundaryIsNextConfiguredGrid,
  impactAnchorsAtExactTimestamp: impactAnchorsAtExactTimestamp,
  tacticalBoundaryDoesNotTeleport: tacticalBoundaryPositionErrorM <= 1e-7,
  tacticalBoundaryDoesNotSnapVelocity: tacticalBoundaryVelocityErrorMps <= 1e-7,
  tacticalBoundaryChangesAccelerationInstead: captainOrder.accepted && accelerationChangeMps2 > 1e-3,
  softTargetLockRemainsPresentationOnly: contract.RULES.cameraMode === 'soft-target-lock' && contract.RULES.cameraMayMutateAuthority === false,
};
const failedChecks = Object.entries(checks).filter(([, passed]) => !passed).map(([name]) => name);
return {
  ok: failedChecks.length === 0,
  schema: 'game.spaceCaptainPhase1MultirateContractProbe.v1',
  checks,
  failedChecks,
  metrics: {
    configuredTacticalSliceSeconds: initial.tacticalSliceSeconds,
    configuredPhysicsStepSeconds: initial.physicsStepSeconds,
    firstPhysicsAuthorityAtSeconds: afterPhysicsAdvance.authority.atSeconds,
    playerFireAtSeconds: fired.playerFireAtSeconds,
    impactAtSeconds: impactSeconds,
    combatBoundarySeconds: fired.combatBoundarySeconds,
    tacticalBoundaryPositionErrorM,
    tacticalBoundaryVelocityErrorMps,
    tacticalBoundaryAccelerationChangeMps2: accelerationChangeMps2,
    staleSnapshotError,
  },
};
