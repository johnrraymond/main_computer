from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from main_computer.gameplay_plugin_content import (
    GAMEPLAY_PLUGIN_CONTENT_SCHEMA,
    assert_valid_gameplay_plugin_content_package,
    validate_gameplay_plugin_content_package,
)
from main_computer.gameplay_plugin_manifest import (
    GAMEPLAY_PLUGIN_MANIFEST_SCHEMA,
    GAMEPLAY_PLUGIN_MANIFEST_VERSION,
)


ROOT = Path(__file__).resolve().parents[1]
CONTENT_SCHEMA_PATH = ROOT / "game_projects" / "schema" / "gameplay-plugin-content.v1.schema.json"


def _load_content_schema() -> dict[str, Any]:
    return json.loads(CONTENT_SCHEMA_PATH.read_text(encoding="utf-8"))


def _valid_manifest() -> dict[str, Any]:
    return {
        "schema": GAMEPLAY_PLUGIN_MANIFEST_SCHEMA,
        "manifestVersion": GAMEPLAY_PLUGIN_MANIFEST_VERSION,
        "id": "plugin.generated.vela-cave-followup.001",
        "title": "Vela Cave Followup",
        "kind": "generated-gameplay-plugin",
        "origin": {
            "type": "local-ai",
            "modelProfile": "local-director-v1",
            "seed": "fixture-seed",
        },
        "scope": {
            "canAdd": [
                "scenario",
                "encounter",
                "dialogue",
                "evidence",
                "consequence",
                "objective",
                "receipt",
            ],
            "canModify": ["generated-content-only"],
            "cannotModify": [
                "engine-code",
                "renderer-code",
                "runtime-code",
                "save-schema",
                "runtime-state",
                "base-project-content",
                "base-scenarios",
                "executable-code",
            ],
            "allowedSystems": ["system.vela-gate"],
            "allowedDestinations": ["destination.vela-gate.subsurface-cavern"],
        },
        "content": {
            "entryPoints": ["scenario.generated.vela-cave-followup"],
            "scenarios": [
                {
                    "id": "scenario.generated.vela-cave-followup",
                    "path": "content/scenarios/vela-cave-followup.json",
                    "title": "Vela Cave Followup",
                    "encounterIds": ["encounter.generated.vela-cave-run"],
                    "receiptIds": ["receipt.generated.vela-cave-followup-complete"],
                }
            ],
            "encounters": [
                {
                    "id": "encounter.generated.vela-cave-run",
                    "path": "content/encounters/vela-cave-run.json",
                    "title": "Cave Run",
                    "template": "encounter-template.cave-combat-run",
                    "actorArchetypeIds": ["actor-archetype.cave-guard"],
                    "objectiveTypeIds": ["objective-type.clear-hostiles", "objective-type.reach-extraction"],
                }
            ],
            "dialogue": [],
            "evidence": [],
            "consequences": [],
            "receipts": [
                {
                    "id": "receipt.generated.vela-cave-followup-complete",
                    "path": "content/receipts/vela-cave-followup-complete.json",
                }
            ],
            "files": [
                {
                    "kind": "scenario",
                    "path": "content/scenarios/vela-cave-followup.json",
                },
                {
                    "kind": "encounter",
                    "path": "content/encounters/vela-cave-run.json",
                },
            ],
        },
        "requires": {
            "encounterTemplates": ["encounter-template.cave-combat-run"],
            "actorArchetypes": ["actor-archetype.cave-guard"],
            "objectiveTypes": ["objective-type.clear-hostiles", "objective-type.reach-extraction"],
            "consequenceTypes": ["consequence-type.record-receipt"],
            "systems": ["system.vela-gate"],
            "destinations": ["destination.vela-gate.subsurface-cavern"],
            "factions": [],
        },
        "permissions": {
            "allowExecutableCode": False,
            "allowRuntimeStateMutation": False,
            "allowEngineFileMutation": False,
            "allowBaseContentMutation": False,
        },
        "validation": {
            "mustPassSchema": True,
            "mustPassReferenceCheck": True,
            "mustPassSimulation": True,
            "mustRejectExecutableCode": True,
            "mustSupportRollback": True,
        },
        "rollback": {
            "supported": True,
            "mode": "disable-plugin",
            "disableReceipts": ["receipt.generated.vela-cave-followup-complete"],
        },
    }


