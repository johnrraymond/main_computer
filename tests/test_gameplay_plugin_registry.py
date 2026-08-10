from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from main_computer.gameplay_plugin_registry import (
    GAMEPLAY_PLUGIN_ACTIVATION_STATUS_INERT,
    GAMEPLAY_PLUGIN_ENTRY_STATUS_INVALID,
    GAMEPLAY_PLUGIN_ENTRY_STATUS_VALID,
    assert_valid_gameplay_plugin_registry,
    discover_gameplay_plugin_registry,
    validate_gameplay_plugin_registry,
)


ROOT = Path(__file__).resolve().parents[1]
GAME_PROJECT = ROOT / "game_projects" / "webgl-demo"
FIXTURE_PACKAGE = GAME_PROJECT / "plugins" / "hand_authored" / "vela_escape_extension"


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


def test_registry_discovers_hand_authored_plugin_fixture_without_activation() -> None:
    registry = assert_valid_gameplay_plugin_registry(GAME_PROJECT)

    entry = registry.entry_for_plugin_id("plugin.hand-authored.vela-cave-extension.001")

    assert registry.valid is True
    assert registry.plugins_root == GAME_PROJECT / "plugins"
    assert registry.problems == ()
    assert entry is not None
    assert entry.status == GAMEPLAY_PLUGIN_ENTRY_STATUS_VALID
    assert entry.valid is True
    assert entry.inert is True
    assert entry.activated is False
    assert entry.activation_status == GAMEPLAY_PLUGIN_ACTIVATION_STATUS_INERT
    assert entry.relative_package_root == "hand_authored/vela_escape_extension"
    assert entry.source_kind == "hand_authored"
    assert entry.package is not None
    assert entry.package.plugin_id == "plugin.hand-authored.vela-cave-extension.001"


def test_registry_keeps_valid_and_invalid_entries_separate(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    valid_package = project_root / "plugins" / "hand_authored" / "vela_escape_extension"
    invalid_package = project_root / "plugins" / "generated" / "broken_extension"
    _copy_fixture(valid_package)
    _copy_fixture(invalid_package)

    manifest_path = invalid_package / "manifest.json"
    manifest = _load_json(manifest_path)
    manifest["id"] = "plugin.generated.broken-extension.001"
    manifest["content"]["scenarios"][0]["path"] = "content/scenarios/missing-scenario.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    registry = discover_gameplay_plugin_registry(project_root)

    assert registry.valid is False
    assert len(registry.entries) == 2
    assert [entry.relative_package_root for entry in registry.valid_entries] == [
        "hand_authored/vela_escape_extension"
    ]
    assert [entry.relative_package_root for entry in registry.invalid_entries] == [
        "generated/broken_extension"
    ]
    assert registry.invalid_entries[0].status == GAMEPLAY_PLUGIN_ENTRY_STATUS_INVALID
    assert any(
        "content package is missing declared file content/scenarios/missing-scenario.json"
        in problem
        for problem in registry.problems
    )


def test_registry_rejects_duplicate_plugin_ids(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    first = project_root / "plugins" / "hand_authored" / "first"
    second = project_root / "plugins" / "generated" / "second"
    _copy_fixture(first)
    _copy_fixture(second)

    problems = validate_gameplay_plugin_registry(project_root)

    assert any("duplicate gameplay plugin id plugin.hand-authored.vela-cave-extension.001" in problem for problem in problems)

    registry = discover_gameplay_plugin_registry(project_root)
    assert registry.valid is False
    assert {entry.status for entry in registry.entries} == {GAMEPLAY_PLUGIN_ENTRY_STATUS_INVALID}
    assert {entry.inert for entry in registry.entries} == {True}

    with pytest.raises(ValueError, match="duplicate gameplay plugin id plugin.hand-authored.vela-cave-extension.001"):
        assert_valid_gameplay_plugin_registry(project_root)


def test_registry_missing_plugins_directory_is_empty_and_valid(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()

    registry = assert_valid_gameplay_plugin_registry(project_root)

    assert registry.entries == ()
    assert registry.problems == ()
    assert registry.valid is True
    assert registry.plugins_root == project_root / "plugins"


def test_registry_reports_non_directory_plugins_root(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "plugins").write_text("not a directory", encoding="utf-8")

    problems = validate_gameplay_plugin_registry(project_root)

    assert problems == [f"gameplay plugin root must be a directory: {project_root / 'plugins'}"]
