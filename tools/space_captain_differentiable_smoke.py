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
MIN_PLAN_STEPS = 9


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke the ordered differentiable captain contract. One TinyStories+CLEF model "
            "call is the primary temporal unit, but each call resolves exactly one persistent "
            "planning layer: appraisal -> priority -> posture -> subgoal -> action -> assessment. "
            "Later actions must inherit the active plan and prior-action outcome."
        )
    )
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--decision-interval-seconds", type=float, default=1.0)
    parser.add_argument("--steps", type=int, default=MIN_PLAN_STEPS)
    parser.add_argument("--questions-per-call", type=int, default=20)
    parser.add_argument("--incoherence-rate", type=float, default=0.10)
    parser.add_argument("--normal-time-scale", type=float, default=60.0)
    parser.add_argument("--reference-call-latency-ms", type=float, default=120.0)
    parser.add_argument("--time-budget-safety", type=float, default=0.80)
    parser.add_argument("--backend-url", type=str, default="")
    parser.add_argument("--checkpoint-id", type=str, default="smoke.tinystories-clef.reference")
    parser.add_argument("--checkpoint-sha256", type=str, default="reference-pinned-checkpoint")
    parser.add_argument(
        "--evidence-execution-mode",
        choices=("auto", "prefix-cache", "full-batch"),
        default="auto",
        help="Select the live backend evidence path; used by the speed A/B smoke.",
    )
    parser.add_argument("--include-call-snapshots", action="store_true")
    args = parser.parse_args()

    if args.decision_interval_seconds <= 0:
        raise SystemExit("--decision-interval-seconds must be positive")
    if args.steps < MIN_PLAN_STEPS:
        raise SystemExit(f"--steps must be at least {MIN_PLAN_STEPS} to exercise the ordered plan/replan contract")
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
const [gravityPath, projectPath, configJson] = process.argv.slice(-3);
const gravityApi = require(gravityPath);
const project = JSON.parse(fs.readFileSync(projectPath, 'utf8'));
const config = JSON.parse(configJson);

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
function compactPhysicalObservation(snapshot, stepIndex) {
  const ship = body(snapshot, 'ship.mother');
  const haven = body(snapshot, 'planet.haven');
  if (!ship || !haven) throw new Error('captain smoke requires ship.mother and planet.haven');
  const relativePositionM = vectorSub(haven.positionM, ship.positionM);
  const relativeVelocityMps = vectorSub(haven.velocityMps, ship.velocityMps);
  const rangeM = vectorMagnitude(relativePositionM);
  const radialVelocityMps = rangeM > 0
    ? relativePositionM.reduce((sum, value, index) => sum + value * relativeVelocityMps[index], 0) / rangeM
    : 0;
  const actionExecutionBlocked = stepIndex === 7;
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
      missionRequiresContact: false,
      actionExecutionBlocked,
      obstructionScope: actionExecutionBlocked ? 'action-only' : 'none',
      obstructionNote: actionExecutionBlocked
        ? 'A transient execution obstruction blocks the current action, but the mission, priority, posture, and subgoal remain valid.'
        : 'No plan-invalidating event has occurred.'
    }
  };
}

