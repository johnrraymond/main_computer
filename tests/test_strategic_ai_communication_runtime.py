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
SCRIPT_ROOT = ROOT / "main_computer" / "web" / "applications" / "scripts"
COGNITION_RUNTIME = SCRIPT_ROOT / "strategic-ai-runtime.js"
ACTION_RUNTIME = SCRIPT_ROOT / "strategic-ai-action-runtime.js"
SOCIAL_RUNTIME = SCRIPT_ROOT / "strategic-ai-social-runtime.js"
COMMITMENT_RUNTIME = SCRIPT_ROOT / "strategic-ai-commitment-runtime.js"
DIRECTOR_RUNTIME = SCRIPT_ROOT / "strategic-ai-director-runtime.js"
COMMUNICATION_RUNTIME = SCRIPT_ROOT / "strategic-ai-communication-runtime.js"
COORDINATOR_RUNTIME = SCRIPT_ROOT / "strategic-ai-coordinator.js"
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
PROJECT_PATH = ROOT / "game_projects" / "webgl-demo" / "project.json"
SCHEMA_PATH = ROOT / "game_projects" / "schema" / "strategic-ai.v1.schema.json"

SURVIVOR = "actor.vela.survivor"
ORGANIZER = "actor.vela.rescue-organizer"
HAVEN = "actor.solace.haven-coordinator"
OSPREY = "actor.solace.osprey-captain"
SURVIVOR_INTENT = "communicative-intent.vela.survivor-report-sabotage"
PROMISE_INTENT = "communicative-intent.solace.haven-confirm-shuttle-promise"
PROMISE_TYPE = "commitment.solace.shuttle-to-osprey"


def _definition() -> dict[str, Any]:
    project = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))
    return project["metadata"]["strategicAI"]


