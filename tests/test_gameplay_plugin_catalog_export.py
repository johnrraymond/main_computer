from __future__ import annotations

import json
from pathlib import Path

from main_computer.gameplay_plugin_activation import (
    assert_gameplay_plugin_activation_disabled,
    assert_gameplay_plugin_activation_enabled,
)
from main_computer.gameplay_plugin_catalog_export import (
    GAMEPLAY_PLUGIN_CATALOG_EXPORT_KIND,
    GAMEPLAY_PLUGIN_CATALOG_EXPORT_PATH,
    GAMEPLAY_PLUGIN_CATALOG_EXPORT_RUNTIME_STATUS,
    GAMEPLAY_PLUGIN_CATALOG_EXPORT_SCHEMA,
    GAMEPLAY_PLUGIN_CATALOG_EXPORT_STATUS_READY,
    assert_valid_gameplay_plugin_catalog_export,
    build_gameplay_plugin_catalog_export,
    validate_gameplay_plugin_catalog_export,
    write_gameplay_plugin_catalog_export,
)
from main_computer.gameplay_plugin_materializer import assert_valid_gameplay_plugin_materialization


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


def _enabled_project(tmp_path: Path) -> Path:
    project_root = _project_with_fixture(tmp_path)
    assert_valid_gameplay_plugin_materialization(project_root, PLUGIN_ID)
    assert_gameplay_plugin_activation_enabled(project_root, PLUGIN_ID)
    return project_root


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_catalog_export_preview_is_valid_empty_without_activation_manifest(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()

    result = assert_valid_gameplay_plugin_catalog_export(project_root)

    assert result.valid is True
    assert result.wrote_file is False
    assert result.relative_export_path == GAMEPLAY_PLUGIN_CATALOG_EXPORT_PATH
    assert result.export_path == project_root / GAMEPLAY_PLUGIN_CATALOG_EXPORT_PATH
    assert result.status == GAMEPLAY_PLUGIN_CATALOG_EXPORT_STATUS_READY
    assert result.runtime_status == GAMEPLAY_PLUGIN_CATALOG_EXPORT_RUNTIME_STATUS
    assert result.runtime_loaded is False
    assert result.project_json_modified is False
    assert result.activated_in_runtime is False

    payload = result.payload
    assert payload["schema"] == GAMEPLAY_PLUGIN_CATALOG_EXPORT_SCHEMA
    assert payload["kind"] == GAMEPLAY_PLUGIN_CATALOG_EXPORT_KIND
    assert payload["status"] == GAMEPLAY_PLUGIN_CATALOG_EXPORT_STATUS_READY
    assert payload["runtimeStatus"] == GAMEPLAY_PLUGIN_CATALOG_EXPORT_RUNTIME_STATUS
    assert payload["enabledPluginIds"] == []
    assert payload["plugins"] == []
    assert not result.export_path.exists()


def test_catalog_export_writes_enabled_generated_plugin_index(tmp_path: Path) -> None:
    project_root = _enabled_project(tmp_path)

    result = assert_valid_gameplay_plugin_catalog_export(project_root, write=True)

    assert result.valid is True
    assert result.wrote_file is True
    assert result.export_path.exists()
    assert result.relative_export_path == GAMEPLAY_PLUGIN_CATALOG_EXPORT_PATH

    payload = _load_json(result.export_path)
    assert payload == result.payload
    assert payload["schema"] == GAMEPLAY_PLUGIN_CATALOG_EXPORT_SCHEMA
    assert payload["kind"] == GAMEPLAY_PLUGIN_CATALOG_EXPORT_KIND
    assert payload["runtimeStatus"] == GAMEPLAY_PLUGIN_CATALOG_EXPORT_RUNTIME_STATUS
    assert payload["runtimeLoaded"] is False
    assert payload["projectJsonModified"] is False
    assert payload["activatedInRuntime"] is False

    assert payload["enabledPluginIds"] == [PLUGIN_ID]
    assert payload["entryPoints"] == ["scenario.plugin.vela-cave-extension.followup"]
    assert payload["scenarioIds"] == ["scenario.plugin.vela-cave-extension.followup"]
    assert payload["encounterIds"] == ["encounter.plugin.vela-cave-extension.escape-route"]
    assert payload["receiptIds"] == ["receipt.plugin.vela-cave-extension.extracted"]
    assert payload["consequenceIds"] == ["consequence.plugin.vela-cave-extension.route-charted"]

    assert len(payload["plugins"]) == 1
    plugin = payload["plugins"][0]
    assert plugin["pluginId"] == PLUGIN_ID
    assert plugin["generatedRoot"] == "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001"
    assert plugin["runtimeLoaded"] is False
    assert plugin["projectJsonModified"] is False
    assert plugin["activatedInRuntime"] is False

    assert len(payload["documents"]) == 2
    document_ids = {document["id"] for document in payload["documents"]}
    assert document_ids == {
        "scenario.plugin.vela-cave-extension.followup",
        "encounter.plugin.vela-cave-extension.escape-route",
    }
    scenario = next(document for document in payload["documents"] if document["kind"] == "scenario")
    assert scenario["stageIds"] == ["route-opens", "escape-route", "extracted", "route-lost"]
    assert scenario["encounterIds"] == ["encounter.plugin.vela-cave-extension.escape-route"]

    encounter = next(document for document in payload["documents"] if document["kind"] == "encounter")
    assert encounter["template"] == "encounter-template.cave-combat-run"
    assert encounter["objectiveTypes"] == [
        "objective-type.recover-item",
        "objective-type.clear-hostiles",
        "objective-type.reach-extraction",
    ]
    assert encounter["actorArchetypes"] == ["actor-archetype.vela-cave-guard"]


def test_catalog_export_is_deterministic_and_overwrite_is_explicit(tmp_path: Path) -> None:
    project_root = _enabled_project(tmp_path)

    first = assert_valid_gameplay_plugin_catalog_export(project_root, write=True)
    first_text = first.export_path.read_text(encoding="utf-8")

    rejected = write_gameplay_plugin_catalog_export(project_root)
    assert rejected.valid is False
    assert rejected.rejected is True
    assert rejected.wrote_file is False
    assert rejected.problems == (
        f"gameplay plugin catalog export already exists: {GAMEPLAY_PLUGIN_CATALOG_EXPORT_PATH}",
    )

    second = assert_valid_gameplay_plugin_catalog_export(project_root, write=True, overwrite=True)
    assert second.wrote_file is True
    assert second.export_path.read_text(encoding="utf-8") == first_text


def test_catalog_export_omits_disabled_plugins(tmp_path: Path) -> None:
    project_root = _project_with_fixture(tmp_path)
    assert_valid_gameplay_plugin_materialization(project_root, PLUGIN_ID)
    assert_gameplay_plugin_activation_enabled(project_root, PLUGIN_ID)
    assert_gameplay_plugin_activation_disabled(project_root, PLUGIN_ID)

    result = assert_valid_gameplay_plugin_catalog_export(project_root, write=True)
    payload = _load_json(result.export_path)

    assert payload["enabledPluginIds"] == []
    assert payload["disabledPluginIds"] == [PLUGIN_ID]
    assert payload["entryPoints"] == []
    assert payload["documents"] == []
    assert payload["plugins"] == []


def test_catalog_export_rejects_invalid_runtime_catalog_without_writing(tmp_path: Path) -> None:
    project_root = _enabled_project(tmp_path)
    generated_scenario = (
        project_root
        / "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001/"
        / "content/scenarios/vela-cave-extension-followup.json"
    )
    generated_scenario.unlink()

    problems = validate_gameplay_plugin_catalog_export(project_root)
    assert any("enabled gameplay plugin generated file is missing" in problem for problem in problems)

    result = write_gameplay_plugin_catalog_export(project_root)
    assert result.valid is False
    assert result.wrote_file is False
    assert not (project_root / GAMEPLAY_PLUGIN_CATALOG_EXPORT_PATH).exists()


def test_catalog_export_preview_matches_written_payload(tmp_path: Path) -> None:
    project_root = _enabled_project(tmp_path)

    preview = build_gameplay_plugin_catalog_export(project_root)
    written = assert_valid_gameplay_plugin_catalog_export(project_root, write=True)

    assert preview.valid is True
    assert preview.wrote_file is False
    assert written.wrote_file is True
    assert preview.payload == written.payload
