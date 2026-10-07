import json
import tempfile
import unittest
from pathlib import Path

from main_computer.meaningful_game_growth_prompt_v2 import (
    DECISION_SCHEMA_ID,
    DESIGN_SCHEMA_ID,
    _contract_schemas,
    _managed_response_json,
    _schema_error_records,
)


class MeaningfulGameGrowthPromptContractTests(unittest.TestCase):
    def _module_proposal(self):
        return {
            "id": "game.spy-hunt.network-pursuit",
            "responsibility": "Own hidden spy-hunt state, not player navigation authority.",
            "playerMeaning": "Search speed trades against preparation and information quality.",
            "persistentEffectClasses": ["future threat state"],
        }

    def _design(self):
        return {
            "schema": "game.meaningfulGameModuleDesign.v1",
            "moduleId": "game.spy-hunt.network-pursuit",
            "responsibility": {
                "owns": ["hidden spy state"],
                "doesNotOwn": ["player navigation state"],
            },
            "stateTranslation": {
                "readsExistingState": [
                    {
                        "need": "player current system",
                        "source": "space-navigation-runtime.js/currentSystemId",
                        "evidence": ["context.nav"],
                    }
                ],
                "seedsModuleState": [
                    {"from": "system graph", "to": "spy.systemId", "rule": "seeded valid-system choice"}
                ],
                "ownedState": [
                    {"path": "spy.systemId", "authority": "authoritative hidden spy occupancy"}
                ],
                "derivedOnlyState": [
                    {"path": "trackerObservation", "derivedFrom": "spy state plus graph"}
                ],
                "outboundConsequences": [
                    {
                        "type": "future threat state",
                        "target": "runtime gap: campaign consequence commit",
                        "status": "runtime_gap",
                        "evidence": [],
                        "idempotency": "commit once by outcome receipt id",
                    }
                ],
                "persistence": {
                    "save": "serialize authoritative module state",
                    "restore": "restore exact hidden state",
                    "migration": "versioned migration",
                },
            },
            "turnModel": {
                "phases": ["observe", "choose", "resolve", "encounter"],
                "encounterChecks": ["after resolution"],
                "randomnessRule": "seed or record every random choice",
            },
            "informationModel": {
                "tracker": "derived two-track signal",
                "corridorSensors": "derived from bounded transit history",
                "asymmetry": "sensor and stealth capabilities alter observations",
            },
            "economy": {
                "farm": "spend turn to gain capability",
                "hop": "spend turn to traverse one legal route",
                "opportunityCost": "farming ages location information; hopping forgoes farm gain",
            },
            "spyPolicy": {
                "objective": "advance mission while balancing power, information, and contact risk",
                "inputs": ["authoritative spy state", "allowed world state"],
                "legalOutputs": ["FARM", "HOP"],
            },
            "outcomes": {
                "terminalPredicates": ["spy resolved", "spy mission success"],
                "receiptInputs": ["turn history", "costs", "mission progress"],
                "persistentEffectClasses": ["future threat state"],
            },
            "integration": {
                "reads": [
                    {
                        "id": "read.player-system",
                        "need": "player current system",
                        "boundary": "space navigation runtime",
                        "evidence": ["context.nav"],
                    }
                ],
                "writes": [],
                "eventsIn": [],
                "eventsOut": [],
                "runtimeGaps": [
                    {
                        "id": "gap.outcome-commit",
                        "need": "commit persistent outcome",
                        "missingBoundary": "campaign consequence writer",
                        "evidenceReviewed": ["context.effects"],
                        "blockingForCode": False,
                    }
                ],
            },
            "exports": ["createInitialState", "resolveTurn", "deriveOutcome"],
            "invariants": [
                {
                    "id": "inv.hidden-location",
                    "statement": "spy exact system remains authoritative hidden state",
                    "enforcedBy": "module",
                }
            ],
        }


    def test_managed_response_creates_missing_utf8_json_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "response_00.json"
            value, created, normalized = _managed_response_json(path)
            self.assertEqual(value, {})
            self.assertTrue(created)
            self.assertFalse(normalized)
            self.assertEqual(path.read_bytes(), b"{}\n")

    def test_managed_response_accepts_utf16_and_rewrites_canonical_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "response_00.json"
            original = {"schema": "example", "value": 1}
            path.write_bytes(json.dumps(original).encode("utf-16"))
            value, created, normalized = _managed_response_json(path)
            self.assertEqual(value, original)
            self.assertFalse(created)
            self.assertTrue(normalized)
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xff\xfe"))
            self.assertEqual(json.loads(raw.decode("utf-8")), original)

    def test_managed_response_accepts_utf32_and_rewrites_canonical_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "response_00.json"
            original = {"schema": "example", "value": 2}
            path.write_bytes(json.dumps(original).encode("utf-32"))
            value, created, normalized = _managed_response_json(path)
            self.assertEqual(value, original)
            self.assertFalse(created)
            self.assertTrue(normalized)
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xff\xfe\x00\x00"))
            self.assertEqual(json.loads(raw.decode("utf-8")), original)

    def test_schema_files_are_draft_2020_12_and_have_stable_ids(self):
        decision, design = _contract_schemas()
        self.assertEqual(decision["$id"], DECISION_SCHEMA_ID)
        self.assertEqual(design["$id"], DESIGN_SCHEMA_ID)
        self.assertEqual(decision["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(design["$schema"], "https://json-schema.org/draft/2020-12/schema")

    def test_translate_decision_can_request_bounded_context(self):
        response = {
            "schema": "game.meaningfulGameGrowthDecision.v1",
            "phase": "translate",
            "summary": "Need navigation source truth.",
            "moduleProposal": self._module_proposal(),
            "facts": [],
            "unknowns": [
                {"id": "u.nav", "question": "Where is player location authoritative?", "blocking": True}
            ],
            "catalogQueries": [],
            "contextRequests": [
                {
                    "id": "ctx.nav",
                    "path": "game_projects/webgl-demo/web/scripts/space-navigation-runtime.js",
                    "selector": {"kind": "search", "terms": ["currentSystemId"], "contextLines": 50},
                    "reason": "Resolve player-location authority.",
                    "required": True,
                }
            ],
            "readyForDesign": False,
            "readyForCode": False,
            "design": {},
        }
        self.assertEqual(_schema_error_records(response), [])

    def test_translate_decision_cannot_claim_ready_for_code(self):
        response = {
            "schema": "game.meaningfulGameGrowthDecision.v1",
            "phase": "translate",
            "summary": "Invalid shortcut.",
            "moduleProposal": self._module_proposal(),
            "facts": [],
            "unknowns": [],
            "catalogQueries": [],
            "contextRequests": [],
            "readyForDesign": False,
            "readyForCode": True,
            "design": {},
        }
        self.assertTrue(_schema_error_records(response))

    def test_ready_for_code_requires_complete_design_schema(self):
        response = {
            "schema": "game.meaningfulGameGrowthDecision.v1",
            "phase": "design",
            "summary": "Design is source-grounded enough to generate code.",
            "moduleProposal": self._module_proposal(),
            "facts": [],
            "unknowns": [],
            "catalogQueries": [],
            "contextRequests": [],
            "readyForDesign": True,
            "readyForCode": True,
            "design": self._design(),
        }
        self.assertEqual(_schema_error_records(response), [])


if __name__ == "__main__":
    unittest.main()