def _run_node(definition: dict[str, Any]) -> dict[str, Any]:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; communication runtime tests cannot run")

    script = r"""
const fs = require("fs");
const cognition = require(process.argv[1]);
const actions = require(process.argv[2]);
const social = require(process.argv[3]);
const commitments = require(process.argv[4]);
const director = require(process.argv[5]);
const communication = require(process.argv[6]);
const coordinatorApi = require(process.argv[7]);
const definition = JSON.parse(fs.readFileSync(0, "utf8"));

function coordinator(state = undefined) {
  return coordinatorApi.create(definition, {
    state,
    seed: 808,
    cognitionApi: cognition,
    actionApi: actions,
    socialApi: social,
    commitmentApi: commitments,
    directorApi: director,
    communicationApi: communication
  });
}

function performDisabled(instance) {
  return instance.performCommunication(
    "communicative-intent.vela.survivor-report-sabotage",
    "actor.vela.survivor",
    ["actor.vela.rescue-organizer"],
    {disableModel: true}
  );
}

const primary = coordinator();
const initialState = primary.snapshot();
const disabled = performDisabled(primary);

let validRequest = null;
const valid = primary.performCommunication(
  "communicative-intent.vela.survivor-report-sabotage",
  "actor.vela.survivor",
  ["actor.vela.rescue-organizer"],
  {
    modelAdapter: {
      selectTemplate(request) {
        validRequest = request;
        return {templateId: "speech-template.vela.survivor-sabotage-urgent"};
      }
    }
  }
);

let unsafeRequest = null;
const unsafe = primary.performCommunication(
  "communicative-intent.vela.survivor-report-sabotage",
  "actor.vela.survivor",
  ["actor.vela.rescue-organizer"],
  {
    modelAdapter: {
      selectTemplate(request) {
        unsafeRequest = request;
        return {templateId: "speech-template.vela.survivor-secret-unsafe"};
      }
    }
  }
);

const rawText = primary.performCommunication(
  "communicative-intent.vela.survivor-report-sabotage",
  "actor.vela.survivor",
  ["actor.vela.rescue-organizer"],
  {
    modelAdapter: {
      selectTemplate() {
        return {
          templateId: "speech-template.vela.survivor-sabotage-urgent",
          text: "The rescue organizer is compromised."
        };
      }
    }
  }
);

const failedAdapter = primary.performCommunication(
  "communicative-intent.vela.survivor-report-sabotage",
  "actor.vela.survivor",
  ["actor.vela.rescue-organizer"],
  {
    modelAdapter: {
      selectTemplate() {
        throw new Error("model unavailable");
      }
    }
  }
);

let missingCommitmentCode = "";
try {
  primary.performCommunication(
    "communicative-intent.solace.haven-confirm-shuttle-promise",
    "actor.solace.haven-coordinator",
    ["actor.solace.osprey-captain"],
    {disableModel: true}
  );
} catch (error) {
  missingCommitmentCode = error && error.code ? error.code : String(error);
}

const commitment = primary.createCommitment(
  "commitment.solace.shuttle-to-osprey",
  "actor.solace.haven-coordinator",
  "actor.solace.osprey-captain",
  {createdAt: 1}
);
const stateAfterCommitment = primary.snapshot();
const promise = primary.performCommunication(
  "communicative-intent.solace.haven-confirm-shuttle-promise",
  "actor.solace.haven-coordinator",
  ["actor.solace.osprey-captain"],
  {disableModel: true, commitmentId: commitment.commitmentId}
);
const stateAfterPromise = primary.snapshot();

let missingKnowledgeCode = "";
const missingKnowledgeState = JSON.parse(JSON.stringify(definition.stateDefaults));
missingKnowledgeState.beliefs = missingKnowledgeState.beliefs.filter(
  (record) => record.id !== "belief.vela.survivor.beacon-corruption-deliberate"
);
missingKnowledgeState.observations = missingKnowledgeState.observations.filter(
  (record) => record.id !== "observation.vela.survivor.beacon-sabotage"
);
try {
  coordinator(missingKnowledgeState).performCommunication(
    "communicative-intent.vela.survivor-report-sabotage",
    "actor.vela.survivor",
    ["actor.vela.rescue-organizer"],
    {disableModel: true}
  );
} catch (error) {
  missingKnowledgeCode = error && error.code ? error.code : String(error);
}

const replayA = performDisabled(coordinator());
const replayB = performDisabled(coordinator());

const legacyState = JSON.parse(JSON.stringify(definition.stateDefaults));
legacyState.stateVersion = "game.strategicAI.state.v6";
const migrated = {
  cognition: cognition.create(definition, {state: legacyState, seed: 1}).snapshot(),
  action: actions.create(definition, {state: legacyState}).snapshot(),
  social: social.create(definition, {state: legacyState}).snapshot(),
  commitment: commitments.create(definition, {state: legacyState}).snapshot(),
  director: director.create(definition, {state: legacyState}).snapshot(),
  communication: communication.create(definition, {state: legacyState}).snapshot(),
  coordinator: coordinator(legacyState).snapshot()
};

process.stdout.write(JSON.stringify({
  api: {
    schema: communication.SCHEMA,
    definitionVersion: communication.DEFINITION_VERSION,
    stateVersion: communication.STATE_VERSION,
    legacyStateVersions: communication.LEGACY_STATE_VERSIONS
  },
  disabled,
  valid,
  validRequest,
  unsafe,
  unsafeRequest,
  rawText,
  failedAdapter,
  missingCommitmentCode,
  commitment,
  promise,
  missingKnowledgeCode,
  replayA,
  replayB,
  initialState,
  stateAfterCommitment,
  stateAfterPromise,
  migrated
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
        ],
        input=json.dumps(definition),
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def communication_report() -> tuple[dict[str, Any], dict[str, Any]]:
    definition = _definition()
    return definition, _run_node(definition)


def test_communication_runtime_loads_before_coordinator() -> None:
    html = APPLICATIONS_HTML.read_text(encoding="utf-8")
    director = html.index(
        "<!-- @include applications/scripts/strategic-ai-director-runtime.js -->"
    )
    communication = html.index(
        "<!-- @include applications/scripts/strategic-ai-communication-runtime.js -->"
    )
    coordinator = html.index(
        "<!-- @include applications/scripts/strategic-ai-coordinator.js -->"
    )
    scene = html.index("<!-- @include applications/scripts/scene-viewer.js -->")
    assert director < communication < coordinator < scene


def test_all_strategic_scripts_parse() -> None:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; strategic syntax cannot be checked")
    for path in (
        COGNITION_RUNTIME,
        ACTION_RUNTIME,
        SOCIAL_RUNTIME,
        COMMITMENT_RUNTIME,
        DIRECTOR_RUNTIME,
        COMMUNICATION_RUNTIME,
        COORDINATOR_RUNTIME,
    ):
        subprocess.run(
            [node, "--check", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )


def test_communication_contract_is_generic_and_closed(
    communication_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    definition, report = communication_report
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
    assert len(definition["communicationClaims"]) == 4
    assert len(definition["communicativeIntents"]) == 3
    assert len(definition["speechTemplates"]) == 7
    runtime_text = COMMUNICATION_RUNTIME.read_text(encoding="utf-8").lower()
    assert "vela." not in runtime_text
    assert "solace." not in runtime_text


def test_model_disabled_uses_deterministic_fallback(
    communication_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = communication_report
    result = report["disabled"]
    assert result["mode"] == "fallback"
    assert result["fallbackReason"] == "model-disabled"
    assert result["templateId"] == "speech-template.vela.survivor-sabotage-plain"
    assert result["text"] == "I witnessed the beacon sequence was deliberately altered."
    assert result["claimIds"] == ["communication-claim.vela.beacon-sabotage"]


def test_safe_adapter_can_choose_only_an_authorized_template(
    communication_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = communication_report
    result = report["valid"]
    request = report["validRequest"]
    assert result["mode"] == "adapter"
    assert result["fallbackReason"] == ""
    assert result["templateId"] == "speech-template.vela.survivor-sabotage-urgent"
    assert request["safeTemplateIds"] == [
        "speech-template.vela.survivor-sabotage-plain",
        "speech-template.vela.survivor-sabotage-urgent",
    ]
    assert "speech-template.vela.survivor-secret-unsafe" not in request[
        "safeTemplateIds"
    ]


def test_unauthorized_secret_template_falls_back_without_disclosure(
    communication_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = communication_report
    result = report["unsafe"]
    assert result["mode"] == "fallback"
    assert result["fallbackReason"] == "adapter-template-unsafe"
    assert result["templateId"] == "speech-template.vela.survivor-sabotage-plain"
    assert "organizer is compromised" not in result["text"].lower()
    assert "communication-claim.vela.organizer-compromised" not in result["claimIds"]


def test_raw_model_text_and_adapter_failure_both_fall_back(
    communication_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = communication_report
    assert report["rawText"]["fallbackReason"] == "adapter-output-invalid"
    assert "compromised" not in report["rawText"]["text"].lower()
    assert report["failedAdapter"]["fallbackReason"] == "adapter-failed"
    assert (
        report["failedAdapter"]["templateId"]
        == "speech-template.vela.survivor-sabotage-plain"
    )


def test_claim_requires_sufficient_actor_knowledge(
    communication_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = communication_report
    assert report["missingKnowledgeCode"] == "claim-knowledge-insufficient"


def test_promise_wording_requires_structured_commitment(
    communication_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = communication_report
    assert report["missingCommitmentCode"] == "commitment-required"
    promise = report["promise"]
    commitment = report["commitment"]
    assert commitment["status"] == "pending"
    assert promise["speechAct"] == "promise"
    assert promise["structuredCommitmentIds"] == [commitment["commitmentId"]]
    assert "in force" in promise["text"]
    assert "Promise the rescue shuttle to Osprey" in promise["text"]


def test_wording_cannot_mutate_strategic_or_canonical_state(
    communication_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = communication_report
    assert report["stateAfterCommitment"] == report["stateAfterPromise"]
    assert (
        report["stateAfterCommitment"]["canonicalState"]
        == report["stateAfterPromise"]["canonicalState"]
    )
    assert report["initialState"]["canonicalState"] == report[
        "stateAfterPromise"
    ]["canonicalState"]
    assert report["stateAfterPromise"]["receipts"] == []
    assert report["stateAfterPromise"]["proposals"] == []
    assert report["stateAfterPromise"]["outcomes"] == []


def test_communication_replay_is_deterministic(
    communication_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = communication_report
    assert report["replayA"] == report["replayB"]


def test_v6_state_migrates_to_v7_without_fabricated_communications(
    communication_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = communication_report
    for state in report["migrated"].values():
        assert state["stateVersion"] == "game.strategicAI.state.v8"
        assert "communicationRecords" not in state


def test_definition_remains_schema_and_reference_valid(
    communication_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    definition, _report = communication_report
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(definition)
    assert validate_strategic_ai_definition(definition) == []

    broken = copy.deepcopy(definition)
    broken["speechTemplates"][0]["claimIds"] = [
        "communication-claim.vela.organizer-compromised"
    ]
    problems = validate_strategic_ai_definition(broken)
    assert any("claimIds do not match claim segments" in problem for problem in problems)
