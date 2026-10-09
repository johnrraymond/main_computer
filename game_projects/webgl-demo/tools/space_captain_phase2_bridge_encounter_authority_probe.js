const contract = globalThis.MainComputerSpaceCaptainMultirateContract;
const runtimeFactory = globalThis.MainComputerBridgeEncounterRuntime;
if (!contract) return {ok:false, error:'multirate-contract-missing'};
if (!runtimeFactory) return {ok:false, error:'bridge-encounter-runtime-missing'};

const EPS = 1e-8;
const START_MS = 1000;
const FIRE_ONE_SECONDS = 7.02;
const runtime = runtimeFactory.create({tacticalSliceSeconds:3, physicsStepSeconds:0.2});

const authorityFingerprint = (snapshot) => JSON.stringify({
  atSeconds: snapshot?.authority?.atSeconds,
  updateCount: snapshot?.authority?.updateCount,
  eventAnchorCount: snapshot?.authority?.eventAnchorCount,
  nextPhysicsAtSeconds: snapshot?.authority?.nextPhysicsAtSeconds,
  nextTacticalBoundaryAtSeconds: snapshot?.authority?.nextTacticalBoundaryAtSeconds,
  lastExactEvent: snapshot?.authority?.lastExactEvent,
  combat: snapshot?.combat,
});

runtime.advance(START_MS,{active:true});
const initial = runtime.snapshot(START_MS,{active:true});
const initialCombat = runtime.combatState();

const firstCommand = runtime.command({type:'fire-primary-weapon'}, START_MS + FIRE_ONE_SECONDS * 1000);
const afterFirstFire = firstCommand.snapshot;
const firstProjectile = afterFirstFire.combat.projectiles[0];
const firstImpactSeconds = Number(firstProjectile.impactAtSeconds);
const hullUnchangedAtFire = Number(afterFirstFire.combat.target.hullPercent) === 100;
const firstFireExact = Math.abs(Number(afterFirstFire.combat.firstFireAtSeconds) - FIRE_ONE_SECONDS) <= EPS;
const firstCombatBoundary = Number(afterFirstFire.combat.combatBoundarySeconds);

runtime.advance(START_MS + firstImpactSeconds * 1000,{active:true});
const afterFirstImpact = runtime.snapshot(START_MS + firstImpactSeconds * 1000,{active:true});
const firstImpactExact =
  afterFirstImpact.authority?.lastExactEvent?.kind === 'Impact' &&
  Math.abs(Number(afterFirstImpact.authority?.lastExactEvent?.atSeconds) - firstImpactSeconds) <= EPS;
const firstImpactDamageApplied =
  Number(afterFirstImpact.combat.target.hullPercent) === 50 &&
  afterFirstImpact.combat.target.disabled === false &&
  afterFirstImpact.combat.projectiles[0].impactApplied === true;

const FIRE_TWO_SECONDS = firstImpactSeconds + 0.41;
const secondCommand = runtime.command({type:'fire-primary-weapon'}, START_MS + FIRE_TWO_SECONDS * 1000);
const afterSecondFire = secondCommand.snapshot;
const secondProjectile = afterSecondFire.combat.projectiles[1];
const secondImpactSeconds = Number(secondProjectile.impactAtSeconds);
const secondFireDoesNotMoveCombatGrid = Math.abs(Number(afterSecondFire.combat.combatBoundarySeconds) - firstCombatBoundary) <= EPS;

runtime.advance(START_MS + secondImpactSeconds * 1000,{active:true});
const destroyed = runtime.snapshot(START_MS + secondImpactSeconds * 1000,{active:true});
const secondImpactExact =
  destroyed.authority?.lastExactEvent?.kind === 'Impact' &&
  Math.abs(Number(destroyed.authority?.lastExactEvent?.atSeconds) - secondImpactSeconds) <= EPS;
const destructionOwnedByRuntime =
  Number(destroyed.combat.target.hullPercent) === 0 &&
  destroyed.combat.target.disabled === true &&
  Math.abs(Number(destroyed.combat.target.destroyedAtSeconds) - secondImpactSeconds) <= EPS &&
  destroyed.combat.terminalOutcome === 'target-destroyed';

