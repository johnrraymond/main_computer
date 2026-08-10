from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from main_computer.gameplay_plugin_staging import (
    GAMEPLAY_PLUGIN_STAGING_PLAN_KIND,
    GAMEPLAY_PLUGIN_STAGING_STATUS_DRY_RUN,
    GAMEPLAY_PLUGIN_STAGING_STATUS_REJECTED,
    assert_valid_gameplay_plugin_staging_plan,
    build_gameplay_plugin_staging_plan,
    validate_gameplay_plugin_staging_plan,
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


def test_staging_plan_reports_what_valid_plugin_would_add_without_activation() -> None:
    plan = assert_valid_gameplay_plugin_staging_plan(GAME_PROJECT, PLUGIN_ID)

    assert plan.kind == GAMEPLAY_PLUGIN_STAGING_PLAN_KIND
    assert plan.status == GAMEPLAY_PLUGIN_STAGING_STATUS_DRY_RUN
    assert plan.valid is True
    assert plan.dry_run is True
    assert plan.activated is False
    assert plan.activation_status == "inert"
    assert plan.plugin_id == PLUGIN_ID
    assert plan.package_root == FIXTURE_PACKAGE
    assert plan.relative_package_root == "hand_authored/vela_escape_extension"

    assert plan.entry_points == ("scenario.plugin.vela-cave-extension.followup",)
    assert plan.scenario_ids == ("scenario.plugin.vela-cave-extension.followup",)
    assert plan.encounter_ids == ("encounter.plugin.vela-cave-extension.escape-route",)
    assert [scenario.path for scenario in plan.scenarios] == [
        "content/scenarios/vela-cave-extension-followup.json"
    ]
    assert [encounter.path for encounter in plan.encounters] == [
        "content/encounters/vela-cave-extension-escape-route.json"
    ]
    assert [receipt.id for receipt in plan.receipts] == [
        "receipt.plugin.vela-cave-extension.extracted"
    ]
    assert [consequence.id for consequence in plan.consequences] == [
        "consequence.plugin.vela-cave-extension.route-charted"
    ]
    assert plan.content_count == 4

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


def test_staging_plan_is_dry_run_only_and_does_not_modify_plugin_files() -> None:
    before_manifest = (FIXTURE_PACKAGE / "manifest.json").read_text(encoding="utf-8")
    before_scenario = (
        FIXTURE_PACKAGE / "content/scenarios/vela-cave-extension-followup.json"
    ).read_text(encoding="utf-8")

    plan = assert_valid_gameplay_plugin_staging_plan(GAME_PROJECT, PLUGIN_ID)

    assert plan.dry_run is True
    assert plan.activated is False
    assert (FIXTURE_PACKAGE / "manifest.json").read_text(encoding="utf-8") == before_manifest
    assert (
        FIXTURE_PACKAGE / "content/scenarios/vela-cave-extension-followup.json"
    ).read_text(encoding="utf-8") == before_scenario


def test_staging_plan_rejects_missing_plugin_id() -> None:
    plan = build_gameplay_plugin_staging_plan(GAME_PROJECT, "")

    assert plan.status == GAMEPLAY_PLUGIN_STAGING_STATUS_REJECTED
    assert plan.valid is False
    assert plan.problems == ("gameplay plugin id is required for staging",)


def test_staging_plan_rejects_unknown_plugin_id() -> None:
    problems = validate_gameplay_plugin_staging_plan(GAME_PROJECT, "plugin.missing")

    assert problems == ["gameplay plugin plugin.missing was not discovered"]


def test_staging_plan_rejects_invalid_selected_plugin(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    broken_package = project_root / "plugins" / "generated" / "broken"
    _copy_fixture(broken_package)

    manifest_path = broken_package / "manifest.json"
    manifest = _load_json(manifest_path)
    manifest["id"] = "plugin.generated.broken.001"
    manifest["content"]["scenarios"][0]["path"] = "content/scenarios/missing.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    plan = build_gameplay_plugin_staging_plan(project_root, "plugin.generated.broken.001")

    assert plan.status == GAMEPLAY_PLUGIN_STAGING_STATUS_REJECTED
    assert plan.valid is False
    assert any(
        "content package is missing declared file content/scenarios/missing.json" in problem
        for problem in plan.problems
    )

    with pytest.raises(ValueError, match="Invalid gameplay plugin staging plan"):
        assert_valid_gameplay_plugin_staging_plan(project_root, "plugin.generated.broken.001")


def test_staging_plan_rejects_duplicate_plugin_ids(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    _copy_fixture(project_root / "plugins" / "hand_authored" / "first")
    _copy_fixture(project_root / "plugins" / "generated" / "second")

    plan = build_gameplay_plugin_staging_plan(project_root, PLUGIN_ID)

    assert plan.status == GAMEPLAY_PLUGIN_STAGING_STATUS_REJECTED
    assert any(f"duplicate gameplay plugin id {PLUGIN_ID}" in problem for problem in plan.problems)
