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
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
PROJECT_PATH = ROOT / "game_projects" / "webgl-demo" / "project.json"
SCHEMA_PATH = ROOT / "game_projects" / "schema" / "strategic-ai.v1.schema.json"


def _load_definition() -> dict[str, Any]:
    project = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))
    return project["metadata"]["strategicAI"]


def _run_node(definition: dict[str, Any]) -> dict[str, Any]:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; strategic AI action tests cannot run")

    script = r"""
const fs = require("fs");
const cognition = require(process.argv[1]);
const actionsApi = require(process.argv[2]);
const definition = JSON.parse(fs.readFileSync(0, "utf8"));

const reportPolicies = {
  "action.fixture.send-status-report": {
    baseScore: 2,
    weights: {}
  },
  "action.fixture.request-relay-inspection": {
    baseScore: -2,
    weights: {}
  }
};
const inspectionPolicies = {
  "action.fixture.send-status-report": {
    baseScore: -2,
    weights: {}
  },
  "action.fixture.request-relay-inspection": {
    baseScore: 2,
    weights: {}
  }
};

function decide(definitionValue, policies, times = 1, state = undefined) {
  const runtime = cognition.create(definitionValue, {
    seed: 7719,
    state,
    actionPolicies: policies
  });
  const receipts = [];
  for (let index = 0; index < times; index += 1) {
    receipts.push(runtime.decide(
      "actor.fixture.watch-officer",
      "checkpoint.strategic-ai.initial"
    ));
  }
  return {runtime, receipts, state: runtime.snapshot()};
}

function reportScenario() {
  const cognitionResult = decide(definition, reportPolicies);
  const actions = actionsApi.create(definition, {state: cognitionResult.state});
  const receipt = cognitionResult.receipts[0];
  const proposal = actions.createProposal(receipt, {status: "operational"}, {createdAt: 5});
  let receiptReuseError = "";
  try {
    actions.createProposal(receipt, {status: "duplicate"}, {createdAt: 6});
  } catch (error) {
    receiptReuseError = error instanceof Error ? error.message : String(error);
  }
  const before = actions.getCanonicalState();
  const outcome = actions.commitProposal(proposal);
  const after = actions.getCanonicalState();
  return {
    receipt,
    proposal,
    receiptReuseError,
    before,
    outcome,
    after,
    state: actions.snapshot()
  };
}

function inspectionScenario() {
  const cognitionResult = decide(definition, inspectionPolicies);
  const actions = actionsApi.create(definition, {state: cognitionResult.state});

  const firstProposal = actions.createProposal(
    cognitionResult.receipts[0],
    {},
    {createdAt: 10}
  );
  const firstOutcome = actions.commitProposal(firstProposal);

  const secondCognition = decide(
    definition,
    inspectionPolicies,
    1,
    actions.snapshot()
  );
  const secondActions = actionsApi.create(definition, {
    state: secondCognition.state
  });
  const beforeSecond = secondActions.getCanonicalState();
  const observationCountBeforeSecond = secondActions.snapshot().observations.length;
  const secondProposal = secondActions.createProposal(
    secondCognition.receipts[0],
    {},
    {createdAt: 11}
  );
  const secondOutcome = secondActions.commitProposal(secondProposal);
  const afterSecond = secondActions.getCanonicalState();
  const observationCountAfterSecond = secondActions.snapshot().observations.length;

  return {
    firstProposal,
    firstOutcome,
    secondProposal,
    secondOutcome,
    beforeSecond,
    afterSecond,
    observationCountBeforeSecond,
    observationCountAfterSecond,
    state: secondActions.snapshot()
  };
}

function rejectionScenarios() {
  const cognitionResult = decide(definition, inspectionPolicies);
  const receipt = cognitionResult.receipts[0];

  const wrongLocationRuntime = actionsApi.create(definition, {state: cognitionResult.state});
  const wrongLocationProposal = wrongLocationRuntime.createProposal(
    receipt,
    {},
    {locationId: "destination.vela-gate.velaris-orbit"}
  );
  const wrongLocationBefore = wrongLocationRuntime.getCanonicalState();
  const wrongLocationOutcome = wrongLocationRuntime.commitProposal(wrongLocationProposal);
  const wrongLocationAfter = wrongLocationRuntime.getCanonicalState();

  const preconditionDefinition = JSON.parse(JSON.stringify(definition));
  preconditionDefinition.stateDefaults.canonicalState.factStates
    .find((record) => record.factId === "fact.fixture.inspection-access-available")
    .value = false;
  const preconditionCognition = decide(preconditionDefinition, inspectionPolicies);
  const preconditionRuntime = actionsApi.create(preconditionDefinition, {
    state: preconditionCognition.state
  });
  const preconditionProposal = preconditionRuntime.createProposal(
    preconditionCognition.receipts[0]
  );
  const preconditionOutcome = preconditionRuntime.commitProposal(preconditionProposal);

  const undeclaredCognition = decide(definition, reportPolicies);
  const undeclaredRuntime = actionsApi.create(definition, {state: undeclaredCognition.state});
  const undeclaredProposal = undeclaredRuntime.createProposal(
    undeclaredCognition.receipts[0],
    {},
    {
      requestedEffects: [{
        effectTypeId: "effect.fixture.inspection-requested",
        payload: {}
      }]
    }
  );
  const undeclaredOutcome = undeclaredRuntime.commitProposal(undeclaredProposal);

  const staleDefinition = JSON.parse(JSON.stringify(definition));
  staleDefinition.checkpoints.push({
    id: "checkpoint.strategic-ai.later",
    label: "Later fixture checkpoint",
    worldTime: 20,
    factIds: [
      "fact.fixture.relay-operational",
      "fact.fixture.inspection-access-available"
    ]
  });
  const staleCognition = decide(staleDefinition, inspectionPolicies);
  staleCognition.state.currentCheckpointId = "checkpoint.strategic-ai.later";
  const staleRuntime = actionsApi.create(staleDefinition, {state: staleCognition.state});
  const staleProposal = staleRuntime.createProposal(staleCognition.receipts[0]);
  const staleOutcome = staleRuntime.commitProposal(staleProposal);

  const actionMismatchState = JSON.parse(JSON.stringify(cognitionResult.state));
  actionMismatchState.proposals.push({
    proposalId: "proposal.fixture.action-mismatch",
    decisionId: receipt.decisionId,
    actorId: receipt.actorId,
    checkpointId: receipt.checkpointId,
    canonicalRevision: receipt.canonicalRevision,
    actionTypeId: "action.fixture.send-status-report",
    locationId: "destination.solace-reach.haven-orbit",
    parameters: {},
    requestedEffects: [{
      effectTypeId: "effect.fixture.report-recorded",
      payload: {}
    }],
    createdAt: 0
  });
  actionMismatchState.actorStates[0].pendingProposalIds.push(
    "proposal.fixture.action-mismatch"
  );
  const actionMismatchRuntime = actionsApi.create(definition, {
    state: actionMismatchState
  });
  const actionMismatchOutcome = actionMismatchRuntime.commitProposal(
    "proposal.fixture.action-mismatch"
  );

  return {
    wrongLocationBefore,
    wrongLocationOutcome,
    wrongLocationAfter,
    preconditionOutcome,
    undeclaredOutcome,
    staleOutcome,
    actionMismatchOutcome
  };
}

function protectedScenario() {
  const protectedDefinition = JSON.parse(JSON.stringify(definition));
  protectedDefinition.actors[0].candidateActionTypeIds = [
    "action.fixture.disable-relay"
  ];
  const policies = {
    "action.fixture.disable-relay": {baseScore: 10, weights: {}}
  };
  const cognitionResult = decide(protectedDefinition, policies);
  const runtime = actionsApi.create(protectedDefinition, {
    state: cognitionResult.state
  });
  const proposal = runtime.createProposal(cognitionResult.receipts[0]);
  const before = runtime.getCanonicalState();
  const outcome = runtime.commitProposal(proposal);
  const after = runtime.getCanonicalState();
  return {proposal, before, outcome, after};
}

function atomicFailureScenario() {
  const atomicDefinition = JSON.parse(JSON.stringify(definition));
  atomicDefinition.effectTypes.push({
    id: "effect.fixture.invalid-fact-update",
    label: "Invalid fact update",
    protected: false,
    category: "general",
    operation: "set-fact",
    requiredAuthorityIds: [],
    targetFactId: "fact.fixture.missing",
    value: false,
    description: "Test-only missing fact target."
  });
  const inspectionAction = atomicDefinition.actionTypes.find(
    (action) => action.id === "action.fixture.request-relay-inspection"
  );
  inspectionAction.effectTypeIds = [
    "effect.fixture.inspection-requested",
    "effect.fixture.invalid-fact-update"
  ];

  const cognitionResult = decide(atomicDefinition, inspectionPolicies);
  const runtime = actionsApi.create(atomicDefinition, {
    state: cognitionResult.state
  });
  const proposal = runtime.createProposal(cognitionResult.receipts[0]);
  const before = runtime.getCanonicalState();
  const observationsBefore = runtime.snapshot().observations;
  const outcome = runtime.commitProposal(proposal);
  const after = runtime.getCanonicalState();
  const observationsAfter = runtime.snapshot().observations;
  return {proposal, before, outcome, after, observationsBefore, observationsAfter};
}

function restoreScenario() {
  const cognitionResult = decide(definition, reportPolicies);
  const initial = actionsApi.create(definition, {state: cognitionResult.state});
  const proposal = initial.createProposal(cognitionResult.receipts[0]);
  const saved = initial.snapshot();

  const restoredA = actionsApi.create(definition, {state: saved});
  const restoredB = actionsApi.create(definition, {state: saved});
  const outcomeA = restoredA.commitProposal(proposal);
  const outcomeB = restoredB.commitProposal(proposal);

  return {
    proposal,
    outcomeA,
    outcomeB,
    stateA: restoredA.snapshot(),
    stateB: restoredB.snapshot()
  };
}

function migrationScenario() {
  const legacy = JSON.parse(JSON.stringify(definition.stateDefaults));
  legacy.stateVersion = "game.strategicAI.state.v1";
  delete legacy.canonicalState;
  delete legacy.proposals;
  delete legacy.outcomes;

  const cognitionRuntime = cognition.create(definition, {
    state: legacy,
    seed: 1,
    actionPolicies: reportPolicies
  });
  const actionRuntime = actionsApi.create(definition, {state: legacy});
  return {
    cognition: cognitionRuntime.snapshot(),
    action: actionRuntime.snapshot()
  };
}

const report = reportScenario();
const inspection = inspectionScenario();
const rejections = rejectionScenarios();
const protectedResult = protectedScenario();
const atomic = atomicFailureScenario();
const restored = restoreScenario();
const migration = migrationScenario();

process.stdout.write(JSON.stringify({
  api: {
    schema: actionsApi.SCHEMA,
    definitionVersion: actionsApi.DEFINITION_VERSION,
    stateVersion: actionsApi.STATE_VERSION,
    legacyStateVersion: actionsApi.LEGACY_STATE_VERSION
  },
  report,
  inspection,
  rejections,
  protectedResult,
  atomic,
  restored,
  migration
}));
"""
    result = subprocess.run(
        [node, "-e", script, str(COGNITION_RUNTIME), str(ACTION_RUNTIME)],
        input=json.dumps(definition),
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def action_report() -> tuple[dict[str, Any], dict[str, Any]]:
    definition = _load_definition()
    return definition, _run_node(definition)


def test_action_runtime_is_loaded_between_cognition_and_scene_viewer() -> None:
    html = APPLICATIONS_HTML.read_text(encoding="utf-8")
    cognition = html.index(
        "<!-- @include applications/scripts/strategic-ai-runtime.js -->"
    )
    action = html.index(
        "<!-- @include applications/scripts/strategic-ai-action-runtime.js -->"
    )
    scene = html.index("<!-- @include applications/scripts/scene-viewer.js -->")
    assert cognition < action < scene


def test_action_runtime_scripts_parse_with_node() -> None:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; strategic AI action syntax cannot be checked")
    for path in (COGNITION_RUNTIME, ACTION_RUNTIME):
        subprocess.run(
            [node, "--check", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )


def test_action_runtime_exports_v3_contract(
    action_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = action_report
    assert report["api"] == {
        "schema": "game.strategicAI.v1",
        "definitionVersion": "game.strategicAI.definition.v8",
        "stateVersion": "game.strategicAI.state.v8",
        "legacyStateVersion": "game.strategicAI.state.v1",
    }


def test_valid_report_commits_one_atomic_event_and_observation(
    action_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = action_report
    scenario = report["report"]
    outcome = scenario["outcome"]

    assert outcome["status"] == "accepted"
    assert "already has proposal" in scenario["receiptReuseError"]
    assert outcome["committedEffectTypeIds"] == ["effect.fixture.report-recorded"]
    assert outcome["consumedResources"] == []
    assert outcome["canonicalRevisionAfter"] == outcome["canonicalRevisionBefore"] + 1
    assert len(scenario["after"]["events"]) == len(scenario["before"]["events"]) + 1
    assert (
        scenario["after"]["factStates"]
        == scenario["before"]["factStates"]
    )
    assert len(outcome["resultingObservationIds"]) == 1


def test_inspection_consumes_once_then_rejects_without_canonical_change(
    action_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = action_report
    scenario = report["inspection"]

    assert scenario["firstOutcome"]["status"] == "accepted"
    assert scenario["firstOutcome"]["consumedResources"] == [
        {"resourceId": "resource.fixture.inspection-window", "amount": 1}
    ]
    balance = scenario["beforeSecond"]["resourceBalances"][0]
    assert balance["quantity"] == 0

    assert scenario["secondOutcome"]["status"] == "rejected"
    assert scenario["secondOutcome"]["rejectionCode"] == "resource-unavailable"
    assert scenario["beforeSecond"] == scenario["afterSecond"]
    assert (
        scenario["observationCountBeforeSecond"]
        == scenario["observationCountAfterSecond"]
    )


def test_authority_location_precondition_checkpoint_and_effect_rejections(
    action_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = action_report
    rejections = report["rejections"]

    assert rejections["wrongLocationOutcome"]["rejectionCode"] == "wrong-location"
    assert rejections["wrongLocationBefore"] == rejections["wrongLocationAfter"]
    assert rejections["preconditionOutcome"]["rejectionCode"] == "precondition-failed"
    assert rejections["undeclaredOutcome"]["rejectionCode"] == "effect-not-allowed"
    assert rejections["staleOutcome"]["rejectionCode"] == "checkpoint-stale"
    assert rejections["actionMismatchOutcome"]["rejectionCode"] == "action-mismatch"


def test_protected_effect_requires_explicit_higher_authority(
    action_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = action_report
    scenario = report["protectedResult"]

    assert scenario["outcome"]["status"] == "rejected"
    assert scenario["outcome"]["rejectionCode"] == "protected-effect-forbidden"
    assert scenario["before"] == scenario["after"]


def test_second_effect_failure_rolls_back_resource_event_and_observation(
    action_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = action_report
    scenario = report["atomic"]

    assert scenario["outcome"]["status"] == "rejected"
    assert scenario["outcome"]["rejectionCode"] == "effect-commit-failed"
    assert scenario["before"] == scenario["after"]
    assert scenario["observationsBefore"] == scenario["observationsAfter"]


def test_save_restore_reproduces_identical_commit(
    action_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = action_report
    scenario = report["restored"]

    assert scenario["outcomeA"] == scenario["outcomeB"]
    assert scenario["stateA"] == scenario["stateB"]


def test_v1_private_state_migrates_to_explicit_v3_defaults(
    action_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    definition, report = action_report
    migration = report["migration"]
    expected_balances = [
        {
            "resourceId": resource["id"],
            "quantity": resource["capacity"],
        }
        for resource in definition["resources"]
    ]

    for state in (migration["cognition"], migration["action"]):
        assert state["stateVersion"] == "game.strategicAI.state.v8"
        assert state["canonicalState"]["revision"] == 0
        assert state["canonicalState"]["resourceBalances"] == expected_balances
        assert state["proposals"] == []
        assert state["outcomes"] == []


def test_committed_and_rejected_states_remain_schema_and_reference_valid(
    action_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    definition, report = action_report
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)

    for state in (
        report["report"]["state"],
        report["inspection"]["state"],
        report["restored"]["stateA"],
    ):
        updated = copy.deepcopy(definition)
        updated["stateDefaults"] = state
        Draft202012Validator(schema).validate(updated)
        assert validate_strategic_ai_definition(updated) == []
