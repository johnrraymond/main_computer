from __future__ import annotations

import json
from pathlib import Path

from main_computer.gameplay_plugin_activation import assert_gameplay_plugin_activation_enabled
from main_computer.gameplay_plugin_catalog_export import write_gameplay_plugin_catalog_export
from main_computer.gameplay_plugin_materializer import assert_valid_gameplay_plugin_materialization
from main_computer.gameplay_plugin_project_catalog import (
    GAMEPLAY_PLUGIN_PROJECT_CATALOG_METADATA_KEY,
    GAMEPLAY_PLUGIN_PROJECT_CATALOG_RUNTIME_STATUS,
    GAMEPLAY_PLUGIN_PROJECT_CATALOG_SCHEMA,
    GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_ABSENT,
    GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_READY,
    GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_REJECTED,
    apply_gameplay_plugin_project_catalog,
    assert_valid_gameplay_plugin_project_catalog,
    read_gameplay_plugin_project_catalog,
    validate_gameplay_plugin_project_catalog,
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
    project_root = tmp_path / "webgl-demo"
    _copy_fixture(project_root / "plugins" / "hand_authored" / "vela_escape_extension")
    return project_root


def _exported_project(tmp_path: Path) -> Path:
    project_root = _project_with_fixture(tmp_path)
    assert_valid_gameplay_plugin_materialization(project_root, PLUGIN_ID)
    assert_gameplay_plugin_activation_enabled(project_root, PLUGIN_ID)
    export = write_gameplay_plugin_catalog_export(project_root)
    assert export.valid is True
    assert export.wrote_file is True
    return project_root


def test_project_catalog_is_valid_empty_static_payload_without_export(tmp_path: Path) -> None:
    project_root = tmp_path / "webgl-demo"
    project_root.mkdir()

    catalog = assert_valid_gameplay_plugin_project_catalog(project_root, project_id="webgl-demo")

    assert catalog.valid is True
    assert catalog.status == GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_ABSENT
    assert catalog.exported is False
    assert catalog.runtime_status == GAMEPLAY_PLUGIN_PROJECT_CATALOG_RUNTIME_STATUS
    assert catalog.runtime_loaded is False
    assert catalog.project_json_modified is False
    assert catalog.activated_in_runtime is False

    payload = catalog.payload
    assert payload["schema"] == GAMEPLAY_PLUGIN_PROJECT_CATALOG_SCHEMA
    assert payload["status"] == GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_ABSENT
    assert payload["projectId"] == "webgl-demo"
    assert payload["exported"] is False
    assert payload["enabledPluginIds"] == []
    assert payload["entryPoints"] == []
    assert payload["scenarios"] == []
    assert payload["encounters"] == []


def test_project_catalog_reads_exported_generated_scenarios_for_browser_project_data(tmp_path: Path) -> None:
    project_root = _exported_project(tmp_path)

    catalog = assert_valid_gameplay_plugin_project_catalog(project_root, project_id="webgl-demo")

    assert catalog.valid is True
    assert catalog.status == GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_READY
    assert catalog.exported is True
    assert catalog.enabled_plugin_ids == (PLUGIN_ID,)
    assert catalog.entry_points == ("scenario.plugin.vela-cave-extension.followup",)
    assert catalog.scenario_ids == ("scenario.plugin.vela-cave-extension.followup",)
    assert catalog.encounter_ids == ("encounter.plugin.vela-cave-extension.escape-route",)
    assert catalog.runtime_loaded is False
    assert catalog.project_json_modified is False
    assert catalog.activated_in_runtime is False

    payload = catalog.payload
    assert payload["sourceCatalog"]["status"] == "ready"
    assert payload["enabledPluginIds"] == [PLUGIN_ID]
    assert payload["entryPoints"] == ["scenario.plugin.vela-cave-extension.followup"]
    assert payload["scenarioIds"] == ["scenario.plugin.vela-cave-extension.followup"]
    assert payload["encounterIds"] == ["encounter.plugin.vela-cave-extension.escape-route"]

    assert [scenario["id"] for scenario in payload["scenarios"]] == [
        "scenario.plugin.vela-cave-extension.followup"
    ]
    assert [encounter["id"] for encounter in payload["encounters"]] == [
        "encounter.plugin.vela-cave-extension.escape-route"
    ]
    assert payload["encounters"][0]["template"] == "encounter-template.cave-combat-run"


def test_apply_project_catalog_exposes_metadata_without_mutating_project_json_payload(tmp_path: Path) -> None:
    project_root = _exported_project(tmp_path)
    project = {"id": "webgl-demo", "metadata": {"existing": True}}

    projected = apply_gameplay_plugin_project_catalog(project_root, project, project_id="webgl-demo")

    assert project == {"id": "webgl-demo", "metadata": {"existing": True}}
    metadata = projected["metadata"]
    assert metadata["existing"] is True
    assert GAMEPLAY_PLUGIN_PROJECT_CATALOG_METADATA_KEY in metadata

    generated_catalog = metadata[GAMEPLAY_PLUGIN_PROJECT_CATALOG_METADATA_KEY]
    assert generated_catalog["status"] == GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_READY
    assert generated_catalog["runtimeLoaded"] is False
    assert generated_catalog["projectJsonModified"] is False
    assert generated_catalog["activatedInRuntime"] is False
    assert generated_catalog["entryPoints"] == ["scenario.plugin.vela-cave-extension.followup"]


def test_project_catalog_rejects_export_that_claims_runtime_loading(tmp_path: Path) -> None:
    project_root = _exported_project(tmp_path)
    export_path = project_root / "generated/gameplay-plugins/runtime-catalog.json"
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    payload["runtimeLoaded"] = True
    export_path.write_text(json.dumps(payload), encoding="utf-8")

    problems = validate_gameplay_plugin_project_catalog(project_root, project_id="webgl-demo")
    assert "generated gameplay catalog export claims runtime loading" in problems

    catalog = read_gameplay_plugin_project_catalog(project_root, project_id="webgl-demo")
    assert catalog.valid is False
    assert catalog.rejected is True
    assert catalog.status == GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_REJECTED
    assert catalog.payload["problems"] == list(catalog.problems)


def test_project_catalog_rejects_unsafe_generated_document_paths(tmp_path: Path) -> None:
    project_root = _exported_project(tmp_path)
    export_path = project_root / "generated/gameplay-plugins/runtime-catalog.json"
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    payload["documentPaths"] = ["generated/gameplay-plugins/ok.json", "../escape.json"]
    payload["documents"][0]["path"] = "generated/../project.json"
    export_path.write_text(json.dumps(payload), encoding="utf-8")

    catalog = read_gameplay_plugin_project_catalog(project_root, project_id="webgl-demo")

    assert catalog.rejected is True
    assert any("document path is unsafe" in problem for problem in catalog.problems)


def test_game_project_read_payload_exposes_generated_catalog_to_browser_project_data(tmp_path: Path) -> None:
    from main_computer.viewport_routes_game import ViewportGameRoutesMixin

    project_root = _exported_project(tmp_path)
    (project_root / "project.json").write_text(
        json.dumps({"id": "webgl-demo", "name": "Demo", "metadata": {"createdBy": "test"}}),
        encoding="utf-8",
    )

    class _GpuForge:
        def apply_prebuilt_atlas_binding(self, *, project_id, project, assets_root):  # noqa: ANN001
            return project

    class _Harness(ViewportGameRoutesMixin):
        def __init__(self, root: Path) -> None:
            self.root = root

        def _game_project_root(self, project_id: str) -> Path:
            assert project_id == "webgl-demo"
            return self.root

        def _game_gpu_forge_service(self) -> _GpuForge:
            return _GpuForge()

        def _game_project_apply_runtime_migrations(self, project):  # noqa: ANN001
            return project

        def _game_file_shared(self, path: Path) -> dict:
            return {"content_hash": "hash", "mtime": 1.0, "bytes": path.stat().st_size}

    payload = _Harness(project_root)._game_project_read_payload("webgl-demo")

    assert payload["ok"] is True
    assert payload["project_id"] == "webgl-demo"
    assert payload["generated_gameplay_catalog"]["status"] == GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_READY

    browser_catalog = payload["project"]["metadata"][GAMEPLAY_PLUGIN_PROJECT_CATALOG_METADATA_KEY]
    assert browser_catalog == payload["generated_gameplay_catalog"]
    assert browser_catalog["runtimeLoaded"] is False
    assert browser_catalog["projectJsonModified"] is False
    assert browser_catalog["activatedInRuntime"] is False
    assert browser_catalog["entryPoints"] == ["scenario.plugin.vela-cave-extension.followup"]