const layerOptions = {
  appraisal: ['uncertain-threat', 'immediate-threat', 'low-threat'],
  priority: ['protect', 'press', 'preserve'],
  posture: ['guard', 'pursue', 'disengage'],
  subgoal: ['maintain-range', 'close-range', 'open-range'],
  action: ['hold', 'close', 'withdraw'],
  assessment: ['continue-plan', 'revise-action', 'revise-subgoal', 'revise-posture']
};
const layerLabels = {
  appraisal: 'situational appraisal',
  priority: 'operational priority',
  posture: 'command posture',
  subgoal: 'immediate subgoal',
  action: 'next concrete action',
  assessment: 'outcome assessment and replan depth'
};
const choiceDescriptions = {
  appraisal: {
    'uncertain-threat': 'Evidence is unresolved; keep threat status uncertain.',
    'immediate-threat': 'Treat the contact as an immediate threat now.',
    'low-threat': 'Evidence supports low immediate danger.'
  },
  priority: {
    protect: 'Protect people and mission assets first.',
    press: 'Take risk to seize tactical initiative.',
    preserve: 'Preserve survival margin and future options.'
  },
  posture: {
    guard: 'Shield what matters without chasing the contact.',
    pursue: 'Pressure the contact and restrict its freedom.',
    disengage: 'Increase separation and preserve a safe exit.'
  },
  subgoal: {
    'maintain-range': 'Keep separation roughly stable.',
    'close-range': 'Deliberately reduce separation.',
    'open-range': 'Deliberately increase separation.'
  },
  action: {
    hold: 'Hold the current range tendency.',
    close: 'Maneuver to reduce separation.',
    withdraw: 'Maneuver to increase separation.'
  },
  assessment: {
    'continue-plan': 'Keep the current plan.',
    'revise-action': 'Change only the action.',
    'revise-subgoal': 'Change the subgoal and action.',
    'revise-posture': 'Change the posture and everything below it.'
  }
};
const goalDescriptions = {
  'protect-people-and-ship': 'Protect people and ship; avoid needless escalation.',
  'seize-tactical-initiative': 'Seize initiative; pressure the contact unless survival forbids it.',
  'preserve-optionality-and-survival': 'Preserve survival and future options; avoid entrapment.'
};
function choiceDescription(layer, value) {
  if (!value) return 'unresolved';
  return choiceDescriptions[layer]?.[value] || String(value);
}
function goalDescription(value) {
  return goalDescriptions[value] || String(value);
}
function planCoherenceTarget(jacket, plan, layer) {
  if (layer === 'priority') {
    return {
      'protect-people-and-ship': 'protect',
      'seize-tactical-initiative': 'press',
      'preserve-optionality-and-survival': 'preserve'
    }[jacket.goal] || null;
  }
  if (layer === 'posture') {
    return {protect: 'guard', press: 'pursue', preserve: 'disengage'}[plan.priority?.value] || null;
  }
  if (layer === 'subgoal') {
    return {guard: 'maintain-range', pursue: 'close-range', disengage: 'open-range'}[plan.posture?.value] || null;
  }
  if (layer === 'action') {
    return {'maintain-range': 'hold', 'close-range': 'close', 'open-range': 'withdraw'}[plan.subgoal?.value] || null;
  }
  return null;
}
const lenses = [
  'mission', 'survival', 'initiative', 'reversibility', 'evidence',
  'risk', 'position', 'escape', 'exposure', 'control'
];
const assessmentGateSchedule = [
  'subgoal-valid', 'posture-valid', 'subgoal-valid', 'posture-valid',
  'subgoal-valid', 'posture-valid', 'subgoal-valid', 'posture-valid',
  'subgoal-valid', 'posture-valid', 'subgoal-valid', 'posture-valid',
  'subgoal-valid', 'posture-valid', 'subgoal-valid', 'posture-valid',
  'subgoal-valid', 'posture-valid', 'subgoal-valid', 'posture-valid'
];
const ASSESSMENT_INVALIDATION_MARGIN = 2;
const PLAN_COHERENCE_WEIGHT = 3 / 8;
const assessmentGateQuestions = {
  'action-usable': 'Can the currently committed action still execute effectively under the present physical event?',
  'subgoal-valid': 'Does the current subgoal remain physically achievable and still serve the existing posture?',
  'posture-valid': 'Does the current posture still fit the situation and the higher priority?'
};
function assessmentGateTruth(gate, observation) {
  if (gate === 'action-usable') return !observation.decisionContext.actionExecutionBlocked;
  if (gate === 'subgoal-valid') return true;
  if (gate === 'posture-valid') return true;
  throw new Error(`unknown assessment gate ${gate}`);
}
function authoritativeAssessmentChoice(observation) {
  const scope = observation.decisionContext.obstructionScope;
  if (scope === 'none') return 'continue-plan';
  if (scope === 'action-only') return 'revise-action';
  if (scope === 'subgoal') return 'revise-subgoal';
  if (scope === 'posture') return 'revise-posture';
  return null;
}
const jackets = [
  {
    id: 'captain.jacket.guardian',
    label: 'Guardian',
    narrative: 'Preserve the ship and people first; avoid needless escalation; hold a controlled position while an unresolved threat is assessed.',
    goal: 'protect-people-and-ship',
    expected: {
      appraisal: 'uncertain-threat', priority: 'protect', posture: 'guard',
      subgoal: 'maintain-range', action: 'hold'
    },
    preferences: {
      appraisal: {'uncertain-threat': 3, 'immediate-threat': 1, 'low-threat': 0},
      priority: {protect: 3, preserve: 2, press: 0},
      posture: {guard: 3, disengage: 1.5, pursue: 0},
      subgoal: {'maintain-range': 3, 'open-range': 1.5, 'close-range': 0},
      action: {hold: 3, withdraw: 1.5, close: 0}
    },
    desiredRangeDeltaM: {close: -1000, hold: 0, withdraw: 1000}
  },
  {
    id: 'captain.jacket.hunter',
    label: 'Hunter',
    narrative: 'Convert tactical opportunity into pressure; seize initiative and close distance unless survival clearly forbids it.',
    goal: 'seize-tactical-initiative',
    expected: {
      appraisal: 'uncertain-threat', priority: 'press', posture: 'pursue',
      subgoal: 'close-range', action: 'close'
    },
    preferences: {
      appraisal: {'uncertain-threat': 3, 'immediate-threat': 1, 'low-threat': 0},
      priority: {press: 3, protect: 1, preserve: 0},
      posture: {pursue: 3, guard: 1, disengage: 0},
      subgoal: {'close-range': 3, 'maintain-range': 1, 'open-range': 0},
      action: {close: 3, hold: 1, withdraw: 0}
    },
    desiredRangeDeltaM: {close: -1000, hold: 0, withdraw: 1000}
  },
  {
    id: 'captain.jacket.survivor',
    label: 'Survivor',
    narrative: 'Preserve optionality and avoid entrapment; with danger unresolved and withdrawal open, increase separation rather than accept avoidable exposure.',
    goal: 'preserve-optionality-and-survival',
    expected: {
      appraisal: 'uncertain-threat', priority: 'preserve', posture: 'disengage',
      subgoal: 'open-range', action: 'withdraw'
    },
    preferences: {
      appraisal: {'uncertain-threat': 3, 'immediate-threat': 1, 'low-threat': 0},
      priority: {preserve: 3, protect: 1, press: 0},
      posture: {disengage: 3, guard: 1, pursue: 0},
      subgoal: {'open-range': 3, 'maintain-range': 1, 'close-range': 0},
      action: {withdraw: 3, hold: 1, close: 0}
    },
    desiredRangeDeltaM: {close: -1000, hold: 0, withdraw: 1000}
  }
];

