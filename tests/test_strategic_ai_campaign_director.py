from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

from main_computer.mcel_node_runtime import resolve_node_executable
from main_computer.strategic_ai_definition import validate_strategic_ai_definition


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"
COGNITION_RUNTIME = SCRIPTS / "strategic-ai-runtime.js"
ACTION_RUNTIME = SCRIPTS / "strategic-ai-action-runtime.js"
SOCIAL_RUNTIME = SCRIPTS / "strategic-ai-social-runtime.js"
COMMITMENT_RUNTIME = SCRIPTS / "strategic-ai-commitment-runtime.js"
DIRECTOR_RUNTIME = SCRIPTS / "strategic-ai-director-runtime.js"
COORDINATOR_RUNTIME = SCRIPTS / "strategic-ai-coordinator.js"
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
PROJECT_PATH = ROOT / "game_projects" / "webgl-demo" / "project.json"
SCHEMA_PATH = ROOT / "game_projects" / "schema" / "strategic-ai.v1.schema.json"

VELA_OPPORTUNITY = "opportunity.campaign.vela-gate-intervention"
SOLACE_OPPORTUNITY = "opportunity.campaign.solace-reach-intervention"
DIRECTOR_PREDICATE = "predicate.campaign.opportunity-window-active"

VELA_ACTORS = {
    "actor.vela.gate-official",
    "actor.vela.rescue-organizer",
    "actor.vela.survivor",
}
SOLACE_ACTORS = {
    "actor.solace.haven-coordinator",
    "actor.solace.osprey-captain",
    "actor.solace.lyria-medic",
}


def _load_project() -> dict[str, Any]:
    return json.loads(PROJECT_PATH.read_text(encoding="utf-8"))


def _load_definition() -> dict[str, Any]:
    return _load_project()["metadata"]["strategicAI"]


