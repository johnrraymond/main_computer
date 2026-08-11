from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from main_computer.gameplay_plugin_import_plan import (
    GAMEPLAY_PLUGIN_IMPORT_OPERATION_ADD_GENERATED_CONTENT,
    GAMEPLAY_PLUGIN_IMPORT_PLAN_KIND,
    GAMEPLAY_PLUGIN_IMPORT_STATUS_PREVIEW,
    GAMEPLAY_PLUGIN_IMPORT_STATUS_REJECTED,
    GAMEPLAY_PLUGIN_IMPORT_TARGET_LAYER,
    assert_valid_gameplay_plugin_import_plan,
    build_gameplay_plugin_import_plan,
    validate_gameplay_plugin_import_plan,
)


ROOT = Path(__file__).resolve().parents[1]
GAME_PROJECT = ROOT / "game_projects" / "webgl-demo"
FIXTURE_PACKAGE = GAME_PROJECT / "plugins" / "hand_authored" / "vela_escape_extension"
PLUGIN_ID = "plugin.hand-authored.vela-cave-extension.001"


def _copy_fixture(target: Path) -> Path:
    for source in FIXTURE_PACKAGE.rglob("*"):
        if source.is_dir():
            continue
        relative = source.relative_to(FIXTURE_PACKAGE)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    return target


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_import_plan_compiles_valid_plugin_content_without_activation() -> None:
    plan = assert_valid_gameplay_plugin_import_plan(GAME_PROJECT, PLUGIN_ID)

    assert plan.kind == GAMEPLAY_PLUGIN_IMPORT_PLAN_KIND
    assert plan.status == GAMEPLAY_PLUGIN_IMPORT_STATUS_PREVIEW
    assert plan.valid is True
    assert plan.dry_run is True
    assert plan.activated is False
    assert plan.target_layer == GAMEPLAY_PLUGIN_IMPORT_TARGET_LAYER
    assert plan.plugin_id == PLUGIN_ID
    assert plan.package_root == FIXTURE_PACKAGE
    assert plan.relative_package_root == "hand_authored/vela_escape_extension"

    assert plan.entry_points == ("scenario.plugin.vela-cave-extension.followup",)
    assert plan.scenario_ids == ("scenario.plugin.vela-cave-extension.followup",)
    assert plan.encounter_ids == ("encounter.plugin.vela-cave-extension.escape-route",)
    assert plan.receipt_ids == ("receipt.plugin.vela-cave-extension.extracted",)
    assert plan.consequence_ids == ("consequence.plugin.vela-cave-extension.route-charted",)

    assert plan.required_encounter_templates == ("encounter-template.cave-combat-run",)
    assert plan.required_actor_archetypes == ("actor-archetype.vela-cave-guard",)
    assert plan.required_objective_types == (
        "objective-type.clear-hostiles",
        "objective-type.recover-item",
        "objective-type.reach-extraction",
    )
    assert plan.required_consequence_types == ("consequence-type.record-receipt",)
    assert plan.allowed_systems == ("system.vela-gate",)
    assert plan.allowed_destinations == ("destination.vela-gate.subsurface-cavern",)
    assert plan.rollback_supported is True
    assert plan.rollback_mode == "disable-plugin"
    assert plan.provenance["requestId"] == "hand-authored-plugin-fixture"


def test_import_plan_reports_generated_content_operations() -> None:
    plan = assert_valid_gameplay_plugin_import_plan(GAME_PROJECT, PLUGIN_ID)

    assert plan.operation_count == 2
    assert [operation.operation for operation in plan.operations] == [
        GAMEPLAY_PLUGIN_IMPORT_OPERATION_ADD_GENERATED_CONTENT,
        GAMEPLAY_PLUGIN_IMPORT_OPERATION_ADD_GENERATED_CONTENT,
    ]
    assert [operation.content_kind for operation in plan.operations] == ["scenario", "encounter"]
    assert [operation.content_id for operation in plan.operations] == [
        "scenario.plugin.vela-cave-extension.followup",
        "encounter.plugin.vela-cave-extension.escape-route",
    ]
    assert [operation.target_layer for operation in plan.operations] == [
        GAMEPLAY_PLUGIN_IMPORT_TARGET_LAYER,
        GAMEPLAY_PLUGIN_IMPORT_TARGET_LAYER,
    ]
    assert [operation.reversible for operation in plan.operations] == [True, True]

    assert plan.target_paths == (
        "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001/content/scenarios/vela-cave-extension-followup.json",
        "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001/content/encounters/vela-cave-extension-escape-route.json",
    )


