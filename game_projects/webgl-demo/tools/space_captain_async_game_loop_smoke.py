from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


GAME_ROOT = Path(__file__).resolve().parents[1]
ROOT = GAME_ROOT.parents[1]
DEFAULT_PROJECT = GAME_ROOT / "project.json"
DEFAULT_GRAVITY = GAME_ROOT / "web" / "scripts" / "space-gravity-runtime.js"


def _parse_csv_floats(value: str) -> list[float]:
    result: list[float] = []
    for raw in str(value).split(","):
        raw = raw.strip()
        if not raw:
            continue
        parsed = float(raw)
        if parsed <= 0:
            raise ValueError("intervals and simulated latencies must be positive")
        result.append(parsed)
    if not result:
        raise ValueError("at least one value is required")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke the asynchronous in-system captain/game-loop contract. Captain judgments run "
            "without blocking smooth viewport prediction or authoritative physics ticks; results "
            "are classified against the game's 1-5 second tactical deadlines and never rewrite past physics."
        )
    )
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--gravity", type=Path, default=DEFAULT_GRAVITY)
    parser.add_argument("--backend-url", type=str, default="")
    parser.add_argument("--checkpoint-id", type=str, default="smoke.tinystories-clef.reference")
    parser.add_argument("--checkpoint-sha256", type=str, default="reference-pinned-checkpoint")
    parser.add_argument("--questions-per-captain", type=int, default=20)
    parser.add_argument("--viewport-hz", type=float, default=60.0)
    parser.add_argument("--intervals-seconds", type=str, default="1,2,3,4,5")
    parser.add_argument(
        "--simulated-latencies-seconds",
        type=str,
        default="0.12,0.62,1.62",
        help="Used only without --backend-url; one latency per captain.",
    )
    parser.add_argument("--evidence-execution-mode", choices=("auto", "prefix-cache", "full-batch"), default="auto")
    parser.add_argument("--request-timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()

    if args.questions_per_captain < 1:
        raise SystemExit("--questions-per-captain must be positive")
    if args.viewport_hz <= 0:
        raise SystemExit("--viewport-hz must be positive")
    if args.request_timeout_seconds <= 0:
        raise SystemExit("--request-timeout-seconds must be positive")
    try:
        intervals = sorted(set(_parse_csv_floats(args.intervals_seconds)))
        simulated = _parse_csv_floats(args.simulated_latencies_seconds)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if len(simulated) < 3:
        raise SystemExit("--simulated-latencies-seconds must provide at least three values")

    node = shutil.which("node")
    if not node:
        print(json.dumps({"ok": False, "error": "node executable not found"}, indent=2))
        return 2
    project = args.project.resolve()
    gravity = args.gravity.resolve()
    for path in (project, gravity):
        if not path.exists():
            print(json.dumps({"ok": False, "error": f"missing required file: {path}"}, indent=2))
            return 2

    config = {
        "backendUrl": args.backend_url,
        "checkpointId": args.checkpoint_id,
        "checkpointSha256": args.checkpoint_sha256,
        "questionsPerCaptain": args.questions_per_captain,
        "viewportHz": args.viewport_hz,
        "intervalsSeconds": intervals,
        "simulatedLatenciesSeconds": simulated[:3],
        "evidenceExecutionMode": args.evidence_execution_mode,
        "requestTimeoutSeconds": args.request_timeout_seconds,
    }

    js = r'''
const fs = require('fs');
const http = require('http');
const https = require('https');
const {performance} = require('perf_hooks');
const [gravityPath, projectPath, configJson] = process.argv.slice(-3);
const gravityApi = require(gravityPath);
const project = JSON.parse(fs.readFileSync(projectPath, 'utf8'));
const config = JSON.parse(configJson);

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
function finite(v, fallback = 0) { const n = Number(v); return Number.isFinite(n) ? n : fallback; }
function clone(v) { return JSON.parse(JSON.stringify(v)); }
function magnitude(v) { return Math.hypot(...v.map(Number)); }
function distance(a, b) { return magnitude(a.map((v, i) => finite(v) - finite(b[i]))); }
function quantile(values, q) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  if (sorted.length === 1) return sorted[0];
  const p = (sorted.length - 1) * q;
  const lo = Math.floor(p), hi = Math.ceil(p);
  return lo === hi ? sorted[lo] : sorted[lo] + (sorted[hi] - sorted[lo]) * (p - lo);
}
function body(snapshot, id) { return snapshot.bodies.find((row) => row.id === id) || null; }
function accelerations(snapshot) {
  return gravityApi.accelerationsForBodies(snapshot.bodies, gravityApi.DEFAULT_G);
}
function predict(snapshot, accelerationRows, dt) {
  return {
    simulationSeconds: snapshot.simulationSeconds + dt,
    bodies: snapshot.bodies.map((entry, index) => ({
      id: entry.id,
      positionM: entry.positionM.map((p, axis) => finite(p) + finite(entry.velocityMps[axis]) * dt + 0.5 * finite(accelerationRows[index][axis]) * dt * dt),
      velocityMps: entry.velocityMps.map((v, axis) => finite(v) + finite(accelerationRows[index][axis]) * dt),
    }))
  };
}
function maxPositionError(predicted, actual) {
  let maximum = 0;
  for (const p of predicted.bodies) {
    const a = body(actual, p.id);
    if (!a) continue;
    maximum = Math.max(maximum, distance(p.positionM, a.positionM));
  }
  return maximum;
}
function maxDisplacement(before, after) {
  let maximum = 0;
  for (const p of before.bodies) {
    const a = body(after, p.id);
    if (!a) continue;
    maximum = Math.max(maximum, distance(p.positionM, a.positionM));
  }
  return maximum;
}
function canonicalQuestions(count) {
  const descriptions = {
    'uncertain-threat': 'Evidence is unresolved; preserve observation before committing.',
    'immediate-threat': 'Treat the contact as an immediate threat requiring urgent action.',
    'low-threat': 'Treat the contact as low immediate danger while maintaining awareness.'
  };
  const pairs = [
    ['uncertain-threat', 'immediate-threat'],
    ['uncertain-threat', 'low-threat'],
    ['immediate-threat', 'low-threat']
  ];
  const lenses = ['crew','mission','uncertainty','survival','initiative','range','evidence','risk'];
  const rows = [];
  for (let i = 0; i < count; i += 1) {
    let [a, b] = pairs[i % pairs.length];
    if (Math.floor(i / pairs.length) % 2) [a, b] = [b, a];
    rows.push({
      id: `async.appraisal.q${String(i + 1).padStart(2, '0')}`,
      optionA: a,
      optionB: b,
      optionAText: descriptions[a],
      optionBText: descriptions[b],
      semanticMode: 'grounded-compact-pairwise-v2',
      text: `Lens=${lenses[i % lenses.length]}. Which alternative better fits the current threat appraisal?`
    });
  }
  return rows;
}
function requestFor(jacket) {
  return {
    schema: 'game.captainDecisionRequest.v6',
    checkpoint: {family: 'tinystories-clef', checkpointId: config.checkpointId, sha256: config.checkpointSha256},
    semanticContext: {
      mode: 'compact-shared-context-v2',
      text: `Doctrine: ${jacket.doctrine} Current state: uncertain contact at moderate range; current action remains in force while deliberation is asynchronous.`
    },
    execution: {evidenceMode: config.evidenceExecutionMode},
    jacket: {id: jacket.id, goal: jacket.goal},
    questions: canonicalQuestions(config.questionsPerCaptain)
  };
}
function postJson(urlText, payload) {
  const parsed = new URL(urlText);
  const lib = parsed.protocol === 'https:' ? https : http;
  const bodyText = JSON.stringify(payload);
  return new Promise((resolve, reject) => {
    const req = lib.request({
      method: 'POST', hostname: parsed.hostname, port: parsed.port,
      path: parsed.pathname + parsed.search,
      headers: {'content-type': 'application/json', 'content-length': Buffer.byteLength(bodyText)},
      timeout: Math.max(1000, Number(config.requestTimeoutSeconds) * 1000)
    }, (res) => {
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        if (res.statusCode < 200 || res.statusCode >= 300) return reject(new Error(`HTTP ${res.statusCode}: ${text.slice(0, 500)}`));
        try { resolve(JSON.parse(text)); } catch (error) { reject(error); }
      });
    });
    req.on('timeout', () => req.destroy(new Error('captain request timeout')));
    req.on('error', reject);
    req.end(bodyText);
  });
}

const jackets = [
  {id: 'captain.jacket.guardian', goal: 'protect-people-and-ship', doctrine: 'Protect people and ship; avoid needless escalation.'},
  {id: 'captain.jacket.hunter', goal: 'seize-tactical-initiative', doctrine: 'Seize tactical initiative; pressure the contact unless survival forbids it.'},
  {id: 'captain.jacket.survivor', goal: 'preserve-optionality-and-survival', doctrine: 'Preserve survival and future options; avoid entrapment.'}
];

async function run() {
  const gravityDefinition = clone(project.metadata.spacePhysics);
  const runtime = gravityApi.create(gravityDefinition, {projectId: project.id});
  runtime.setActiveSystem('system.solace-reach');
  const control = gravityApi.create(gravityDefinition, {projectId: project.id});
  control.setActiveSystem('system.solace-reach');
  const startSnapshot = runtime.snapshot();
  const initialAcceleration = accelerations(startSnapshot);
  const intervals = config.intervalsSeconds.map(Number).sort((a, b) => a - b);
  const maxHorizon = intervals[intervals.length - 1];
  const authoritativeInterval = intervals[0];

  const gradientHorizons = intervals.map((seconds) => {
    const horizonRuntime = gravityApi.create(gravityDefinition, {projectId: project.id});
    horizonRuntime.setActiveSystem('system.solace-reach');
    const before = horizonRuntime.snapshot();
    const predicted = predict(before, accelerations(before), seconds);
    const actual = horizonRuntime.advancePhysicsSeconds(seconds);
    const errorM = maxPositionError(predicted, actual);
    const displacementM = maxDisplacement(before, actual);
    return {
      seconds,
      maximumPredictionErrorM: errorM,
      maximumBodyDisplacementM: displacementM,
      errorFractionOfMaximumDisplacement: displacementM > 0 ? errorM / displacementM : 0
    };
  });

  const launchedAt = performance.now();
  const callStates = jackets.map((jacket, index) => ({
    jacketId: jacket.id,
    launchMs: performance.now() - launchedAt,
    completedMs: null,
    ok: false,
    error: null,
    response: null,
  }));
  const callPromises = jackets.map(async (jacket, index) => {
    const state = callStates[index];
    try {
      let response;
      if (config.backendUrl) {
        response = await postJson(config.backendUrl, requestFor(jacket));
      } else {
        await sleep(Number(config.simulatedLatenciesSeconds[index]) * 1000);
        response = {
          schema: 'game.captainDecisionResponse.v6',
          checkpointId: config.checkpointId,
          checkpointSha256: config.checkpointSha256,
          captainModelCallCount: 1,
          clefHeadForwardCount: 1,
          independentJudgmentCount: config.questionsPerCaptain,
          batchingMode: 'independent-pairwise-questions',
          modelLatencyMs: Number(config.simulatedLatenciesSeconds[index]) * 1000,
          evidenceExecutionModeActual: config.evidenceExecutionMode === 'auto' ? 'reference' : config.evidenceExecutionMode,
          answers: canonicalQuestions(config.questionsPerCaptain).map((q) => ({questionId: q.id, choice: q.optionA}))
        };
      }
      state.response = response;
      state.ok = response
        && response.schema === 'game.captainDecisionResponse.v6'
        && String(response.checkpointId) === String(config.checkpointId)
        && Number(response.captainModelCallCount) === 1
        && Number(response.clefHeadForwardCount) === 1
        && Number(response.independentJudgmentCount) === Number(config.questionsPerCaptain);
      if (!state.ok) state.error = 'backend response failed async captain contract validation';
    } catch (error) {
      state.error = `${error?.name || 'Error'}: ${error?.message || String(error)}`;
    } finally {
      state.completedMs = performance.now() - launchedAt;
    }
    return state;
  });

  const viewportRows = [];
  const boundaryRows = [];
  const framePeriodMs = 1000 / Number(config.viewportHz);
  let anchor = runtime.snapshot();
  let anchorAcceleration = accelerations(anchor);
  let anchorWallSeconds = 0;
  let nextBoundarySeconds = authoritativeInterval;
  let lastFrameMs = performance.now();
  const frameGapsMs = [];

  while ((performance.now() - launchedAt) / 1000 < maxHorizon) {
    const now = performance.now();
    const elapsedSeconds = (now - launchedAt) / 1000;
    const gap = now - lastFrameMs;
    if (viewportRows.length) frameGapsMs.push(gap);
    lastFrameMs = now;

    while (elapsedSeconds + 1e-9 >= nextBoundarySeconds && nextBoundarySeconds <= maxHorizon + 1e-9) {
      const predictedBoundary = predict(anchor, anchorAcceleration, authoritativeInterval);
      const actual = runtime.advancePhysicsSeconds(authoritativeInterval);
      boundaryRows.push({
        wallBoundarySeconds: nextBoundarySeconds,
        simulationSeconds: actual.simulationSeconds,
        maximumViewportCorrectionM: maxPositionError(predictedBoundary, actual),
        captainCallsCompletedAtBoundary: callStates.filter((row) => row.completedMs !== null && row.completedMs <= nextBoundarySeconds * 1000).length,
        captainCallsStillInFlight: callStates.filter((row) => row.completedMs === null || row.completedMs > nextBoundarySeconds * 1000).length,
      });
      anchor = actual;
      anchorAcceleration = accelerations(anchor);
      anchorWallSeconds = nextBoundarySeconds;
      nextBoundarySeconds += authoritativeInterval;
    }

    const localElapsed = Math.max(0, elapsedSeconds - anchorWallSeconds);
    const predictedNow = predict(anchor, anchorAcceleration, localElapsed);
    const ship = body(predictedNow, 'ship.mother');
    viewportRows.push({
      elapsedSeconds,
      shipPositionM: ship ? ship.positionM : null,
      captainCallsStillInFlight: callStates.filter((row) => row.completedMs === null).length,
    });
    const targetNext = launchedAt + (viewportRows.length + 1) * framePeriodMs;
    await sleep(Math.max(0, targetNext - performance.now()));
  }

  while (nextBoundarySeconds <= maxHorizon + 1e-9) {
    const predictedBoundary = predict(anchor, anchorAcceleration, authoritativeInterval);
    const actual = runtime.advancePhysicsSeconds(authoritativeInterval);
    boundaryRows.push({
      wallBoundarySeconds: nextBoundarySeconds,
      simulationSeconds: actual.simulationSeconds,
      maximumViewportCorrectionM: maxPositionError(predictedBoundary, actual),
      captainCallsCompletedAtBoundary: callStates.filter((row) => row.completedMs !== null && row.completedMs <= nextBoundarySeconds * 1000).length,
      captainCallsStillInFlight: callStates.filter((row) => row.completedMs === null || row.completedMs > nextBoundarySeconds * 1000).length,
    });
    anchor = actual;
    anchorAcceleration = accelerations(anchor);
    anchorWallSeconds = nextBoundarySeconds;
    nextBoundarySeconds += authoritativeInterval;
  }

  await Promise.all(callPromises);
  for (let elapsed = 0; elapsed + authoritativeInterval <= maxHorizon + 1e-9; elapsed += authoritativeInterval) {
    control.advancePhysicsSeconds(authoritativeInterval);
  }
  const finalSnapshot = runtime.snapshot();
  const controlSnapshot = control.snapshot();
  const finalPhysicsDifferenceM = maxPositionError(
    {bodies: finalSnapshot.bodies.map((b) => ({id: b.id, positionM: b.positionM}))},
    controlSnapshot
  );

  const readinessByIntervalSeconds = Object.fromEntries(intervals.map((seconds) => {
    const completed = callStates.filter((row) => row.ok && row.completedMs <= seconds * 1000).length;
    return [String(seconds), {
      intervalSeconds: seconds,
      captainsReady: completed,
      captainCount: jackets.length,
      allCaptainsReady: completed === jackets.length,
      lateCaptains: callStates.filter((row) => !row.ok || row.completedMs > seconds * 1000).map((row) => row.jacketId),
    }];
  }));
  const launchSpreadMs = Math.max(...callStates.map((row) => row.launchMs)) - Math.min(...callStates.map((row) => row.launchMs));
  const maxFrameGapMs = frameGapsMs.length ? Math.max(...frameGapsMs) : 0;
  const p95FrameGapMs = quantile(frameGapsMs, 0.95);
  const expectedFrames = maxHorizon * Number(config.viewportHz);
  const viewportTicksWhileCallsInFlight = viewportRows.filter((row) => row.captainCallsStillInFlight > 0).length;
  const maxBoundaryCorrectionM = boundaryRows.length ? Math.max(...boundaryRows.map((row) => row.maximumViewportCorrectionM)) : 0;
  const maxGradientErrorFraction = gradientHorizons.length ? Math.max(...gradientHorizons.map((row) => row.errorFractionOfMaximumDisplacement)) : 0;
  const checkpointPinned = callStates.every((row) => row.ok && String(row.response?.checkpointId) === String(config.checkpointId));
  const exactPhysicsTicks = Math.abs(finalSnapshot.simulationSeconds - maxHorizon) <= 1e-9
    && boundaryRows.length === Math.floor((maxHorizon + 1e-9) / authoritativeInterval);
  const smoothViewport = viewportRows.length >= Math.max(1, expectedFrames * 0.5)
    && maxFrameGapMs <= Math.max(250, framePeriodMs * 8)
    && viewportRows.every((row) => Array.isArray(row.shipPositionM) && row.shipPositionM.every(Number.isFinite));

  const checks = {
    threeCaptainRequestsLaunchedWithoutSequentialAwait: launchSpreadMs < 50,
    viewportContinuesWhileCaptainRequestsAreInFlight: viewportTicksWhileCallsInFlight > 0,
    viewportHeartbeatRemainsResponsive: smoothViewport,
    authoritativePhysicsAdvancesOnClockNotCaptainCompletion: exactPhysicsTicks,
    gradientPredictionSupportsOneToFiveSecondViewportSmoothing: maxGradientErrorFraction < 1e-5,
    viewportCorrectionAtAuthoritativeTicksIsSmall: maxBoundaryCorrectionM < 1,
    captainResponsesCannotRetroactivelyMutatePhysics: finalPhysicsDifferenceM < 1e-9,
    captainCheckpointPinnedAcrossConcurrentRequests: checkpointPinned,
    captainResponseContractPreservedAcrossConcurrentRequests: callStates.every((row) => row.ok),
  };
  const failedChecks = Object.entries(checks).filter(([, value]) => value !== true).map(([key]) => key);
  const calls = callStates.map((row) => ({
    jacketId: row.jacketId,
    launchMs: row.launchMs,
    completedMs: row.completedMs,
    wallLatencyMs: row.completedMs - row.launchMs,
    backendModelLatencyMs: Number(row.response?.modelLatencyMs || 0),
    ok: row.ok,
    error: row.error,
    earliestEligibleControlBoundarySeconds: intervals.find((seconds) => row.completedMs <= seconds * 1000) ?? null,
    missedControlBoundariesSeconds: intervals.filter((seconds) => row.completedMs > seconds * 1000),
    evidenceExecutionModeActual: row.response?.evidenceExecutionModeActual || null,
  }));

  return {
    ok: failedChecks.length === 0,
    timingReadinessIsDiagnostic: true,
    semanticsIgnored: true,
    contract: {
      captainScheduling: 'asynchronous-to-viewport-and-authoritative-physics',
      currentActionPolicy: 'current action remains active until a future control boundary publishes a ready result',
      lateResultPolicy: 'never rewrite past physics; result is eligible only at a control boundary at or after completion',
      viewportPolicy: 'predict smooth in-between motion from authoritative position, velocity, and local acceleration gradient',
    },
    checks,
    failedChecks,
    metrics: {
      captainCount: jackets.length,
      questionsPerCaptain: Number(config.questionsPerCaptain),
      logicalCandidateSequencesAcrossCaptains: jackets.length * Number(config.questionsPerCaptain) * 2 * 3,
      intervalsSeconds: intervals,
      authoritativeIntervalSeconds: authoritativeInterval,
      viewportHz: Number(config.viewportHz),
      viewportFrames: viewportRows.length,
      expectedViewportFrames: expectedFrames,
      viewportTicksWhileCallsInFlight,
      frameGapP95Ms: p95FrameGapMs,
      frameGapMaxMs: maxFrameGapMs,
      captainLaunchSpreadMs: launchSpreadMs,
      readinessByIntervalSeconds,
      maximumViewportCorrectionM: maxBoundaryCorrectionM,
      maximumGradientErrorFraction: maxGradientErrorFraction,
      finalPhysicsDifferenceFromNoCaptainControlM: finalPhysicsDifferenceM,
      finalSimulationSeconds: finalSnapshot.simulationSeconds,
    },
    gradientHorizonDiagnostics: gradientHorizons,
    authoritativeBoundaries: boundaryRows,
    captainCalls: calls,
  };
}

run().then((result) => {
  process.stdout.write(JSON.stringify(result));
}).catch((error) => {
  process.stdout.write(JSON.stringify({ok: false, error: `${error?.name || 'Error'}: ${error?.message || String(error)}`, stack: error?.stack || null}));
  process.exitCode = 2;
});
'''

    proc = subprocess.run(
        [node, "-", str(gravity), str(project), json.dumps(config, separators=(",", ":"))],
        cwd=ROOT,
        input=js,
        capture_output=True,
        text=True,
        check=False,
        timeout=max(args.request_timeout_seconds * 3 + max(intervals) + 10, 30),
    )
    if not proc.stdout.strip():
        print(json.dumps({"ok": False, "error": "async game-loop node smoke returned no JSON", "stderr": proc.stderr[-8000:]}, indent=2))
        return 2
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"could not decode async game-loop smoke JSON: {exc}", "stdoutTail": proc.stdout[-8000:], "stderrTail": proc.stderr[-8000:]}, indent=2))
        return 2
    if proc.stderr:
        result["stderr"] = proc.stderr[-8000:]
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
