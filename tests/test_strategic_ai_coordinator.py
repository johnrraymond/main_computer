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
COGNITION_RUNTIME = (
    ROOT / "main_computer" / "web" / "applications" / "scripts"
    / "strategic-ai-runtime.js"
)
ACTION_RUNTIME = (
    ROOT / "main_computer" / "web" / "applications" / "scripts"
    / "strategic-ai-action-runtime.js"
)
COORDINATOR_RUNTIME = (
    ROOT / "main_computer" / "web" / "applications" / "scripts"
    / "strategic-ai-coordinator.js"
)
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
PROJECT_PATH = ROOT / "game_projects" / "webgl-demo" / "project.json"
SCHEMA_PATH = ROOT / "game_projects" / "schema" / "strategic-ai.v1.schema.json"


def _definition() -> dict[str, Any]:
    project = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))
    return project["metadata"]["strategicAI"]


def _run_node(definition: dict[str, Any]) -> dict[str, Any]:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; strategic AI coordinator tests cannot run")

    script = r"""
const fs = require("fs");
const cognition = require(process.argv[1]);
const actionsApi = require(process.argv[2]);
const coordinatorApi = require(process.argv[3]);
const definition = JSON.parse(fs.readFileSync(0, "utf8"));
const actorId = "actor.fixture.watch-officer";

function reportTurn(state = undefined) {
  const coordinator = coordinatorApi.create(definition, {
    seed: 442,
    state
  });
  return coordinator.runTurn(actorId, {
    parameters: {status: "operational"},
    proposalOptions: {createdAt: 5}
  });
}

function contradictionObservation(id) {
  return {
    id,
    observerId: actorId,
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
  };
}

const report = reportTurn();
const deterministicA = reportTurn();
const deterministicB = reportTurn();

const inspectionCoordinator = coordinatorApi.create(definition, {seed: 442});
const inspection = inspectionCoordinator.runTurn(actorId, {
  observations: [
    contradictionObservation("observation.fixture.coordinator-offline")
  ],
  proposalOptions: {createdAt: 10}
});

const rejectionCoordinator = coordinatorApi.create(definition, {seed: 442});
const rejection = rejectionCoordinator.runTurn(actorId, {
  proposalOptions: {
    locationId: "destination.vela-gate.velaris-orbit",
    createdAt: 6
  }
});

const staleCognition = cognition.create(definition, {seed: 442});
const staleReceiptA = staleCognition.decide(
  actorId,
  "checkpoint.strategic-ai.initial"
);
const staleReceiptB = staleCognition.decide(
  actorId,
  "checkpoint.strategic-ai.initial"
);
const staleActions = actionsApi.create(definition, {
  state: staleCognition.snapshot()
});
const staleProposalA = staleActions.createProposal(
  staleReceiptA,
  {},
  {createdAt: 7}
);
const staleProposalB = staleActions.createProposal(
  staleReceiptB,
  {},
  {createdAt: 8}
);
const staleOutcomeA = staleActions.commitProposal(staleProposalA);
const canonicalBeforeStale = staleActions.getCanonicalState();
const observationsBeforeStale = staleActions.snapshot().observations;
const staleOutcomeB = staleActions.commitProposal(staleProposalB);
const canonicalAfterStale = staleActions.getCanonicalState();
const observationsAfterStale = staleActions.snapshot().observations;

const savedState = report.state;
const restoredA = coordinatorApi.create(definition, {
  seed: 442,
  state: savedState
});
const restoredB = coordinatorApi.create(definition, {
  seed: 442,
  state: savedState
});
const restoredTurnA = restoredA.runTurn(actorId, {
  observations: [
    contradictionObservation("observation.fixture.restored-offline")
  ],
  proposalOptions: {createdAt: 12}
});
const restoredTurnB = restoredB.runTurn(actorId, {
  observations: [
    contradictionObservation("observation.fixture.restored-offline")
  ],
  proposalOptions: {createdAt: 12}
});

const legacyCognition = cognition.create(definition, {seed: 442});
legacyCognition.decide(actorId, "checkpoint.strategic-ai.initial");
const legacyState = legacyCognition.snapshot();
legacyState.stateVersion = "game.strategicAI.state.v2";
delete legacyState.receipts[0].canonicalRevision;
delete legacyState.receipts[0].policyProfileId;
const migratedCognition = cognition.create(definition, {
  seed: 442,
  state: legacyState
});
const migratedState = migratedCognition.snapshot();
const migratedActions = actionsApi.create(definition, {
  state: migratedState
});
let legacyProposalError = "";
try {
  migratedActions.createProposal(migratedState.receipts[0]);
} catch (error) {
  legacyProposalError = error instanceof Error ? error.message : String(error);
}

process.stdout.write(JSON.stringify({
  api: {
    schema: coordinatorApi.SCHEMA,
    definitionVersion: coordinatorApi.DEFINITION_VERSION,
    stateVersion: coordinatorApi.STATE_VERSION
  },
  report,
  deterministicA,
  deterministicB,
  inspection,
  rejection,
  stale: {
    staleReceiptA,
    staleReceiptB,
    staleProposalA,
    staleProposalB,
    staleOutcomeA,
    staleOutcomeB,
    canonicalBeforeStale,
    canonicalAfterStale,
    observationsBeforeStale,
    observationsAfterStale,
    state: staleActions.snapshot()
  },
  restoredTurnA,
  restoredTurnB,
  legacy: {
    migratedState,
    legacyProposalError
  }
}));
"""
    result = subprocess.run(
        [
            node,
            "-e",
            script,
            str(COGNITION_RUNTIME),
            str(ACTION_RUNTIME),
            str(COORDINATOR_RUNTIME),
        ],
        input=json.dumps(definition),
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def coordinator_report() -> tuple[dict[str, Any], dict[str, Any]]:
    definition = _definition()
    return definition, _run_node(definition)


def test_coordinator_loads_after_both_runtimes_and_before_scene() -> None:
    html = APPLICATIONS_HTML.read_text(encoding="utf-8")
    cognition = html.index(
        "<!-- @include applications/scripts/strategic-ai-runtime.js -->"
    )
    action = html.index(
        "<!-- @include applications/scripts/strategic-ai-action-runtime.js -->"
    )
    social = html.index(
        "<!-- @include applications/scripts/strategic-ai-social-runtime.js -->"
    )
    coordinator = html.index(
        "<!-- @include applications/scripts/strategic-ai-coordinator.js -->"
    )
    scene = html.index("<!-- @include applications/scripts/scene-viewer.js -->")
    assert cognition < action < social < coordinator < scene


def test_coordinator_script_parses_with_node() -> None:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; strategic AI coordinator syntax cannot be checked")
    subprocess.run(
        [node, "--check", str(COORDINATOR_RUNTIME)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_coordinator_exports_v4_contract(
    coordinator_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = coordinator_report
    assert report["api"] == {
        "schema": "game.strategicAI.v1",
        "definitionVersion": "game.strategicAI.definition.v8",
        "stateVersion": "game.strategicAI.state.v8",
    }


def test_authored_policy_drives_complete_report_turn(
    coordinator_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = coordinator_report
    turn = report["report"]

    assert turn["policyProfileId"] == "policy.fixture.watch-officer"
    assert turn["decision"]["selectedActionTypeId"] == (
        "action.fixture.send-status-report"
    )
    assert turn["decision"]["canonicalRevision"] == 0
    assert turn["proposal"]["canonicalRevision"] == 0
    assert turn["outcome"]["status"] == "accepted"
    assert turn["canonicalRevisionBefore"] == 0
    assert turn["canonicalRevisionAfter"] == 1
    assert len(turn["resultingObservationIds"]) == 1
    assert turn["resultingBeliefUpdates"]
    resulting_id = turn["resultingObservationIds"][0]
    assert any(
        resulting_id in belief["basisIds"]
        for belief in turn["resultingBeliefUpdates"]
    )


def test_authored_policy_changes_to_inspection_after_contradiction(
    coordinator_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = coordinator_report
    turn = report["inspection"]

    assert turn["incomingObservationIds"] == [
        "observation.fixture.coordinator-offline"
    ]
    assert turn["incomingBeliefUpdates"]
    assert turn["decision"]["selectedActionTypeId"] == (
        "action.fixture.request-relay-inspection"
    )
    assert turn["outcome"]["status"] == "accepted"
    assert turn["outcome"]["consumedResources"] == [
        {
            "resourceId": "resource.fixture.inspection-window",
            "amount": 1,
        }
    ]


def test_stale_simultaneous_decision_is_rejected_before_other_checks(
    coordinator_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = coordinator_report
    stale = report["stale"]

    assert stale["staleReceiptA"]["canonicalRevision"] == 0
    assert stale["staleReceiptB"]["canonicalRevision"] == 0
    assert stale["staleOutcomeA"]["status"] == "accepted"
    assert stale["staleOutcomeB"]["status"] == "rejected"
    assert stale["staleOutcomeB"]["rejectionCode"] == (
        "canonical-revision-stale"
    )
    assert stale["canonicalBeforeStale"] == stale["canonicalAfterStale"]
    assert stale["observationsBeforeStale"] == stale["observationsAfterStale"]


def test_rejected_turn_creates_no_result_observation_or_belief_update(
    coordinator_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    definition, report = coordinator_report
    turn = report["rejection"]

    assert turn["outcome"]["status"] == "rejected"
    assert turn["outcome"]["rejectionCode"] == "wrong-location"
    assert turn["canonicalRevisionBefore"] == turn["canonicalRevisionAfter"] == 0
    assert turn["resultingObservationIds"] == []
    assert turn["resultingBeliefUpdates"] == []
    assert len(turn["state"]["observations"]) == len(
        definition["stateDefaults"]["observations"]
    )
    assert len(turn["state"]["beliefs"]) == len(
        definition["stateDefaults"]["beliefs"]
    )


def test_full_turn_and_save_restore_are_deterministic(
    coordinator_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = coordinator_report

    assert report["deterministicA"] == report["deterministicB"]
    assert report["restoredTurnA"] == report["restoredTurnB"]


def test_v2_receipts_migrate_unbound_instead_of_fabricating_revision(
    coordinator_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = coordinator_report
    migrated = report["legacy"]["migratedState"]

    assert migrated["stateVersion"] == "game.strategicAI.state.v8"
    assert migrated["receipts"][0]["canonicalRevision"] is None
    assert migrated["receipts"][0]["policyProfileId"] is None
    assert "no canonical revision binding" in report["legacy"][
        "legacyProposalError"
    ]


def test_complete_turn_states_remain_schema_and_reference_valid(
    coordinator_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    definition, report = coordinator_report
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)

    for state in (
        report["report"]["state"],
        report["inspection"]["state"],
        report["rejection"]["state"],
        report["stale"]["state"],
        report["restoredTurnA"]["state"],
    ):
        updated = copy.deepcopy(definition)
        updated["stateDefaults"] = state
        Draft202012Validator(schema).validate(updated)
        assert validate_strategic_ai_definition(updated) == []
