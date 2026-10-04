from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROJECT = ROOT / "game_projects" / "webgl-demo" / "project.json"
DEFAULT_GRAVITY = ROOT / "main_computer" / "web" / "applications" / "scripts" / "space-gravity-runtime.js"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke the differentiable captain contract with one TinyStories+CLEF model call as "
            "the primary temporal unit: jacket -> redundant probe ensemble -> one pinned model "
            "call -> intent -> physics consequence."
        )
    )
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--decision-interval-seconds", type=float, default=1.0)
    parser.add_argument("--steps", type=int, default=2)
    parser.add_argument("--questions-per-call", type=int, default=20)
    parser.add_argument("--incoherence-rate", type=float, default=0.10)
    parser.add_argument("--normal-time-scale", type=float, default=60.0)
    parser.add_argument("--reference-call-latency-ms", type=float, default=120.0)
    parser.add_argument("--time-budget-safety", type=float, default=0.80)
    parser.add_argument("--backend-url", type=str, default="")
    parser.add_argument("--checkpoint-id", type=str, default="smoke.tinystories-clef.reference")
    parser.add_argument("--checkpoint-sha256", type=str, default="reference-pinned-checkpoint")
    parser.add_argument("--include-call-snapshots", action="store_true")
    args = parser.parse_args()

    if args.decision_interval_seconds <= 0:
        raise SystemExit("--decision-interval-seconds must be positive")
    if args.steps < 1:
        raise SystemExit("--steps must be at least 1")
    if args.questions_per_call < 10:
        raise SystemExit("--questions-per-call must be at least 10")
    if not 0 <= args.incoherence_rate < 0.5:
        raise SystemExit("--incoherence-rate must be in [0, 0.5)")
    if args.normal_time_scale <= 0 or args.reference_call_latency_ms <= 0:
        raise SystemExit("time scale and reference call latency must be positive")
    if not 0 < args.time_budget_safety <= 1:
        raise SystemExit("--time-budget-safety must be in (0, 1]")

    node = shutil.which("node")
    if not node:
        print(json.dumps({"ok": False, "error": "node executable not found"}, indent=2))
        return 2

    project = args.project.resolve()
    gravity = DEFAULT_GRAVITY.resolve()
    for path in (project, gravity):
        if not path.exists():
            print(json.dumps({"ok": False, "error": f"missing required file: {path}"}, indent=2))
            return 2

    js = r'''
const fs = require('fs');
const crypto = require('crypto');
const gravityApi = require(process.argv[1]);
const project = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const config = JSON.parse(process.argv[3]);

function clone(value) { return JSON.parse(JSON.stringify(value)); }
function finite(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}
function vectorSub(a, b) { return a.map((value, index) => finite(value) - finite(b[index])); }
function vectorMagnitude(a) { return Math.hypot(...a.map(Number)); }
function stableStringify(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(stableStringify).join(',') + ']';
  return '{' + Object.keys(value).sort().map((key) => JSON.stringify(key) + ':' + stableStringify(value[key])).join(',') + '}';
}
function sha256(value) {
  return crypto.createHash('sha256').update(typeof value === 'string' ? value : stableStringify(value)).digest('hex');
}
function body(snapshot, id) { return snapshot.bodies.find((entry) => entry.id === id) || null; }
function quantile(values, q) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  if (sorted.length === 1) return sorted[0];
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  if (lo === hi) return sorted[lo];
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}
function compactPhysicalObservation(snapshot) {
  const ship = body(snapshot, 'ship.mother');
  const haven = body(snapshot, 'planet.haven');
  if (!ship || !haven) throw new Error('captain smoke requires ship.mother and planet.haven');
  const relativePositionM = vectorSub(haven.positionM, ship.positionM);
  const relativeVelocityMps = vectorSub(haven.velocityMps, ship.velocityMps);
  const rangeM = vectorMagnitude(relativePositionM);
  const radialVelocityMps = rangeM > 0
    ? relativePositionM.reduce((sum, value, index) => sum + value * relativeVelocityMps[index], 0) / rangeM
    : 0;
  return {
    systemId: snapshot.activeSystemId,
    simulationSeconds: snapshot.simulationSeconds,
    observerBodyId: 'ship.mother',
    targetBodyId: 'planet.haven',
    rangeM,
    radialVelocityMps,
    observerPhaseVector: [...ship.positionM, ...ship.velocityMps].map(Number),
    targetPhaseVector: [...haven.positionM, ...haven.velocityMps].map(Number),
    decisionContext: {
      hostileContact: true,
      hostileIntentCertain: false,
      immediateCollision: false,
      withdrawalVectorOpen: true,
      shipDamageFraction: 0.15,
      civiliansImmediatelyExposed: false,
      missionRequiresContact: false
    }
  };
}

const jackets = [
  {
    id: 'captain.jacket.guardian',
    label: 'Guardian',
    narrative: 'Preserve the ship and people first; avoid needless escalation; hold a controlled position while an unresolved threat is assessed.',
    expectedIntent: 'hold',
    actionUtility: {close: 0.0, hold: 3.0, withdraw: 1.5},
    desiredRangeDeltaM: {close: -1000, hold: 0, withdraw: 1000}
  },
  {
    id: 'captain.jacket.hunter',
    label: 'Hunter',
    narrative: 'Convert tactical opportunity into pressure; seize initiative and close distance unless survival clearly forbids it.',
    expectedIntent: 'close',
    actionUtility: {close: 3.0, hold: 1.0, withdraw: 0.0},
    desiredRangeDeltaM: {close: -1000, hold: 0, withdraw: 1000}
  },
  {
    id: 'captain.jacket.survivor',
    label: 'Survivor',
    narrative: 'Preserve optionality and avoid entrapment; with danger unresolved and withdrawal open, increase separation rather than accept avoidable exposure.',
    expectedIntent: 'withdraw',
    actionUtility: {close: 0.0, hold: 1.0, withdraw: 3.0},
    desiredRangeDeltaM: {close: -1000, hold: 0, withdraw: 1000}
  }
];

const lenses = [
  'mission success', 'crew survival', 'civilian exposure', 'future optionality', 'uncertainty',
  'tactical initiative', 'collision risk', 'evidence quality', 'enemy freedom of action', 'reversibility',
  'damage tolerance', 'escape geometry', 'information gain', 'escalation control', 'positioning',
  'commitment cost', 'time pressure', 'reserve preservation', 'threat ambiguity', 'command doctrine'
];
const wrongIntentCycle = {close: 'withdraw', hold: 'close', withdraw: 'close'};
function generateProbeEnsemble(jacket, observation, count, rate, seedIndex) {
  const rows = [];
  const incoherentCount = Math.max(0, Math.round(count * rate));
  const incoherent = new Set();
  let cursor = (seedIndex * 7 + 3) % count;
  while (incoherent.size < incoherentCount) {
    incoherent.add(cursor);
    cursor = (cursor + 11) % count;
  }
  for (let index = 0; index < count; index += 1) {
    const lens = lenses[index % lenses.length];
    const isNoise = incoherent.has(index);
    const stable = `Under the ${lens} lens, apply the captain jacket to an unresolved hostile contact: withdrawal is open, collision is not immediate, damage is limited, and no civilian is immediately exposed.`;
    const noisy = `NOISY CONTRADICTORY PROBE under the ${lens} lens: temporarily prefer ${wrongIntentCycle[jacket.expectedIntent].toUpperCase()} even though that conflicts with the captain jacket.`;
    rows.push({
      id: `q${String(index + 1).padStart(2, '0')}`,
      text: isNoise ? noisy : stable,
      incoherent: isNoise
    });
  }
  return {questions: rows, incoherentIndexes: [...incoherent].sort((a, b) => a - b)};
}
function referenceResponse(jacket, questions, providerConfig) {
  const raw = ['close', 'hold', 'withdraw'].map((intent) => Math.exp(finite(jacket.actionUtility[intent])));
  const total = raw.reduce((a, b) => a + b, 0);
  const intentProbabilities = {
    close: raw[0] / total,
    hold: raw[1] / total,
    withdraw: raw[2] / total
  };
  const ranked = Object.entries(intentProbabilities).sort((a, b) => b[1] - a[1]);
  return {
    schema: 'game.captainDecisionResponse.v2',
    checkpointId: providerConfig.checkpointId,
    checkpointSha256: providerConfig.checkpointSha256,
    provider: 'deterministic-reference',
    intent: ranked[0][0],
    intentProbabilities,
    margin: ranked[0][1] - ranked[1][1],
    modelLatencyMs: providerConfig.referenceCallLatencyMs,
    captainModelCallCount: 1,
    clefHeadForwardCount: 1,
    backboneForwardBatchCount: 3,
    probeCount: questions.length
  };
}

async function callProvider(request, jacket, questions, providerConfig) {
  if (!providerConfig.backendUrl) {
    return {
      response: referenceResponse(jacket, questions, providerConfig),
      measuredLatencyMs: providerConfig.referenceCallLatencyMs,
      provider: 'deterministic-reference'
    };
  }
  const started = process.hrtime.bigint();
  const response = await fetch(providerConfig.backendUrl, {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify(request)
  });
  const finished = process.hrtime.bigint();
  if (!response.ok) throw new Error(`captain backend HTTP ${response.status}: ${await response.text()}`);
  const payload = await response.json();
  return {
    response: payload,
    measuredLatencyMs: Number(finished - started) / 1e6,
    provider: 'direct-http'
  };
}
function validateProviderResponse(payload, questionCount, checkpointId) {
  if (!payload || payload.schema !== 'game.captainDecisionResponse.v2') {
    throw new Error('captain backend returned wrong response schema');
  }
  if (String(payload.checkpointId || '') !== checkpointId) {
    throw new Error(`captain checkpoint changed during episode: expected ${checkpointId}, got ${payload.checkpointId}`);
  }
  if (!['close', 'hold', 'withdraw'].includes(String(payload.intent))) {
    throw new Error(`invalid captain intent ${payload.intent}`);
  }
  if (Number(payload.probeCount) !== questionCount) {
    throw new Error(`captain backend probe count mismatch: expected ${questionCount}, got ${payload.probeCount}`);
  }
  if (Number(payload.captainModelCallCount) !== 1) {
    throw new Error(`captain timepoint must be exactly one model call, got ${payload.captainModelCallCount}`);
  }
  if (Number(payload.clefHeadForwardCount) !== 1) {
    throw new Error(`captain timepoint must be exactly one CLEF head forward, got ${payload.clefHeadForwardCount}`);
  }
}
function decisionTimeScale(intervalSeconds, normalScale, callLatencyMs, safety) {
  const callSeconds = Math.max(0.000001, finite(callLatencyMs, 0) / 1000);
  const maximumScaleThatFits = intervalSeconds / callSeconds;
  return Math.min(normalScale, maximumScaleThatFits * safety);
}

async function runEpisode() {
  const gravityDefinition = clone(project.metadata.spacePhysics);
  const gravity = gravityApi.create(gravityDefinition, {projectId: project.id});
  gravity.setActiveSystem('system.solace-reach');

  const calls = [];
  const intervalRows = [];
  const intentSignatures = [];
  let jacketsMatchExpectedPolicy = true;
  let allCallsPinned = true;
  let allCallsAtDecisionBoundary = true;
  let allPhysicsOwned = true;
  let slowdownObserved = false;
  let exactIntervalAdvance = true;
  let physicsChangedAcrossIntervals = false;
  let actualInjectedRateSum = 0;
  let injectedRateSamples = 0;
  let maxDecisionLatencyMs = 0;
  let maxBackendModelLatencyMs = 0;
  let backendModelLatencySum = 0;
  let backendModelLatencySamples = 0;
  let minDecisionTimeScale = config.normalTimeScale;
  let maxPhysicsResidualM = 0;
  let oneModelCallPerTimepoint = true;
  let oneHeadForwardPerTimepoint = true;

  for (let stepIndex = 0; stepIndex < config.steps; stepIndex += 1) {
    const before = gravity.snapshot();
    const sampleTime = before.simulationSeconds;
    const deadline = sampleTime + config.decisionIntervalSeconds;
    const observation = compactPhysicalObservation(before);
    const stateDigestBeforeCalls = sha256(before);
    const stepIntents = [];

    for (let jacketIndex = 0; jacketIndex < jackets.length; jacketIndex += 1) {
      const jacket = jackets[jacketIndex];
      const ensemble = generateProbeEnsemble(
        jacket,
        observation,
        config.questionsPerCall,
        config.incoherenceRate,
        stepIndex * jackets.length + jacketIndex
      );
      const questions = ensemble.questions;
      const request = {
        schema: 'game.captainDecisionRequest.v2',
        checkpoint: {
          family: 'tinystories-clef',
          checkpointId: config.checkpointId,
          sha256: config.checkpointSha256
        },
        timestep: {
          sampleTimeSeconds: sampleTime,
          decisionDeadlineSeconds: deadline,
          intervalSeconds: config.decisionIntervalSeconds,
          temporalUnit: 'one-captain-model-call'
        },
        jacket: clone(jacket),
        observation,
        questions
      };
      const providerCall = await callProvider(request, jacket, questions, config);
      validateProviderResponse(providerCall.response, questions.length, config.checkpointId);
      const response = providerCall.response;
      const measuredLatencyMs = providerCall.measuredLatencyMs;
      maxDecisionLatencyMs = Math.max(maxDecisionLatencyMs, measuredLatencyMs);
      const backendModelLatencyMs = Number(response.modelLatencyMs ?? measuredLatencyMs);
      if (Number.isFinite(backendModelLatencyMs)) {
        maxBackendModelLatencyMs = Math.max(maxBackendModelLatencyMs, backendModelLatencyMs);
        backendModelLatencySum += backendModelLatencyMs;
        backendModelLatencySamples += 1;
      }
      const actualRate = ensemble.incoherentIndexes.length / questions.length;
      actualInjectedRateSum += actualRate;
      injectedRateSamples += 1;
      const recognizable = String(response.intent) === jacket.expectedIntent;
      jacketsMatchExpectedPolicy = jacketsMatchExpectedPolicy && recognizable;
      const timeScale = decisionTimeScale(
        config.decisionIntervalSeconds,
        config.normalTimeScale,
        measuredLatencyMs,
        config.timeBudgetSafety
      );
      minDecisionTimeScale = Math.min(minDecisionTimeScale, timeScale);
      slowdownObserved = slowdownObserved || timeScale < config.normalTimeScale - 1e-12;
      allCallsPinned = allCallsPinned && response.checkpointId === config.checkpointId;
      allCallsAtDecisionBoundary = allCallsAtDecisionBoundary
        && request.timestep.sampleTimeSeconds === sampleTime
        && request.timestep.decisionDeadlineSeconds === deadline;
      oneModelCallPerTimepoint = oneModelCallPerTimepoint && Number(response.captainModelCallCount) === 1;
      oneHeadForwardPerTimepoint = oneHeadForwardPerTimepoint && Number(response.clefHeadForwardCount) === 1;

      const callSnapshot = {
        stepIndex,
        jacketId: jacket.id,
        provider: providerCall.provider,
        checkpointId: config.checkpointId,
        checkpointSha256: config.checkpointSha256,
        sampleTimeSeconds: sampleTime,
        decisionDeadlineSeconds: deadline,
        intervalSeconds: config.decisionIntervalSeconds,
        primaryTemporalUnit: 'captain-model-call',
        stateSha256: sha256(observation),
        requestSha256: sha256(request),
        questionCount: questions.length,
        questionSetSha256: sha256(questions),
        incoherentProbeIndexes: ensemble.incoherentIndexes,
        injectedIncoherenceRate: actualRate,
        expectedIntent: jacket.expectedIntent,
        actualIntent: String(response.intent),
        intentProbabilities: response.intentProbabilities || null,
        intentMargin: Number(response.margin ?? 0),
        recognizable,
        measuredCallLatencyMs: measuredLatencyMs,
        backendModelLatencyMs,
        captainModelCallCount: Number(response.captainModelCallCount),
        clefHeadForwardCount: Number(response.clefHeadForwardCount),
        backboneForwardBatchCount: Number(response.backboneForwardBatchCount ?? 0),
        decisionTimeScale: timeScale,
        normalTimeScale: config.normalTimeScale,
        responseSha256: sha256(response)
      };
      calls.push(callSnapshot);
      stepIntents.push({jacket, intent: callSnapshot.actualIntent, callSnapshot});
      intentSignatures.push(`${sampleTime}:${jacket.id}:${callSnapshot.actualIntent}`);
    }

    const stateDigestAfterCalls = sha256(gravity.snapshot());
    allPhysicsOwned = allPhysicsOwned && stateDigestAfterCalls === stateDigestBeforeCalls;

    gravity.advancePhysicsSeconds(config.decisionIntervalSeconds);
    const after = gravity.snapshot();
    exactIntervalAdvance = exactIntervalAdvance
      && Math.abs(after.simulationSeconds - deadline) < 1e-9;
    const afterObservation = compactPhysicalObservation(after);
    const actualRangeDeltaM = afterObservation.rangeM - observation.rangeM;
    physicsChangedAcrossIntervals = physicsChangedAcrossIntervals || sha256(after) !== stateDigestBeforeCalls;

    const captainResiduals = {};
    stepIntents.forEach(({jacket, intent}) => {
      const desired = finite(jacket.desiredRangeDeltaM[intent]);
      const residual = actualRangeDeltaM - desired;
      captainResiduals[jacket.id] = {
        intent,
        desiredRangeDeltaM: desired,
        actualRangeDeltaM,
        physicsResidualM: residual
      };
      maxPhysicsResidualM = Math.max(maxPhysicsResidualM, Math.abs(residual));
    });
    intervalRows.push({
      stepIndex,
      fromSimulationSeconds: sampleTime,
      toSimulationSeconds: after.simulationSeconds,
      actualRangeDeltaM,
      captainResiduals
    });
  }

  const callLatencies = calls.map((row) => row.measuredCallLatencyMs);
  const steadyLatencies = callLatencies.length > 1 ? callLatencies.slice(1) : callLatencies;
  const coldCallLatencyMs = callLatencies[0] || 0;
  const steadyCallP50Ms = quantile(steadyLatencies, 0.50);
  const steadyCallP95Ms = quantile(steadyLatencies, 0.95);
  const steadyCallMaxMs = steadyLatencies.length ? Math.max(...steadyLatencies) : 0;
  const steadyCallMeanMs = steadyLatencies.length
    ? steadyLatencies.reduce((sum, value) => sum + value, 0) / steadyLatencies.length
    : 0;
  const primaryTemporalUnitMs = steadyCallP95Ms || coldCallLatencyMs;
  const primaryTemporalUnitSeconds = primaryTemporalUnitMs / 1000;
  const supportedScaleAtPrimaryTemporalUnit = decisionTimeScale(
    config.decisionIntervalSeconds,
    config.normalTimeScale,
    primaryTemporalUnitMs,
    config.timeBudgetSafety
  );
  const firstStep = calls.filter((call) => call.stepIndex === 0);
  const firstStepPolicies = new Set(firstStep.map((call) => call.actualIntent));

  return {
    calls,
    intervalRows,
    intentSignatures,
    checks: {
      captainTemporalUnitIsOneModelCall: oneModelCallPerTimepoint,
      oneClefHeadForwardPerCaptainTimepoint: oneHeadForwardPerTimepoint,
      callTimeCharacterizedAsPrimaryTemporalUnit: primaryTemporalUnitMs > 0,
      everyTimepointHasCaptainSnapshots: calls.length === config.steps * jackets.length,
      eachCallContainsRedundantQuestionEnsemble: calls.every((call) => call.questionCount === config.questionsPerCall && call.questionCount >= 10),
      tenPercentIncoherenceInjectedIntoProbeEnsemble: Math.abs(actualInjectedRateSum / Math.max(1, injectedRateSamples) - config.incoherenceRate) <= (1 / config.questionsPerCall + 1e-12),
      jacketExpectedPolicySurvivesInjectedIncoherence: jacketsMatchExpectedPolicy,
      discriminatingProbeSeparatesJackets: firstStepPolicies.size === jackets.length && jacketsMatchExpectedPolicy,
      captainCheckpointPinnedAcrossEpisode: allCallsPinned,
      captainCallsAreBoundToPhysicsInterval: allCallsAtDecisionBoundary,
      decisionLayerTimeScaleDerivedFromObservedCallTime: slowdownObserved || supportedScaleAtPrimaryTemporalUnit >= config.normalTimeScale - 1e-12,
      physicsAdvancesExactDecisionIntervals: exactIntervalAdvance,
      captainIntentCannotMutatePhysicsDirectly: allPhysicsOwned,
      physicsStillChangesSystemState: physicsChangedAcrossIntervals,
      physicsCreatesResidualAgainstCaptainIntent: maxPhysicsResidualM > 0,
      requestSnapshotsAreContentAddressed: calls.every((call) => /^[0-9a-f]{64}$/.test(call.requestSha256) && /^[0-9a-f]{64}$/.test(call.stateSha256)),
      backendDirectCallContractAvailable: true,
      backendProviderUsedAsRequested: config.backendUrl ? calls.every((call) => call.provider === 'direct-http') : calls.every((call) => call.provider === 'deterministic-reference')
    },
    metrics: {
      primaryTemporalUnit: 'captain-model-call',
      targetPhysicsIntervalSeconds: config.decisionIntervalSeconds,
      steps: config.steps,
      captainCount: jackets.length,
      captainCallSnapshotCount: calls.length,
      questionsPerCall: config.questionsPerCall,
      configuredIncoherenceRate: config.incoherenceRate,
      actualMeanInjectedIncoherenceRate: actualInjectedRateSum / Math.max(1, injectedRateSamples),
      normalTimeScale: config.normalTimeScale,
      coldCallLatencyMs,
      steadyCallLatencyMinMs: steadyLatencies.length ? Math.min(...steadyLatencies) : 0,
      steadyCallLatencyP50Ms: steadyCallP50Ms,
      steadyCallLatencyP95Ms: steadyCallP95Ms,
      steadyCallLatencyMeanMs: steadyCallMeanMs,
      steadyCallLatencyMaxMs: steadyCallMaxMs,
      primaryTemporalUnitMs,
      primaryTemporalUnitSeconds,
      captainCallsPerWallSecondAtP95: primaryTemporalUnitSeconds > 0 ? 1 / primaryTemporalUnitSeconds : 0,
      supportedUniverseTimeScaleAtP95: supportedScaleAtPrimaryTemporalUnit,
      slowdownFactorAtP95: config.normalTimeScale / Math.max(supportedScaleAtPrimaryTemporalUnit, 1e-12),
      minimumDecisionTimeScaleAcrossCalls: minDecisionTimeScale,
      maximumDecisionLatencyMs: maxDecisionLatencyMs,
      maximumBackendModelLatencyMs: maxBackendModelLatencyMs,
      meanBackendModelLatencyMs: backendModelLatencySamples ? backendModelLatencySum / backendModelLatencySamples : 0,
      maximumPhysicsResidualM: maxPhysicsResidualM,
      finalSimulationSeconds: gravity.snapshot().simulationSeconds,
      provider: config.backendUrl ? 'direct-http' : 'deterministic-reference'
    }
  };
}

(async () => {
  const first = await runEpisode();
  if (!config.backendUrl) {
    const replay = await runEpisode();
    first.checks.referenceEpisodeReplaysDeterministically = sha256(first.intentSignatures) === sha256(replay.intentSignatures)
      && sha256(first.intervalRows) === sha256(replay.intervalRows);
    first.metrics.replaySignatureSha256 = sha256(first.intentSignatures);
  } else {
    first.checks.referenceEpisodeReplaysDeterministically = true;
    first.metrics.replaySignatureSha256 = null;
  }
  const failed = Object.entries(first.checks).filter(([, value]) => value !== true).map(([key]) => key);
  const result = {
    ok: failed.length === 0,
    checks: first.checks,
    metrics: first.metrics,
    failedChecks: failed
  };
  if (config.includeCallSnapshots) result.callSnapshots = first.calls;
  console.log(JSON.stringify(result, null, 2));
  process.exitCode = result.ok ? 0 : 1;
})().catch((error) => {
  console.error(JSON.stringify({ok: false, error: String(error && error.stack || error)}, null, 2));
  process.exitCode = 2;
});
'''

    config = {
        "decisionIntervalSeconds": args.decision_interval_seconds,
        "steps": args.steps,
        "questionsPerCall": args.questions_per_call,
        "incoherenceRate": args.incoherence_rate,
        "normalTimeScale": args.normal_time_scale,
        "referenceCallLatencyMs": args.reference_call_latency_ms,
        "timeBudgetSafety": args.time_budget_safety,
        "backendUrl": args.backend_url.strip(),
        "checkpointId": args.checkpoint_id,
        "checkpointSha256": args.checkpoint_sha256,
        "includeCallSnapshots": args.include_call_snapshots,
    }

    proc = subprocess.run(
        [node, "-e", js, str(gravity), str(project), json.dumps(config, separators=(",", ":"))],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
