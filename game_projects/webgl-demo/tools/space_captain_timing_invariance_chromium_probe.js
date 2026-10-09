const matrix = args?.matrix || [];
const renderHzValues = args?.renderHzValues || [30, 60, 120, 165];
const EPS = 1e-9;

const distanceState = (a, b) => {
  const ids = ['ship.alpha', 'ship.beta'];
  let worst = 0;
  for (const id of ids) {
    const sa = a?.ships?.[id] || {};
    const sb = b?.ships?.[id] || {};
    for (const key of ['xM', 'yM', 'vxMps', 'vyMps']) {
      worst = Math.max(worst, Math.abs(Number(sa[key] || 0) - Number(sb[key] || 0)));
    }
  }
  return worst;
};

function simulate(config, renderHz) {
  const tactical = Number(config.tacticalSliceSeconds);
  const physics = Number(config.physicsStepSeconds);
  const runtime = globalThis.MainComputerBridgeViewscreenEncounterRuntime?.create?.({
    tacticalSliceSeconds: tactical,
    physicsStepSeconds: physics,
  });
  if (!runtime) throw new Error('bridge-runtime-missing');

  const startMs = 1000;
  const fireAt = tactical * 2.34;
  const endSeconds = tactical * 5;
  const framePeriod = 1 / Number(renderHz);
  const expectedCombatBoundary = tactical * 3;
  let fired = false;
  let frames = 0;
  let hardLockFrames = 0;
  let bothVisibleFrames = 0;
  let maxTargetOffset = 0;
  let minTargetOffset = Infinity;
  const phases = new Set();
  runtime.advance(startMs, {active: true});
  let lastSnapshot = runtime.snapshot(startMs, {active: true});

  const sample = (snapshot) => {
    frames += 1;
    phases.add(snapshot.phase);
    const offset = Math.hypot(...snapshot.viewScreen.targetOffsetNormalized.map(Number));
    maxTargetOffset = Math.max(maxTargetOffset, offset);
    minTargetOffset = Math.min(minTargetOffset, offset);
    if (snapshot.viewScreen.hardLockRetained) hardLockFrames += 1;
    if (snapshot.viewScreen.bothShipsVisible) bothVisibleFrames += 1;
  };
  sample(lastSnapshot);

  let t = framePeriod;
  while (t < endSeconds - EPS) {
    if (!fired && fireAt <= t + EPS) {
      runtime.playerFire(startMs + fireAt * 1000);
      fired = true;
    }
    runtime.advance(startMs + t * 1000, {active: true});
    lastSnapshot = runtime.snapshot(startMs + t * 1000, {active: true});
    sample(lastSnapshot);
    t += framePeriod;
  }
  if (!fired) {
    runtime.playerFire(startMs + fireAt * 1000);
    fired = true;
  }
  runtime.advance(startMs + endSeconds * 1000, {active: true});
  lastSnapshot = runtime.snapshot(startMs + endSeconds * 1000, {active: true});
  sample(lastSnapshot);

  const authorityRate = Number(lastSnapshot.authority.updateCount || 0) / endSeconds;
  const nominalAuthorityRate = 1 / physics;
  const checks = {
    configuredTacticalSlicePreserved: Math.abs(Number(lastSnapshot.tacticalSliceSeconds) - tactical) <= EPS,
    configuredPhysicsStepPreserved: Math.abs(Number(lastSnapshot.physicsStepSeconds) - physics) <= EPS,
    tacticalGridReactionIsExact: Math.abs(Number(lastSnapshot.combatBoundarySeconds) - expectedCombatBoundary) <= 1e-8,
    playerFireTimestampIsExact: Math.abs(Number(lastSnapshot.playerFireAtSeconds) - fireAt) <= 1e-8,
    impactFollowsFire: Number(lastSnapshot.impactAtSeconds) > fireAt,
    encounterReachesCombat: lastSnapshot.phase === 'combat' && phases.has('combat'),
    boardingPhasesPrecedeHostility: phases.has('boarding-approach') && phases.has('boarding-velocity-match') && phases.has('boarding-prep'),
    authorityCadenceTracksConfiguredStep: authorityRate >= nominalAuthorityRate * 0.70 && authorityRate <= nominalAuthorityRate * 1.05,
    softLockIsNotHardCentered: maxTargetOffset > 0.03 && minTargetOffset > 0.001,
    softLockRetainsEnemy: hardLockFrames / Math.max(1, frames) >= 0.99,
    zoomedOutViewKeepsBothShips: bothVisibleFrames / Math.max(1, frames) >= 0.99,
  };
  const failedChecks = Object.entries(checks).filter(([, value]) => !value).map(([name]) => name);
  return {
    ok: failedChecks.length === 0,
    tacticalSliceSeconds: tactical,
    physicsStepSeconds: physics,
    renderHz: Number(renderHz),
    checks,
    failedChecks,
    metrics: {
      frames,
      endSimulationSeconds: endSeconds,
      playerFireAtSeconds: Number(lastSnapshot.playerFireAtSeconds),
      impactAtSeconds: Number(lastSnapshot.impactAtSeconds),
      combatBoundarySeconds: Number(lastSnapshot.combatBoundarySeconds),
      authorityUpdateCount: Number(lastSnapshot.authority.updateCount || 0),
      authorityUpdatesPerSimulationSecond: authorityRate,
      nominalAuthorityHz: nominalAuthorityRate,
      eventAnchorCount: Number(lastSnapshot.authority.eventAnchorCount || 0),
      hardLockFraction: hardLockFrames / Math.max(1, frames),
      bothVisibleFraction: bothVisibleFrames / Math.max(1, frames),
      maxTargetOffsetNormalized: maxTargetOffset,
      minTargetOffsetNormalized: minTargetOffset,
      phases: [...phases],
    },
    finalSnapshot: lastSnapshot,
  };
}

