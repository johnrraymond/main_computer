from __future__ import annotations

import json
from pathlib import Path

import pytest

from main_computer.gameplay_plugin_activation import (
    GAMEPLAY_PLUGIN_ACTIVATION_MANIFEST_PATH,
    GAMEPLAY_PLUGIN_ACTIVATION_STATUS_DISABLED,
    assert_gameplay_plugin_activation_disabled,
    assert_gameplay_plugin_activation_enabled,
)
from main_computer.gameplay_plugin_materializer import assert_valid_gameplay_plugin_materialization
from main_computer.gameplay_plugin_runtime_catalog import (
    GAMEPLAY_PLUGIN_RUNTIME_CATALOG_KIND,
    GAMEPLAY_PLUGIN_RUNTIME_CATALOG_RUNTIME_STATUS,
    GAMEPLAY_PLUGIN_RUNTIME_CATALOG_SCHEMA,
    GAMEPLAY_PLUGIN_RUNTIME_CATALOG_STATUS_READY,
    GAMEPLAY_PLUGIN_RUNTIME_CATALOG_STATUS_REJECTED,
    assert_valid_gameplay_plugin_runtime_catalog,
    build_gameplay_plugin_runtime_catalog,
    validate_gameplay_plugin_runtime_catalog,
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


def _project_with_fixture(tmp_path: Path) -> Path:
    project_root = tmp_path / "project"
    _copy_fixture(project_root / "plugins" / "hand_authored" / "vela_escape_extension")
    return project_root


def _materialized_project(tmp_path: Path) -> Path:
    project_root = _project_with_fixture(tmp_path)
    assert_valid_gameplay_plugin_materialization(project_root, PLUGIN_ID)
    return project_root


def _enabled_project(tmp_path: Path) -> Path:
    project_root = _materialized_project(tmp_path)
    assert_gameplay_plugin_activation_enabled(project_root, PLUGIN_ID)
    return project_root


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_missing_activation_manifest_builds_valid_empty_runtime_catalog(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()

    catalog = assert_valid_gameplay_plugin_runtime_catalog(project_root)

    assert catalog.kind == GAMEPLAY_PLUGIN_RUNTIME_CATALOG_KIND
    assert catalog.schema == GAMEPLAY_PLUGIN_RUNTIME_CATALOG_SCHEMA
    assert catalog.status == GAMEPLAY_PLUGIN_RUNTIME_CATALOG_STATUS_READY
    assert catalog.runtime_status == GAMEPLAY_PLUGIN_RUNTIME_CATALOG_RUNTIME_STATUS
    assert catalog.valid is True
    assert catalog.runtime_loaded is False
    assert catalog.project_json_modified is False
    assert catalog.activated_in_runtime is False
    assert catalog.entries == ()
    assert catalog.enabled_plugin_ids == ()
    assert catalog.disabled_plugin_ids == ()
    assert catalog.documents == ()
    assert catalog.problems == ()


def test_runtime_catalog_reports_enabled_generated_scenarios_and_encounters(tmp_path: Path) -> None:
    project_root = _enabled_project(tmp_path)

    catalog = assert_valid_gameplay_plugin_runtime_catalog(project_root)

    assert catalog.valid is True
    assert catalog.enabled_plugin_ids == (PLUGIN_ID,)
    assert catalog.disabled_plugin_ids == ()
    assert catalog.entry_points == ("scenario.plugin.vela-cave-extension.followup",)
    assert catalog.scenario_ids == ("scenario.plugin.vela-cave-extension.followup",)
    assert catalog.encounter_ids == ("encounter.plugin.vela-cave-extension.escape-route",)
    assert catalog.receipt_ids == ("receipt.plugin.vela-cave-extension.extracted",)
    assert catalog.consequence_ids == ("consequence.plugin.vela-cave-extension.route-charted",)

    assert len(catalog.entries) == 1
    entry = catalog.entries[0]
    assert entry.valid is True
    assert entry.plugin_id == PLUGIN_ID
    assert entry.generated_root == "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001"
    assert entry.target_layer == "generated-plugin-content"
    assert entry.runtime_loaded is False
    assert entry.project_json_modified is False
    assert entry.activated_in_runtime is False
    assert entry.problems == ()

    assert entry.document_paths == (
        "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001/content/scenarios/vela-cave-extension-followup.json",
        "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001/content/encounters/vela-cave-extension-escape-route.json",
    )


def test_runtime_catalog_compiles_document_metadata_for_future_runtime_loader(tmp_path: Path) -> None:
    project_root = _enabled_project(tmp_path)

    catalog = assert_valid_gameplay_plugin_runtime_catalog(project_root)
    entry = catalog.entries[0]

    assert [document.kind for document in catalog.documents] == ["scenario", "encounter"]

    scenario = entry.scenario_documents[0]
    assert scenario.plugin_id == PLUGIN_ID
    assert scenario.kind == "scenario"
    assert scenario.id == "scenario.plugin.vela-cave-extension.followup"
    assert scenario.title == "Vela Cave Extension Followup"
    assert scenario.stage_ids == ("route-opens", "escape-route", "extracted", "route-lost")
    assert scenario.encounter_ids == ("encounter.plugin.vela-cave-extension.escape-route",)
    assert scenario.receipt_ids == ("receipt.plugin.vela-cave-extension.extracted",)
    assert scenario.template == ""

    encounter = entry.encounter_documents[0]
    assert encounter.kind == "encounter"
    assert encounter.id == "encounter.plugin.vela-cave-extension.escape-route"
    assert encounter.template == "encounter-template.cave-combat-run"
    assert encounter.objective_types == (
        "objective-type.recover-item",
        "objective-type.clear-hostiles",
        "objective-type.reach-extraction",
    )
    assert encounter.actor_archetypes == ("actor-archetype.vela-cave-guard",)
    assert encounter.receipt_ids == ("receipt.plugin.vela-cave-extension.extracted",)
    assert encounter.consequence_types == ("consequence-type.record-receipt",)


def test_runtime_catalog_ignores_disabled_plugins_without_runtime_loading(tmp_path: Path) -> None:
    project_root = _materialized_project(tmp_path)
    assert_gameplay_plugin_activation_enabled(project_root, PLUGIN_ID)
    result = assert_gameplay_plugin_activation_disabled(project_root, PLUGIN_ID)

    assert result.status == GAMEPLAY_PLUGIN_ACTIVATION_STATUS_DISABLED

    catalog = assert_valid_gameplay_plugin_runtime_catalog(project_root)

    assert catalog.valid is True
    assert catalog.enabled_plugin_ids == ()
    assert catalog.disabled_plugin_ids == (PLUGIN_ID,)
    assert catalog.entries == ()
    assert catalog.documents == ()
    assert catalog.runtime_loaded is False
    assert catalog.project_json_modified is False
    assert catalog.activated_in_runtime is False


def test_runtime_catalog_rejects_invalid_activation_manifest(tmp_path: Path) -> None:
    project_root = _enabled_project(tmp_path)
    manifest_path = project_root / GAMEPLAY_PLUGIN_ACTIVATION_MANIFEST_PATH
    payload = _load_json(manifest_path)
    payload["runtimeLoaded"] = True
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    catalog = build_gameplay_plugin_runtime_catalog(project_root)

    assert catalog.status == GAMEPLAY_PLUGIN_RUNTIME_CATALOG_STATUS_REJECTED
    assert catalog.rejected is True
    assert catalog.valid is False
    assert any("runtime loading" in problem for problem in catalog.problems)


def test_runtime_catalog_rejects_missing_generated_content_file(tmp_path: Path) -> None:
    project_root = _enabled_project(tmp_path)
    generated_scenario = (
        project_root
        / "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001/content/scenarios/vela-cave-extension-followup.json"
    )
    generated_scenario.unlink()

    catalog = build_gameplay_plugin_runtime_catalog(project_root)

    assert catalog.rejected is True
    assert any("generated file is missing" in problem for problem in catalog.problems)
    validation_problems = validate_gameplay_plugin_runtime_catalog(project_root)
    assert any("generated file is missing" in problem for problem in validation_problems)


def test_runtime_catalog_rejects_generated_document_id_mismatch(tmp_path: Path) -> None:
    project_root = _enabled_project(tmp_path)
    generated_scenario = (
        project_root
        / "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001/content/scenarios/vela-cave-extension-followup.json"
    )
    payload = _load_json(generated_scenario)
    payload["id"] = "scenario.plugin.tampered"
    generated_scenario.write_text(json.dumps(payload), encoding="utf-8")

    catalog = build_gameplay_plugin_runtime_catalog(project_root)

    assert catalog.rejected is True
    assert any("id mismatch" in problem for problem in catalog.problems)

    with pytest.raises(ValueError, match="Invalid gameplay plugin runtime catalog"):
        assert_valid_gameplay_plugin_runtime_catalog(project_root)


def test_runtime_catalog_rejects_entry_point_missing_from_generated_scenarios(tmp_path: Path) -> None:
    project_root = _enabled_project(tmp_path)
    manifest_path = project_root / GAMEPLAY_PLUGIN_ACTIVATION_MANIFEST_PATH
    payload = _load_json(manifest_path)
    payload["entries"][0]["entryPoints"] = ["scenario.plugin.missing"]
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    catalog = build_gameplay_plugin_runtime_catalog(project_root)

    assert catalog.rejected is True
    assert any("entry point is not a generated scenario" in problem for problem in catalog.problems)
