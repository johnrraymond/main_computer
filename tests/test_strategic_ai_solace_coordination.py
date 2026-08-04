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
SOCIAL_RUNTIME = (
    ROOT / "main_computer" / "web" / "applications" / "scripts"
    / "strategic-ai-social-runtime.js"
)
COMMITMENT_RUNTIME = (
    ROOT / "main_computer" / "web" / "applications" / "scripts"
    / "strategic-ai-commitment-runtime.js"
)
COORDINATOR_RUNTIME = (
    ROOT / "main_computer" / "web" / "applications" / "scripts"
    / "strategic-ai-coordinator.js"
)
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
PROJECT_PATH = ROOT / "game_projects" / "webgl-demo" / "project.json"
SCHEMA_PATH = ROOT / "game_projects" / "schema" / "strategic-ai.v1.schema.json"

HAVEN = "actor.solace.haven-coordinator"
OSPREY = "actor.solace.osprey-captain"
LYRIA = "actor.solace.lyria-medic"
PROMISE = "commitment.solace.shuttle-to-osprey"


def _load_definition() -> dict[str, Any]:
    project = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))
    return project["metadata"]["strategicAI"]


def _run_node(definition: dict[str, Any]) -> dict[str, Any]:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; Solace coordination tests cannot run")

    script = r"""
const fs = require("fs");
const cognition = require(process.argv[1]);
const actions = require(process.argv[2]);
const social = require(process.argv[3]);
const commitments = require(process.argv[4]);
const coordinatorApi = require(process.argv[5]);
const definition = JSON.parse(fs.readFileSync(0, "utf8"));

function createCoordinator(state = undefined) {
  return coordinatorApi.create(definition, {
    state,
    seed: 6206,
    cognitionApi: cognition,
    actionApi: actions,
    socialApi: social,
    commitmentApi: commitments
  });
}

function createPromise(coordinator) {
  return coordinator.createCommitment(
    "commitment.solace.shuttle-to-osprey",
    "actor.solace.haven-coordinator",
    "actor.solace.osprey-captain",
    {createdAt: 1}
  );
}

function keptScenario(state = undefined) {
  const coordinator = createCoordinator(state);
  const commitment = createPromise(coordinator);
  const allocation = coordinator.runTurn(
    "actor.solace.haven-coordinator",
    {proposalOptions: {createdAt: 2}}
  );
  const osprey = coordinator.runTurn(
    "actor.solace.osprey-captain",
    {proposalOptions: {createdAt: 3}}
  );
  const beforeRejected = coordinator.snapshot();
  const lyriaAfter = coordinator.runTurn(
    "actor.solace.lyria-medic",
    {proposalOptions: {createdAt: 4}}
  );
  const afterRejected = coordinator.snapshot();
  return {
    commitment,
    allocation,
    osprey,
    lyriaAfter,
    beforeRejected,
    afterRejected,
    state: coordinator.snapshot()
  };
}

function brokenScenario(state = undefined) {
  const coordinator = createCoordinator(state);
  const commitment = createPromise(coordinator);
  const lyria = coordinator.runTurn(
    "actor.solace.lyria-medic",
    {proposalOptions: {createdAt: 2}}
  );
  const osprey = coordinator.runTurn(
    "actor.solace.osprey-captain",
    {proposalOptions: {createdAt: 3}}
  );
  const beforeRejected = coordinator.snapshot();
  const havenAfter = coordinator.runTurn(
    "actor.solace.haven-coordinator",
    {proposalOptions: {createdAt: 4}}
  );
  const afterRejected = coordinator.snapshot();
  return {
    commitment,
    lyria,
    osprey,
    havenAfter,
    beforeRejected,
    afterRejected,
    state: coordinator.snapshot()
  };
}

function restoredKeptScenario() {
  const original = createCoordinator();
  createPromise(original);
  const saved = original.snapshot();

  const a = createCoordinator(saved);
  const b = createCoordinator(saved);
  const turnA = a.runTurn(
    "actor.solace.haven-coordinator",
    {proposalOptions: {createdAt: 2}}
  );
  const turnB = b.runTurn(
    "actor.solace.haven-coordinator",
    {proposalOptions: {createdAt: 2}}
  );
  return {
    turnA,
    turnB,
    stateA: a.snapshot(),
    stateB: b.snapshot()
  };
}

function migrationScenario() {
  const legacy = JSON.parse(JSON.stringify(definition.stateDefaults));
  legacy.stateVersion = "game.strategicAI.state.v4";
  delete legacy.commitments;
  delete legacy.cooperationModels;

  const migrated = {
    cognition: cognition.create(definition, {state: legacy, seed: 1}).snapshot(),
    action: actions.create(definition, {state: legacy}).snapshot(),
    social: social.create(definition, {state: legacy}).snapshot(),
    commitment: commitments.create(definition, {state: legacy}).snapshot(),
    coordinator: createCoordinator(legacy).snapshot()
  };
  return migrated;
}

function invalidCommitmentScenario() {
  const runtime = commitments.create(definition);
  let unauthorized = "";
  try {
    runtime.createCommitment(
      "commitment.solace.shuttle-to-osprey",
      "actor.solace.lyria-medic",
      "actor.solace.osprey-captain"
    );
  } catch (error) {
    unauthorized = error instanceof Error ? error.message : String(error);
  }
  return {unauthorized};
}

const keptA = keptScenario();
const keptB = keptScenario();
const brokenA = brokenScenario();
const brokenB = brokenScenario();
const restored = restoredKeptScenario();
const migration = migrationScenario();
const invalid = invalidCommitmentScenario();

process.stdout.write(JSON.stringify({
  api: {
    schema: commitments.SCHEMA,
    definitionVersion: commitments.DEFINITION_VERSION,
    stateVersion: commitments.STATE_VERSION,
    legacyStateVersions: commitments.LEGACY_STATE_VERSIONS
  },
  keptA,
  keptB,
  brokenA,
  brokenB,
  restored,
  migration,
  invalid
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
            str(COORDINATOR_RUNTIME),
        ],
        input=json.dumps(definition),
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def solace_report() -> tuple[dict[str, Any], dict[str, Any]]:
    definition = _load_definition()
    return definition, _run_node(definition)


def test_commitment_runtime_loads_before_coordinator() -> None:
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
    commitment = html.index(
        "<!-- @include applications/scripts/strategic-ai-commitment-runtime.js -->"
    )
    coordinator = html.index(
        "<!-- @include applications/scripts/strategic-ai-coordinator.js -->"
    )
    scene = html.index("<!-- @include applications/scripts/scene-viewer.js -->")
    assert cognition < action < social < commitment < coordinator < scene


def test_strategic_scripts_parse_with_node() -> None:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; strategic runtime syntax cannot be checked")
    for path in (
        COGNITION_RUNTIME,
        ACTION_RUNTIME,
        SOCIAL_RUNTIME,
        COMMITMENT_RUNTIME,
        COORDINATOR_RUNTIME,
    ):
        subprocess.run(
            [node, "--check", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )


def test_solace_definition_is_authored_without_core_branches(
    solace_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    definition, report = solace_report
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
    }

    actor_ids = {
        actor["id"]
        for actor in definition["actors"]
        if actor["id"].startswith("actor.solace.")
    }
    assert actor_ids == {HAVEN, OSPREY, LYRIA}
    assert len(definition["commitmentTypes"]) == 1
    assert len(definition["cooperationProfiles"]) == 1
    assert "solace." not in COMMITMENT_RUNTIME.read_text(encoding="utf-8").lower()
    assert "vela." not in COMMITMENT_RUNTIME.read_text(encoding="utf-8").lower()


def test_kept_promise_consumes_shuttle_and_increases_cooperation(
    solace_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = solace_report
    scenario = report["keptA"]
    allocation = scenario["allocation"]
    resolution = allocation["commitmentResolutions"][0]["commitment"]

    assert allocation["outcome"]["status"] == "accepted"
    assert resolution["status"] == "kept"
    assert resolution["resolutionReason"] == "promised-action-committed"
    assert len(resolution["observationIds"]) == 2
    assert allocation["state"]["cooperationModels"][0]["trust"] == 0.91
    assert allocation["outcome"]["consumedResources"] == [
        {"resourceId": "resource.solace.rescue-shuttle", "amount": 1}
    ]

    osprey = scenario["osprey"]
    assert osprey["commitmentMetrics"]["commitmentTrust"] == 0.91
    assert (
        osprey["decision"]["selectedActionTypeId"]
        == "action.solace.share-osprey-manifests"
    )


def test_competing_lyria_plan_breaks_promise_and_reduces_cooperation(
    solace_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = solace_report
    scenario = report["brokenA"]
    lyria = scenario["lyria"]
    resolution = lyria["commitmentResolutions"][0]["commitment"]

    assert lyria["outcome"]["status"] == "accepted"
    assert resolution["status"] == "broken"
    assert resolution["resolutionReason"] == "pledged-resource-diverted"
    assert lyria["state"]["cooperationModels"][0]["trust"] == 0.11

    osprey = scenario["osprey"]
    assert osprey["commitmentMetrics"]["commitmentTrust"] == 0.11
    assert (
        osprey["decision"]["selectedActionTypeId"]
        == "action.solace.withhold-osprey-manifests"
    )


def test_intervention_order_exhausts_resource_and_changes_commitment_outcome(
    solace_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = solace_report
    kept = report["keptA"]
    broken = report["brokenA"]

    assert kept["allocation"]["commitmentResolutions"][0]["commitment"]["status"] == "kept"
    assert broken["lyria"]["commitmentResolutions"][0]["commitment"]["status"] == "broken"

    assert kept["lyriaAfter"]["outcome"]["status"] == "rejected"
    assert kept["lyriaAfter"]["outcome"]["rejectionCode"] == "resource-unavailable"
    assert broken["havenAfter"]["outcome"]["status"] == "rejected"
    assert broken["havenAfter"]["outcome"]["rejectionCode"] == "resource-unavailable"

    assert (
        kept["beforeRejected"]["canonicalState"]
        == kept["afterRejected"]["canonicalState"]
    )
    assert (
        broken["beforeRejected"]["canonicalState"]
        == broken["afterRejected"]["canonicalState"]
    )


def test_commitment_observations_update_beliefs_without_fabricating_canon(
    solace_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    definition, report = solace_report
    kept = report["keptA"]["allocation"]
    broken = report["brokenA"]["lyria"]

    assert OSPREY in kept["resultingBeliefUpdatesByActor"]
    assert OSPREY in broken["resultingBeliefUpdatesByActor"]
    assert any(
        belief["proposition"]["predicate"]
        == "predicate.solace.shuttle-promise-kept"
        and belief["proposition"]["value"] is True
        for belief in kept["resultingBeliefUpdatesByActor"][OSPREY]
    )
    assert any(
        belief["proposition"]["predicate"]
        == "predicate.solace.shuttle-promise-kept"
        and belief["proposition"]["value"] is False
        for belief in broken["resultingBeliefUpdatesByActor"][OSPREY]
    )

    canonical_ids = {
        fact["id"]: fact["proposition"]["value"]
        for fact in definition["facts"]
    }
    assert "fact.solace.shuttle-promise-kept" not in canonical_ids


def test_commitment_replay_and_save_restore_are_deterministic(
    solace_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = solace_report
    assert report["keptA"] == report["keptB"]
    assert report["brokenA"] == report["brokenB"]
    assert report["restored"]["turnA"] == report["restored"]["turnB"]
    assert report["restored"]["stateA"] == report["restored"]["stateB"]


def test_v4_state_migrates_to_empty_commitments_and_authored_trust_model(
    solace_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = solace_report
    for state in report["migration"].values():
        assert state["stateVersion"] == "game.strategicAI.state.v8"
        assert state["commitments"] == []
        assert state["cooperationModels"] == [
            {
                "modelId": "cooperation-model.runtime.actor.solace.osprey-captain",
                "profileId": "cooperation.solace.osprey-trusts-haven",
                "holderActorId": OSPREY,
                "subjectActorId": HAVEN,
                "trust": 0.55,
                "commitmentIds": [],
                "updatedAt": 0,
            }
        ]


def test_unauthorized_actor_cannot_make_solace_promise(
    solace_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition, report = solace_report
    assert "cannot make commitment" in report["invalid"]["unauthorized"]


def test_generated_solace_states_remain_schema_and_reference_valid(
    solace_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    definition, report = solace_report
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)

    for state in (
        report["keptA"]["state"],
        report["brokenA"]["state"],
        report["restored"]["stateA"],
    ):
        updated = copy.deepcopy(definition)
        updated["stateDefaults"] = state
        Draft202012Validator(schema).validate(updated)
        assert validate_strategic_ai_definition(updated) == []