def _valid_scenario() -> dict[str, Any]:
    return {
        "schema": GAMEPLAY_PLUGIN_CONTENT_SCHEMA,
        "kind": "scenario",
        "id": "scenario.generated.vela-cave-followup",
        "title": "Vela Cave Followup",
        "entry": {
            "systemId": "system.vela-gate",
            "destinationId": "destination.vela-gate.subsurface-cavern",
        },
        "entryStage": "arrival",
        "completionStages": ["extraction"],
        "encounterIds": ["encounter.generated.vela-cave-run"],
        "receiptIds": ["receipt.generated.vela-cave-followup-complete"],
        "stages": [
            {
                "id": "arrival",
                "kind": "briefing",
                "title": "A second tunnel opens",
                "objective": "Follow the route from the old Vela holding cavern.",
                "nextStage": "cave-run",
            },
            {
                "id": "cave-run",
                "kind": "encounter",
                "title": "Clear the cave route",
                "encounterId": "encounter.generated.vela-cave-run",
                "onComplete": "extraction",
                "onFail": "failed",
            },
            {
                "id": "extraction",
                "kind": "resolution",
                "title": "Beam back",
                "receiptIds": ["receipt.generated.vela-cave-followup-complete"],
            },
            {
                "id": "failed",
                "kind": "failure",
                "title": "Route lost",
            },
        ],
    }


def _valid_encounter() -> dict[str, Any]:
    return {
        "schema": GAMEPLAY_PLUGIN_CONTENT_SCHEMA,
        "kind": "encounter",
        "id": "encounter.generated.vela-cave-run",
        "title": "Cave Run",
        "template": "encounter-template.cave-combat-run",
        "location": {
            "systemId": "system.vela-gate",
            "destinationId": "destination.vela-gate.subsurface-cavern",
        },
        "participants": [
            {
                "role": "hostile",
                "actorArchetypeId": "actor-archetype.cave-guard",
                "count": 6,
            }
        ],
        "objectives": [
            {
                "id": "clear-hostiles",
                "type": "objective-type.clear-hostiles",
                "label": "Clear the cave guards",
                "required": True,
            },
            {
                "id": "reach-extraction",
                "type": "objective-type.reach-extraction",
                "label": "Reach the transporter room",
                "required": True,
            },
        ],
        "winConditions": [
            {
                "objectiveIds": ["clear-hostiles", "reach-extraction"],
            }
        ],
        "lossConditions": [],
        "outcomes": [
            {
                "id": "outcome.generated.vela-cave-followup-win",
                "when": "win",
                "receiptIds": ["receipt.generated.vela-cave-followup-complete"],
                "consequenceTypeIds": ["consequence-type.record-receipt"],
            }
        ],
    }


def _valid_content_package() -> dict[str, Any]:
    return {
        "content/scenarios/vela-cave-followup.json": _valid_scenario(),
        "content/encounters/vela-cave-run.json": _valid_encounter(),
    }


def test_gameplay_plugin_content_schema_declares_closed_v1_contract() -> None:
    schema = _load_content_schema()

    Draft202012Validator.check_schema(schema)

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == GAMEPLAY_PLUGIN_CONTENT_SCHEMA
    assert schema["oneOf"]
    assert schema["$defs"]["scenario"]["additionalProperties"] is False
    assert schema["$defs"]["encounter"]["additionalProperties"] is False

    for definition_name in (
        "scenarioEntry",
        "stage",
        "choice",
        "participant",
        "objective",
        "condition",
        "outcome",
    ):
        assert schema["$defs"][definition_name]["additionalProperties"] is False


def test_valid_scenario_and_encounter_pass_schema_and_semantic_validation() -> None:
    schema = _load_content_schema()
    validator = Draft202012Validator(schema)
    manifest = _valid_manifest()
    content = _valid_content_package()

    schema_errors = [
        error.message
        for document in content.values()
        for error in sorted(validator.iter_errors(document), key=lambda item: item.path)
    ]

    assert schema_errors == []
    assert validate_gameplay_plugin_content_package(manifest, content) == []
    assert_valid_gameplay_plugin_content_package(manifest, content)


