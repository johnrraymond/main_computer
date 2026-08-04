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
        pytest.skip("node is unavailable; Vela Gate strategic AI tests cannot run")

    script = r"""
const fs = require("fs");
const cognition = require(process.argv[1]);
const coordinatorApi = require(process.argv[2]);
const definition = JSON.parse(fs.readFileSync(0, "utf8"));

const OFFICIAL = "actor.vela.gate-official";
const ORGANIZER = "actor.vela.rescue-organizer";
const SURVIVOR = "actor.vela.survivor";
const SEED = 811;

function createCoordinator(state = undefined) {
  return coordinatorApi.create(definition, {seed: SEED, state});
}

function freshTurn(actorId, observations = [], createdAt = 5) {
  const coordinator = createCoordinator();
  return coordinator.runTurn(actorId, {
    observations,
    proposalOptions: {createdAt}
  });
}

function captainRetainedObservation(id) {
  return {
    id,
    observerId: ORGANIZER,
    proposition: {
      predicate: "predicate.vela.captain-retained-evidence",
      arguments: ["actor.captain", "evidence.vela.atlas-copy"],
      value: true
    },
    channelId: "channel.vela.captain-observation",
    sourceId: "source.vela.captain-telemetry",
    reliability: 0.99,
    observedAt: 4,
    visibility: "private"
  };
}

const evaluations = {};
for (const actorId of [OFFICIAL, ORGANIZER, SURVIVOR]) {
  const runtime = cognition.create(definition, {seed: SEED});
  evaluations[actorId] = runtime.evaluateCandidates(actorId);
}

const officialA = freshTurn(OFFICIAL);
const officialB = freshTurn(OFFICIAL);
const organizerBaseline = freshTurn(ORGANIZER);
const organizerAfterCaptain = freshTurn(
  ORGANIZER,
  [captainRetainedObservation("observation.vela.organizer.captain-retained")]
);
const survivor = freshTurn(SURVIVOR);

const sequenceCoordinator = createCoordinator();
const sequenceOfficial = sequenceCoordinator.runTurn(OFFICIAL, {
  proposalOptions: {createdAt: 5}
});
const sequenceOrganizer = sequenceCoordinator.runTurn(ORGANIZER, {
  proposalOptions: {createdAt: 6}
});

const restoredA = createCoordinator(sequenceOfficial.state);
const restoredB = createCoordinator(sequenceOfficial.state);
const restoredOrganizerA = restoredA.runTurn(ORGANIZER, {
  proposalOptions: {createdAt: 6}
});
const restoredOrganizerB = restoredB.runTurn(ORGANIZER, {
  proposalOptions: {createdAt: 6}
});

process.stdout.write(JSON.stringify({
  evaluations,
  officialA,
  officialB,
  organizerBaseline,
  organizerAfterCaptain,
  survivor,
  sequenceOfficial,
  sequenceOrganizer,
  restoredOrganizerA,
  restoredOrganizerB
}));
"""
    result = subprocess.run(
        [
            node,
            "-e",
            script,
            str(COGNITION_RUNTIME),
            str(COORDINATOR_RUNTIME),
        ],
        input=json.dumps(definition),
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def vela_report() -> tuple[dict[str, Any], dict[str, Any]]:
    definition = _definition()
    return definition, _run_node(definition)


def _by_id(records: list[dict[str, Any]], record_id: str) -> dict[str, Any]:
    return next(record for record in records if record["id"] == record_id)


def _canonical_value(state: dict[str, Any], fact_id: str) -> Any:
    record = next(
        item
        for item in state["canonicalState"]["factStates"]
        if item["factId"] == fact_id
    )
    return record["value"]


def test_three_actors_have_distinct_locations_goals_and_private_knowledge(
    vela_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    definition, _report = vela_report
    actors = {
        actor["id"]: actor
        for actor in definition["actors"]
        if actor["id"].startswith("actor.vela.")
    }

    assert set(actors) == {OFFICIAL, ORGANIZER, SURVIVOR}
    assert {
        actors[OFFICIAL]["localDestinationId"],
        actors[ORGANIZER]["localDestinationId"],
        actors[SURVIVOR]["localDestinationId"],
    } == {
        "destination.vela-gate.velaris-orbit",
        "destination.vela-gate.seraph-relay",
        "destination.vela-gate.chiron-observatory",
    }
    assert set(actors[OFFICIAL]["goalIds"]).isdisjoint(
        actors[ORGANIZER]["goalIds"]
    )
    assert set(actors[ORGANIZER]["goalIds"]).isdisjoint(
        actors[SURVIVOR]["goalIds"]
    )
    assert set(actors[OFFICIAL]["initialBeliefs"]).isdisjoint(
        actors[ORGANIZER]["initialBeliefs"]
    )
    assert set(actors[ORGANIZER]["initialBeliefs"]).isdisjoint(
        actors[SURVIVOR]["initialBeliefs"]
    )


def test_official_acts_proactively_and_notifies_other_destinations(
    vela_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = vela_report
    turn = report["officialA"]

    assert turn["decision"]["selectedActionTypeId"] == (
        "action.vela.move-patrol-to-chiron"
    )
    assert turn["outcome"]["status"] == "accepted"
    assert turn["outcome"]["consumedResources"] == [
        {
            "resourceId": "resource.vela.patrol-deployment",
            "amount": 1,
        }
    ]
    assert _canonical_value(turn["state"], "fact.vela.patrol-at-chiron") is True
    assert len(turn["outcome"]["committedEffectTypeIds"]) == 2

    updates = turn["resultingBeliefUpdatesByActor"]
    assert set(updates) == {OFFICIAL, ORGANIZER, SURVIVOR}
    assert all(updates[actor_id] for actor_id in updates)

    result_observations = {
        observation["observerId"]
        for observation in turn["state"]["observations"]
        if observation["id"] in turn["resultingObservationIds"]
    }
    assert result_observations == {OFFICIAL, ORGANIZER, SURVIVOR}


def test_reliable_captain_observation_changes_organizer_strategy(
    vela_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = vela_report
    baseline = report["organizerBaseline"]
    revised = report["organizerAfterCaptain"]

    assert baseline["decision"]["selectedActionTypeId"] == (
        "action.vela.restrict-witness-access"
    )
    assert revised["incomingObservationIds"] == [
        "observation.vela.organizer.captain-retained"
    ]
    assert revised["incomingBeliefUpdates"]
    assert revised["decision"]["selectedActionTypeId"] == (
        "action.vela.leak-beacon-evidence"
    )
    assert revised["outcome"]["status"] == "accepted"
    assert _canonical_value(revised["state"], "fact.vela.evidence-public") is True


def test_patrol_move_at_velaris_changes_organizer_behavior_at_seraph(
    vela_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = vela_report
    official = report["sequenceOfficial"]
    organizer = report["sequenceOrganizer"]

    assert official["actorId"] == OFFICIAL
    assert official["canonicalRevisionAfter"] == 1
    assert organizer["actorId"] == ORGANIZER
    assert organizer["canonicalRevisionBefore"] == 1
    assert organizer["decision"]["selectedActionTypeId"] == (
        "action.vela.leak-beacon-evidence"
    )
    assert organizer["outcome"]["status"] == "accepted"
    assert organizer["canonicalRevisionAfter"] == 2

    patrol_observation_ids = {
        observation["id"]
        for observation in official["state"]["observations"]
        if (
            observation["observerId"] == ORGANIZER
            and observation["proposition"]["predicate"]
            == "predicate.vela.patrol-at-chiron"
        )
    }
    assert patrol_observation_ids
    organizer_beliefs = {
        belief["id"]: belief
        for belief in official["state"]["beliefs"]
        if belief["holderId"] == ORGANIZER
    }
    assert any(
        patrol_observation_ids.intersection(belief["basisIds"])
        for belief in organizer_beliefs.values()
    )


def test_survivor_makes_coherent_mistake_from_false_belief(
    vela_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    definition, report = vela_report
    turn = report["survivor"]

    assert _canonical_value(
        definition["stateDefaults"],
        "fact.vela.rescue-organizer-compromised",
    ) is False
    false_belief = _by_id(
        definition["stateDefaults"]["beliefs"],
        "belief.vela.survivor.organizer-compromised",
    )
    assert false_belief["proposition"]["value"] is True
    assert false_belief["confidence"] == 0.88
    assert false_belief["id"] in turn["decision"]["beliefIds"]
    assert turn["decision"]["selectedActionTypeId"] == (
        "action.vela.refuse-rescue-guild-contact"
    )
    assert turn["outcome"]["status"] == "accepted"


def test_vela_behavior_and_save_restore_are_deterministic(
    vela_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = vela_report

    assert report["officialA"] == report["officialB"]
    assert report["restoredOrganizerA"] == report["restoredOrganizerB"]


def test_vela_receipts_explain_policy_metrics_and_alternatives(
    vela_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _definition_value, report = vela_report

    for actor_id, expected_policy, expected_candidates in (
        (OFFICIAL, "policy.vela.gate-official", 4),
        (ORGANIZER, "policy.vela.rescue-organizer", 3),
        (SURVIVOR, "policy.vela.survivor", 3),
    ):
        evaluation = report["evaluations"][actor_id]
        assert evaluation["policyProfileId"] == expected_policy
        assert len(evaluation["candidateActions"]) == expected_candidates
        for candidate in evaluation["candidateActions"]:
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


def test_generated_vela_states_remain_schema_and_reference_valid(
    vela_report: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    definition, report = vela_report
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)

    for turn in (
        report["officialA"],
        report["organizerBaseline"],
        report["organizerAfterCaptain"],
        report["survivor"],
        report["sequenceOrganizer"],
        report["restoredOrganizerA"],
    ):
        updated = copy.deepcopy(definition)
        updated["stateDefaults"] = turn["state"]
        Draft202012Validator(schema).validate(updated)
        assert validate_strategic_ai_definition(updated) == []