const shotsBeforeRejectedFire = Number(destroyed.combat.shotsFired);
const rejected = runtime.command({type:'fire-primary-weapon'}, START_MS + (secondImpactSeconds + 0.05) * 1000);
const rejectedFireDoesNotMutateCombat =
  rejected.accepted === false &&
  rejected.reason === 'target-destroyed' &&
  Number(rejected.snapshot.combat.shotsFired) === shotsBeforeRejectedFire &&
  Number(rejected.snapshot.combat.target.hullPercent) === 0;

// Observation inside the next authority interval may update presentation/camera state,
// but may not mutate the authoritative encounter/combat state.
const beforePredictionFingerprint = authorityFingerprint(rejected.snapshot);
const nextRequired = Math.min(
  Number(rejected.snapshot.authority.nextPhysicsAtSeconds),
  Number(rejected.snapshot.authority.nextTacticalBoundaryAtSeconds)
);
const safePredictionSeconds = Math.min(
  secondImpactSeconds + 0.05 + 0.001,
  nextRequired - 0.001
);
const predicted = runtime.snapshot(START_MS + safePredictionSeconds * 1000,{active:true});
const predictionDoesNotMutateEncounterTruth = authorityFingerprint(predicted) === beforePredictionFingerprint;

const checks = {
  phase1ContractStillLoaded: contract.SCHEMA === 'game.spaceCaptainMultirateContract.v1',
  runtimeHasSingleAuthoritySchema: runtimeFactory.SCHEMA === 'game.bridgeEncounterRuntime.v1',
  initialCombatTruthIsOwnedByRuntime: Number(initialCombat.target.hullPercent) === 100 && initialCombat.target.disabled === false && initialCombat.shotsFired === 0,
  firstFireCommandAccepted: firstCommand.accepted === true,
  playerFireAnchorsAtExactTimestamp: firstFireExact && afterFirstFire.authority?.lastExactEvent?.kind === 'player-fire',
  firingDoesNotApplyDamageBeforeImpact: hullUnchangedAtFire,
  firstImpactAnchorsAtExactTimestamp: firstImpactExact,
  firstImpactAppliesAuthoritativeDamage: firstImpactDamageApplied,
  hostileReactionGridComesFromFirstFire: Math.abs(firstCombatBoundary - 9.0) <= EPS,
  secondFireDoesNotMoveTacticalGrid: secondFireDoesNotMoveCombatGrid,
  secondImpactAnchorsAtExactTimestamp: secondImpactExact,
  destructionIsAuthoritativeRuntimeState: destructionOwnedByRuntime,
  destroyedTargetRejectsFurtherFire: rejectedFireDoesNotMutateCombat,
  snapshotPredictionCannotMutateEncounterTruth: predictionDoesNotMutateEncounterTruth,
  runtimeOwnsProjectileLedger: destroyed.combat.projectiles.length === 2 && destroyed.combat.projectiles.every(p => p.impactApplied === true),
};
const failedChecks = Object.entries(checks).filter(([,passed])=>!passed).map(([name])=>name);
return {
  ok: failedChecks.length === 0,
  schema:'game.spaceCaptainPhase2BridgeEncounterAuthorityProbe.v1',
  checks,
  failedChecks,
  metrics:{
    configuredTacticalSliceSeconds:destroyed.tacticalSliceSeconds,
    configuredPhysicsStepSeconds:destroyed.physicsStepSeconds,
    firstFireAtSeconds:afterFirstFire.combat.firstFireAtSeconds,
    firstImpactAtSeconds:firstImpactSeconds,
    firstHullAfterImpact:afterFirstImpact.combat.target.hullPercent,
    secondFireAtSeconds:afterSecondFire.combat.lastFireAtSeconds,
    secondImpactAtSeconds:secondImpactSeconds,
    finalHullPercent:destroyed.combat.target.hullPercent,
    shotsFired:destroyed.combat.shotsFired,
    combatBoundarySeconds:destroyed.combat.combatBoundarySeconds,
    terminalOutcome:destroyed.combat.terminalOutcome,
    eventAnchorCount:destroyed.authority.eventAnchorCount,
  }
};
