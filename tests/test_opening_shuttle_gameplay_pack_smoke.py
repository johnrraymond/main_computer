from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

from main_computer.gameplay_plugin_activation import assert_gameplay_plugin_activation_enabled
from main_computer.gameplay_plugin_catalog_export import write_gameplay_plugin_catalog_export
from main_computer.gameplay_plugin_materializer import assert_valid_gameplay_plugin_materialization
from main_computer.gameplay_plugin_project_catalog import assert_valid_gameplay_plugin_project_catalog


ROOT = Path(__file__).resolve().parents[1]
GAME_PROJECT = ROOT / "game_projects" / "webgl-demo"
SCRIPT_ROOT = ROOT / "main_computer" / "web" / "applications" / "scripts"
SCENARIO_RUNTIME = SCRIPT_ROOT / "system-scenario-runtime.js"
SCENE_VIEWER = SCRIPT_ROOT / "scene-viewer.js"
WEBGL_DESKTOP = SCRIPT_ROOT / "webgl-desktop.js"
PROJECT_PATH = GAME_PROJECT / "project.json"
FIXTURE_PACKAGE = (
    GAME_PROJECT
    / "plugins"
    / "hand_authored"
    / "opening_shuttle_ambush_extension"
)

PLUGIN_ID = "plugin.hand-authored.opening-shuttle-ambush.001"
SCENARIO_ID = "scenario.plugin.opening-shuttle-ambush.elite-wave"
ENCOUNTER_ID = "encounter.plugin.opening-shuttle-ambush.elite-wave"


def _copy_fixture(target: Path) -> None:
    for source in FIXTURE_PACKAGE.rglob("*"):
        if source.is_dir():
            continue
        relative = source.relative_to(FIXTURE_PACKAGE)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())


def _project_with_loaded_opening_shuttle_pack(tmp_path: Path) -> dict:
    project_root = tmp_path / "webgl-demo"
    _copy_fixture(
        project_root
        / "plugins"
        / "hand_authored"
        / "opening_shuttle_ambush_extension"
    )
    assert_valid_gameplay_plugin_materialization(project_root, PLUGIN_ID)
    assert_gameplay_plugin_activation_enabled(project_root, PLUGIN_ID)
    write_gameplay_plugin_catalog_export(project_root)
    return assert_valid_gameplay_plugin_project_catalog(
        project_root,
        project_id="webgl-demo",
    ).payload


