from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from main_computer.gameplay_plugin_package import (
    GAMEPLAY_PLUGIN_MANIFEST_FILE,
    assert_valid_gameplay_plugin_package_dir,
    read_gameplay_plugin_package,
    validate_gameplay_plugin_package_dir,
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
MANIFEST_SCHEMA_PATH = ROOT / "game_projects" / "schema" / "gameplay-plugin-manifest.v1.schema.json"
CONTENT_SCHEMA_PATH = ROOT / "game_projects" / "schema" / "gameplay-plugin-content.v1.schema.json"


def _copy_fixture(tmp_path: Path) -> Path:
    target = tmp_path / "vela_escape_extension"
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


def test_hand_authored_vela_plugin_fixture_has_expected_package_shape() -> None:
    assert (FIXTURE_PACKAGE / GAMEPLAY_PLUGIN_MANIFEST_FILE).exists()
    assert (FIXTURE_PACKAGE / "content/scenarios/vela-cave-extension-followup.json").exists()
    assert (FIXTURE_PACKAGE / "content/encounters/vela-cave-extension-escape-route.json").exists()


def test_hand_authored_vela_plugin_fixture_passes_json_schemas() -> None:
    manifest_schema = _load_json(MANIFEST_SCHEMA_PATH)
    content_schema = _load_json(CONTENT_SCHEMA_PATH)
    manifest_validator = Draft202012Validator(manifest_schema)
    content_validator = Draft202012Validator(content_schema)

    manifest = _load_json(FIXTURE_PACKAGE / GAMEPLAY_PLUGIN_MANIFEST_FILE)
    content_documents = [
        _load_json(FIXTURE_PACKAGE / "content/scenarios/vela-cave-extension-followup.json"),
        _load_json(FIXTURE_PACKAGE / "content/encounters/vela-cave-extension-escape-route.json"),
    ]

    assert [error.message for error in manifest_validator.iter_errors(manifest)] == []
    assert [
        error.message
        for document in content_documents
        for error in content_validator.iter_errors(document)
    ] == []


def test_package_loader_loads_manifest_and_declared_content_without_activation() -> None:
    package = assert_valid_gameplay_plugin_package_dir(FIXTURE_PACKAGE)

    assert package.plugin_id == "plugin.hand-authored.vela-cave-extension.001"
    assert package.manifest_path == FIXTURE_PACKAGE / GAMEPLAY_PLUGIN_MANIFEST_FILE
    assert package.declared_content_paths == (
        "content/encounters/vela-cave-extension-escape-route.json",
        "content/scenarios/vela-cave-extension-followup.json",
    )
    assert sorted(package.content_by_path) == sorted(package.declared_content_paths)

    permissions = package.manifest["permissions"]
    assert permissions["allowExecutableCode"] is False
    assert permissions["allowRuntimeStateMutation"] is False
    assert permissions["allowEngineFileMutation"] is False
    assert permissions["allowBaseContentMutation"] is False


def test_package_loader_reports_missing_manifest(tmp_path: Path) -> None:
    package_root = tmp_path / "empty-plugin"
    package_root.mkdir()

    package, problems = read_gameplay_plugin_package(package_root)

    assert package is None
    assert any("gameplay plugin manifest is missing" in problem for problem in problems)


def test_package_loader_reports_missing_declared_content(tmp_path: Path) -> None:
    package_root = _copy_fixture(tmp_path)
    (package_root / "content/encounters/vela-cave-extension-escape-route.json").unlink()

    problems = validate_gameplay_plugin_package_dir(package_root)

    assert any(
        "content package is missing declared file content/encounters/vela-cave-extension-escape-route.json"
        in problem
        for problem in problems
    )


def test_package_loader_rejects_undeclared_scenario_or_encounter_files(tmp_path: Path) -> None:
    package_root = _copy_fixture(tmp_path)
    rogue = package_root / "content/scenarios/rogue-extra.json"
    rogue.write_text(
        json.dumps(
            {
                "schema": "game.gameplayPluginContent.v1",
                "kind": "scenario",
                "id": "scenario.plugin.vela-cave-extension.rogue",
                "title": "Rogue Scenario",
                "entryStage": "start",
                "stages": [
                    {
                        "id": "start",
                        "kind": "resolution",
                        "title": "Rogue",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    problems = validate_gameplay_plugin_package_dir(package_root)

    assert any(
        "content package contains undeclared scenario/encounter file content/scenarios/rogue-extra.json"
        in problem
        for problem in problems
    )


def test_package_loader_rejects_manifest_path_escape(tmp_path: Path) -> None:
    package_root = _copy_fixture(tmp_path)
    manifest_path = package_root / GAMEPLAY_PLUGIN_MANIFEST_FILE
    manifest = _load_json(manifest_path)
    manifest["content"]["scenarios"][0]["path"] = "../escape.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    problems = validate_gameplay_plugin_package_dir(package_root)

    assert any("unsafe gameplay plugin path '../escape.json'" in problem for problem in problems)
    assert any("manifest.content.scenarios[0].path must be a safe relative POSIX path" in problem for problem in problems)