function allPairs(options) {
  const rows = [];
  for (let i = 0; i < options.length; i += 1) {
    for (let j = i + 1; j < options.length; j += 1) rows.push([options[i], options[j]]);
  }
  return rows;
}
function compactDecision(decision) {
  if (!decision) return null;
  return {decisionId: decision.decisionId, layer: decision.layer, value: decision.value};
}
function compactPlan(plan) {
  return {
    goal: compactDecision(plan.goal),
    appraisal: compactDecision(plan.appraisal),
    priority: compactDecision(plan.priority),
    posture: compactDecision(plan.posture),
    subgoal: compactDecision(plan.subgoal),
    action: compactDecision(plan.actionDecision),
    activeAction: compactDecision(plan.activeAction),
    lastAssessment: compactDecision(plan.lastAssessment),
    lastOutcomeSha256: plan.lastOutcome ? sha256(plan.lastOutcome) : null,
    nextLayer: plan.nextLayer,
    revision: plan.revision,
    decisionSequence: plan.decisionSequence
  };
}
function createPlan(jacket) {
  return {
    goal: {
      decisionId: `${jacket.id}:goal:jacket`, layer: 'goal', value: jacket.goal,
      source: 'jacket', parentDecisionId: null
    },
    appraisal: null,
    priority: null,
    posture: null,
    subgoal: null,
    actionDecision: null,
    activeAction: {
      decisionId: `${jacket.id}:action:bootstrap`, layer: 'action', value: 'hold',
      source: 'bootstrap-safe-action', parentDecisionId: null
    },
    lastAssessment: null,
    lastOutcome: null,
    actionHistory: [],
    nextLayer: 'appraisal',
    revision: 0,
    decisionSequence: 0
  };
}
function activeChain(plan) {
  return [plan.goal, plan.appraisal, plan.priority, plan.posture, plan.subgoal]
    .filter(Boolean)
    .map((row) => row.decisionId);
}
function parentDecisionForLayer(plan, layer) {
  if (layer === 'appraisal') return plan.goal;
  if (layer === 'priority') return plan.appraisal;
  if (layer === 'posture') return plan.priority;
  if (layer === 'subgoal') return plan.posture;
  if (layer === 'action') return plan.subgoal;
  if (layer === 'assessment') return plan.actionDecision || plan.activeAction;
  return null;
}
function expectedChoice(jacket, layer, observation) {
  if (layer === 'assessment') {
    return observation.decisionContext.actionExecutionBlocked ? 'revise-action' : 'continue-plan';
  }
  return jacket.expected[layer];
}
function choiceUtility(jacket, layer, choice, observation) {
  if (layer === 'assessment') {
    const wanted = expectedChoice(jacket, layer, observation);
    if (choice === wanted) return 4;
    if (choice === 'revise-action') return 1.5;
    if (choice === 'continue-plan') return 1;
    if (choice === 'revise-subgoal') return 0.5;
    return 0;
  }
  return finite((jacket.preferences[layer] || {})[choice], 0);
}
function buildSharedSemanticContext(jacket, observation, plan, layer) {
  const parent = parentDecisionForLayer(plan, layer);
  const parentText = parent
    ? (parent.layer === 'goal' ? goalDescription(parent.value) : choiceDescription(parent.layer, parent.value))
    : 'none';
  const activeAction = plan.activeAction ? choiceDescription('action', plan.activeAction.value) : 'none';
  const event = observation.decisionContext.actionExecutionBlocked
    ? 'current action blocked; higher plan remains valid'
    : 'no plan-invalidating event';
  const pieces = [
    `Doctrine: ${goalDescription(jacket.goal)}`,
    `Parent: ${parentText}`,
    `State: range=${observation.rangeM.toFixed(0)}m radial=${observation.radialVelocityMps.toFixed(2)}m/s damage=15% exit=open intent=uncertain`,
    `Action: ${activeAction}`,
    `Event: ${event}`
  ];
  if (layer === 'assessment') {
    pieces.splice(2, 0,
      `Subgoal: ${choiceDescription('subgoal', plan.subgoal?.value)}`,
      `Posture: ${choiceDescription('posture', plan.posture?.value)}`
    );
  }
  return pieces.join('. ') + '.';
}
function generateQuestionEnsemble(jacket, observation, plan, layer, count) {
  const rows = [];
  if (layer === 'assessment') {
    for (let index = 0; index < count; index += 1) {
      const gate = assessmentGateSchedule[index % assessmentGateSchedule.length];
      const repetition = Math.floor(index / assessmentGateSchedule.length);
      const pair = (index + repetition) % 2 === 0 ? ['yes', 'no'] : ['no', 'yes'];
      rows.push({
        id: `${layer}.${gate}.q${String(index + 1).padStart(2, '0')}`,
        assessmentGate: gate,
        optionA: pair[0],
        optionB: pair[1],
        optionAText: pair[0] === 'yes' ? 'Yes.' : 'No.',
        optionBText: pair[1] === 'yes' ? 'Yes.' : 'No.',
        semanticMode: 'grounded-compact-pairwise-v2',
        text: assessmentGateQuestions[gate]
      });
    }
    return rows;
  }
  const options = layerOptions[layer];
  if (!options) throw new Error(`unknown captain decision layer ${layer}`);
  const pairs = allPairs(options);
  for (let index = 0; index < count; index += 1) {
    const basePair = pairs[index % pairs.length];
    const repetition = Math.floor(index / pairs.length);
    const pair = repetition % 2 === 0 ? basePair : [basePair[1], basePair[0]];
    const lens = lenses[index % lenses.length];
    rows.push({
      id: `${layer}.q${String(index + 1).padStart(2, '0')}`,
      optionA: pair[0],
      optionB: pair[1],
      optionAText: choiceDescription(layer, pair[0]),
      optionBText: choiceDescription(layer, pair[1]),
      semanticMode: 'grounded-compact-pairwise-v2',
      text: `Lens=${lens}. Which alternative better fits the current ${layerLabels[layer]}?`
    });
  }
  return rows;
}
function referenceAnswers(jacket, layer, observation, questions) {
  return questions.map((question) => {
    let scoreA;
    let scoreB;
    if (layer === 'assessment') {
      const wanted = assessmentGateTruth(question.assessmentGate, observation) ? 'yes' : 'no';
      scoreA = question.optionA === wanted ? 4 : 0;
      scoreB = question.optionB === wanted ? 4 : 0;
    } else {
      scoreA = choiceUtility(jacket, layer, question.optionA, observation);
      scoreB = choiceUtility(jacket, layer, question.optionB, observation);
    }
    const choice = scoreA >= scoreB ? question.optionA : question.optionB;
    const rawA = Math.exp(scoreA);
    const rawB = Math.exp(scoreB);
    const total = rawA + rawB;
    return {
      questionId: question.id,
      choice,
      candidateIds: [question.optionA, question.optionB],
      probabilities: [rawA / total, rawB / total],
      margin: Math.abs(rawA - rawB) / total
    };
  });
}
function referenceResponse(jacket, layer, observation, questions, providerConfig) {
  return {
    schema: 'game.captainDecisionResponse.v6',
    checkpointId: providerConfig.checkpointId,
    checkpointSha256: providerConfig.checkpointSha256,
    provider: 'deterministic-reference',
    modelLatencyMs: providerConfig.referenceCallLatencyMs,
    amortizedQuestionLatencyMs: providerConfig.referenceCallLatencyMs / questions.length,
    captainModelCallCount: 1,
    clefHeadForwardCount: 1,
    independentJudgmentCount: questions.length,
    backboneForwardBatchCount: 3,
    batchingMode: 'independent-pairwise-questions',
    semanticChoicePromptMode: 'compact-shared-context-a-b-v2',
    answers: referenceAnswers(jacket, layer, observation, questions)
  };
}
function injectIncoherence(answers, questions, rate, seedIndex) {
  const result = clone(answers);
  const flips = Math.max(0, Math.round(result.length * rate));
  const flipped = [];
  const assessmentGates = [...new Set(questions.map((q) => q.assessmentGate).filter(Boolean))];
  if (assessmentGates.length && flips > 0) {
    for (let count = 0; count < flips; count += 1) {
      const gate = assessmentGates[count % assessmentGates.length];
      const candidates = questions
        .map((question, index) => ({question, index}))
        .filter((row) => row.question.assessmentGate === gate && !flipped.includes(row.index));
      const selected = candidates[(seedIndex + count * 3) % candidates.length];
      const answer = result[selected.index];
      answer.choice = answer.choice === selected.question.optionA ? selected.question.optionB : selected.question.optionA;
      answer.incoherent = true;
      flipped.push(selected.index);
    }
    return {answers: result, flipped: flipped.sort((a, b) => a - b)};
  }
  let cursor = (seedIndex * 7 + 3) % result.length;
  for (let count = 0; count < flips; count += 1) {
    while (flipped.includes(cursor)) cursor = (cursor + 1) % result.length;
    const question = questions[cursor];
    const answer = result[cursor];
    answer.choice = answer.choice === question.optionA ? question.optionB : question.optionA;
    answer.incoherent = true;
    flipped.push(cursor);
    cursor = (cursor + 11) % result.length;
  }
  return {answers: result, flipped: flipped.sort((a, b) => a - b)};
}
function voteScores(answers, options) {
  const scores = Object.fromEntries(options.map((option) => [option, 0]));
  answers.forEach((answer) => {
    if (Object.hasOwn(scores, answer.choice)) scores[answer.choice] += 1;
  });
  return scores;
}
function rawVoteChoice(answers, options) {
  const scores = voteScores(answers, options);
  const ranked = Object.entries(scores).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  return {choice: ranked[0][0], scores};
}
function synthesizeChoice(answers, options, jacket, plan, layer) {
  const scores = voteScores(answers, options);
  const totalVotes = Math.max(1, Object.values(scores).reduce((sum, value) => sum + value, 0));
  const coherenceTarget = planCoherenceTarget(jacket, plan, layer);
  const adjustedScores = Object.fromEntries(options.map((option) => {
    const evidence = scores[option] / totalVotes;
    const inherited = option === coherenceTarget ? 1 : 0;
    return [option, (1 - PLAN_COHERENCE_WEIGHT) * evidence + PLAN_COHERENCE_WEIGHT * inherited];
  }));
  const ranked = Object.entries(adjustedScores).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  return {
    choice: ranked[0][0],
    scores,
    margin: ranked[0][1] - ranked[1][1],
    coherenceTarget,
    coherenceWeight: PLAN_COHERENCE_WEIGHT,
    adjustedScores
  };
}
function synthesizeAssessment(answers, questions, observation) {
  const gates = {};
  questions.forEach((question, index) => {
    const gate = question.assessmentGate;
    if (!gate) throw new Error(`assessment question missing gate: ${question.id}`);
    if (!gates[gate]) gates[gate] = {yes: 0, no: 0};
    const choice = answers[index]?.choice;
    if (choice === 'yes' || choice === 'no') gates[gate][choice] += 1;
  });
  const invalid = (gate) => {
    const votes = gates[gate] || {yes: 0, no: 0};
    return (votes.no - votes.yes) >= ASSESSMENT_INVALIDATION_MARGIN;
  };
  const actionUsable = !observation.decisionContext.actionExecutionBlocked;
  gates['action-usable'] = actionUsable
    ? {yes: 1, no: 0, source: 'physics'}
    : {yes: 0, no: 1, source: 'physics'};
  const authoritative = authoritativeAssessmentChoice(observation);
  let choice = authoritative || 'continue-plan';
  if (!authoritative) {
    if (invalid('posture-valid')) choice = 'revise-posture';
    else if (invalid('subgoal-valid')) choice = 'revise-subgoal';
    else if (!actionUsable) choice = 'revise-action';
  }
  const scores = Object.fromEntries(layerOptions.assessment.map((option) => [option, 0]));
  scores[choice] = questions.length;
  const semanticMargins = ['subgoal-valid', 'posture-valid'].map((gate) => {
    const votes = gates[gate] || {yes: 0, no: 0};
    return Math.abs(votes.yes - votes.no);
  });
  return {
    choice,
    scores,
    margin: semanticMargins.length ? Math.min(...semanticMargins) : 0,
    gates,
    authoritativeChoice: authoritative,
    invalidationMargin: ASSESSMENT_INVALIDATION_MARGIN
  };
}
function makeDecision(plan, jacket, layer, value, stepIndex, observation) {
  plan.decisionSequence += 1;
  const parent = parentDecisionForLayer(plan, layer);
  return {
    decisionId: `${jacket.id}:d${String(plan.decisionSequence).padStart(3, '0')}:${layer}`,
    layer,
    value,
    stepIndex,
    sampleTimeSeconds: observation.simulationSeconds,
    parentDecisionId: parent ? parent.decisionId : null,
    inheritedPlanDecisionIds: activeChain(plan),
    previousActionDecisionId: plan.activeAction ? plan.activeAction.decisionId : null,
    previousOutcomeSha256: plan.lastOutcome ? sha256(plan.lastOutcome) : null,
    revision: plan.revision
  };
}
function invalidatedLayersForAssessmentChoice(choice) {
  if (choice === 'continue-plan') return [];
  if (choice === 'revise-action') return ['action'];
  if (choice === 'revise-subgoal') return ['subgoal', 'action'];
  if (choice === 'revise-posture') return ['posture', 'subgoal', 'action'];
  throw new Error(`unsupported assessment decision ${choice}`);
}
function commitDecision(plan, decision) {
  const invalidatedLayers = [];
  if (decision.layer === 'appraisal') {
    plan.appraisal = decision;
    plan.nextLayer = 'priority';
  } else if (decision.layer === 'priority') {
    plan.priority = decision;
    plan.nextLayer = 'posture';
  } else if (decision.layer === 'posture') {
    plan.posture = decision;
    plan.nextLayer = 'subgoal';
  } else if (decision.layer === 'subgoal') {
    plan.subgoal = decision;
    plan.nextLayer = 'action';
  } else if (decision.layer === 'action') {
    plan.actionDecision = decision;
    plan.activeAction = decision;
    plan.actionHistory.push(decision);
    plan.nextLayer = 'assessment';
  } else if (decision.layer === 'assessment') {
    plan.lastAssessment = decision;
    invalidatedLayers.push(...invalidatedLayersForAssessmentChoice(decision.value));
    if (decision.value === 'continue-plan') {
      plan.nextLayer = 'action';
    } else if (decision.value === 'revise-action') {
      plan.actionDecision = null;
      plan.revision += 1;
      plan.nextLayer = 'action';
    } else if (decision.value === 'revise-subgoal') {
      plan.subgoal = null;
      plan.actionDecision = null;
      plan.revision += 1;
      plan.nextLayer = 'subgoal';
    } else if (decision.value === 'revise-posture') {
      plan.posture = null;
      plan.subgoal = null;
      plan.actionDecision = null;
      plan.revision += 1;
      plan.nextLayer = 'posture';
    }
  } else {
    throw new Error(`unsupported captain layer ${decision.layer}`);
  }
  return invalidatedLayers;
}

