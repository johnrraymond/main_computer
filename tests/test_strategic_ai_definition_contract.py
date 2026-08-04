from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any

from main_computer.strategic_ai_definition import (
    STRATEGIC_AI_DEFINITION_VERSION,
    STRATEGIC_AI_SCHEMA,
    STRATEGIC_AI_STATE_VERSION,
    validate_strategic_ai_definition,
)


ROOT = Path(__file__).resolve().parents[1]
PROJECT_PATHS = (
    ROOT / "game_projects" / "webgl-demo" / "project.json",
    ROOT / "game_projects" / "starter-game" / "project.json",
    ROOT / "game_projects" / "new-game" / "project.json",
)
SCHEMA_PATH = ROOT / "game_projects" / "schema" / "strategic-ai.v1.schema.json"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _strategic_ai(project: dict[str, Any]) -> dict[str, Any]:
    definition = (project.get("metadata") or {}).get("strategicAI")
    if not isinstance(definition, dict):
        raise AssertionError("missing project.metadata.strategicAI")
    return definition


def _navigation(project: dict[str, Any]) -> dict[str, Any]:
    definition = (project.get("metadata") or {}).get("spaceNavigation")
    if not isinstance(definition, dict):
        raise AssertionError("missing project.metadata.spaceNavigation")
    return definition


class StrategicAIDefinitionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.projects = [_load_json(path) for path in PROJECT_PATHS]
        self.definition = _strategic_ai(self.projects[0])
        self.navigation = _navigation(self.projects[0])

    def test_schema_declares_closed_v1_contract(self) -> None:
        schema = _load_json(SCHEMA_PATH)

        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["$id"], STRATEGIC_AI_SCHEMA)
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["schema"]["const"], STRATEGIC_AI_SCHEMA)
        self.assertEqual(
            schema["properties"]["definitionVersion"]["const"],
            STRATEGIC_AI_DEFINITION_VERSION,
        )
        self.assertEqual(
            schema["properties"]["stateVersion"]["const"],
            STRATEGIC_AI_STATE_VERSION,
        )

        required = set(schema["required"])
        self.assertTrue(
            {
                "actors",
                "facts",
                "evidence",
                "goals",
                "actionTypes",
                "policyProfiles",
                "reportRoutes",
                "captainModelProfiles",
                "effectTypes",
                "observationChannels",
                "checkpoints",
                "stateDefaults",
            }.issubset(required)
        )
        for definition_name in (
            "actor",
            "policyProfile",
            "reportRoute",
            "captainModelProfile",
            "fact",
            "evidence",
            "goal",
            "actionType",
            "effectType",
            "observation",
            "belief",
            "memory",
            "receipt",
            "report",
            "captainModel",
            "stateDefaults",
        ):
            self.assertFalse(schema["$defs"][definition_name]["additionalProperties"])

        probability = schema["$defs"]["probability"]
        self.assertEqual(probability["minimum"], 0)
        self.assertEqual(probability["maximum"], 1)
        self.assertIn("basisIds", schema["$defs"]["belief"]["required"])
        self.assertIn("checkpointId", schema["$defs"]["receipt"]["required"])
        self.assertIn("actorId", schema["$defs"]["receipt"]["required"])
        self.assertIn("policyProfileId", schema["$defs"]["receipt"]["required"])
        self.assertIn("canonicalRevision", schema["$defs"]["receipt"]["required"])
        self.assertIn("canonicalRevision", schema["$defs"]["proposal"]["required"])
        self.assertEqual(
            schema["$defs"]["stateDefaults"]["properties"]["stateVersion"]["const"],
            STRATEGIC_AI_STATE_VERSION,
        )

    def test_default_projects_share_one_valid_definition(self) -> None:
        definitions = [_strategic_ai(project) for project in self.projects]
        self.assertEqual(definitions[1:], definitions[:-1])

        for project, definition in zip(self.projects, definitions):
            self.assertEqual(definition["schema"], STRATEGIC_AI_SCHEMA)
            self.assertEqual(
                definition["definitionVersion"],
                STRATEGIC_AI_DEFINITION_VERSION,
            )
            self.assertEqual(definition["stateVersion"], STRATEGIC_AI_STATE_VERSION)
            self.assertEqual(
                [],
                validate_strategic_ai_definition(
                    definition,
                    space_navigation=_navigation(project),
                ),
            )

    def test_fixture_and_vela_prototype_are_bounded_and_preserve_false_belief(self) -> None:
        definition = self.definition
        state = definition["stateDefaults"]

        self.assertEqual(7, len(definition["actors"]))
        self.assertEqual(7, len(definition["policyProfiles"]))
        self.assertEqual(3, len(definition["reportRoutes"]))
        self.assertEqual(3, len(definition["captainModelProfiles"]))
        self.assertEqual(15, len(definition["facts"]))
        self.assertEqual(6, len(definition["evidence"]))
        self.assertEqual(14, len(definition["goals"]))
        self.assertEqual(17, len(definition["actionTypes"]))
        self.assertEqual(6, len(definition["resources"]))
        self.assertEqual(1, len(definition["checkpoints"]))
        self.assertEqual(9, len(state["observations"]))
        self.assertEqual(14, len(state["beliefs"]))
        self.assertEqual(7, len(state["memories"]))
        self.assertEqual([], state["receipts"])
        self.assertEqual([], state["proposals"])
        self.assertEqual([], state["outcomes"])
        self.assertEqual([], state["reports"])
        self.assertEqual(3, len(state["captainModels"]))
        self.assertEqual(1, len(definition["commitmentTypes"]))
        self.assertEqual(1, len(definition["cooperationProfiles"]))
        self.assertEqual([], state["commitments"])
        self.assertEqual(1, len(state["cooperationModels"]))
        self.assertEqual(0, state["canonicalState"]["revision"])
        self.assertEqual(1, state["canonicalState"]["resourceBalances"][0]["quantity"])
        self.assertEqual(
            "policy.fixture.watch-officer",
            definition["actors"][0]["policyProfileId"],
        )

        vela_actor_ids = {
            actor["id"]
            for actor in definition["actors"]
            if actor["id"].startswith("actor.vela.")
        }
        self.assertEqual(
            {
                "actor.vela.gate-official",
                "actor.vela.rescue-organizer",
                "actor.vela.survivor",
            },
            vela_actor_ids,
        )

        canonical = next(
            fact
            for fact in definition["facts"]
            if fact["id"] == "fact.fixture.relay-operational"
        )
        false_belief = next(
            belief
            for belief in state["beliefs"]
            if belief["id"] == "belief.fixture.relay-offline-stale"
        )
        self.assertTrue(canonical["proposition"]["value"])
        self.assertFalse(false_belief["proposition"]["value"])
        self.assertEqual(
            canonical["proposition"]["predicate"],
            false_belief["proposition"]["predicate"],
        )
        self.assertEqual(
            canonical["proposition"]["arguments"],
            false_belief["proposition"]["arguments"],
        )
        self.assertEqual(
            ["evidence.fixture.stale-relay-report"],
            false_belief["basisIds"],
        )
        self.assertEqual(0.35, false_belief["confidence"])

    def test_fixture_actor_resolves_to_authored_local_destination(self) -> None:
        actor = self.definition["actors"][0]
        systems = {system["id"]: system for system in self.navigation["systems"]}

        self.assertIn(actor["systemId"], systems)
        destinations = {
            destination["id"]
            for destination in systems[actor["systemId"]]["localDestinations"]
        }
        self.assertIn(actor["localDestinationId"], destinations)

    def test_validator_rejects_broken_actor_goal_reference(self) -> None:
        broken = copy.deepcopy(self.definition)
        broken["actors"][0]["goalIds"] = ["goal.fixture.missing"]

        problems = validate_strategic_ai_definition(
            broken,
            space_navigation=self.navigation,
        )
        self.assertTrue(
            any("actor actor.fixture.watch-officer.goalIds references missing" in problem for problem in problems),
            problems,
        )

    def test_validator_rejects_broken_source_and_effect_references(self) -> None:
        broken = copy.deepcopy(self.definition)
        broken["evidence"][0]["sourceId"] = "source.fixture.missing"
        broken["actionTypes"][0]["effectTypeIds"] = ["effect.fixture.missing"]

        problems = validate_strategic_ai_definition(
            broken,
            space_navigation=self.navigation,
        )
        self.assertTrue(
            any("evidence evidence.fixture.stale-relay-report references missing source" in problem for problem in problems),
            problems,
        )
        self.assertTrue(
            any("actionType action.fixture.send-status-report.effectTypeIds references missing" in problem for problem in problems),
            problems,
        )

    def test_validator_rejects_belief_without_provenance(self) -> None:
        broken = copy.deepcopy(self.definition)
        broken["stateDefaults"]["beliefs"][0]["basisIds"] = []

        problems = validate_strategic_ai_definition(
            broken,
            space_navigation=self.navigation,
        )
        self.assertTrue(
            any("belief belief.fixture.relay-operational has no provenance basis" in problem for problem in problems),
            problems,
        )

    def test_validator_rejects_duplicate_identity(self) -> None:
        broken = copy.deepcopy(self.definition)
        broken["facts"].append(copy.deepcopy(broken["facts"][0]))

        problems = validate_strategic_ai_definition(
            broken,
            space_navigation=self.navigation,
        )
        self.assertTrue(
            any("duplicate id fact.fixture.relay-operational in facts" in problem for problem in problems),
            problems,
        )

    def test_validator_rejects_invalid_navigation_placement(self) -> None:
        broken = copy.deepcopy(self.definition)
        broken["actors"][0]["localDestinationId"] = (
            "destination.vela-gate.velaris-orbit"
        )

        problems = validate_strategic_ai_definition(
            broken,
            space_navigation=self.navigation,
        )
        self.assertTrue(
            any("outside system system.solace-reach" in problem for problem in problems),
            problems,
        )

    def test_validator_checks_receipt_actor_checkpoint_and_action(self) -> None:
        broken = copy.deepcopy(self.definition)
        broken["stateDefaults"]["receipts"] = [
            {
                "decisionId": "decision.fixture.invalid",
                "actorId": "actor.fixture.missing",
                "checkpointId": "checkpoint.fixture.missing",
                "policyProfileId": "policy.fixture.missing",
                "canonicalRevision": 0,
                "activeGoalIds": [
                    "goal.fixture.preserve-relay-continuity"
                ],
                "beliefIds": [
                    "belief.fixture.relay-operational"
                ],
                "candidateActions": [
                    {
                        "actionTypeId": "action.fixture.missing",
                        "score": 0.4
                    }
                ],
                "rejections": [],
                "selectedActionTypeId": "action.fixture.missing",
                "expectedEffectTypeIds": [
                    "effect.fixture.report-recorded"
                ],
                "confidence": 0.4,
                "randomSeed": 7
            }
        ]

        problems = validate_strategic_ai_definition(
            broken,
            space_navigation=self.navigation,
        )
        self.assertTrue(
            any("receipt decision.fixture.invalid references missing actor" in problem for problem in problems),
            problems,
        )
        self.assertTrue(
            any("receipt decision.fixture.invalid references missing checkpoint" in problem for problem in problems),
            problems,
        )
        self.assertTrue(
            any("receipt decision.fixture.invalid references missing selected action" in problem for problem in problems),
            problems,
        )
        self.assertTrue(
            any("candidateActions[0] references missing action" in problem for problem in problems),
            problems,
        )


    def test_validator_rejects_missing_or_incomplete_policy_profile(self) -> None:
        broken = copy.deepcopy(self.definition)
        broken["actors"][0]["policyProfileId"] = "policy.fixture.missing"

        problems = validate_strategic_ai_definition(
            broken,
            space_navigation=self.navigation,
        )
        self.assertTrue(
            any("references missing policy profile" in problem for problem in problems),
            problems,
        )

        broken = copy.deepcopy(self.definition)
        del broken["policyProfiles"][0]["actionPolicies"][
            "action.fixture.request-relay-inspection"
        ]
        problems = validate_strategic_ai_definition(
            broken,
            space_navigation=self.navigation,
        )
        self.assertTrue(
            any("lacks candidate action policies" in problem for problem in problems),
            problems,
        )


    def test_validator_rejects_unauthorized_report_route_channel(self) -> None:
        broken = copy.deepcopy(self.definition)
        route = next(
            item
            for item in broken["reportRoutes"]
            if item["id"] == "route.vela.guild-direct"
        )
        route["recipientActorIds"] = ["actor.vela.gate-official"]

        problems = validate_strategic_ai_definition(
            broken,
            space_navigation=self.navigation,
        )
        self.assertTrue(
            any("delivers unauthorized channel" in problem for problem in problems),
            problems,
        )

    def test_validator_rejects_broken_report_ancestry(self) -> None:
        broken = copy.deepcopy(self.definition)
        source = next(
            item
            for item in broken["stateDefaults"]["observations"]
            if item["id"] == "observation.vela.organizer.partial-witness"
        )
        recipient_observation = {
            "id": "observation.report.fixture.invalid",
            "observerId": "actor.vela.survivor",
            "proposition": copy.deepcopy(source["proposition"]),
            "channelId": "channel.vela.guild-report",
            "sourceId": source["sourceId"],
            "reliability": 0.3956,
            "observedAt": 1,
            "visibility": "private",
            "reportId": "report.fixture.invalid",
            "originObservationId": "observation.vela.missing",
        }
        broken["stateDefaults"]["observations"].append(recipient_observation)
        broken["stateDefaults"]["reports"].append(
            {
                "reportId": "report.fixture.invalid",
                "routeId": "route.vela.guild-direct",
                "senderActorId": "actor.vela.rescue-organizer",
                "recipientActorId": "actor.vela.survivor",
                "sourceObservationId": source["id"],
                "originObservationId": "observation.vela.missing",
                "parentReportIds": ["report.fixture.missing-parent"],
                "proposition": copy.deepcopy(source["proposition"]),
                "reliability": 0.3956,
                "distortion": 0,
                "sentAt": 0,
                "receivedAt": 1,
                "visibility": "private",
                "recipientObservationId": recipient_observation["id"],
            }
        )

        problems = validate_strategic_ai_definition(
            broken,
            space_navigation=self.navigation,
        )
        self.assertTrue(
            any("references missing origin observation" in problem for problem in problems),
            problems,
        )
        self.assertTrue(
            any("parentReportIds references missing" in problem for problem in problems),
            problems,
        )

    def test_validator_rejects_captain_model_profile_mismatch(self) -> None:
        broken = copy.deepcopy(self.definition)
        broken["stateDefaults"]["captainModels"][0]["holderActorId"] = (
            "actor.vela.rescue-organizer"
        )

        problems = validate_strategic_ai_definition(
            broken,
            space_navigation=self.navigation,
        )
        self.assertTrue(
            any("holderActorId does not match profile" in problem for problem in problems),
            problems,
        )



if __name__ == "__main__":
    unittest.main()
