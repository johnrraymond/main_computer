from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

from main_computer.gameplay_plugin_activation import assert_gameplay_plugin_activation_enabled
from main_computer.gameplay_plugin_catalog_export import write_gameplay_plugin_catalog_export
from main_computer.gameplay_plugin_import_plan import assert_valid_gameplay_plugin_import_plan
from main_computer.gameplay_plugin_materializer import assert_valid_gameplay_plugin_materialization
from main_computer.gameplay_plugin_package import assert_valid_gameplay_plugin_package_dir
from main_computer.gameplay_plugin_project_catalog import assert_valid_gameplay_plugin_project_catalog
from main_computer.gameplay_plugin_staging import assert_valid_gameplay_plugin_staging_plan
from main_computer.gameplay_template_registry import assert_gameplay_plugin_matches_template_registry


ROOT = Path(__file__).resolve().parents[1]
GAME_PROJECT = ROOT / "game_projects" / "webgl-demo"
SCRIPT_ROOT = ROOT / "main_computer" / "web" / "applications" / "scripts"
SCENARIO_RUNTIME = SCRIPT_ROOT / "system-scenario-runtime.js"
FIXTURE_PACKAGE = (
    GAME_PROJECT
    / "plugins"
    / "hand_authored"
    / "opening_shuttle_ambush_extension"
)
PLUGIN_ID = "plugin.hand-authored.opening-shuttle-ambush.001"
SCENARIO_ID = "scenario.plugin.opening-shuttle-ambush.elite-wave"
ENCOUNTER_ID = "encounter.plugin.opening-shuttle-ambush.elite-wave"
RECEIPT_ID = "receipt.plugin.opening-shuttle-ambush.cleared"
CONSEQUENCE_ID = "consequence.plugin.opening-shuttle-ambush.system-marked"


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
    _copy_fixture(project_root / "plugins" / "hand_authored" / "opening_shuttle_ambush_extension")
    return project_root


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_node(script: str) -> dict:
    if not shutil.which("node"):
        raise RuntimeError("node is required for system scenario runtime fixture preview tests")
    result = subprocess.run(
        ["node", "-e", textwrap.dedent(script), str(SCENARIO_RUNTIME)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def test_opening_shuttle_plugin_fixture_matches_template_registry() -> None:
    package = assert_valid_gameplay_plugin_package_dir(FIXTURE_PACKAGE)

    assert package.plugin_id == PLUGIN_ID
    assert package.declared_content_paths == (
        "content/encounters/opening-shuttle-ambush-elite-wave.json",
        "content/scenarios/opening-shuttle-ambush-elite-wave.json",
    )

    assert_gameplay_plugin_matches_template_registry(package.manifest, package.content_by_path)

    encounter = package.content_by_path["content/encounters/opening-shuttle-ambush-elite-wave.json"]
    assert encounter["template"] == "encounter-template.shuttle-ambush"
    assert encounter["location"] == {
        "systemId": "system.solace-reach",
        "destinationId": "destination.solace-reach.haven-orbit",
    }
    assert [objective["type"] for objective in encounter["objectives"]] == [
        "objective-type.survive",
        "objective-type.clear-hostiles",
        "objective-type.reach-destination",
    ]
    assert encounter["participants"] == [
        {
            "role": "hostile",
            "actorArchetypeId": "actor-archetype.shuttle-raider",
            "count": 3,
        }
    ]


def test_opening_shuttle_plugin_fixture_materializes_and_exports_without_runtime_activation(
    tmp_path: Path,
) -> None:
    project_root = _project_with_fixture(tmp_path)

    staging = assert_valid_gameplay_plugin_staging_plan(project_root, PLUGIN_ID)
    assert staging.entry_points == (SCENARIO_ID,)
    assert staging.scenario_ids == (SCENARIO_ID,)
    assert staging.encounter_ids == (ENCOUNTER_ID,)
    assert [receipt.id for receipt in staging.receipts] == [RECEIPT_ID]
    assert [consequence.id for consequence in staging.consequences] == [CONSEQUENCE_ID]
    assert staging.required_encounter_templates == ("encounter-template.shuttle-ambush",)
    assert staging.required_actor_archetypes == ("actor-archetype.shuttle-raider",)
    assert staging.required_objective_types == (
        "objective-type.survive",
        "objective-type.clear-hostiles",
        "objective-type.reach-destination",
    )
    assert staging.required_consequence_types == (
        "consequence-type.record-receipt",
        "consequence-type.mark-system",
    )
    assert staging.allowed_systems == ("system.solace-reach",)
    assert staging.allowed_destinations == ("destination.solace-reach.haven-orbit",)
    assert staging.content_count == 4

    import_plan = assert_valid_gameplay_plugin_import_plan(project_root, PLUGIN_ID)
    assert import_plan.target_paths == (
        "generated/gameplay-plugins/plugin.hand-authored.opening-shuttle-ambush.001/content/scenarios/opening-shuttle-ambush-elite-wave.json",
        "generated/gameplay-plugins/plugin.hand-authored.opening-shuttle-ambush.001/content/encounters/opening-shuttle-ambush-elite-wave.json",
    )
    assert import_plan.receipt_ids == (RECEIPT_ID,)
    assert import_plan.consequence_ids == (CONSEQUENCE_ID,)

    materialization = assert_valid_gameplay_plugin_materialization(project_root, PLUGIN_ID)
    assert materialization.valid is True
    assert materialization.file_count == 2
    assert materialization.activated is False
    assert materialization.runtime_loaded is False
    assert materialization.project_json_modified is False

    activation = assert_gameplay_plugin_activation_enabled(project_root, PLUGIN_ID)
    assert activation.valid is True
    assert activation.entry is not None
    assert activation.entry.enabled is True
    assert activation.runtime_loaded is False
    assert activation.project_json_modified is False
    assert activation.activated_in_runtime is False

    export = write_gameplay_plugin_catalog_export(project_root)
    assert export.valid is True
    assert export.runtime_loaded is False
    assert export.project_json_modified is False
    assert export.activated_in_runtime is False

    payload = export.payload
    assert payload["enabledPluginIds"] == [PLUGIN_ID]
    assert payload["entryPoints"] == [SCENARIO_ID]
    assert payload["scenarioIds"] == [SCENARIO_ID]
    assert payload["encounterIds"] == [ENCOUNTER_ID]
    assert payload["receiptIds"] == [RECEIPT_ID]
    assert payload["consequenceIds"] == [CONSEQUENCE_ID]

    scenario = next(document for document in payload["documents"] if document["kind"] == "scenario")
    encounter = next(document for document in payload["documents"] if document["kind"] == "encounter")
    assert scenario["id"] == SCENARIO_ID
    assert scenario["encounterIds"] == [ENCOUNTER_ID]
    assert scenario["receiptIds"] == [RECEIPT_ID]
    assert encounter["id"] == ENCOUNTER_ID
    assert encounter["template"] == "encounter-template.shuttle-ambush"
    assert encounter["objectiveTypes"] == [
        "objective-type.survive",
        "objective-type.clear-hostiles",
        "objective-type.reach-destination",
    ]
    assert encounter["actorArchetypes"] == ["actor-archetype.shuttle-raider"]
    assert encounter["objectives"] == [
        {
            "id": "survive-boarding",
            "type": "objective-type.survive",
            "required": True,
            "label": "Survive the triple-strength boarding escalation",
        },
        {
            "id": "clear-raiders",
            "type": "objective-type.clear-hostiles",
            "required": True,
            "label": "Defeat the 3x-health elite boarding leader",
        },
        {
            "id": "reach-haven-orbit",
            "type": "objective-type.reach-destination",
            "required": True,
            "label": "Reach Haven orbit after neutralizing the 3x-health leader",
        },
    ]
    assert encounter["participants"] == [
        {
            "role": "hostile",
            "actorArchetypeId": "actor-archetype.shuttle-raider",
            "count": 3,
        }
    ]
    assert encounter["location"] == {
        "systemId": "system.solace-reach",
        "destinationId": "destination.solace-reach.haven-orbit",
    }
    assert encounter["receiptIds"] == [RECEIPT_ID]
    assert encounter["consequenceTypes"] == [
        "consequence-type.record-receipt",
        "consequence-type.mark-system",
    ]

    project_catalog = assert_valid_gameplay_plugin_project_catalog(project_root, project_id="webgl-demo")
    assert project_catalog.valid is True
    assert project_catalog.entry_points == (SCENARIO_ID,)
    assert project_catalog.scenario_ids == (SCENARIO_ID,)
    assert project_catalog.encounter_ids == (ENCOUNTER_ID,)
    project_encounter = next(
        document for document in project_catalog.payload["documents"] if document["kind"] == "encounter"
    )
    assert project_encounter["participants"][0]["count"] == 3
    assert project_encounter["objectives"][1]["id"] == "clear-raiders"
    assert project_encounter["location"]["systemId"] == "system.solace-reach"


def test_opening_shuttle_exported_catalog_builds_preview_only_executor_contract(
    tmp_path: Path,
) -> None:
    project_root = _project_with_fixture(tmp_path)
    assert_valid_gameplay_plugin_materialization(project_root, PLUGIN_ID)
    assert_gameplay_plugin_activation_enabled(project_root, PLUGIN_ID)
    export = write_gameplay_plugin_catalog_export(project_root)
    project_catalog = assert_valid_gameplay_plugin_project_catalog(project_root, project_id="webgl-demo")

    result = _run_node(
        f"""
        const scenarioApi = require(process.argv[1]);
        const catalog = {json.dumps(project_catalog.payload)};
        const preview = scenarioApi.generatedScenarioStartPreview(catalog, "{SCENARIO_ID}");
        const command = scenarioApi.generatedScenarioStartCommandGate(catalog, "{SCENARIO_ID}", {{
          generatedScenarioActivationEnabled: true,
          source: "opening-shuttle-fixture-preview-test"
        }});
        const shellState = scenarioApi.generatedScenarioPreviewShellState(command, {{
          activeSystemId: "system.solace-reach",
          sequence: 1
        }});
        const handoff = scenarioApi.generatedScenarioTemplateHandoff(shellState);
        const status = scenarioApi.generatedTemplateExecutorStatus(
          handoff,
          {{registry: scenarioApi.generatedTemplateExecutorRegistry()}}
        );
        console.log(JSON.stringify({{preview, command, shellState, handoff, status}}));
        """
    )

    assert export.valid is True
    assert result["preview"]["canPreviewStart"] is True
    assert result["preview"]["startable"] is False
    assert result["command"]["accepted"] is True
    assert result["shellState"]["active"] is True
    assert result["handoff"]["accepted"] is True
    assert result["handoff"]["templateId"] == "encounter-template.shuttle-ambush"
    assert result["handoff"]["templateInput"]["objectives"][0] == {
        "id": "survive-boarding",
        "type": "objective-type.survive",
        "required": True,
        "label": "Survive the triple-strength boarding escalation",
    }
    assert result["handoff"]["templateInput"]["actors"] == [
        {
            "role": "hostile",
            "actorArchetypeId": "actor-archetype.shuttle-raider",
            "count": 3,
        }
    ]

    status = result["status"]
    assert status["accepted"] is False
    assert status["blocked"] is True
    assert status["reason"] == "generated-template-execution-disabled"
    assert status["executionStatus"] == "disabled-no-op"
    assert status["rendererHandoff"] is False
    assert status["gameplayTemplateExecution"] is False
    assert status["saveStateMutated"] is False
    assert status["executed"] is False

    preview_contract = status["templatePreview"]
    assert preview_contract["schema"] == "game.generatedTemplateExecutorPreview.shuttleAmbush.v1"
    assert preview_contract["previewKind"] == "shuttle-ambush-plan"
    assert preview_contract["templateId"] == "encounter-template.shuttle-ambush"
    assert preview_contract["scenario"]["id"] == SCENARIO_ID
    assert preview_contract["encounter"]["id"] == ENCOUNTER_ID
    assert preview_contract["encounter"]["location"] == {
        "systemId": "system.solace-reach",
        "destinationId": "destination.solace-reach.haven-orbit",
    }
    assert [objective["id"] for objective in preview_contract["objectiveSequence"]] == [
        "survive-boarding",
        "clear-raiders",
        "reach-haven-orbit",
    ]
    assert [objective["type"] for objective in preview_contract["objectiveSequence"]] == [
        "objective-type.survive",
        "objective-type.clear-hostiles",
        "objective-type.reach-destination",
    ]
    assert preview_contract["hostiles"] == [
        {
            "role": "hostile",
            "actorArchetypeId": "actor-archetype.shuttle-raider",
            "count": 3,
        }
    ]
    assert preview_contract["waves"][0]["count"] == 3
    assert preview_contract["completion"]["receiptIds"] == [RECEIPT_ID]
    assert preview_contract["completion"]["consequenceTypes"] == [
        "consequence-type.record-receipt",
        "consequence-type.mark-system",
    ]
    assert preview_contract["execution"]["rendererHandoff"] is False
    assert preview_contract["execution"]["gameplayTemplateExecution"] is False
    assert preview_contract["execution"]["saveStateMutated"] is False