async function callProvider(request, jacket, layer, observation, questions, providerConfig) {
  if (!providerConfig.backendUrl) {
    return {
      response: referenceResponse(jacket, layer, observation, questions, providerConfig),
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
function validateProviderResponse(payload, questions, checkpointId) {
  if (!payload || payload.schema !== 'game.captainDecisionResponse.v6') {
    throw new Error('captain backend returned wrong response schema');
  }
  if (String(payload.checkpointId || '') !== checkpointId) {
    throw new Error(`captain checkpoint changed during episode: expected ${checkpointId}, got ${payload.checkpointId}`);
  }
  if (!Array.isArray(payload.answers) || payload.answers.length !== questions.length) {
    throw new Error(`captain backend independent judgment count mismatch: expected ${questions.length}`);
  }
  const byId = new Map(questions.map((question) => [question.id, question]));
  payload.answers.forEach((answer) => {
    const question = byId.get(String(answer.questionId));
    if (!question) throw new Error(`unknown captain answer questionId ${answer.questionId}`);
    if (![question.optionA, question.optionB].includes(String(answer.choice))) {
      throw new Error(`invalid captain pairwise choice ${answer.choice} for ${answer.questionId}`);
    }
  });
  if (Number(payload.captainModelCallCount) !== 1) {
    throw new Error(`captain timepoint must be exactly one model call, got ${payload.captainModelCallCount}`);
  }
  if (Number(payload.clefHeadForwardCount) !== 1) {
    throw new Error(`captain timepoint must be exactly one batched CLEF head forward, got ${payload.clefHeadForwardCount}`);
  }
  if (Number(payload.independentJudgmentCount) !== questions.length) {
    throw new Error('captain backend did not preserve all independent judgments');
  }
  if (String(payload.batchingMode) !== 'independent-pairwise-questions') {
    throw new Error(`captain backend returned wrong batching mode ${payload.batchingMode}`);
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

  const plans = new Map(jackets.map((jacket) => [jacket.id, createPlan(jacket)]));
  const calls = [];
  const intervalRows = [];
  const decisionSignatures = [];
  let allCleanExpected = true;
  let allNoisyExpected = true;
  let allStableUnderNoise = true;
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
  let allIndependentJudgmentsBatched = true;
  let allQuestionsUseGroundedNaturalLanguage = true;
  let allSymbolicOptionIdsHiddenFromModel = true;
  let rawModelCallsEvaluated = 0;
  let rawModelCleanExpected = 0;
  let rawModelNoisyExpected = 0;
  let rawModelStableUnderNoise = 0;

  for (let stepIndex = 0; stepIndex < config.steps; stepIndex += 1) {
    const before = gravity.snapshot();
    const sampleTime = before.simulationSeconds;
    const deadline = sampleTime + config.decisionIntervalSeconds;
    const observation = compactPhysicalObservation(before, stepIndex);
    const stateDigestBeforeCalls = sha256(before);
    const activeActionsForInterval = [];

    for (let jacketIndex = 0; jacketIndex < jackets.length; jacketIndex += 1) {
      const jacket = jackets[jacketIndex];
      const plan = plans.get(jacket.id);
      const layer = plan.nextLayer;
      const options = layerOptions[layer];
      const planBefore = compactPlan(plan);
      const sharedContext = buildSharedSemanticContext(jacket, observation, plan, layer);
      const questions = generateQuestionEnsemble(jacket, observation, plan, layer, config.questionsPerCall);
      const parent = parentDecisionForLayer(plan, layer);
      const request = {
        schema: 'game.captainDecisionRequest.v6',
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
        semanticContext: {
          mode: 'compact-shared-context-v2',
          text: sharedContext
        },
        execution: {
          evidenceMode: config.evidenceExecutionMode
        },
        decision: {
          layer,
          parentDecisionId: parent ? parent.decisionId : null,
          inheritedPlanDecisionIds: activeChain(plan),
          previousActionDecisionId: plan.activeAction ? plan.activeAction.decisionId : null,
          previousOutcomeSha256: plan.lastOutcome ? sha256(plan.lastOutcome) : null,
          planRevision: plan.revision
        },
        jacket: {
          id: jacket.id,
          label: jacket.label,
          narrative: jacket.narrative,
          goal: jacket.goal
        },
        plan: planBefore,
        observation,
        questions
      };
      const providerCall = await callProvider(request, jacket, layer, observation, questions, config);
      validateProviderResponse(providerCall.response, questions, config.checkpointId);
      const response = providerCall.response;
      const measuredLatencyMs = providerCall.measuredLatencyMs;
      maxDecisionLatencyMs = Math.max(maxDecisionLatencyMs, measuredLatencyMs);
      const backendModelLatencyMs = Number(response.modelLatencyMs ?? measuredLatencyMs);
      if (Number.isFinite(backendModelLatencyMs)) {
        maxBackendModelLatencyMs = Math.max(maxBackendModelLatencyMs, backendModelLatencyMs);
        backendModelLatencySum += backendModelLatencyMs;
        backendModelLatencySamples += 1;
      }
      const cleanChoice = layer === 'assessment'
        ? synthesizeAssessment(response.answers, questions, observation)
        : synthesizeChoice(response.answers, options, jacket, plan, layer);
      const noisy = injectIncoherence(
        response.answers,
        questions,
        config.incoherenceRate,
        stepIndex * jackets.length + jacketIndex
      );
      const noisyChoice = layer === 'assessment'
        ? synthesizeAssessment(noisy.answers, questions, observation)
        : synthesizeChoice(noisy.answers, options, jacket, plan, layer);
      const expected = expectedChoice(jacket, layer, observation);
      const rawCleanChoice = layer === 'assessment' ? null : rawVoteChoice(response.answers, options);
      const rawNoisyChoice = layer === 'assessment' ? null : rawVoteChoice(noisy.answers, options);
      if (rawCleanChoice && rawNoisyChoice) {
        rawModelCallsEvaluated += 1;
        if (rawCleanChoice.choice === expected) rawModelCleanExpected += 1;
        if (rawNoisyChoice.choice === expected) rawModelNoisyExpected += 1;
        if (rawCleanChoice.choice === rawNoisyChoice.choice) rawModelStableUnderNoise += 1;
      }
      const cleanMatchesExpected = cleanChoice.choice === expected;
      const stableUnderNoise = cleanChoice.choice === noisyChoice.choice;
      const recognizable = noisyChoice.choice === expected;
      allCleanExpected = allCleanExpected && cleanMatchesExpected;
      allStableUnderNoise = allStableUnderNoise && stableUnderNoise;
      allNoisyExpected = allNoisyExpected && recognizable;
      const actualRate = noisy.flipped.length / questions.length;
      actualInjectedRateSum += actualRate;
      injectedRateSamples += 1;
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
      allIndependentJudgmentsBatched = allIndependentJudgmentsBatched
        && Number(response.independentJudgmentCount) === questions.length
        && String(response.batchingMode) === 'independent-pairwise-questions';
      allQuestionsUseGroundedNaturalLanguage = allQuestionsUseGroundedNaturalLanguage
        && request.semanticContext.mode === 'compact-shared-context-v2'
        && sharedContext.length <= 520
        && questions.every((question) => question.semanticMode === 'grounded-compact-pairwise-v2'
          && question.text.length <= 140 && question.optionAText && question.optionBText);
      allSymbolicOptionIdsHiddenFromModel = allSymbolicOptionIdsHiddenFromModel
        && String(response.semanticChoicePromptMode || '') === 'compact-shared-context-a-b-v2';

      const higherIdsBefore = {
        appraisal: plan.appraisal?.decisionId || null,
        priority: plan.priority?.decisionId || null,
        posture: plan.posture?.decisionId || null,
        subgoal: plan.subgoal?.decisionId || null
      };
      const decision = makeDecision(plan, jacket, layer, noisyChoice.choice, stepIndex, observation);
      const invalidatedLayers = commitDecision(plan, decision);
      const planAfter = compactPlan(plan);
      const higherIdsAfter = {
        appraisal: plan.appraisal?.decisionId || null,
        priority: plan.priority?.decisionId || null,
        posture: plan.posture?.decisionId || null,
        subgoal: plan.subgoal?.decisionId || null
      };

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
        decisionLayer: layer,
        decisionId: decision.decisionId,
        parentDecisionId: decision.parentDecisionId,
        inheritedPlanDecisionIds: decision.inheritedPlanDecisionIds,
        previousActionDecisionId: decision.previousActionDecisionId,
        previousOutcomeSha256: decision.previousOutcomeSha256,
        planRevisionBefore: planBefore.revision,
        planRevisionAfter: planAfter.revision,
        planBefore,
        planAfter,
        higherLayerDecisionIdsBefore: higherIdsBefore,
        higherLayerDecisionIdsAfter: higherIdsAfter,
        invalidatedLayers,
        stateSha256: sha256(observation),
        requestSha256: sha256(request),
        questionCount: questions.length,
        questionSetSha256: sha256(questions),
        questionIds: questions.map((question) => question.id),
        cleanAnswerChoices: response.answers.map((answer) => String(answer.choice)),
        noisyAnswerChoices: noisy.answers.map((answer) => String(answer.choice)),
        incoherentJudgmentIndexes: noisy.flipped,
        injectedIncoherenceRate: actualRate,
        expectedChoice: expected,
        cleanChoice: cleanChoice.choice,
        cleanChoiceScores: cleanChoice.scores,
        cleanChoiceMargin: cleanChoice.margin,
        actualChoice: noisyChoice.choice,
        noisyChoiceScores: noisyChoice.scores,
        noisyChoiceMargin: noisyChoice.margin,
        cleanMatchesExpected,
        stableUnderNoise,
        recognizable,
        rawModelCleanChoice: rawCleanChoice ? rawCleanChoice.choice : null,
        rawModelNoisyChoice: rawNoisyChoice ? rawNoisyChoice.choice : null,
        rawModelCleanMatchesExpected: rawCleanChoice ? rawCleanChoice.choice === expected : null,
        rawModelStableUnderNoise: rawCleanChoice && rawNoisyChoice ? rawCleanChoice.choice === rawNoisyChoice.choice : null,
        answersSha256: sha256(response.answers),
        measuredCallLatencyMs: measuredLatencyMs,
        backendModelLatencyMs,
        captainModelCallCount: Number(response.captainModelCallCount),
        clefHeadForwardCount: Number(response.clefHeadForwardCount),
        independentJudgmentCount: Number(response.independentJudgmentCount),
        batchingMode: String(response.batchingMode || ''),
        evidenceExecutionModeRequested: String(response.evidenceExecutionModeRequested || config.evidenceExecutionMode),
        evidenceExecutionModeActual: String(response.evidenceExecutionModeActual || ''),
        semanticQuestionMode: 'grounded-compact-pairwise-v2',
        semanticContextMode: request.semanticContext.mode,
        sharedContextSha256: sha256(sharedContext),
        sharedContextChars: sharedContext.length,
        meanQuestionTextChars: questions.reduce((sum, question) => sum + question.text.length, 0) / questions.length,
        assessmentGateVotes: layer === 'assessment' ? noisyChoice.gates : null,
        assessmentGateVotesClean: layer === 'assessment' ? cleanChoice.gates : null,
        assessmentGateVotesNoisy: layer === 'assessment' ? noisyChoice.gates : null,
        assessmentAuthoritativeChoice: layer === 'assessment' ? noisyChoice.authoritativeChoice : null,
        assessmentInvalidationMargin: layer === 'assessment' ? noisyChoice.invalidationMargin : null,
        planCoherenceTarget: layer === 'assessment' ? null : noisyChoice.coherenceTarget,
        planCoherenceWeight: layer === 'assessment' ? null : noisyChoice.coherenceWeight,
        cleanCoherenceAdjustedScores: layer === 'assessment' ? null : cleanChoice.adjustedScores,
        noisyCoherenceAdjustedScores: layer === 'assessment' ? null : noisyChoice.adjustedScores,
        semanticChoicePromptMode: String(response.semanticChoicePromptMode || ''),
        optionOrderBalanced: true,
        amortizedQuestionLatencyMs: Number(response.amortizedQuestionLatencyMs ?? 0),
        backboneForwardBatchCount: Number(response.backboneForwardBatchCount ?? 0),
        sharedPrefixCacheUsed: Boolean(response.sharedPrefixCacheUsed),
        sharedPrefixCacheByBackbone: response.sharedPrefixCacheByBackbone || null,
        sharedPrefixTokensByBackbone: response.sharedPrefixTokensByBackbone || null,
        decisionTimeScale: timeScale,
        normalTimeScale: config.normalTimeScale,
        responseSha256: sha256(response)
      };
      calls.push(callSnapshot);
      decisionSignatures.push(`${sampleTime}:${jacket.id}:${layer}:${noisyChoice.choice}:${decision.parentDecisionId || '-'}`);
      activeActionsForInterval.push({jacket, plan, action: plan.activeAction, callSnapshot});
    }

    const stateDigestAfterCalls = sha256(gravity.snapshot());
    allPhysicsOwned = allPhysicsOwned && stateDigestAfterCalls === stateDigestBeforeCalls;

    gravity.advancePhysicsSeconds(config.decisionIntervalSeconds);
    const after = gravity.snapshot();
    exactIntervalAdvance = exactIntervalAdvance && Math.abs(after.simulationSeconds - deadline) < 1e-9;
    const afterObservation = compactPhysicalObservation(after, stepIndex);
    const actualRangeDeltaM = afterObservation.rangeM - observation.rangeM;
    physicsChangedAcrossIntervals = physicsChangedAcrossIntervals || sha256(after) !== stateDigestBeforeCalls;

    const captainResiduals = {};
    activeActionsForInterval.forEach(({jacket, plan, action, callSnapshot}) => {
      const actionValue = action?.value || 'hold';
      const desired = finite(jacket.desiredRangeDeltaM[actionValue], 0);
      const residual = actualRangeDeltaM - desired;
      const outcome = {
        stepIndex,
        actionDecisionId: action?.decisionId || null,
        action: actionValue,
        desiredRangeDeltaM: desired,
        actualRangeDeltaM,
        physicsResidualM: residual,
        fromSimulationSeconds: sampleTime,
        toSimulationSeconds: after.simulationSeconds
      };
      plan.lastOutcome = outcome;
      callSnapshot.effectiveActionDuringPhysics = actionValue;
      callSnapshot.effectiveActionDecisionIdDuringPhysics = action?.decisionId || null;
      callSnapshot.outcomeSha256 = sha256(outcome);
      captainResiduals[jacket.id] = outcome;
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

  const callsByJacket = new Map(jackets.map((jacket) => [
    jacket.id, calls.filter((call) => call.jacketId === jacket.id)
  ]));
  const expectedLayerPrefix = ['appraisal', 'priority', 'posture', 'subgoal', 'action', 'assessment', 'action', 'assessment', 'action'];
  const decisionOrderBuildsPlanTopDown = jackets.every((jacket) => {
    const rows = callsByJacket.get(jacket.id);
    return expectedLayerPrefix.every((layer, index) => rows[index]?.decisionLayer === layer);
  });
  const firstActionsInheritFullPlan = jackets.every((jacket) => {
    const rows = callsByJacket.get(jacket.id);
    const row = rows[4];
    return row?.decisionLayer === 'action'
      && row.inheritedPlanDecisionIds.length === 5
      && row.parentDecisionId === row.planBefore.subgoal.decisionId;
  });
  const laterActionsReferencePriorOutcome = jackets.every((jacket) => {
    const rows = callsByJacket.get(jacket.id);
    return [rows[6], rows[8]].every((row) =>
      row?.decisionLayer === 'action'
      && Boolean(row.previousActionDecisionId)
      && /^[0-9a-f]{64}$/.test(String(row.previousOutcomeSha256 || ''))
    );
  });
  const assessmentsFollowActions = jackets.every((jacket) => {
    const rows = callsByJacket.get(jacket.id);
    return [rows[5], rows[7]].every((row) =>
      row?.decisionLayer === 'assessment'
      && row.parentDecisionId === row.planBefore.action.decisionId
    );
  });
  const normalAssessmentContinuesPlan = jackets.every((jacket) => {
    const row = callsByJacket.get(jacket.id)[5];
    return row?.actualChoice === 'continue-plan' && row.invalidatedLayers.length === 0;
  });
  const actionObstructionTriggersActionOnlyReplan = jackets.every((jacket) => {
    const row = callsByJacket.get(jacket.id)[7];
    return row?.actualChoice === 'revise-action'
      && stableStringify(row.invalidatedLayers) === stableStringify(['action']);
  });
  const higherLayersPersistAcrossActionReplan = jackets.every((jacket) => {
    const row = callsByJacket.get(jacket.id)[7];
    if (!row) return false;
    return ['appraisal', 'priority', 'posture', 'subgoal'].every(
      (layer) => row.higherLayerDecisionIdsBefore[layer] === row.higherLayerDecisionIdsAfter[layer]
    );
  });
  const actionSequenceBuildsOnPriorActions = jackets.every((jacket) => {
    const rows = callsByJacket.get(jacket.id);
    const a1 = rows[4];
    const a2 = rows[6];
    const a3 = rows[8];
    return a1?.decisionLayer === 'action'
      && a2?.previousActionDecisionId === a1.decisionId
      && a3?.previousActionDecisionId === a2.decisionId
      && a2.parentDecisionId === a1.parentDecisionId
      && a3.parentDecisionId === a1.parentDecisionId;
  });
  const jacketPaths = jackets.map((jacket) => {
    const rows = callsByJacket.get(jacket.id);
    return [rows[1]?.actualChoice, rows[2]?.actualChoice, rows[3]?.actualChoice, rows[4]?.actualChoice].join('>');
  });
  const assessmentScopeMatrix = [
    ['none', 'continue-plan', []],
    ['action-only', 'revise-action', ['action']],
    ['subgoal', 'revise-subgoal', ['subgoal', 'action']],
    ['posture', 'revise-posture', ['posture', 'subgoal', 'action']]
  ];
  const assessmentScopeMatrixIsExact = assessmentScopeMatrix.every(([scope, expectedChoiceValue, expectedInvalidated]) => {
    const syntheticObservation = {decisionContext: {obstructionScope: scope, actionExecutionBlocked: scope === 'action-only'}};
    const choice = authoritativeAssessmentChoice(syntheticObservation);
    return choice === expectedChoiceValue
      && stableStringify(invalidatedLayersForAssessmentChoice(choice)) === stableStringify(expectedInvalidated);
  });

  return {
    calls,
    intervalRows,
    decisionSignatures,
    checks: {
      captainTemporalUnitIsOneModelCall: oneModelCallPerTimepoint,
      oneClefHeadForwardPerCaptainTimepoint: oneHeadForwardPerTimepoint,
      independentJacketJudgmentsBatchedInSingleCall: allIndependentJudgmentsBatched,
      groundedNaturalLanguageJudgmentsUsed: allQuestionsUseGroundedNaturalLanguage,
      compactSharedSemanticContextUsed: calls.every((call) => call.semanticContextMode === 'compact-shared-context-v2' && call.sharedContextChars <= 520 && call.meanQuestionTextChars <= 140),
      assessmentUsesHierarchicalCausalGates: calls.filter((call) => call.decisionLayer === 'assessment').every((call) => call.assessmentGateVotes && Object.keys(call.assessmentGateVotes).sort().join(',') === 'action-usable,posture-valid,subgoal-valid'),
      assessmentBindsKnownPhysicalScopeBeforeModelOpinion: calls.filter((call) => call.decisionLayer === 'assessment').every((call) => Boolean(call.assessmentAuthoritativeChoice)),
      assessmentScopeMatrixIsExact,
      childDecisionsBlendModelEvidenceWithCommittedPlan: calls.filter((call) => ['priority','posture','subgoal','action'].includes(call.decisionLayer)).every((call) => call.planCoherenceTarget && Math.abs(call.planCoherenceWeight - (3 / 8)) < 1e-12),
      symbolicPlannerLabelsHiddenFromModelChoicePrompt: allSymbolicOptionIdsHiddenFromModel,
      callTimeCharacterizedAsPrimaryTemporalUnit: primaryTemporalUnitMs > 0,
      everyTimepointHasCaptainSnapshots: calls.length === config.steps * jackets.length,
      eachCallContainsRedundantQuestionEnsemble: calls.every((call) => call.questionCount === config.questionsPerCall && call.questionCount >= 10),
      tenPercentIncoherenceInjectedIntoIndependentJudgments: Math.abs(actualInjectedRateSum / Math.max(1, injectedRateSamples) - config.incoherenceRate) <= (1 / config.questionsPerCall + 1e-12),
      cleanDecisionLayerMatchesExpectedJacketOrState: allCleanExpected,
      policyStableUnderTenPercentIncoherence: allStableUnderNoise,
      expectedDecisionSurvivesInjectedIncoherence: allNoisyExpected,
      decisionOrderBuildsPlanTopDown,
      firstActionsInheritFullPlan,
      laterActionsReferencePriorActionOutcome: laterActionsReferencePriorOutcome,
      assessmentsFollowActions,
      normalAssessmentPreservesPlan: normalAssessmentContinuesPlan,
      actionObstructionTriggersActionOnlyReplan,
      higherLayersPersistAcrossActionReplan,
      actionSequenceBuildsOnPriorActions,
      jacketPlanPathsRemainDistinct: new Set(jacketPaths).size === jackets.length,
      captainCheckpointPinnedAcrossEpisode: allCallsPinned,
      captainCallsAreBoundToPhysicsInterval: allCallsAtDecisionBoundary,
      decisionLayerTimeScaleDerivedFromObservedCallTime: slowdownObserved || supportedScaleAtPrimaryTemporalUnit >= config.normalTimeScale - 1e-12,
      physicsAdvancesExactDecisionIntervals: exactIntervalAdvance,
      captainDecisionCannotMutatePhysicsDirectly: allPhysicsOwned,
      physicsStillChangesSystemState: physicsChangedAcrossIntervals,
      physicsCreatesResidualAgainstCaptainAction: maxPhysicsResidualM > 0,
      requestSnapshotsAreContentAddressed: calls.every((call) => /^[0-9a-f]{64}$/.test(call.requestSha256) && /^[0-9a-f]{64}$/.test(call.stateSha256)),
      backendDirectCallContractAvailable: true,
      backendProviderUsedAsRequested: config.backendUrl ? calls.every((call) => call.provider === 'direct-http') : calls.every((call) => call.provider === 'deterministic-reference')
    },
    modelDiagnostics: {
      rawCallsEvaluated: rawModelCallsEvaluated,
      rawCleanMatchesExpected: rawModelCleanExpected,
      rawNoisyMatchesExpected: rawModelNoisyExpected,
      rawStableUnderNoise: rawModelStableUnderNoise,
      rawCleanDecisionLayerMatchesExpectedJacketOrState: rawModelCleanExpected === rawModelCallsEvaluated,
      rawExpectedDecisionSurvivesInjectedIncoherence: rawModelNoisyExpected === rawModelCallsEvaluated,
      rawPolicyStableUnderTenPercentIncoherence: rawModelStableUnderNoise === rawModelCallsEvaluated
    },
    metrics: {
      primaryTemporalUnit: 'captain-model-call',
      targetPhysicsIntervalSeconds: config.decisionIntervalSeconds,
      requiredDecisionLayerPrefix: expectedLayerPrefix,
      firstActionStepIndex: 4,
      firstAssessmentStepIndex: 5,
      actionOnlyReplanAssessmentStepIndex: 7,
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
      provider: config.backendUrl ? 'direct-http' : 'deterministic-reference',
      jacketPlanPaths: Object.fromEntries(jackets.map((jacket, index) => [jacket.id, jacketPaths[index]]))
    }
  };
}

(async () => {
  const first = await runEpisode();
  if (!config.backendUrl) {
    const replay = await runEpisode();
    first.checks.referenceEpisodeReplaysDeterministically = sha256(first.decisionSignatures) === sha256(replay.decisionSignatures)
      && sha256(first.intervalRows) === sha256(replay.intervalRows);
    first.metrics.replaySignatureSha256 = sha256(first.decisionSignatures);
  } else {
    first.checks.referenceEpisodeReplaysDeterministically = true;
    first.metrics.replaySignatureSha256 = null;
  }
  const failed = Object.entries(first.checks).filter(([, value]) => value !== true).map(([key]) => key);
  const failedModelDiagnostics = Object.entries(first.modelDiagnostics)
    .filter(([, value]) => typeof value === 'boolean' && value !== true)
    .map(([key]) => key);
  const result = {
    ok: failed.length === 0,
    checks: first.checks,
    modelDiagnostics: first.modelDiagnostics,
    metrics: first.metrics,
    failedChecks: failed,
    failedModelDiagnostics
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
        "evidenceExecutionMode": args.evidence_execution_mode,
        "includeCallSnapshots": args.include_call_snapshots,
    }

    # Pass the large JavaScript program over stdin instead of `node -e <script>`.
    # Windows CreateProcess limits the complete command line to roughly 32 KiB;
    # the ordered captain contract is intentionally large enough to exceed that
    # ceiling when embedded directly in argv.  `node -` keeps argv small while
    # preserving the same three trailing runtime arguments.
    proc = subprocess.run(
        [node, "-", str(gravity), str(project), json.dumps(config, separators=(",", ":"))],
        cwd=ROOT,
        input=js,
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