def _run_node(definition: dict[str, Any]) -> dict[str, Any]:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; campaign-director tests cannot run")

    script = r"""
const fs = require("fs");
const cognition = require(process.argv[1]);
const actions = require(process.argv[2]);
const social = require(process.argv[3]);
const commitments = require(process.argv[4]);
const director = require(process.argv[5]);
const coordinatorApi = require(process.argv[6]);
const definition = JSON.parse(fs.readFileSync(0, "utf8"));

function createCoordinator(state = undefined) {
  return coordinatorApi.create(definition, {
    state,
    seed: 7707,
    cognitionApi: cognition,
    actionApi: actions,
    socialApi: social,
    commitmentApi: commitments,
    directorApi: director
  });
}

function activation(routeSystemId, selectedAt) {
  const coordinator = createCoordinator();
  const before = coordinator.snapshot();
  const result = coordinator.activateCampaignRoute(routeSystemId, {
    selectedAt,
    canonicalRevision: 0,
    reason: "captain-selected-major-route"
  });
  return {before, result, state: coordinator.snapshot()};
}

function reversibleSequence() {
  const coordinator = createCoordinator();
  const vela = coordinator.activateCampaignRoute("system.vela-gate", {
    selectedAt: 1
  });
  const reversed = coordinator.deactivateCampaignOpportunity(
    "opportunity.campaign.vela-gate-intervention",
    {selectedAt: 2}
  );
  const solace = coordinator.activateCampaignRoute("system.solace-reach", {
    selectedAt: 3
  });
  const expired = coordinator.expireCampaignOpportunities(6);
  return {vela, reversed, solace, expired, state: coordinator.snapshot()};
}

function saveRestore() {
  const original = createCoordinator();
  original.activateCampaignRoute("system.vela-gate", {selectedAt: 1});
  const saved = original.snapshot();

  const a = createCoordinator(saved);
  const b = createCoordinator(saved);
  const resultA = a.deactivateCampaignOpportunity(
    "opportunity.campaign.vela-gate-intervention",
    {selectedAt: 2}
  );
  const resultB = b.deactivateCampaignOpportunity(
    "opportunity.campaign.vela-gate-intervention",
    {selectedAt: 2}
  );
  return {
    resultA,
    resultB,
    stateA: a.snapshot(),
    stateB: b.snapshot()
  };
}

function migration() {
  const legacy = JSON.parse(JSON.stringify(definition.stateDefaults));
  legacy.stateVersion = "game.strategicAI.state.v5";
  delete legacy.campaignOpportunityStates;
  delete legacy.directorReceipts;

  return {
    cognition: cognition.create(definition, {state: legacy, seed: 1}).snapshot(),
    action: actions.create(definition, {state: legacy}).snapshot(),
    social: social.create(definition, {state: legacy}).snapshot(),
    commitment: commitments.create(definition, {state: legacy}).snapshot(),
    director: director.create(definition, {state: legacy}).snapshot(),
    coordinator: createCoordinator(legacy).snapshot()
  };
}

function invalid() {
  const unknown = createCoordinator();
  let unknownRoute = "";
  try {
    unknown.activateCampaignRoute("system.not-authored", {selectedAt: 1});
  } catch (error) {
    unknownRoute = error instanceof Error ? error.message : String(error);
  }

  const duplicate = createCoordinator();
  duplicate.activateCampaignRoute("system.vela-gate", {selectedAt: 1});
  let duplicateActivation = "";
  try {
    duplicate.activateCampaignRoute("system.vela-gate", {selectedAt: 2});
  } catch (error) {
    duplicateActivation = error instanceof Error ? error.message : String(error);
  }

  const stale = createCoordinator();
  let staleRevision = "";
  try {
    stale.activateCampaignRoute("system.solace-reach", {
      selectedAt: 1,
      canonicalRevision: 9
    });
  } catch (error) {
    staleRevision = error instanceof Error ? error.message : String(error);
  }
  return {unknownRoute, duplicateActivation, staleRevision};
}

const velaA = activation("system.vela-gate", 1);
const velaB = activation("system.vela-gate", 1);
const solaceA = activation("system.solace-reach", 1);
const solaceB = activation("system.solace-reach", 1);
const reversible = reversibleSequence();
const restored = saveRestore();
const migrated = migration();
const invalidCases = invalid();

process.stdout.write(JSON.stringify({
  api: {
    schema: director.SCHEMA,
    definitionVersion: director.DEFINITION_VERSION,
    stateVersion: director.STATE_VERSION,
    legacyStateVersions: director.LEGACY_STATE_VERSIONS,
    predicate: director.OPPORTUNITY_PREDICATE
  },
  velaA,
  velaB,
  solaceA,
  solaceB,
  reversible,
  restored,
  migrated,
  invalidCases
}));
"""
    result = subprocess.run(
        [
            node,
            "-e",
            script,
            str(COGNITION_RUNTIME),
            str(ACTION_RUNTIME),
            str(SOCIAL_RUNTIME),
            str(COMMITMENT_RUNTIME),
            str(DIRECTOR_RUNTIME),
            str(COORDINATOR_RUNTIME),
        ],
        input=json.dumps(definition),
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def director_report() -> tuple[dict[str, Any], dict[str, Any]]:
    definition = _load_definition()
    return definition, _run_node(definition)


def _states_by_id(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        record["opportunityId"]: record
        for record in state["campaignOpportunityStates"]
    }


def test_director_runtime_loads_before_coordinator() -> None:
    html = APPLICATIONS_HTML.read_text(encoding="utf-8")
    commitment = html.index(
        "<!-- @include applications/scripts/strategic-ai-commitment-runtime.js -->"
    )
    director = html.index(
        "<!-- @include applications/scripts/strategic-ai-director-runtime.js -->"
    )
    coordinator = html.index(
        "<!-- @include applications/scripts/strategic-ai-coordinator.js -->"
    )
    scene = html.index("<!-- @include applications/scripts/scene-viewer.js -->")
    assert commitment < director < coordinator < scene


def test_all_strategic_scripts_parse_with_node() -> None:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; strategic runtime syntax cannot be checked")
    for path in (
        COGNITION_RUNTIME,
        ACTION_RUNTIME,
        SOCIAL_RUNTIME,
        COMMITMENT_RUNTIME,
        DIRECTOR_RUNTIME,
        COORDINATOR_RUNTIME,
    ):
        subprocess.run(
            [node, "--check", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )


def test_director_contract_is_generic_and_bounded(
    director_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    definition, report = director_report
    assert report["api"] == {
        "schema": "game.strategicAI.v1",
        "definitionVersion": "game.strategicAI.definition.v8",
        "stateVersion": "game.strategicAI.state.v8",
        "legacyStateVersions": [
            "game.strategicAI.state.v1",
            "game.strategicAI.state.v2",
            "game.strategicAI.state.v3",
            "game.strategicAI.state.v4",
            "game.strategicAI.state.v5",
            "game.strategicAI.state.v6",
            "game.strategicAI.state.v7",
        ],
        "predicate": DIRECTOR_PREDICATE,
    }

    runtime_text = DIRECTOR_RUNTIME.read_text(encoding="utf-8").lower()
    assert "vela." not in runtime_text
    assert "solace." not in runtime_text

    opportunities = {
        opportunity["id"]: opportunity
        for opportunity in definition["campaignOpportunities"]
    }
    assert set(opportunities) == {VELA_OPPORTUNITY, SOLACE_OPPORTUNITY}
    for opportunity in opportunities.values():
        assert set(opportunity).isdisjoint(
            {
                "actionTypeId",
                "effectTypeId",
                "evidenceId",
                "factId",
                "forcedActorId",
            }
        )
        assert opportunity["windowDuration"] == 3


def test_selecting_vela_activates_only_vela_and_leaves_solace_viable(
    director_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = director_report
    scenario = report["velaA"]
    result = scenario["result"]
    states = _states_by_id(result["state"])

    assert result["receipt"]["operation"] == "activate"
    assert result["receipt"]["opportunityId"] == VELA_OPPORTUNITY
    assert result["receipt"]["canonicalRevision"] == 0
    assert result["receipt"]["expiresAt"] == 4
    assert states[VELA_OPPORTUNITY]["status"] == "active"
    assert states[SOLACE_OPPORTUNITY]["status"] == "available"
    assert set(result["beliefUpdatesByActor"]) == VELA_ACTORS

    before = scenario["before"]
    after = result["state"]
    assert before["canonicalState"] == after["canonicalState"]
    assert before["proposals"] == after["proposals"]
    assert before["outcomes"] == after["outcomes"]
    assert before["receipts"] == after["receipts"]


def test_selecting_solace_activates_only_solace_and_leaves_vela_viable(
    director_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = director_report
    result = report["solaceA"]["result"]
    states = _states_by_id(result["state"])

    assert result["receipt"]["opportunityId"] == SOLACE_OPPORTUNITY
    assert states[SOLACE_OPPORTUNITY]["status"] == "active"
    assert states[VELA_OPPORTUNITY]["status"] == "available"
    assert set(result["beliefUpdatesByActor"]) == SOLACE_ACTORS


def test_director_emits_only_fixed_opportunity_observations(
    director_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    definition, report = director_report
    result = report["velaA"]["result"]
    observations = {
        observation["id"]: observation
        for observation in result["state"]["observations"]
    }

    assert set(result["observationIds"]) == set(result["receipt"]["observationIds"])
    for observation_id in result["observationIds"]:
        proposition = observations[observation_id]["proposition"]
        assert proposition == {
            "predicate": DIRECTOR_PREDICATE,
            "arguments": [VELA_OPPORTUNITY, "system.vela-gate"],
            "value": True,
        }

    evidence_before = copy.deepcopy(definition["evidence"])
    assert evidence_before == definition["evidence"]


def test_route_selection_is_reversible_and_expiry_is_receipted(
    director_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = director_report
    sequence = report["reversible"]

    reversed_result = sequence["reversed"]
    assert reversed_result["receipt"]["operation"] == "deactivate"
    assert reversed_result["receipt"]["reason"] == "route-selection-reversed"
    assert reversed_result["opportunityState"]["status"] == "available"

    solace = sequence["solace"]
    assert solace["receipt"]["operation"] == "activate"
    assert solace["receipt"]["expiresAt"] == 6

    assert len(sequence["expired"]) == 1
    expiration = sequence["expired"][0]
    assert expiration["receipt"]["operation"] == "expire"
    assert expiration["receipt"]["reason"] == "authored-window-expired"
    assert expiration["opportunityState"]["status"] == "closed"

    states = _states_by_id(sequence["state"])
    assert states[VELA_OPPORTUNITY]["status"] == "available"
    assert states[SOLACE_OPPORTUNITY]["status"] == "closed"
    assert len(sequence["state"]["directorReceipts"]) == 4


def test_director_does_not_force_actor_actions(
    director_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = director_report
    for scenario_name in ("velaA", "solaceA"):
        scenario = report[scenario_name]
        before = scenario["before"]
        after = scenario["state"]
        assert before["receipts"] == after["receipts"]
        assert before["proposals"] == after["proposals"]
        assert before["outcomes"] == after["outcomes"]
        assert before["canonicalState"] == after["canonicalState"]


def test_unknown_duplicate_and_stale_selections_are_rejected(
    director_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = director_report
    invalid = report["invalidCases"]
    assert "No authored campaign opportunity" in invalid["unknownRoute"]
    assert "is active" in invalid["duplicateActivation"]
    assert "canonical revision is stale" in invalid["staleRevision"]


def test_director_replay_and_save_restore_are_deterministic(
    director_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = director_report
    assert report["velaA"] == report["velaB"]
    assert report["solaceA"] == report["solaceB"]
    assert report["restored"]["resultA"] == report["restored"]["resultB"]
    assert report["restored"]["stateA"] == report["restored"]["stateB"]


def test_v5_state_migrates_to_authored_available_opportunities(
    director_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = director_report
    expected_states = [
        {
            "opportunityId": VELA_OPPORTUNITY,
            "status": "available",
            "activatedAt": None,
            "expiresAt": None,
            "activationReceiptId": None,
            "activationCount": 0,
        },
        {
            "opportunityId": SOLACE_OPPORTUNITY,
            "status": "available",
            "activatedAt": None,
            "expiresAt": None,
            "activationReceiptId": None,
            "activationCount": 0,
        },
    ]
    for state in report["migrated"].values():
        assert state["stateVersion"] == "game.strategicAI.state.v8"
        assert state["campaignOpportunityStates"] == expected_states
        assert state["directorReceipts"] == []


def test_closed_schema_rejects_action_or_evidence_controls() -> None:
    definition = _load_definition()
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    broken = copy.deepcopy(definition)
    broken["campaignOpportunities"][0]["actionTypeId"] = (
        "action.vela.move-patrol-to-chiron"
    )
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(broken)

    problems = validate_strategic_ai_definition(broken)
    assert any("forbidden actor or evidence controls" in problem for problem in problems)


def test_generated_director_states_remain_schema_and_reference_valid(
    director_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    definition, report = director_report
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)

    for state in (
        report["velaA"]["state"],
        report["solaceA"]["state"],
        report["reversible"]["state"],
        report["restored"]["stateA"],
    ):
        updated = copy.deepcopy(definition)
        updated["stateDefaults"] = state
        Draft202012Validator(schema).validate(updated)
        assert validate_strategic_ai_definition(updated) == []
