from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from main_computer.mcel_node_runtime import resolve_node_executable
from main_computer.strategic_ai_definition import validate_strategic_ai_definition


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATH = (
    ROOT
    / "main_computer"
    / "web"
    / "applications"
    / "scripts"
    / "strategic-ai-runtime.js"
)
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
PROJECT_PATH = ROOT / "game_projects" / "webgl-demo" / "project.json"
SCHEMA_PATH = ROOT / "game_projects" / "schema" / "strategic-ai.v1.schema.json"

ACTION_POLICIES = {
    "action.fixture.send-status-report": {
        "baseScore": 0.0,
        "weights": {
            "goalPriority": 0.5,
            "evidenceSupport": 1.0,
            "uncertainty": -0.6,
            "memoryRelevance": 0.1,
        },
    },
    "action.fixture.request-relay-inspection": {
        "baseScore": 0.0,
        "weights": {
            "goalPriority": 0.5,
            "evidenceSupport": 0.1,
            "uncertainty": 1.0,
            "memoryRelevance": 0.05,
        },
    },
}


def _load_project() -> dict[str, Any]:
    return json.loads(PROJECT_PATH.read_text(encoding="utf-8"))


def _run_node(payload: dict[str, Any]) -> dict[str, Any]:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; strategic AI runtime tests cannot run")

    script = r"""
const fs = require("fs");
const runtime = require(process.argv[1]);
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const definition = payload.definition;
const options = {
  seed: payload.seed,
  state: payload.state,
  actionPolicies: payload.actionPolicies
};
if (options.state === null) delete options.state;

function runScenario(definitionValue, runtimeOptions) {
  const ai = runtime.create(definitionValue, runtimeOptions);
  const canonicalBefore = JSON.stringify(ai.canonicalFacts());
  const initialMemory = ai.retrieveMemories(
    "actor.fixture.watch-officer",
    {
      proposition: {
        predicate: "predicate.fixture.relay-operational",
        arguments: ["relay.fixture.haven-navigation"],
        value: true
      }
    },
    5
  );
  const firstEvaluation = ai.evaluateCandidates("actor.fixture.watch-officer");
  const firstReceipt = ai.decide(
    "actor.fixture.watch-officer",
    "checkpoint.strategic-ai.initial"
  );
  ai.ingestObservation({
    id: "observation.fixture.relay-offline-follow-up",
    observerId: "actor.fixture.watch-officer",
    proposition: {
      predicate: "predicate.fixture.relay-operational",
      arguments: ["relay.fixture.haven-navigation"],
      value: false
    },
    channelId: "channel.fixture.relay-telemetry",
    sourceId: "source.fixture.relay-sensor",
    reliability: 0.98,
    observedAt: 10,
    visibility: "private"
  });
  const changedBeliefs = ai.updateBeliefs(
    "actor.fixture.watch-officer",
    ["observation.fixture.relay-offline-follow-up"]
  );
  const secondEvaluation = ai.evaluateCandidates("actor.fixture.watch-officer");
  const finalStateBeforeSecondDecision = ai.snapshot();
  const secondReceipt = ai.decide(
    "actor.fixture.watch-officer",
    "checkpoint.strategic-ai.initial"
  );
  const canonicalAfter = JSON.stringify(ai.canonicalFacts());
  return {
    canonicalBefore,
    canonicalAfter,
    initialMemory,
    firstEvaluation,
    firstReceipt,
    changedBeliefs,
    secondEvaluation,
    finalStateBeforeSecondDecision,
    secondReceipt,
    finalState: ai.snapshot(),
    receipts: ai.getReceipts()
  };
}

const primary = runScenario(definition, options);
const replay = runScenario(definition, options);

const restoredA = runtime.create(definition, {
  seed: payload.seed,
  state: primary.finalStateBeforeSecondDecision,
  actionPolicies: payload.actionPolicies
});
const restoredB = runtime.create(definition, {
  seed: payload.seed,
  state: primary.finalStateBeforeSecondDecision,
  actionPolicies: payload.actionPolicies
});
const restoredReceiptA = restoredA.decide(
  "actor.fixture.watch-officer",
  "checkpoint.strategic-ai.initial"
);
const restoredReceiptB = restoredB.decide(
  "actor.fixture.watch-officer",
  "checkpoint.strategic-ai.initial"
);

const unauthorizedDefinition = JSON.parse(JSON.stringify(definition));
unauthorizedDefinition.actors[0].authorityIds = ["authority.fixture.report-status"];
const unauthorized = runtime.create(unauthorizedDefinition, {
  seed: payload.seed,
  actionPolicies: payload.actionPolicies
});
const unauthorizedEvaluation = unauthorized.evaluateCandidates(
  "actor.fixture.watch-officer"
);

process.stdout.write(JSON.stringify({
  api: {
    schema: runtime.SCHEMA,
    definitionVersion: runtime.DEFINITION_VERSION,
    stateVersion: runtime.STATE_VERSION,
    metrics: runtime.SCORE_METRICS
  },
  primary,
  replay,
  restoredReceiptA,
  restoredReceiptB,
  unauthorizedEvaluation
}));
"""
    result = subprocess.run(
        [node, "-e", script, str(RUNTIME_PATH)],
        input=json.dumps(payload),
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def _runtime_report() -> tuple[dict[str, Any], dict[str, Any]]:
    project = _load_project()
    definition = project["metadata"]["strategicAI"]
    report = _run_node(
        {
            "definition": definition,
            "state": None,
            "seed": 441920,
            "actionPolicies": ACTION_POLICIES,
        }
    )
    return definition, report


def test_runtime_is_loaded_before_scene_viewer() -> None:
    html = APPLICATIONS_HTML.read_text(encoding="utf-8")
    strategic = html.index(
        "<!-- @include applications/scripts/strategic-ai-runtime.js -->"
    )
    scene = html.index("<!-- @include applications/scripts/scene-viewer.js -->")
    assert strategic < scene


def test_runtime_script_parses_with_node() -> None:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; strategic AI runtime syntax cannot be checked")
    subprocess.run(
        [node, "--check", str(RUNTIME_PATH)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_runtime_exports_versioned_headless_api() -> None:
    _definition, report = _runtime_report()
    assert report["api"] == {
        "schema": "game.strategicAI.v1",
        "definitionVersion": "game.strategicAI.definition.v8",
        "stateVersion": "game.strategicAI.state.v8",
        "metrics": [
            "goalPriority",
            "evidenceSupport",
            "uncertainty",
            "memoryRelevance",
            "observationReliability",
            "captainCooperation",
            "captainEvidenceDiscipline",
            "captainAuthorityResistance",
            "commitmentTrust",
        ],
    }


def test_direct_evidence_selects_report_then_contradiction_selects_inspection() -> None:
    _definition, report = _runtime_report()
    primary = report["primary"]

    assert (
        primary["firstReceipt"]["selectedActionTypeId"]
        == "action.fixture.send-status-report"
    )
    assert (
        primary["secondReceipt"]["selectedActionTypeId"]
        == "action.fixture.request-relay-inspection"
    )
    assert primary["firstEvaluation"]["metrics"]["evidenceSupport"] > primary[
        "secondEvaluation"
    ]["metrics"]["evidenceSupport"]
    assert primary["firstEvaluation"]["metrics"]["uncertainty"] < primary[
        "secondEvaluation"
    ]["metrics"]["uncertainty"]


def test_belief_revision_preserves_canonical_truth_and_provenance() -> None:
    _definition, report = _runtime_report()
    primary = report["primary"]

    assert primary["canonicalBefore"] == primary["canonicalAfter"]
    changed = {belief["id"]: belief for belief in primary["changedBeliefs"]}
    assert "observation.fixture.relay-offline-follow-up" in changed[
        "belief.fixture.relay-operational"
    ]["basisIds"]
    assert "observation.fixture.relay-offline-follow-up" in changed[
        "belief.fixture.relay-offline-stale"
    ]["basisIds"]
    assert changed["belief.fixture.relay-operational"]["confidence"] < 0.96
    assert changed["belief.fixture.relay-offline-stale"]["confidence"] > 0.35


def test_memory_retrieval_is_bounded_ranked_and_inspectable() -> None:
    _definition, report = _runtime_report()
    memories = report["primary"]["initialMemory"]

    assert len(memories) == 1
    assert memories[0]["id"] == "memory.fixture.relay-telemetry"
    assert 0 <= memories[0]["retrievalScore"] <= 1


def test_receipts_are_deterministic_and_include_score_components() -> None:
    _definition, report = _runtime_report()

    assert report["primary"]["receipts"] == report["replay"]["receipts"]
    assert report["restoredReceiptA"] == report["restoredReceiptB"]
    for receipt in report["primary"]["receipts"]:
        assert receipt["randomSeed"] == 441920
        assert receipt["candidateActions"]
        for candidate in receipt["candidateActions"]:
            assert "scoreComponents" in candidate
            assert set(candidate["scoreComponents"]) == {
                "baseScore",
                "goalPriority",
                "evidenceSupport",
                "uncertainty",
                "memoryRelevance",
                "observationReliability",
            "captainCooperation",
            "captainEvidenceDiscipline",
                "captainAuthorityResistance",
                "commitmentTrust",
            }


def test_missing_authority_rejects_candidate_without_changing_definition() -> None:
    definition, report = _runtime_report()
    rejection = report["unauthorizedEvaluation"]["rejections"]

    assert rejection == [
        {
            "actionTypeId": "action.fixture.request-relay-inspection",
            "reason": "missing authority authority.fixture.request-inspection",
        }
    ]
    assert definition["stateDefaults"]["receipts"] == []


def test_generated_state_and_receipts_remain_schema_and_reference_valid() -> None:
    definition, report = _runtime_report()
    updated = copy.deepcopy(definition)
    updated["stateDefaults"] = report["primary"]["finalState"]

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(updated)
    assert validate_strategic_ai_definition(updated) == []