const rows = [];
for (const config of matrix) {
  for (const renderHz of renderHzValues) rows.push(simulate(config, renderHz));
}

const renderInvariance = [];
for (const config of matrix) {
  const matches = rows.filter(row =>
    row.tacticalSliceSeconds === Number(config.tacticalSliceSeconds) &&
    row.physicsStepSeconds === Number(config.physicsStepSeconds)
  );
  const reference = matches[0];
  let maxStateDifference = 0;
  let maxImpactTimeDifference = 0;
  let maxCombatBoundaryDifference = 0;
  for (const row of matches.slice(1)) {
    maxStateDifference = Math.max(maxStateDifference, distanceState(reference.finalSnapshot, row.finalSnapshot));
    maxImpactTimeDifference = Math.max(maxImpactTimeDifference, Math.abs(reference.metrics.impactAtSeconds - row.metrics.impactAtSeconds));
    maxCombatBoundaryDifference = Math.max(maxCombatBoundaryDifference, Math.abs(reference.metrics.combatBoundarySeconds - row.metrics.combatBoundarySeconds));
  }
  renderInvariance.push({
    tacticalSliceSeconds: Number(config.tacticalSliceSeconds),
    physicsStepSeconds: Number(config.physicsStepSeconds),
    ok: maxStateDifference <= 1e-7 && maxImpactTimeDifference <= 1e-8 && maxCombatBoundaryDifference <= 1e-8,
    maxFinalAuthoritativeStateDifference: maxStateDifference,
    maxImpactTimeDifferenceSeconds: maxImpactTimeDifference,
    maxCombatBoundaryDifferenceSeconds: maxCombatBoundaryDifference,
  });
}

const checks = {
  everyConfigurationIsCoherent: rows.every(row => row.ok),
  renderCadenceDoesNotChangeAuthoritativeSolution: renderInvariance.every(row => row.ok),
  allRequestedRenderCadencesWereExercised: renderHzValues.every(hz => rows.some(row => row.renderHz === Number(hz))),
};
const failedChecks = Object.entries(checks).filter(([, value]) => !value).map(([name]) => name);
return {
  ok: failedChecks.length === 0,
  schema: 'game.spaceCaptainTimingInvarianceChromiumProbe.v1',
  checks,
  failedChecks,
  matrixSize: matrix.length,
  renderHzValues,
  rowCount: rows.length,
  renderInvariance,
  rows: rows.map(({finalSnapshot, ...row}) => row),
};
