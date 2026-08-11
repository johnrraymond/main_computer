from __future__ import annotations

import json
from pathlib import Path

from main_computer.gameplay_plugin_activation import (
    GAMEPLAY_PLUGIN_ACTIVATION_KIND,
    GAMEPLAY_PLUGIN_ACTIVATION_MANIFEST_PATH,
    GAMEPLAY_PLUGIN_ACTIVATION_RUNTIME_STATUS,
    GAMEPLAY_PLUGIN_ACTIVATION_SCHEMA,
    GAMEPLAY_PLUGIN_ACTIVATION_STATUS_DISABLED,
    GAMEPLAY_PLUGIN_ACTIVATION_STATUS_ENABLED,
    assert_gameplay_plugin_activation_disabled,
    assert_gameplay_plugin_activation_enabled,
    disable_gameplay_plugin,
    enable_gameplay_plugin,
    read_gameplay_plugin_activation_manifest,
    validate_gameplay_plugin_activation,
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


def _materialized_project(tmp_path: Path) -> Path:
    project_root = _project_with_fixture(tmp_path)
    assert_valid_gameplay_plugin_materialization(project_root, PLUGIN_ID)
    return project_root


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_missing_activation_manifest_is_valid_empty_metadata_snapshot(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()

    manifest = read_gameplay_plugin_activation_manifest(project_root)

    assert manifest.valid is True
    assert manifest.entries == ()
    assert manifest.enabled_plugin_ids == ()
    assert manifest.disabled_plugin_ids == ()
    assert manifest.relative_manifest_path == GAMEPLAY_PLUGIN_ACTIVATION_MANIFEST_PATH
    assert manifest.runtime_status == GAMEPLAY_PLUGIN_ACTIVATION_RUNTIME_STATUS
    assert manifest.runtime_loaded is False
    assert manifest.project_json_modified is False


def test_enable_materialized_plugin_writes_metadata_only_activation_manifest(tmp_path: Path) -> None:
    project_root = _materialized_project(tmp_path)

    result = assert_gameplay_plugin_activation_enabled(project_root, PLUGIN_ID)

    assert result.valid is True
    assert result.status == GAMEPLAY_PLUGIN_ACTIVATION_STATUS_ENABLED
    assert result.runtime_status == GAMEPLAY_PLUGIN_ACTIVATION_RUNTIME_STATUS
    assert result.runtime_loaded is False
    assert result.project_json_modified is False
    assert result.activated_in_runtime is False
    assert result.manifest_path == GAMEPLAY_PLUGIN_ACTIVATION_MANIFEST_PATH

    manifest_path = project_root / GAMEPLAY_PLUGIN_ACTIVATION_MANIFEST_PATH
    assert manifest_path.exists()
    payload = _load_json(manifest_path)
    assert payload["schema"] == GAMEPLAY_PLUGIN_ACTIVATION_SCHEMA
    assert payload["kind"] == GAMEPLAY_PLUGIN_ACTIVATION_KIND
    assert payload["runtimeStatus"] == GAMEPLAY_PLUGIN_ACTIVATION_RUNTIME_STATUS
    assert payload["runtimeLoaded"] is False
    assert payload["projectJsonModified"] is False
    assert len(payload["entries"]) == 1

    entry = payload["entries"][0]
    assert entry["pluginId"] == PLUGIN_ID
    assert entry["status"] == GAMEPLAY_PLUGIN_ACTIVATION_STATUS_ENABLED
    assert entry["generatedRoot"] == "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001"
    assert entry["receiptPath"].endswith("/materialization-receipt.json")
    assert entry["rollbackPath"].endswith("/rollback-plan.json")
    assert entry["entryPoints"] == ["scenario.plugin.vela-cave-extension.followup"]
    assert entry["scenarios"] == ["scenario.plugin.vela-cave-extension.followup"]
    assert entry["encounters"] == ["encounter.plugin.vela-cave-extension.escape-route"]
    assert entry["runtimeLoaded"] is False
    assert entry["projectJsonModified"] is False
    assert entry["activatedInRuntime"] is False

    reread = read_gameplay_plugin_activation_manifest(project_root)
    assert reread.valid is True
    assert reread.enabled_plugin_ids == (PLUGIN_ID,)
    assert reread.disabled_plugin_ids == ()
    assert reread.entry_for_plugin_id(PLUGIN_ID) is not None


def test_disable_materialized_plugin_updates_existing_activation_entry(tmp_path: Path) -> None:
    project_root = _materialized_project(tmp_path)
    assert_gameplay_plugin_activation_enabled(project_root, PLUGIN_ID)

    result = assert_gameplay_plugin_activation_disabled(project_root, PLUGIN_ID)

    assert result.valid is True
    assert result.status == GAMEPLAY_PLUGIN_ACTIVATION_STATUS_DISABLED
    assert result.entry is not None
    assert result.entry.disabled is True
    assert result.entry.safe is True

    manifest = read_gameplay_plugin_activation_manifest(project_root)
    assert len(manifest.entries) == 1
    assert manifest.enabled_plugin_ids == ()
    assert manifest.disabled_plugin_ids == (PLUGIN_ID,)


def test_activation_rejects_plugin_before_materialization_and_does_not_write_manifest(tmp_path: Path) -> None:
    project_root = _project_with_fixture(tmp_path)

    result = enable_gameplay_plugin(project_root, PLUGIN_ID)

    assert result.rejected is True
    assert any("not materialized" in problem for problem in result.problems)
    assert not (project_root / GAMEPLAY_PLUGIN_ACTIVATION_MANIFEST_PATH).exists()

    validation_problems = validate_gameplay_plugin_activation(project_root, PLUGIN_ID)
    assert any("not materialized" in problem for problem in validation_problems)


def test_activation_rejects_materialization_receipt_that_claims_runtime_loading(tmp_path: Path) -> None:
    project_root = _materialized_project(tmp_path)
    receipt_path = (
        project_root
        / "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001/materialization-receipt.json"
    )
    receipt = _load_json(receipt_path)
    receipt["runtimeLoaded"] = True
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = enable_gameplay_plugin(project_root, PLUGIN_ID)

    assert result.rejected is True
    assert any("must not claim runtime loading" in problem for problem in result.problems)
    assert not (project_root / GAMEPLAY_PLUGIN_ACTIVATION_MANIFEST_PATH).exists()


def test_read_manifest_rejects_duplicate_activation_entries(tmp_path: Path) -> None:
    project_root = _materialized_project(tmp_path)
    result = assert_gameplay_plugin_activation_enabled(project_root, PLUGIN_ID)
    payload = _load_json(project_root / GAMEPLAY_PLUGIN_ACTIVATION_MANIFEST_PATH)
    payload["entries"].append(dict(payload["entries"][0]))
    (project_root / GAMEPLAY_PLUGIN_ACTIVATION_MANIFEST_PATH).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    manifest = read_gameplay_plugin_activation_manifest(project_root)

    assert manifest.valid is False
    assert any("duplicate gameplay plugin activation entry" in problem for problem in manifest.problems)

    disabled = disable_gameplay_plugin(project_root, PLUGIN_ID)
    assert disabled.rejected is True
    assert any("duplicate gameplay plugin activation entry" in problem for problem in disabled.problems)
