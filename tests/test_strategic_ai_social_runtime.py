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
COORDINATOR_RUNTIME = (
    ROOT / "main_computer" / "web" / "applications" / "scripts"
    / "strategic-ai-coordinator.js"
)
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
PROJECT_PATH = ROOT / "game_projects" / "webgl-demo" / "project.json"
SCHEMA_PATH = ROOT / "game_projects" / "schema" / "strategic-ai.v1.schema.json"

OFFICIAL = "actor.vela.gate-official"
ORGANIZER = "actor.vela.rescue-organizer"
SURVIVOR = "actor.vela.survivor"


def _definition() -> dict[str, Any]:
    project = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))
    return project["metadata"]["strategicAI"]


def _run_node(definition: dict[str, Any]) -> dict[str, Any]:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; strategic AI social tests cannot run")

    script = r"""
const fs = require("fs");
const cognitionApi = require(process.argv[1]);
const actionApi = require(process.argv[2]);
const socialApi = require(process.argv[3]);
const coordinatorApi = require(process.argv[4]);
const definition = JSON.parse(fs.readFileSync(0, "utf8"));

const OFFICIAL = "actor.vela.gate-official";
const ORGANIZER = "actor.vela.rescue-organizer";
const SURVIVOR = "actor.vela.survivor";
const SOURCE_OBSERVATION = "observation.vela.organizer.partial-witness";

function canonicalValue(state, factId) {
  return state.canonicalState.factStates.find(
    (record) => record.factId === factId
  ).value;
}

function privateAndAncestryScenario() {
  const social = socialApi.create(definition);
  const canonicalBefore = social.snapshot().canonicalState;
  let publicError = "";
  try {
    social.propagatePublicObservation(ORGANIZER, SOURCE_OBSERVATION);
  } catch (error) {
    publicError = error instanceof Error ? error.message : String(error);
  }

  const direct = social.createReport(
    "route.vela.guild-direct",
    ORGANIZER,
    SURVIVOR,
    SOURCE_OBSERVATION,
    {sentAt: 4}
  );
  const distorted = social.createReport(
    "route.vela.rumor-chain",
    SURVIVOR,
    OFFICIAL,
    direct.observation.id,
    {
      sentAt: 6,
      distortion: 0.4,
      proposition: {
        predicate: "predicate.vela.beacon-corruption-deliberate",
        arguments: ["system.vela-gate"],
        value: false
      }
    }
  );
  const state = social.snapshot();
  const restored = socialApi.create(definition, {state}).snapshot();
  return {
    publicError,
    direct,
    distorted,
    canonicalBefore,
    canonicalAfter: state.canonicalState,
    state,
    restored,
    canonicalTruth: canonicalValue(
      state,
      "fact.vela.beacon-corruption-deliberate"
    )
  };
}

function publicCaptainScenario() {
  const cognition = cognitionApi.create(definition, {seed: 7201});
  const captainObservation = cognition.ingestObservation({
    id: "observation.vela.organizer.captain-public",
    observerId: ORGANIZER,
    proposition: {
      predicate: "predicate.vela.captain-retained-evidence",
      arguments: ["actor.captain", "evidence.vela.atlas-copy"],
      value: true
    },
    channelId: "channel.vela.captain-observation",
    sourceId: "source.vela.captain-telemetry",
    reliability: 0.96,
    observedAt: 8,
    visibility: "public"
  });
  const social = socialApi.create(definition, {state: cognition.snapshot()});
  const canonicalBefore = social.snapshot().canonicalState;
  const deliveries = social.propagatePublicObservation(
    ORGANIZER,
    captainObservation.id
  );
  const organizerModel = social.updateCaptainModel(
    ORGANIZER,
    [captainObservation.id]
  );
  const officialObservation = deliveries.find(
    (delivery) => delivery.observation.observerId === OFFICIAL
  ).observation;
  const survivorObservation = deliveries.find(
    (delivery) => delivery.observation.observerId === SURVIVOR
  ).observation;
  const officialModel = social.updateCaptainModel(
    OFFICIAL,
    [officialObservation.id]
  );
  const survivorModel = social.updateCaptainModel(
    SURVIVOR,
    [survivorObservation.id]
  );
  const state = social.snapshot();
  return {
    captainObservation,
    deliveries,
    organizerModel,
    officialModel,
    survivorModel,
    organizerMetrics: social.captainMetrics(ORGANIZER),
    officialMetrics: social.captainMetrics(OFFICIAL),
    survivorMetrics: social.captainMetrics(SURVIVOR),
    canonicalBefore,
    canonicalAfter: state.canonicalState,
    state
  };
}

function coordinatorScenario() {
  const baseline = coordinatorApi.create(definition, {seed: 7201}).runTurn(
    ORGANIZER
  );
  const coordinator = coordinatorApi.create(definition, {seed: 7201});
  const revised = coordinator.runTurn(ORGANIZER, {
    observations: [{
      id: "observation.vela.organizer.captain-coordinator-public",
      observerId: ORGANIZER,
      proposition: {
        predicate: "predicate.vela.captain-retained-evidence",
        arguments: ["actor.captain", "evidence.vela.atlas-copy"],
        value: true
      },
      channelId: "channel.vela.captain-observation",
      sourceId: "source.vela.captain-telemetry",
      reliability: 0.96,
      observedAt: 9,
      visibility: "public"
    }],
    publicObservations: [{
      senderActorId: ORGANIZER,
      observationId: "observation.vela.organizer.captain-coordinator-public"
    }]
  });
  return {baseline, revised};
}

function deterministicScenario() {
  function run() {
    const social = socialApi.create(definition);
    const first = social.createReport(
      "route.vela.guild-direct",
      ORGANIZER,
      SURVIVOR,
      SOURCE_OBSERVATION,
      {sentAt: 4}
    );
    const second = social.createReport(
      "route.vela.rumor-chain",
      SURVIVOR,
      OFFICIAL,
      first.observation.id,
      {
        sentAt: 6,
        distortion: 0.4,
        proposition: {
          predicate: "predicate.vela.beacon-corruption-deliberate",
          arguments: ["system.vela-gate"],
          value: false
        }
      }
    );
    social.updateCaptainModel(OFFICIAL, [second.observation.id]);
    return social.snapshot();
  }
  return {a: run(), b: run()};
}

function migrationScenario() {
  const legacy = JSON.parse(JSON.stringify(definition.stateDefaults));
  legacy.stateVersion = "game.strategicAI.state.v3";
  delete legacy.reports;
  delete legacy.captainModels;
  return {
    cognition: cognitionApi.create(definition, {state: legacy}).snapshot(),
    action: actionApi.create(definition, {state: legacy}).snapshot(),
    social: socialApi.create(definition, {state: legacy}).snapshot()
  };
}

process.stdout.write(JSON.stringify({
  api: {
    schema: socialApi.SCHEMA,
    definitionVersion: socialApi.DEFINITION_VERSION,
    stateVersion: socialApi.STATE_VERSION
  },
  privateAndAncestry: privateAndAncestryScenario(),
  publicCaptain: publicCaptainScenario(),
  coordinator: coordinatorScenario(),
  deterministic: deterministicScenario(),
  migration: migrationScenario()
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
            str(COORDINATOR_RUNTIME),
        ],
        input=json.dumps(definition),
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def social_report() -> tuple[dict[str, Any], dict[str, Any]]:
    definition = _definition()
    return definition, _run_node(definition)


def test_social_runtime_loads_before_coordinator_and_scene() -> None:
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


def test_social_runtime_scripts_parse_with_node() -> None:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; strategic AI social syntax cannot be checked")
    for path in (
        COGNITION_RUNTIME,
        ACTION_RUNTIME,
        SOCIAL_RUNTIME,
        COORDINATOR_RUNTIME,
    ):
        subprocess.run(
            [node, "--check", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )


def test_social_runtime_exports_v4_contract(
    social_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = social_report
    assert report["api"] == {
        "schema": "game.strategicAI.v1",
        "definitionVersion": "game.strategicAI.definition.v8",
        "stateVersion": "game.strategicAI.state.v8",
    }


def test_private_observation_requires_explicit_authorized_report(
    social_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = social_report
    scenario = report["privateAndAncestry"]

    assert "is not public" in scenario["publicError"]
    assert scenario["direct"]["report"]["routeId"] == "route.vela.guild-direct"
    assert scenario["direct"]["report"]["senderActorId"] == ORGANIZER
    assert scenario["direct"]["report"]["recipientActorId"] == SURVIVOR
    assert scenario["direct"]["report"]["reliability"] == pytest.approx(0.3956)
    assert scenario["direct"]["observation"]["visibility"] == "private"


def test_two_hop_rumor_preserves_origin_and_records_distortion(
    social_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = social_report
    scenario = report["privateAndAncestry"]
    direct = scenario["direct"]
    distorted = scenario["distorted"]

    assert distorted["report"]["originObservationId"] == (
        "observation.vela.organizer.partial-witness"
    )
    assert distorted["report"]["parentReportIds"] == [
        direct["report"]["reportId"]
    ]
    assert distorted["observation"]["originObservationId"] == (
        "observation.vela.organizer.partial-witness"
    )
    assert distorted["report"]["distortion"] == 0.4
    assert distorted["report"]["proposition"]["value"] is False
    assert distorted["report"]["reliability"] == pytest.approx(0.137669)
    assert scenario["canonicalTruth"] is True
    assert scenario["canonicalBefore"] == scenario["canonicalAfter"]


def test_save_restore_preserves_report_ancestry(
    social_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = social_report
    scenario = report["privateAndAncestry"]

    assert scenario["state"] == scenario["restored"]
    assert scenario["state"]["reports"][1]["parentReportIds"] == [
        scenario["state"]["reports"][0]["reportId"]
    ]


def test_public_act_propagates_with_source_and_confidence(
    social_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = social_report
    scenario = report["publicCaptain"]

    assert len(scenario["deliveries"]) == 2
    assert {
        delivery["observation"]["observerId"]
        for delivery in scenario["deliveries"]
    } == {OFFICIAL, SURVIVOR}
    for delivery in scenario["deliveries"]:
        assert delivery["report"]["originObservationId"] == (
            "observation.vela.organizer.captain-public"
        )
        assert delivery["report"]["distortion"] == 0
        assert delivery["report"]["reliability"] == pytest.approx(0.8832)
        assert delivery["observation"]["visibility"] == "public"
    assert scenario["canonicalBefore"] == scenario["canonicalAfter"]


def test_same_captain_action_is_interpreted_differently(
    social_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = social_report
    scenario = report["publicCaptain"]
    organizer = scenario["organizerMetrics"]
    official = scenario["officialMetrics"]

    assert organizer["captainEvidenceDiscipline"] > official[
        "captainEvidenceDiscipline"
    ]
    assert official["captainAuthorityResistance"] > organizer[
        "captainAuthorityResistance"
    ]
    assert organizer["captainCooperation"] > official["captainCooperation"]
    assert scenario["officialModel"]["reportIds"]
    assert scenario["survivorModel"]["reportIds"]


def test_captain_model_changes_later_verified_action(
    social_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = social_report
    baseline = report["coordinator"]["baseline"]
    revised = report["coordinator"]["revised"]

    assert baseline["decision"]["selectedActionTypeId"] == (
        "action.vela.restrict-witness-access"
    )
    assert revised["decision"]["selectedActionTypeId"] == (
        "action.vela.leak-beacon-evidence"
    )
    assert revised["captainMetrics"]["captainEvidenceDiscipline"] > 0.7
    assert revised["outcome"]["status"] == "accepted"
    assert len(revised["propagatedReportIds"]) == 2
    assert set(revised["captainModelUpdatesBeforeDecision"]) == {
        OFFICIAL,
        ORGANIZER,
        SURVIVOR,
    }


def test_social_replay_is_deterministic(
    social_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = social_report
    assert report["deterministic"]["a"] == report["deterministic"]["b"]


def test_v3_state_migrates_to_empty_reports_and_authored_models(
    social_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = social_report
    for state in report["migration"].values():
        assert state["stateVersion"] == "game.strategicAI.state.v8"
        assert state["reports"] == []
        assert len(state["captainModels"]) == 3
        assert all(
            set(model["tendencies"]) == {
                "tendency.captain.cooperation",
                "tendency.captain.evidence-discipline",
                "tendency.captain.authority-resistance",
            }
            for model in state["captainModels"]
        )


def test_generated_social_states_are_schema_and_reference_valid(
    social_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    definition, report = social_report
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)

    states = (
        report["privateAndAncestry"]["state"],
        report["publicCaptain"]["state"],
        report["coordinator"]["revised"]["state"],
        report["deterministic"]["a"],
    )
    for state in states:
        updated = copy.deepcopy(definition)
        updated["stateDefaults"] = state
        Draft202012Validator(schema).validate(updated)
        assert validate_strategic_ai_definition(updated) == []
