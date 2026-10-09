from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


GAME_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROJECT = GAME_ROOT / "project.json"
SCRIPTS = GAME_ROOT / "web" / "scripts"


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke game boot -> universe -> gravity -> ship-centered astrometrics.")
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--real-seconds", type=float, default=10.0)
    args = parser.parse_args()

    node = shutil.which("node")
    if not node:
        print(json.dumps({"ok": False, "error": "node executable not found"}, indent=2))
        return 2

    project = args.project.resolve()
    runtimes = {
        "navigation": SCRIPTS / "space-navigation-runtime.js",
        "universe": SCRIPTS / "space-universe-runtime.js",
        "gravity": SCRIPTS / "space-gravity-runtime.js",
        "astrometrics": SCRIPTS / "space-astrometrics-runtime.js",
    }
    for name, path in runtimes.items():
        if not path.exists():
            print(json.dumps({"ok": False, "error": f"missing {name} runtime: {path}"}, indent=2))
            return 2

    js = r"""
const fs = require('fs');
const navApi = require(process.argv[1]);
const universeApi = require(process.argv[2]);
const gravityApi = require(process.argv[3]);
const astroApi = require(process.argv[4]);
const project = JSON.parse(fs.readFileSync(process.argv[5], 'utf8'));
const realSeconds = Number(process.argv[6]);
const sceneViewerSource = fs.readFileSync(process.argv[7], 'utf8');

const navigation = navApi.create(project.metadata.spaceNavigation, {projectId: project.id});
let nav = navigation.snapshot(0);
const universe = universeApi.create(project.metadata.spaceUniverse, {
  projectId: project.id,
  navigationDefinition: project.metadata.spaceNavigation
});
universe.updateClock(nav, 0);
const gravity = gravityApi.create(project.metadata.spacePhysics, {projectId: project.id});
gravity.setActiveSystem(nav.currentSystemId);
gravity.advanceToSimulationSeconds(universe.snapshot(nav.currentSystemId).universeSeconds);
const astrometrics = astroApi.create({projectId: project.id, universeRuntime: universe});
const beforeGravity = gravity.snapshot();
const before = astrometrics.observe({navigationSnapshot: nav, physicsSnapshot: beforeGravity});

function body(snapshot, id) {
  return snapshot.bodies.find((entry) => entry.id === id);
}
function visible(snapshot, id) {
  return snapshot.visibleObjects.find((entry) => entry.id === id);
}
const haven0 = body(beforeGravity, 'planet.haven');

// Front-end trajectory contract: each body exposes a finite 6-D phase vector,
// smooth motion differentiates back to velocity/acceleration, and frame changes
// preserve state while allowing a moving body to be represented as local zero.
function vectorSub(a, b) {
  return a.map((value, index) => Number(value) - Number(b[index]));
}
function vectorAdd(a, b) {
  return a.map((value, index) => Number(value) + Number(b[index]));
}
function vectorScale(a, scalar) {
  return a.map((value) => Number(value) * Number(scalar));
}
function vectorMagnitude(a) {
  return Math.hypot(...a.map(Number));
}
function vectorDistance(a, b) {
  return vectorMagnitude(vectorSub(a, b));
}
function phaseVector(entry) {
  return [...entry.positionM.map(Number), ...entry.velocityMps.map(Number)];
}
function freshGravityAt(seconds) {
  const runtime = gravityApi.create(project.metadata.spacePhysics, {projectId: project.id});
  runtime.setActiveSystem(nav.currentSystemId);
  runtime.advanceToSimulationSeconds(seconds);
  return {runtime, snapshot: runtime.snapshot()};
}

const derivativeCenterSeconds = Math.max(2 * gravity.fixedStepSeconds, 600);
const derivativeDtSeconds = gravity.fixedStepSeconds;
const derivativeMinus = freshGravityAt(derivativeCenterSeconds - derivativeDtSeconds).snapshot;
const derivativeCenterState = freshGravityAt(derivativeCenterSeconds);
const derivativeCenter = derivativeCenterState.snapshot;
const derivativePlus = freshGravityAt(derivativeCenterSeconds + derivativeDtSeconds).snapshot;
const centerAccelerations = gravityApi.accelerationsForBodies(
  derivativeCenter.bodies,
  derivativeCenterState.runtime.gravitationalConstant
);

let maxPositionDerivativeRelativeError = 0;
let maxVelocityDerivativeRelativeError = 0;
let differentiatedBodyCount = 0;
for (let index = 0; index < derivativeCenter.bodies.length; index += 1) {
  const centerBody = derivativeCenter.bodies[index];
  const minusBody = body(derivativeMinus, centerBody.id);
  const plusBody = body(derivativePlus, centerBody.id);
  if (!minusBody || !plusBody) continue;
  const positionDerivative = vectorScale(
    vectorSub(plusBody.positionM, minusBody.positionM),
    1 / (2 * derivativeDtSeconds)
  );
  const velocityDerivative = vectorScale(
    vectorSub(plusBody.velocityMps, minusBody.velocityMps),
    1 / (2 * derivativeDtSeconds)
  );
  const velocityScale = Math.max(vectorMagnitude(centerBody.velocityMps), 1);
  const accelerationScale = Math.max(vectorMagnitude(centerAccelerations[index]), 1e-12);
  maxPositionDerivativeRelativeError = Math.max(
    maxPositionDerivativeRelativeError,
    vectorDistance(positionDerivative, centerBody.velocityMps) / velocityScale
  );
  maxVelocityDerivativeRelativeError = Math.max(
    maxVelocityDerivativeRelativeError,
    vectorDistance(velocityDerivative, centerAccelerations[index]) / accelerationScale
  );
  differentiatedBodyCount += 1;
}

const frameCenter = body(derivativeCenter, 'star.solace-reach-a');
const frameTarget = body(derivativeCenter, 'planet.haven');
const nestedTarget = body(derivativeCenter, 'moon.haven-i');
const frameCenterMinus = body(derivativeMinus, 'star.solace-reach-a');
const frameCenterPlus = body(derivativePlus, 'star.solace-reach-a');
const centerRelativePhase = frameCenter ? [
  ...vectorSub(frameCenter.positionM, frameCenter.positionM),
  ...vectorSub(frameCenter.velocityMps, frameCenter.velocityMps)
] : [];
const targetInCenterPosition = frameCenter && frameTarget ? vectorSub(frameTarget.positionM, frameCenter.positionM) : [];
const targetInCenterVelocity = frameCenter && frameTarget ? vectorSub(frameTarget.velocityMps, frameCenter.velocityMps) : [];
const nestedInTargetPosition = frameTarget && nestedTarget ? vectorSub(nestedTarget.positionM, frameTarget.positionM) : [];
const nestedInTargetVelocity = frameTarget && nestedTarget ? vectorSub(nestedTarget.velocityMps, frameTarget.velocityMps) : [];
const reconstructedNestedPosition = frameCenter && frameTarget && nestedTarget
  ? vectorAdd(frameCenter.positionM, vectorAdd(targetInCenterPosition, nestedInTargetPosition))
  : [];
const reconstructedNestedVelocity = frameCenter && frameTarget && nestedTarget
  ? vectorAdd(frameCenter.velocityMps, vectorAdd(targetInCenterVelocity, nestedInTargetVelocity))
  : [];
const movingFrameDisplacementM = frameCenterMinus && frameCenterPlus
  ? vectorDistance(frameCenterPlus.positionM, frameCenterMinus.positionM)
  : 0;
const nestedFramePositionReconstructionErrorM = nestedTarget
  ? vectorDistance(reconstructedNestedPosition, nestedTarget.positionM)
  : Infinity;
const nestedFrameVelocityReconstructionErrorMps = nestedTarget
  ? vectorDistance(reconstructedNestedVelocity, nestedTarget.velocityMps)
  : Infinity;

// Reference hybrid-flow contract. Impact() is the sole deliberate discontinuity:
// position remains continuous at the event while velocity changes by impulse/mass,
// and both trajectory segments remain smooth under the same integrator.
function Impact(state, bodyId, impulseKgMps) {
  const target = state.bodies.find((entry) => entry.id === bodyId);
  if (!target) throw new Error(`Impact target missing: ${bodyId}`);
  if (!(target.massKg > 0)) throw new Error(`Impact target must have positive mass: ${bodyId}`);
  const beforePositionM = target.positionM.slice();
  const beforeVelocityMps = target.velocityMps.slice();
  target.velocityMps = target.velocityMps.map(
    (value, index) => value + Number(impulseKgMps[index]) / target.massKg
  );
  return {
    beforePositionM,
    afterPositionM: target.positionM.slice(),
    beforeVelocityMps,
    afterVelocityMps: target.velocityMps.slice()
  };
}
const impactMassKg = 1000;
const impactImpulseKgMps = [500, -200, 100];
const impactState = {
  id: 'smoke.impact-system',
  epochSeconds: 0,
  simulationSeconds: 0,
  bodies: [{
    id: 'smoke.transient',
    label: 'Smoke Transient',
    kind: 'transient',
    massKg: impactMassKg,
    radiusM: 1,
    positionM: [0, 0, 0],
    velocityMps: [10, 2, -1]
  }]
};
gravityApi.velocityVerletStep(impactState, 2, gravity.gravitationalConstant);
const preImpactPositionM = impactState.bodies[0].positionM.slice();
const preImpactVelocityMps = impactState.bodies[0].velocityMps.slice();
const impactEvent = Impact(impactState, 'smoke.transient', impactImpulseKgMps);
const expectedImpactDeltaVelocityMps = impactImpulseKgMps.map((value) => value / impactMassKg);
const actualImpactDeltaVelocityMps = vectorSub(impactEvent.afterVelocityMps, impactEvent.beforeVelocityMps);
const impactPositionJumpM = vectorDistance(impactEvent.afterPositionM, impactEvent.beforePositionM);
const impactVelocityJumpErrorMps = vectorDistance(actualImpactDeltaVelocityMps, expectedImpactDeltaVelocityMps);
gravityApi.velocityVerletStep(impactState, 2, gravity.gravitationalConstant);
const postImpactPositionM = impactState.bodies[0].positionM.slice();
const preImpactSegmentVelocity = vectorScale(preImpactPositionM, 1 / 2);
const postImpactSegmentVelocity = vectorScale(vectorSub(postImpactPositionM, impactEvent.afterPositionM), 1 / 2);
const preImpactSegmentDerivativeErrorMps = vectorDistance(preImpactSegmentVelocity, preImpactVelocityMps);
const postImpactSegmentDerivativeErrorMps = vectorDistance(postImpactSegmentVelocity, impactEvent.afterVelocityMps);

universe.updateClock(nav, realSeconds);
gravity.advanceToSimulationSeconds(universe.snapshot(nav.currentSystemId).universeSeconds);
const afterGravity = gravity.snapshot();
const after = astrometrics.observe({navigationSnapshot: nav, physicsSnapshot: afterGravity});
const haven1 = body(afterGravity, 'planet.haven');

const finitePhaseVectors = derivativeCenter.bodies.every((entry) => {
  const phase = phaseVector(entry);
  return phase.length === 6 && phase.every(Number.isFinite);
});
const localAstrometricObjects = before.visibleObjects.filter((entry) => entry.local);
const normalizedObserverDirections = localAstrometricObjects.every((entry) =>
  Math.abs(vectorMagnitude(entry.direction) - 1) < 1e-12
);
const observerBody = body(beforeGravity, before.observer?.bodyId);
const observerRelativeVectorsMatchGravity = Boolean(observerBody) && localAstrometricObjects.every((entry) => {
  const physical = body(beforeGravity, entry.id);
  if (!physical) return false;
  const expected = vectorSub(physical.positionM, observerBody.positionM);
  return vectorDistance(expected, entry.relativePositionM) < 1e-6;
});

// A second system center adds another independent hyperbolic baseline to every remote estimate.
universe.observeCatalogFromSystem('system.vela-gate');
const paxEstimate = universe.snapshot('system.vela-gate').catalog.find((entry) => entry.id === 'system.pax');

// Exercise the replacement astrometric presentation seam rather than the deleted legacy viewscreen module.
const presentationSource = fs.readFileSync(process.argv[8], 'utf8');
const previousProjection = globalThis.MainComputerBridgeViewscreenProjection;
globalThis.MainComputerBridgeViewscreenProjection = {SCHEMA:'game.bridgeViewscreenProjection.v1'};
Function(presentationSource)();
const presentationApi = globalThis.MainComputerBridgeViewscreenPresentation;
const astrometricMode = presentationApi?.selectModeForSources?.({
  encounterActive:false,
  navigationSnapshot:nav,
  astrometricSnapshot:before
});
const presentationSystem = presentationApi?.createSystem?.({initialMode:'encounter', selectedAtSimulationSeconds:0, displayPowered:true});
presentationSystem?.select?.(astrometricMode, realSeconds);
const astrometricPresentation = presentationSystem?.present?.({
  navigationSnapshot:nav,
  astrometricSnapshot:before,
  simulationSeconds:realSeconds,
  tracked:true
});
const astrometricRendererBridgeInstalled = Boolean(
  astrometricMode === 'astrometric'
  && astrometricPresentation?.schema === 'game.bridgeViewscreenPresentation.v1'
  && astrometricPresentation?.mode === 'astrometric'
  && astrometricPresentation?.targetObject?.id === before.targetObject?.id
  && Array.isArray(astrometricPresentation?.catalog?.stars)
);
globalThis.MainComputerBridgeViewscreenProjection = previousProjection;

const encounterStart = sceneViewerSource.indexOf('        openingEnemyEncounterActive(');
const encounterEnd = sceneViewerSource.indexOf('        enemyShipHullPercent(', encounterStart);
let destroyedEncounterExpiresAfterPresentation = false;
if (encounterStart >= 0 && encounterEnd > encounterStart) {
  const encounterMethods = Function(`return ({${sceneViewerSource.slice(encounterStart, encounterEnd).trim()}});`)();
  const startSystemId = nav.startSystemId || nav.currentSystemId;
  const runtime = {
    ...encounterMethods,
    navigationSnapshot() {
      return {
        currentSystemId: startSystemId,
        startSystemId,
        travelling: false,
        lastCompletedRouteId: null,
        lastArrivalAtMs: null,
        elapsedWorldTime: 0
      };
    },
    bridgeEncounterStatus(nowMs) {
      return {targetDestroyed:true, lastImpactAgeMs:Number(nowMs)-1000};
    }
  };
  destroyedEncounterExpiresAfterPresentation = Boolean(
    runtime.openingEnemyEncounterActive(2899) === true
    && runtime.openingEnemyEncounterActive(2900) === false
  );
}

const checks = {
  astrometricRendererBridgeInstalled,
  destroyedEncounterExpiresAfterPresentation,
  universeCatalogLoaded: universe.snapshot(nav.currentSystemId).systemCount === project.metadata.spaceNavigation.systems.length,
  observerIsMotherShip: before.observer?.bodyId === 'ship.mother',
  targetIsHaven: before.targetObjectId === 'planet.haven',
  havenComesFromGravity: visible(before, 'planet.haven')?.source === 'local-gravity',
  moonComesFromGravity: visible(before, 'moon.haven-i')?.source === 'local-gravity',
  remoteVelaAComesFromHyperbolicCatalog: visible(before, 'star.vela-gate-a')?.source === 'hyperbolic-catalog',
  remoteVelaBComesFromHyperbolicCatalog: visible(before, 'star.vela-gate-b')?.source === 'hyperbolic-catalog',
  startupBaselineRecorded: before.baselineCount === project.metadata.spaceNavigation.systems.length - 1,
  secondBaselineRecorded: paxEstimate?.estimatorCount === 2,
  clockAdvanced: after.universeSeconds > before.universeSeconds,
  gravitySynchronizedToClock: afterGravity.simulationSeconds <= after.universeSeconds && (after.universeSeconds - afterGravity.simulationSeconds) < gravity.fixedStepSeconds,
  havenMovedUnderGravity: Math.hypot(
    haven1.positionM[0] - haven0.positionM[0],
    haven1.positionM[1] - haven0.positionM[1],
    haven1.positionM[2] - haven0.positionM[2]
  ) > 0,
  frontendPhaseVectorsAreFinite6D: finitePhaseVectors,
  frontendPositionDerivativeMatchesVelocity: maxPositionDerivativeRelativeError < 1e-6,
  frontendVelocityDerivativeMatchesGravity: maxVelocityDerivativeRelativeError < 1e-3,
  frontendMovingReferenceFrameCanBeLocalZero: Boolean(
    frameCenter
    && centerRelativePhase.length === 6
    && centerRelativePhase.every((value) => value === 0)
    && movingFrameDisplacementM > 0
    && vectorMagnitude(frameCenter.velocityMps) > 0
  ),
  frontendNestedFrameTransformIsReversible: Boolean(
    nestedFramePositionReconstructionErrorM < 1e-3
    && nestedFrameVelocityReconstructionErrorMps < 1e-6
  ),
  frontendObserverDirectionsAreNormalized: normalizedObserverDirections,
  frontendObserverRelativeVectorsMatchGravity: observerRelativeVectorsMatchGravity,
  frontendImpactPreservesPositionContinuity: impactPositionJumpM === 0,
  frontendImpactAppliesImpulseVelocityJump: impactVelocityJumpErrorMps < 1e-12,
  frontendPreAndPostImpactSegmentsRemainDifferentiable: Boolean(
    preImpactSegmentDerivativeErrorMps < 1e-12
    && postImpactSegmentDerivativeErrorMps < 1e-12
  )
};
const ok = Object.values(checks).every(Boolean);
console.log(JSON.stringify({
  ok,
  checks,
  metrics: {
    systemCount: universe.snapshot(nav.currentSystemId).systemCount,
    startupBaselineCount: before.baselineCount,
    paxEstimatorCountAfterSecondBaseline: paxEstimate?.estimatorCount || 0,
    visibleObjectCount: before.visibleObjectCount,
    localObjectCount: before.localObjectCount,
    catalogObjectCount: before.catalogObjectCount,
    targetDistanceM: before.targetObject?.distanceM || null,
    targetAngularRadiusDeg: (before.targetObject?.angularRadiusRad || 0) * 180 / Math.PI,
    universeSecondsBefore: before.universeSeconds,
    universeSecondsAfter: after.universeSeconds,
    gravitySimulationSecondsAfter: afterGravity.simulationSeconds,
    frontendPhaseVectorDimensions: 6,
    differentiatedBodyCount,
    derivativeSampleSeconds: derivativeCenterSeconds,
    derivativeDtSeconds,
    maxPositionDerivativeRelativeError,
    maxVelocityDerivativeRelativeError,
    movingFrameOriginBodyId: frameCenter?.id || null,
    movingFrameDisplacementM,
    nestedFramePositionReconstructionErrorM,
    nestedFrameVelocityReconstructionErrorMps,
    impactPositionJumpM,
    impactDeltaVelocityMps: actualImpactDeltaVelocityMps,
    preImpactSegmentDerivativeErrorMps,
    postImpactSegmentDerivativeErrorMps
  }
}));
process.exitCode = ok ? 0 : 1;
"""
    result = subprocess.run(
        [
            node,
            "-e",
            js,
            str(runtimes["navigation"].resolve()),
            str(runtimes["universe"].resolve()),
            str(runtimes["gravity"].resolve()),
            str(runtimes["astrometrics"].resolve()),
            str(project),
            str(args.real_seconds),
            str((SCRIPTS / "scene-viewer.js").resolve()),
            str((SCRIPTS / "bridge-viewscreen-presentation.js").resolve()),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        print(json.dumps({"ok": False, "error": result.stderr.strip() or result.stdout.strip()}, indent=2))
        return result.returncode
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(json.dumps({"ok": False, "error": result.stderr.strip() or result.stdout.strip()}, indent=2))
        return 2

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
