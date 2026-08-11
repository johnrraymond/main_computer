from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from main_computer.gameplay_plugin_import_plan import assert_valid_gameplay_plugin_import_plan
from main_computer.gameplay_plugin_materializer import (
    GAMEPLAY_PLUGIN_MATERIALIZATION_KIND,
    GAMEPLAY_PLUGIN_MATERIALIZATION_RECEIPT_FILE,
    GAMEPLAY_PLUGIN_MATERIALIZATION_ROLLBACK_FILE,
    GAMEPLAY_PLUGIN_MATERIALIZATION_RUNTIME_STATUS,
    GAMEPLAY_PLUGIN_MATERIALIZATION_STATUS_MATERIALIZED,
    GAMEPLAY_PLUGIN_MATERIALIZATION_STATUS_REJECTED,
    assert_valid_gameplay_plugin_materialization,
    materialize_gameplay_plugin_generated_content,
    validate_gameplay_plugin_materialization,
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


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_materializer_writes_validated_plugin_content_to_generated_layer(tmp_path: Path) -> None:
    project_root = _project_with_fixture(tmp_path)
    import_plan = assert_valid_gameplay_plugin_import_plan(project_root, PLUGIN_ID)

    result = assert_valid_gameplay_plugin_materialization(project_root, PLUGIN_ID)

    assert result.kind == GAMEPLAY_PLUGIN_MATERIALIZATION_KIND
    assert result.status == GAMEPLAY_PLUGIN_MATERIALIZATION_STATUS_MATERIALIZED
    assert result.valid is True
    assert result.activated is False
    assert result.runtime_loaded is False
    assert result.project_json_modified is False
    assert result.runtime_status == GAMEPLAY_PLUGIN_MATERIALIZATION_RUNTIME_STATUS
    assert result.plugin_id == PLUGIN_ID
    assert result.target_layer == import_plan.target_layer
    assert result.relative_generated_root == (
        "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001"
    )
    assert result.file_count == 2

    assert result.target_paths == import_plan.target_paths
    for operation, materialized_file in zip(import_plan.operations, result.materialized_files, strict=True):
        source = project_root / "plugins" / "hand_authored" / "vela_escape_extension" / operation.source_path
        target = project_root / materialized_file.target_path

        assert target.exists()
        assert _load_json(target) == _load_json(source)
        assert materialized_file.content_kind == operation.content_kind
        assert materialized_file.content_id == operation.content_id
        assert materialized_file.source_path == operation.source_path
        assert materialized_file.bytes_written == target.stat().st_size
        assert len(materialized_file.sha256) == 64


def test_materializer_writes_activation_receipt_and_rollback_manifest(tmp_path: Path) -> None:
    project_root = _project_with_fixture(tmp_path)

    result = assert_valid_gameplay_plugin_materialization(project_root, PLUGIN_ID)

    receipt_path = project_root / result.receipt_path
    rollback_path = project_root / result.rollback_path
    assert result.receipt_path == (
        "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001/"
        f"{GAMEPLAY_PLUGIN_MATERIALIZATION_RECEIPT_FILE}"
    )
    assert result.rollback_path == (
        "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001/"
        f"{GAMEPLAY_PLUGIN_MATERIALIZATION_ROLLBACK_FILE}"
    )
    assert receipt_path.exists()
    assert rollback_path.exists()

    receipt = _load_json(receipt_path)
    rollback = _load_json(rollback_path)

    assert receipt["schema"] == "game.gameplayPluginMaterializationReceipt.v1"
    assert receipt["pluginId"] == PLUGIN_ID
    assert receipt["status"] == GAMEPLAY_PLUGIN_MATERIALIZATION_STATUS_MATERIALIZED
    assert receipt["runtimeStatus"] == GAMEPLAY_PLUGIN_MATERIALIZATION_RUNTIME_STATUS
    assert receipt["activated"] is False
    assert receipt["runtimeLoaded"] is False
    assert receipt["projectJsonModified"] is False
    assert receipt["entryPoints"] == ["scenario.plugin.vela-cave-extension.followup"]
    assert receipt["scenarios"] == ["scenario.plugin.vela-cave-extension.followup"]
    assert receipt["encounters"] == ["encounter.plugin.vela-cave-extension.escape-route"]
    assert receipt["rollback"]["supported"] is True
    assert receipt["rollback"]["mode"] == "disable-plugin"
    assert [file["targetPath"] for file in receipt["files"]] == list(result.target_paths)

    assert rollback["schema"] == "game.gameplayPluginRollbackPlan.v1"
    assert rollback["pluginId"] == PLUGIN_ID
    assert rollback["supported"] is True
    assert rollback["mode"] == "disable-plugin"
    assert rollback["activated"] is False
    assert rollback["runtimeLoaded"] is False
    assert rollback["projectJsonModified"] is False
    assert rollback["deleteGeneratedFiles"] == list(result.target_paths)
    assert rollback["deleteMetadataFiles"] == [result.receipt_path, result.rollback_path]
    assert rollback["preserveBaseContent"] is True
    assert rollback["preserveRuntimeState"] is True


def test_materializer_does_not_modify_plugin_package_or_project_manifest(tmp_path: Path) -> None:
    project_root = _project_with_fixture(tmp_path)
    package_root = project_root / "plugins" / "hand_authored" / "vela_escape_extension"
    before_manifest = (package_root / "manifest.json").read_text(encoding="utf-8")
    before_scenario = (
        package_root / "content/scenarios/vela-cave-extension-followup.json"
    ).read_text(encoding="utf-8")
    project_manifest = project_root / "project.json"
    project_manifest.write_text('{"id":"webgl-demo"}\n', encoding="utf-8")
    before_project_manifest = project_manifest.read_text(encoding="utf-8")

    result = assert_valid_gameplay_plugin_materialization(project_root, PLUGIN_ID)

    assert result.valid is True
    assert (package_root / "manifest.json").read_text(encoding="utf-8") == before_manifest
    assert (
        package_root / "content/scenarios/vela-cave-extension-followup.json"
    ).read_text(encoding="utf-8") == before_scenario
    assert project_manifest.read_text(encoding="utf-8") == before_project_manifest


def test_materializer_validation_preview_does_not_write_generated_files(tmp_path: Path) -> None:
    project_root = _project_with_fixture(tmp_path)

    problems = validate_gameplay_plugin_materialization(project_root, PLUGIN_ID)

    assert problems == []
    assert not (project_root / "generated").exists()


def test_materializer_rejects_existing_generated_content_without_overwrite(tmp_path: Path) -> None:
    project_root = _project_with_fixture(tmp_path)
    first = assert_valid_gameplay_plugin_materialization(project_root, PLUGIN_ID)
    assert first.valid is True

    second = materialize_gameplay_plugin_generated_content(project_root, PLUGIN_ID)

    assert second.status == GAMEPLAY_PLUGIN_MATERIALIZATION_STATUS_REJECTED
    assert second.valid is False
    assert any("generated gameplay plugin target already exists" in problem for problem in second.problems)
    assert any("generated gameplay plugin metadata already exists" in problem for problem in second.problems)

    with pytest.raises(ValueError, match="Invalid gameplay plugin materialization"):
        assert_valid_gameplay_plugin_materialization(project_root, PLUGIN_ID)


def test_materializer_allows_explicit_overwrite_of_generated_layer(tmp_path: Path) -> None:
    project_root = _project_with_fixture(tmp_path)
    first = assert_valid_gameplay_plugin_materialization(project_root, PLUGIN_ID)
    target = project_root / first.target_paths[0]
    target.write_text('{"corrupted": true}\n', encoding="utf-8")

    second = assert_valid_gameplay_plugin_materialization(project_root, PLUGIN_ID, overwrite=True)

    assert second.valid is True
    assert _load_json(target)["id"] == "scenario.plugin.vela-cave-extension.followup"


def test_materializer_rejects_unknown_or_invalid_plugins(tmp_path: Path) -> None:
    project_root = _project_with_fixture(tmp_path)

    assert validate_gameplay_plugin_materialization(project_root, "plugin.missing") == [
        "gameplay plugin plugin.missing was not discovered"
    ]

    broken_root = tmp_path / "broken-project"
    broken_package = broken_root / "plugins" / "generated" / "broken"
    _copy_fixture(broken_package)
    manifest_path = broken_package / "manifest.json"
    manifest = _load_json(manifest_path)
    manifest["id"] = "plugin.generated.broken.001"
    manifest["content"]["scenarios"][0]["path"] = "content/scenarios/missing.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    problems = validate_gameplay_plugin_materialization(broken_root, "plugin.generated.broken.001")
    assert any(
        "content package is missing declared file content/scenarios/missing.json" in problem
        for problem in problems
    )


def test_materializer_rejects_duplicate_plugin_ids(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _copy_fixture(project_root / "plugins" / "hand_authored" / "first")
    _copy_fixture(project_root / "plugins" / "generated" / "second")

    result = materialize_gameplay_plugin_generated_content(project_root, PLUGIN_ID)

    assert result.status == GAMEPLAY_PLUGIN_MATERIALIZATION_STATUS_REJECTED
    assert any(f"duplicate gameplay plugin id {PLUGIN_ID}" in problem for problem in result.problems)
