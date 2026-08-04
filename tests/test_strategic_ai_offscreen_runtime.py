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
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"
COGNITION_RUNTIME = SCRIPTS / "strategic-ai-runtime.js"
ACTION_RUNTIME = SCRIPTS / "strategic-ai-action-runtime.js"
SOCIAL_RUNTIME = SCRIPTS / "strategic-ai-social-runtime.js"
COMMITMENT_RUNTIME = SCRIPTS / "strategic-ai-commitment-runtime.js"
DIRECTOR_RUNTIME = SCRIPTS / "strategic-ai-director-runtime.js"
COMMUNICATION_RUNTIME = SCRIPTS / "strategic-ai-communication-runtime.js"
COORDINATOR_RUNTIME = SCRIPTS / "strategic-ai-coordinator.js"
OFFSCREEN_RUNTIME = SCRIPTS / "strategic-ai-offscreen-runtime.js"
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
PROJECT_PATH = ROOT / "game_projects" / "webgl-demo" / "project.json"
SCHEMA_PATH = ROOT / "game_projects" / "schema" / "strategic-ai.v1.schema.json"


def _definition() -> dict[str, Any]:
    project = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))
    return project["metadata"]["strategicAI"]


def _run_node(definition: dict[str, Any]) -> dict[str, Any]:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; off-screen runtime tests cannot run")

    script = r"""
const fs = require("fs");
const cognition = require(process.argv[1]);
const actions = require(process.argv[2]);
const social = require(process.argv[3]);
const commitments = require(process.argv[4]);
const director = require(process.argv[5]);
const communication = require(process.argv[6]);
const coordinator = require(process.argv[7]);
const offscreen = require(process.argv[8]);
const definition = JSON.parse(fs.readFileSync(0, "utf8"));

function create(state = undefined, customDefinition = definition) {
  return offscreen.create(customDefinition, {
    state,
    seed: 9901,
    coordinatorApi: coordinator
  });
}

function runVela() {
  const runtime = create();
  const early = runtime.simulateUntil(2, {
    activeSystemId: "system.solace-reach",
    budget: 99
  });
  const rumor = runtime.simulateUntil(3, {
    activeSystemId: "system.solace-reach",
    budget: 1
  });
  const briefing = runtime.simulateUntil(4, {
    activeSystemId: "system.solace-reach",
    budget: 1
  });
  return {
    early,
    rumor,
    briefing,
    summary: runtime.getReturnSummary("system.vela-gate"),
    state: runtime.snapshot()
  };
}

function runSolace() {
  const runtime = create();
  const first = runtime.simulateUntil(3, {
    activeSystemId: "system.vela-gate",
    budget: 2
  });
  const second = runtime.simulateUntil(3, {
    activeSystemId: "system.vela-gate",
    budget: 4
  });
  return {
    first,
    second,
    summary: runtime.getReturnSummary("system.solace-reach"),
    state: runtime.snapshot()
  };
}

function saveRestore() {
  const initial = create();
  initial.simulateUntil(2, {
    activeSystemId: "system.solace-reach",
    budget: 4
  });
  const saved = initial.snapshot();
  const left = create(saved);
  const right = create(saved);
  const leftTurn = left.simulateUntil(4, {
    activeSystemId: "system.solace-reach",
    budget: 2
  });
  const rightTurn = right.simulateUntil(4, {
    activeSystemId: "system.solace-reach",
    budget: 2
  });
  return {
    leftTurn,
    rightTurn,
    leftState: left.snapshot(),
    rightState: right.snapshot()
  };
}

function activeSystemSkip() {
  const runtime = create();
  const result = runtime.simulateUntil(4, {
    activeSystemId: "system.vela-gate",
    budget: 4
  });
  return {result, state: runtime.snapshot()};
}

function migrateV7() {
  const legacy = JSON.parse(JSON.stringify(definition.stateDefaults));
  legacy.stateVersion = "game.strategicAI.state.v7";
  delete legacy.offscreenSimulationTime;
  delete legacy.offscreenStepStates;
  delete legacy.offscreenSimulationReceipts;
  return {
    cognition: cognition.create(definition, {state: legacy, seed: 1}).snapshot(),
    action: actions.create(definition, {state: legacy}).snapshot(),
    social: social.create(definition, {state: legacy}).snapshot(),
    commitment: commitments.create(definition, {state: legacy}).snapshot(),
    director: director.create(definition, {state: legacy}).snapshot(),
    communication: communication.create(definition, {state: legacy}).snapshot(),
    coordinator: coordinator.create(definition, {state: legacy, seed: 1}).snapshot(),
    offscreen: create(legacy).snapshot()
  };
}

function protectedSchedule() {
  const invalid = JSON.parse(JSON.stringify(definition));
  const actor = invalid.actors.find(
    (record) => record.id === "actor.fixture.watch-officer"
  );
  actor.candidateActionTypeIds.push("action.fixture.disable-relay");
  const profile = invalid.policyProfiles.find(
    (record) => record.id === actor.policyProfileId
  );
  profile.actionPolicies["action.fixture.disable-relay"] = {
    baseScore: 5,
    weights: {}
  };
  invalid.offscreenSchedules = [{
    id: "offscreen-schedule.fixture.protected",
    label: "Protected fixture schedule",
    systemId: "system.solace-reach",
    steps: [{
      id: "offscreen-step.fixture.disable-relay",
      kind: "actor-turn",
      dueAt: 1,
      cost: 1,
      deadlineAt: null,
      actorId: actor.id,
      allowedActionTypeIds: ["action.fixture.disable-relay"],
      description: "Synthetic protected step for boundary proof."
    }],
    description: "Synthetic schedule used only by the test."
  }];
  invalid.stateDefaults.offscreenStepStates =
    offscreen.defaultOffscreenStepStates(invalid);
  invalid.stateDefaults.offscreenSimulationReceipts = [];
  invalid.stateDefaults.offscreenSimulationTime = 0;

  let invalidCode = "";
  let invalidDetails = [];
  try {
    create(undefined, invalid);
  } catch (error) {
    invalidCode = error.code || "";
    invalidDetails = error.details || [];
  }

  const valid = JSON.parse(JSON.stringify(invalid));
  const validActor = valid.actors.find(
    (record) => record.id === "actor.fixture.watch-officer"
  );
  validActor.authorityIds.push("authority.fixture.protected-relay-control");
  valid.offscreenSchedules[0].steps[0].deadlineAt = 5;
  valid.stateDefaults.offscreenStepStates =
    offscreen.defaultOffscreenStepStates(valid);

  const runtime = create(undefined, valid);
  const beforeDeadline = runtime.simulateUntil(4, {
    activeSystemId: "system.vela-gate",
    budget: 4
  });
  const atDeadline = runtime.simulateUntil(5, {
    activeSystemId: "system.vela-gate",
    budget: 4
  });
  return {
    invalidCode,
    invalidDetails,
    beforeDeadline,
    atDeadline,
    state: runtime.snapshot()
  };
}

const velaA = runVela();
const velaB = runVela();
const solaceA = runSolace();
const solaceB = runSolace();

process.stdout.write(JSON.stringify({
  api: {
    schema: offscreen.SCHEMA,
    definitionVersion: offscreen.DEFINITION_VERSION,
    stateVersion: offscreen.STATE_VERSION,
    legacyStateVersions: offscreen.LEGACY_STATE_VERSIONS
  },
  velaA,
  velaB,
  solaceA,
  solaceB,
  saveRestore: saveRestore(),
  activeSystemSkip: activeSystemSkip(),
  migration: migrateV7(),
  protected: protectedSchedule()
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
            str(COMMUNICATION_RUNTIME),
            str(COORDINATOR_RUNTIME),
            str(OFFSCREEN_RUNTIME),
        ],
        input=json.dumps(definition),
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def offscreen_report() -> tuple[dict[str, Any], dict[str, Any]]:
    definition = _definition()
    return definition, _run_node(definition)


def test_offscreen_runtime_loads_after_coordinator() -> None:
    html = APPLICATIONS_HTML.read_text(encoding="utf-8")
    coordinator = html.index(
        "<!-- @include applications/scripts/strategic-ai-coordinator.js -->"
    )
    offscreen = html.index(
        "<!-- @include applications/scripts/strategic-ai-offscreen-runtime.js -->"
    )
    scene = html.index("<!-- @include applications/scripts/scene-viewer.js -->")
    assert coordinator < offscreen < scene


def test_all_strategic_scripts_parse() -> None:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; strategic runtime syntax cannot be checked")
    for path in (
        COGNITION_RUNTIME,
        ACTION_RUNTIME,
        SOCIAL_RUNTIME,
        COMMITMENT_RUNTIME,
        DIRECTOR_RUNTIME,
        COMMUNICATION_RUNTIME,
        COORDINATOR_RUNTIME,
        OFFSCREEN_RUNTIME,
    ):
        subprocess.run(
            [node, "--check", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )


def test_offscreen_contract_is_generic_and_budgeted(
    offscreen_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    definition, report = offscreen_report
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
    assert definition["offscreenSimulationBudget"] == 4
    assert {record["id"] for record in definition["offscreenSchedules"]} == {
        "offscreen-schedule.vela-gate-opening",
        "offscreen-schedule.solace-reach-relief",
    }
    runtime_text = OFFSCREEN_RUNTIME.read_text(encoding="utf-8").lower()
    assert "vela." not in runtime_text
    assert "solace." not in runtime_text


def test_active_system_is_not_simulated(
    offscreen_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = offscreen_report
    skipped = report["activeSystemSkip"]
    receipt = skipped["result"]["receipt"]
    assert receipt["skippedScheduleIds"] == [
        "offscreen-schedule.vela-gate-opening"
    ]
    vela_states = [
        state
        for state in skipped["state"]["offscreenStepStates"]
        if state["scheduleId"] == "offscreen-schedule.vela-gate-opening"
    ]
    assert {state["status"] for state in vela_states} == {"pending"}


def test_budget_caps_work_and_defers_expensive_step(
    offscreen_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = offscreen_report
    vela = report["velaA"]
    assert vela["early"]["receipt"]["budget"] == 4
    assert vela["early"]["receipt"]["consumedBudget"] == 3

    solace = report["solaceA"]
    assert solace["first"]["receipt"]["budget"] == 2
    assert solace["first"]["receipt"]["consumedBudget"] == 2
    assert solace["first"]["receipt"]["deferredStepIds"] == [
        "offscreen-step.solace.allocate-shuttle"
    ]
    assert solace["second"]["receipt"]["processedStepIds"] == [
        "offscreen-step.solace.allocate-shuttle"
    ]


def test_reports_arrive_only_after_authored_latency(
    offscreen_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = offscreen_report
    vela = report["velaA"]
    assert vela["early"]["state"]["reports"] == []
    assert vela["rumor"]["receipt"]["processedStepIds"] == [
        "offscreen-step.vela.rumor-reaches-official"
    ]
    delivered = vela["state"]["reports"][0]
    assert delivered["sentAt"] == 1
    assert delivered["receivedAt"] == 3
    assert delivered["routeId"] == "route.vela.rumor-chain"


def test_verified_boundaries_create_explainable_records(
    offscreen_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = offscreen_report
    vela = report["velaA"]["state"]
    assert len(vela["directorReceipts"]) == 1
    assert len(vela["outcomes"]) == 1
    assert vela["outcomes"][0]["status"] == "accepted"
    assert len(vela["reports"]) == 1

    solace = report["solaceA"]["state"]
    assert len(solace["commitments"]) == 1
    assert solace["commitments"][0]["status"] == "kept"
    assert len(solace["outcomes"]) == 1
    assert solace["outcomes"][0]["status"] == "accepted"


def test_return_summary_explains_offscreen_changes(
    offscreen_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = offscreen_report
    for key, system_id in (
        ("velaA", "system.vela-gate"),
        ("solaceA", "system.solace-reach"),
    ):
        summary = report[key]["summary"]
        assert summary["systemId"] == system_id
        assert summary["canonicalRevision"] == 1
        steps = summary["schedules"][0]["steps"]
        assert steps
        assert {step["status"] for step in steps} == {"completed"}
        for step in steps:
            assert step["description"]
            assert step["reason"]
            assert step["resultIds"]


def test_replay_and_save_restore_are_deterministic(
    offscreen_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = offscreen_report
    assert report["velaA"] == report["velaB"]
    assert report["solaceA"] == report["solaceB"]
    restored = report["saveRestore"]
    assert restored["leftTurn"] == restored["rightTurn"]
    assert restored["leftState"] == restored["rightState"]


def test_protected_steps_require_authority_and_deadline(
    offscreen_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = offscreen_report
    protected = report["protected"]
    assert protected["invalidCode"] == "schedule-invalid"
    joined = "\n".join(protected["invalidDetails"])
    assert "requires an explicit deadline" in joined
    assert "lacks authorities" in joined
    assert protected["beforeDeadline"]["receipt"]["processedStepIds"] == []
    assert protected["atDeadline"]["receipt"]["processedStepIds"] == [
        "offscreen-step.fixture.disable-relay"
    ]
    assert protected["state"]["outcomes"][0]["status"] == "accepted"
    assert protected["state"]["canonicalState"]["revision"] == 1


def test_v7_state_migrates_to_authored_pending_schedule(
    offscreen_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    definition, report = offscreen_report
    expected_ids = {
        step["id"]
        for schedule in definition["offscreenSchedules"]
        for step in schedule["steps"]
    }
    for state in report["migration"].values():
        assert state["stateVersion"] == "game.strategicAI.state.v8"
        assert state["offscreenSimulationTime"] == 0
        assert state["offscreenSimulationReceipts"] == []
        assert {
            record["stepId"] for record in state["offscreenStepStates"]
        } == expected_ids
        assert {record["status"] for record in state["offscreenStepStates"]} == {
            "pending"
        }


def test_generated_states_remain_schema_and_reference_valid(
    offscreen_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    definition, report = offscreen_report
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)

    for state in (
        report["velaA"]["state"],
        report["solaceA"]["state"],
        report["saveRestore"]["leftState"],
    ):
        updated = copy.deepcopy(definition)
        updated["stateDefaults"] = state
        Draft202012Validator(schema).validate(updated)
        assert validate_strategic_ai_definition(updated) == []
