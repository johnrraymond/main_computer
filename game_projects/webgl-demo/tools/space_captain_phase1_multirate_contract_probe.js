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
runtime.advance(START_MS, {active:true});
const initial = runtime.snapshot(START_MS, {active:true});
const initialAuthority = authorityFingerprint(initial);

// A presentation read before the next authority boundary may predict, but must not mutate authority.
const predictedOnly = runtime.snapshot(START_MS + 100, {active:true});
const predictionAuthorityUnchanged = authorityFingerprint(predictedOnly) === initialAuthority;

// A read that crosses an unprocessed authority boundary must fail rather than catch authority up.
let staleSnapshotRejected = false;
let staleSnapshotError = '';
try {
  runtime.snapshot(START_MS + 250, {active:true});
} catch (error) {
  staleSnapshotError = String(error?.message || error);
  staleSnapshotRejected = staleSnapshotError.includes('BRIDGE_VIEWSCREEN_AUTHORITY_STALE');
}
const authorityStillUnchangedAfterRejectedRead = runtime.authorityAtSeconds === 0 && runtime.authorityUpdateCount === 0;

runtime.advance(START_MS + 250, {active:true});
const afterPhysicsAdvance = runtime.snapshot(START_MS + 250, {active:true});
const firstPhysicsAnchorIsConfiguredGrid = Math.abs(Number(afterPhysicsAdvance.authority.atSeconds) - 0.2) <= EPS;
const onePhysicsUpdateProcessed = Number(afterPhysicsAdvance.authority.updateCount) === 1;

// Move into the deterministic encounter and fire at a non-grid timestamp.
const FIRE_SECONDS = 7.02;
runtime.playerFire(START_MS + FIRE_SECONDS * 1000);
const fired = runtime.snapshot(START_MS + FIRE_SECONDS * 1000, {active:true});
const playerFireIsExactSemanticEvent =
  Math.abs(Number(fired.playerFireAtSeconds) - FIRE_SECONDS) <= EPS &&
  fired.authority?.lastExactEvent?.kind === 'player-fire' &&
  Math.abs(Number(fired.authority?.lastExactEvent?.atSeconds) - FIRE_SECONDS) <= EPS;
const combatBoundaryIsNextConfiguredGrid = Math.abs(Number(fired.combatBoundarySeconds) - 9.0) <= EPS;

const impactSeconds = Number(fired.impactAtSeconds);
runtime.advance(START_MS + impactSeconds * 1000, {active:true});
const impacted = runtime.snapshot(START_MS + impactSeconds * 1000, {active:true});
const impactAnchorsAtExactTimestamp =
  impacted.authority?.lastExactEvent?.kind === 'Impact' &&
  Math.abs(Number(impacted.authority?.lastExactEvent?.atSeconds) - impactSeconds) <= EPS &&
  Math.abs(Number(impacted.authority?.atSeconds) - impactSeconds) <= EPS;

// Characterize the 9s tactical boundary: position and velocity remain continuous while acceleration changes.
runtime.advance(START_MS + 8999, {active:true});
const beforeBoundary = runtime.snapshot(START_MS + 8999, {active:true});
const targetBefore = beforeBoundary.ships['ship.beta'];
const dt = 0.001;
const predictedAtBoundary = {
  xM: Number(targetBefore.xM) + Number(targetBefore.vxMps) * dt + 0.5 * Number(targetBefore.axMps2) * dt * dt,
  yM: Number(targetBefore.yM) + Number(targetBefore.vyMps) * dt + 0.5 * Number(targetBefore.ayMps2) * dt * dt,
  vxMps: Number(targetBefore.vxMps) + Number(targetBefore.axMps2) * dt,
  vyMps: Number(targetBefore.vyMps) + Number(targetBefore.ayMps2) * dt,
};
runtime.advance(START_MS + 9000, {active:true});
const atBoundary = runtime.snapshot(START_MS + 9000, {active:true});
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
  tacticalBoundaryChangesAccelerationInstead: accelerationChangeMps2 > 1e-3,
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