def test_content_validator_requires_manifest_declared_files_to_exist() -> None:
    manifest = _valid_manifest()
    content = _valid_content_package()
    del content["content/encounters/vela-cave-run.json"]

    problems = validate_gameplay_plugin_content_package(manifest, content)

    assert any(
        "content package is missing declared file content/encounters/vela-cave-run.json" in problem
        for problem in problems
    )


def test_content_validator_rejects_stage_graph_reference_errors() -> None:
    manifest = _valid_manifest()
    content = _valid_content_package()
    scenario = content["content/scenarios/vela-cave-followup.json"]
    scenario["stages"][1]["onComplete"] = "missing-stage"
    scenario["stages"].append(
        {
            "id": "dead-branch",
            "kind": "resolution",
            "title": "Never reached",
        }
    )

    problems = validate_gameplay_plugin_content_package(manifest, content)

    assert any("references missing stage missing-stage" in problem for problem in problems)
    assert any("unreachable stages" in problem and "dead-branch" in problem for problem in problems)


def test_content_validator_rejects_executable_fields() -> None:
    manifest = _valid_manifest()
    content = _valid_content_package()
    content["content/scenarios/vela-cave-followup.json"]["stages"][0]["script"] = "spawnCheat()"

    problems = validate_gameplay_plugin_content_package(manifest, content)

    assert any("forbidden executable/runtime field" in problem for problem in problems)
    assert any("$.stages[0].script" in problem for problem in problems)


def test_content_validator_rejects_manifest_content_mismatches() -> None:
    manifest = _valid_manifest()
    content = _valid_content_package()
    scenario = content["content/scenarios/vela-cave-followup.json"]
    scenario["id"] = "scenario.generated.not-the-manifest-id"
    scenario["encounterIds"] = ["encounter.generated.different"]

    problems = validate_gameplay_plugin_content_package(manifest, content)

    assert any("id must match manifest id scenario.generated.vela-cave-followup" in problem for problem in problems)
    assert any("encounterIds must match manifest declaration" in problem for problem in problems)


def test_content_validator_rejects_undeclared_template_archetype_objective_and_receipt_refs() -> None:
    manifest = _valid_manifest()
    content = _valid_content_package()
    encounter = content["content/encounters/vela-cave-run.json"]
    encounter["template"] = "encounter-template.unknown"
    encounter["participants"][0]["actorArchetypeId"] = "actor-archetype.unknown"
    encounter["objectives"][0]["type"] = "objective-type.unknown"
    encounter["outcomes"][0]["receiptIds"] = ["receipt.generated.unknown"]
    encounter["outcomes"][0]["consequenceTypeIds"] = ["consequence-type.unknown"]

    problems = validate_gameplay_plugin_content_package(manifest, content)

    assert any("template is not declared" in problem for problem in problems)
    assert any("actorArchetypeId is not declared" in problem for problem in problems)
    assert any("objectives[0].type is not declared" in problem for problem in problems)
    assert any("receiptIds references undeclared receipt" in problem for problem in problems)
    assert any("consequenceTypeIds references undeclared consequence type" in problem for problem in problems)


def test_content_validator_rejects_wrong_system_or_destination_scope() -> None:
    manifest = _valid_manifest()
    content = _valid_content_package()
    scenario = content["content/scenarios/vela-cave-followup.json"]
    scenario["entry"]["systemId"] = "system.pax"
    encounter = content["content/encounters/vela-cave-run.json"]
    encounter["location"]["destinationId"] = "destination.pax.witness-room"

    problems = validate_gameplay_plugin_content_package(manifest, content)

    assert any("entry.systemId is not allowed" in problem for problem in problems)
    assert any("location.destinationId is not allowed" in problem for problem in problems)


def test_assert_valid_content_package_raises_actionable_message() -> None:
    manifest = _valid_manifest()
    content = _valid_content_package()
    content["content/encounters/vela-cave-run.json"]["winConditions"][0]["objectiveIds"] = [
        "missing-objective"
    ]

    with pytest.raises(ValueError) as exc_info:
        assert_valid_gameplay_plugin_content_package(manifest, content)

    message = str(exc_info.value)
    assert "Invalid gameplay plugin content package" in message
    assert "missing-objective" in message
