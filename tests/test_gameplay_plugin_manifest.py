from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from main_computer.gameplay_plugin_manifest import (
    GAMEPLAY_PLUGIN_MANIFEST_SCHEMA,
    GAMEPLAY_PLUGIN_MANIFEST_VERSION,
    assert_valid_gameplay_plugin_manifest,
    validate_gameplay_plugin_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "game_projects" / "schema" / "gameplay-plugin-manifest.v1.schema.json"


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


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
            "promptSummary": "Create a short cave escape follow-up.",
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
                    "objectiveTypeIds": ["objective-type.reach-extraction"],
                }
            ],
            "dialogue": [
                {
                    "id": "dialogue.generated.vela-cave-warning",
                    "path": "content/dialogue/vela-cave-warning.json",
                }
            ],
            "evidence": [
                {
                    "id": "evidence.generated.vela-cave-log",
                    "path": "content/evidence/vela-cave-log.json",
                }
            ],
            "consequences": [
                {
                    "id": "consequence.generated.vela-gate-exposed",
                    "path": "content/consequences/vela-gate-exposed.json",
                }
            ],
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
            "objectiveTypes": ["objective-type.reach-extraction"],
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
        "provenance": {
            "requestId": "fixture-request",
            "sourceSnapshotId": "fixture-snapshot",
            "generatedBy": "test",
            "validationReportPath": "content/validation/schema-report.json",
            "simulationReportPath": "content/validation/simulation-report.json",
        },
    }


def test_gameplay_plugin_manifest_schema_declares_closed_v1_contract() -> None:
    schema = _load_schema()

    Draft202012Validator.check_schema(schema)

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == GAMEPLAY_PLUGIN_MANIFEST_SCHEMA
    assert schema["properties"]["schema"]["const"] == GAMEPLAY_PLUGIN_MANIFEST_SCHEMA
    assert schema["properties"]["manifestVersion"]["const"] == GAMEPLAY_PLUGIN_MANIFEST_VERSION
    assert schema["additionalProperties"] is False
    assert {
        "schema",
        "manifestVersion",
        "id",
        "kind",
        "origin",
        "scope",
        "content",
        "requires",
        "permissions",
        "validation",
        "rollback",
    }.issubset(set(schema["required"]))

    for definition_name in (
        "origin",
        "scope",
        "contentRef",
        "scenarioRef",
        "encounterRef",
        "contentFile",
        "content",
        "requirements",
        "permissions",
        "validation",
        "rollback",
    ):
        assert schema["$defs"][definition_name]["additionalProperties"] is False


def test_safe_manifest_passes_schema_and_semantic_validation() -> None:
    manifest = _valid_manifest()
    validator = Draft202012Validator(_load_schema())

    errors = sorted(validator.iter_errors(manifest), key=lambda error: error.path)
    assert errors == []
    assert validate_gameplay_plugin_manifest(manifest) == []
    assert_valid_gameplay_plugin_manifest(manifest)


def test_schema_blocks_runtime_mutation_permissions() -> None:
    manifest = _valid_manifest()
    manifest["permissions"]["allowExecutableCode"] = True

    errors = list(Draft202012Validator(_load_schema()).iter_errors(manifest))

    assert errors
    assert any("False was expected" in error.message for error in errors)


def test_semantic_validator_blocks_code_paths_and_missing_references() -> None:
    manifest = _valid_manifest()
    manifest["content"]["entryPoints"] = ["scenario.generated.missing-entry"]
    manifest["content"]["scenarios"][0]["encounterIds"] = ["encounter.generated.missing"]
    manifest["content"]["files"].append(
        {
            "kind": "manifest-support",
            "path": "content/scripts/generated-cheat.js",
        }
    )

    problems = validate_gameplay_plugin_manifest(manifest)

    assert any("entryPoints references undeclared scenarios" in problem for problem in problems)
    assert any("references missing encounter encounter.generated.missing" in problem for problem in problems)
    assert any("must not reference executable or runtime code" in problem for problem in problems)


def test_semantic_validator_requires_declared_template_archetype_and_objective_refs() -> None:
    manifest = _valid_manifest()
    manifest["requires"]["encounterTemplates"] = []
    manifest["requires"]["actorArchetypes"] = []
    manifest["requires"]["objectiveTypes"] = []

    problems = validate_gameplay_plugin_manifest(manifest)

    assert any("template references missing required template" in problem for problem in problems)
    assert any("actorArchetypeIds references missing required archetype" in problem for problem in problems)
    assert any("objectiveTypeIds references missing required objective type" in problem for problem in problems)


def test_assert_valid_manifest_raises_with_actionable_message() -> None:
    manifest = _valid_manifest()
    manifest["scope"]["cannotModify"].remove("renderer-code")

    with pytest.raises(ValueError) as exc_info:
        assert_valid_gameplay_plugin_manifest(manifest)

    message = str(exc_info.value)
    assert "Invalid gameplay plugin manifest" in message
    assert "renderer-code" in message