def _run_node(script: str, *args: Path) -> dict:
    if not shutil.which("node"):
        raise RuntimeError("node is required for the shuttle gameplay-pack smoke proof")
    result = subprocess.run(
        ["node", "-e", textwrap.dedent(script), *[str(arg) for arg in args]],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def test_legacy_json_pack_catalog_is_preview_only_for_live_shuttle_scene(tmp_path: Path) -> None:
    """Patch X: loaded JSON catalog data no longer drives live shuttle behavior by inference."""

    catalog = _project_with_loaded_opening_shuttle_pack(tmp_path)
    project = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))
    catalog_path = tmp_path / "runtime-catalog.json"
    project_path = tmp_path / "project.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    project_path.write_text(json.dumps(project), encoding="utf-8")

    result = _run_node(
        f"""
        const fs = require("fs");
        const scenarioApi = require(process.argv[1]);
        const sceneSource = fs.readFileSync(process.argv[2], "utf8");

        const catalog = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
        const project = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
        const pluginId = "{PLUGIN_ID}";
        const scenarioId = "{SCENARIO_ID}";

        const start = sceneSource.indexOf("        createOpeningShuttleEncounterRuntimeState");
        const end = sceneSource.indexOf("        canAlienOccupy(", start);
        if (start < 0 || end < 0) throw new Error("opening shuttle hook methods not found");
        const methodSource = sceneSource.slice(start, end).trim()
          .replace(/\\n        (?=(isOpening|opening|award|completeOpening|record|spawnOpening|resolve|combatSnapshot|publish|emitCombatState|spawnAlien))/g, ",\\n        ");
        const methods = Function(`return ({{${{methodSource}}}});`)();

        const scenarioRuntime = scenarioApi.create(project.metadata.systemScenarios, {{
          projectId: "webgl-demo-legacy-json-pack",
          storage: null,
          restore: false,
          generatedGameplayCatalog: catalog,
          activeGameplayPackIds: [pluginId]
        }});

        globalThis.MainComputerSystemScenarioRuntime = {{
          current() {{
            return scenarioRuntime;
          }}
        }};

        const scene = {{
          combat: {{
            enabled: true,
            transport: {{
              maxAlive: 4,
              beamDurationMs: 900,
              spawnPoints: [
                {{id: "port-aft-pad", position: [-2.9, -0.55, 2.55]}},
                {{id: "starboard-aft-pad", position: [2.9, -0.55, 2.55]}},
                {{id: "center-pad", position: [0, -0.55, 0.3]}}
              ]
            }},
            alien: {{maxHealth: 60}}
          }},
          aliens: [],
          transportSequence: 0,
          gameOver: false,
          combatClockMs: 0,
          emitted: 0,
          characterAIPhase() {{ return "shuttle"; }},
          emitCombatState() {{ this.emitted += 1; }}
        }};
        Object.assign(scene, methods);
        scene.openingShuttleEncounter = scene.createOpeningShuttleEncounterRuntimeState();
        scene.spawnAlien(100);
        const firstAlien = scene.aliens[0];

        scene.recordOpeningShuttleEncounterEvent(
          "alien-defeated",
          {{alienId: "boarding-alien-1", spawnId: "port-aft-pad", kills: 1}},
          1200
        );
        scene.recordOpeningShuttleEncounterEvent(
          "alien-defeated",
          {{alienId: "boarding-alien-2", spawnId: "starboard-aft-pad", kills: 2}},
          1800
        );

        const spawned = scene.resolveOpeningShuttleEliteWave(1810);
        const snapshot = scene.openingShuttleEncounterSnapshot();
        scenarioRuntime.setOpeningShuttleEncounterBridgeSnapshot(snapshot, {{
          source: "opening-shuttle-legacy-json-preview-only-smoke"
        }});
        const diagnostic = scenarioRuntime.openingShuttleEncounterBridgeDiagnostic({{
          scenarioId,
          source: "opening-shuttle-legacy-json-preview-only-smoke"
        }});

        console.log(JSON.stringify({{
          config: scenarioRuntime.openingShuttleGameplayPackConfig({{baseHostileCount: 2}}),
          selection: scenarioRuntime.activeGameplayPackSelection(),
          preview: scenarioRuntime.generatedScenarioStartPreview(scenarioId),
          firstAlien,
          spawned,
          snapshot,
          diagnostic,
          summary: scenarioRuntime.summary()
        }}));
        """,
        SCENARIO_RUNTIME,
        SCENE_VIEWER,
        catalog_path,
        project_path,
    )

    assert result["selection"]["mode"] == "selected"
    assert result["selection"]["activePluginIds"] == [PLUGIN_ID]
    assert result["config"]["available"] is True
    assert result["config"]["active"] is True
    assert result["config"]["extraHostileCount"] == 1
    assert result["config"]["hostileHealthMultiplier"] == 3

    assert result["preview"]["scenarioId"] == SCENARIO_ID
    assert result["preview"]["canPreviewStart"] is True
    assert result["preview"]["startable"] is False

    assert result["firstAlien"]["health"] == 60
    assert result["firstAlien"]["maxHealth"] == 60
    assert result["firstAlien"]["healthMultiplier"] == 1
    assert result["firstAlien"]["packPowered"] is False
    assert result["spawned"] is False

    snapshot = result["snapshot"]
    assert snapshot["eliteWave"]["enabled"] is False
    assert snapshot["eliteWave"]["packConfigured"] is False
    assert snapshot["eliteWave"]["healthMultiplier"] == 1
    assert snapshot["jsGameplayPack"]["installed"] is False
    assert snapshot["packLine"] == "Gameplay Pack: None — base game"
    assert snapshot["execution"]["additionalSpawnRequested"] is False
    assert snapshot["execution"]["generatedPluginExecution"] is False
    assert snapshot["execution"]["generatedTemplateExecution"] is False

    diagnostic = result["diagnostic"]
    assert diagnostic["coverage"]["generatedPreviewAvailable"] is True
    assert diagnostic["coverage"]["authoredHostileCount"] == 3
    assert diagnostic["coverage"]["liveDefeats"] == 2
    assert diagnostic["coverage"]["hostileDefeatsSatisfied"] is False

def test_reload_pack_selection_allows_explicit_none_to_override_metadata() -> None:
    """The reload selector must let the player choose None even if project metadata enables a pack."""

    desktop = WEBGL_DESKTOP.read_text(encoding="utf-8")
    storage_read = desktop.index("window.localStorage?.getItem?.(WEBGL_ENABLED_GAMEPLAY_PACKS_KEY)")
    legacy_storage_read = desktop.index("window.localStorage?.getItem?.(WEBGL_LEGACY_ACTIVE_GAMEPLAY_PACKS_KEY)")
    query_read = desktop.index('new URLSearchParams(window.location?.search || "")')
    metadata_read = desktop.index("const configured = metadata.activeGameplayPackIds")

    assert metadata_read < storage_read < legacy_storage_read < query_read
    assert "webglParseGameplayPackStorageValue" in desktop
    assert "selected = parsed;" in desktop
    assert 'selectionSource = "local-storage";' in desktop
    assert 'params.has("gameplayPack")' in desktop
    assert 'params.has("gameplayPacks")' in desktop
    assert "selected = webglNormalizeGameplayPackIds(queryValue);" in desktop
    assert 'selectionSource = "query-param";' in desktop
