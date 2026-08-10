from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from main_computer.gameplay_plugin_package import validate_gameplay_plugin_package_dir
from main_computer.gameplay_template_registry import (
    GAMEPLAY_TEMPLATE_REGISTRY_SCHEMA,
    GAMEPLAY_TEMPLATE_REGISTRY_VERSION,
    assert_gameplay_plugin_matches_template_registry,
    default_gameplay_template_registry,
    validate_gameplay_plugin_content_against_template_registry,
    validate_gameplay_plugin_manifest_against_template_registry,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PACKAGE = (
    ROOT
    / "game_projects"
    / "webgl-demo"
    / "plugins"
    / "hand_authored"
    / "vela_escape_extension"
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_manifest() -> dict[str, Any]:
    return _load_json(FIXTURE_PACKAGE / "manifest.json")


def _fixture_content_by_path() -> dict[str, Any]:
    return {
        "content/scenarios/vela-cave-extension-followup.json": _load_json(
            FIXTURE_PACKAGE / "content/scenarios/vela-cave-extension-followup.json"
        ),
        "content/encounters/vela-cave-extension-escape-route.json": _load_json(
            FIXTURE_PACKAGE / "content/encounters/vela-cave-extension-escape-route.json"
        ),
    }


def _copy_fixture(target: Path) -> Path:
    for source in FIXTURE_PACKAGE.rglob("*"):
        if source.is_dir():
            continue
        relative = source.relative_to(FIXTURE_PACKAGE)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    return target


def test_default_template_registry_names_supported_gameplay_primitives() -> None:
    registry = default_gameplay_template_registry()

    assert registry.schema == GAMEPLAY_TEMPLATE_REGISTRY_SCHEMA
    assert registry.registry_version == GAMEPLAY_TEMPLATE_REGISTRY_VERSION
    assert registry.template_ids == (
        "encounter-template.boarding-defense",
        "encounter-template.cave-combat-run",
        "encounter-template.surface-transporter-extraction",
        "encounter-template.shuttle-ambush",
        "encounter-template.social-investigation",
    )
    assert "objective-type.clear-hostiles" in registry.objective_types
    assert "objective-type.recover-item" in registry.objective_types
    assert "objective-type.reach-extraction" in registry.objective_types
    assert "actor-archetype.vela-cave-guard" in registry.actor_archetypes
    assert "consequence-type.record-receipt" in registry.consequence_types


def test_hand_authored_vela_fixture_matches_template_registry() -> None:
    manifest = _fixture_manifest()
    content_by_path = _fixture_content_by_path()

    assert validate_gameplay_plugin_manifest_against_template_registry(manifest) == []
    assert validate_gameplay_plugin_content_against_template_registry(manifest, content_by_path) == []
    assert_gameplay_plugin_matches_template_registry(manifest, content_by_path)


def test_manifest_validator_rejects_unknown_template_registry_ids() -> None:
    manifest = _fixture_manifest()
    manifest["requires"]["encounterTemplates"] = ["encounter-template.imaginary"]
    manifest["content"]["encounters"][0]["template"] = "encounter-template.imaginary"

    problems = validate_gameplay_plugin_manifest_against_template_registry(manifest)

    assert any(
        "manifest.requires.encounterTemplates contains ids not in gameplay template registry"
        in problem
        for problem in problems
    )
    assert any(
        "manifest.content.encounters[0].template is not in gameplay template registry"
        in problem
        for problem in problems
    )


def test_registry_rejects_objectives_that_known_template_does_not_support() -> None:
    manifest = _fixture_manifest()
    content_by_path = copy.deepcopy(_fixture_content_by_path())

    manifest["requires"]["objectiveTypes"].append("objective-type.gather-evidence")
    manifest["content"]["encounters"][0]["objectiveTypeIds"].append("objective-type.gather-evidence")
    encounter = content_by_path["content/encounters/vela-cave-extension-escape-route.json"]
    encounter["objectives"][0]["type"] = "objective-type.gather-evidence"

    manifest_problems = validate_gameplay_plugin_manifest_against_template_registry(manifest)
    content_problems = validate_gameplay_plugin_content_against_template_registry(manifest, content_by_path)

    assert any(
        "objective-type.gather-evidence which is not allowed by template encounter-template.cave-combat-run"
        in problem
        for problem in manifest_problems
    )
    assert any(
        "content.encounters[0].objectives[0].type objective-type.gather-evidence is not allowed by template "
        "encounter-template.cave-combat-run"
        in problem
        for problem in content_problems
    )


def test_package_validation_runs_template_registry_checks(tmp_path: Path) -> None:
    package_root = _copy_fixture(tmp_path / "bad_template_plugin")
    manifest_path = package_root / "manifest.json"
    encounter_path = package_root / "content/encounters/vela-cave-extension-escape-route.json"

    manifest = _load_json(manifest_path)
    encounter = _load_json(encounter_path)

    manifest["requires"]["objectiveTypes"].append("objective-type.gather-evidence")
    manifest["content"]["encounters"][0]["objectiveTypeIds"].append("objective-type.gather-evidence")
    encounter["objectives"][0]["type"] = "objective-type.gather-evidence"

    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    encounter_path.write_text(json.dumps(encounter), encoding="utf-8")

    problems = validate_gameplay_plugin_package_dir(package_root)

    assert any(
        "objective-type.gather-evidence which is not allowed by template encounter-template.cave-combat-run"
        in problem
        for problem in problems
    )
    assert any(
        "content.encounters[0].objectives[0].type objective-type.gather-evidence is not allowed by template "
        "encounter-template.cave-combat-run"
        in problem
        for problem in problems
    )


def test_assertion_reports_template_registry_problems() -> None:
    manifest = _fixture_manifest()
    manifest["requires"]["actorArchetypes"] = ["actor-archetype.imaginary"]
    manifest["content"]["encounters"][0]["actorArchetypeIds"] = ["actor-archetype.imaginary"]

    with pytest.raises(ValueError, match="Invalid gameplay plugin template registry references"):
        assert_gameplay_plugin_matches_template_registry(manifest, _fixture_content_by_path())