def test_import_plan_compiled_documents_preserve_scenario_and_encounter_shape() -> None:
    plan = assert_valid_gameplay_plugin_import_plan(GAME_PROJECT, PLUGIN_ID)

    scenario = next(document for document in plan.documents if document.kind == "scenario")
    encounter = next(document for document in plan.documents if document.kind == "encounter")

    assert scenario.stage_ids == ("route-opens", "escape-route", "extracted", "route-lost")
    assert scenario.encounter_ids == ("encounter.plugin.vela-cave-extension.escape-route",)
    assert scenario.receipt_ids == ("receipt.plugin.vela-cave-extension.extracted",)
    assert scenario.template == ""

    assert encounter.template == "encounter-template.cave-combat-run"
    assert encounter.objective_types == (
        "objective-type.recover-item",
        "objective-type.clear-hostiles",
        "objective-type.reach-extraction",
    )
    assert encounter.actor_archetypes == ("actor-archetype.vela-cave-guard",)
    assert encounter.receipt_ids == ("receipt.plugin.vela-cave-extension.extracted",)
    assert encounter.consequence_types == ("consequence-type.record-receipt",)


def test_import_plan_is_preview_only_and_does_not_write_generated_content() -> None:
    before_manifest = (FIXTURE_PACKAGE / "manifest.json").read_text(encoding="utf-8")
    plan = assert_valid_gameplay_plugin_import_plan(GAME_PROJECT, PLUGIN_ID)

    assert plan.dry_run is True
    assert plan.activated is False
    assert (FIXTURE_PACKAGE / "manifest.json").read_text(encoding="utf-8") == before_manifest
    for target_path in plan.target_paths:
        assert not (GAME_PROJECT / target_path).exists()


def test_import_plan_rejects_unknown_plugin_id() -> None:
    plan = build_gameplay_plugin_import_plan(GAME_PROJECT, "plugin.missing")

    assert plan.status == GAMEPLAY_PLUGIN_IMPORT_STATUS_REJECTED
    assert plan.valid is False
    assert plan.problems == ("gameplay plugin plugin.missing was not discovered",)


def test_import_plan_rejects_invalid_selected_plugin(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    broken_package = project_root / "plugins" / "generated" / "broken"
    _copy_fixture(broken_package)

    manifest_path = broken_package / "manifest.json"
    manifest = _load_json(manifest_path)
    manifest["id"] = "plugin.generated.broken.001"
    manifest["content"]["encounters"][0]["path"] = "content/encounters/missing.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    problems = validate_gameplay_plugin_import_plan(project_root, "plugin.generated.broken.001")

    assert any(
        "content package is missing declared file content/encounters/missing.json" in problem
        for problem in problems
    )

    with pytest.raises(ValueError, match="Invalid gameplay plugin import plan"):
        assert_valid_gameplay_plugin_import_plan(project_root, "plugin.generated.broken.001")


def test_import_plan_rejects_duplicate_plugin_ids(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _copy_fixture(project_root / "plugins" / "hand_authored" / "first")
    _copy_fixture(project_root / "plugins" / "generated" / "second")

    plan = build_gameplay_plugin_import_plan(project_root, PLUGIN_ID)

    assert plan.status == GAMEPLAY_PLUGIN_IMPORT_STATUS_REJECTED
    assert any(f"duplicate gameplay plugin id {PLUGIN_ID}" in problem for problem in plan.problems)
