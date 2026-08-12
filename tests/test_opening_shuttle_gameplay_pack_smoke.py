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


def test_loaded_pack_selection_drives_shuttle_behavior_and_none_does_not(tmp_path: Path) -> None:
    """Patch P: loaded pack -> selected on reload -> live shuttle behavior differs from None."""

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
        const end = sceneSource.indexOf("        combatSnapshot(", start);
        if (start < 0 || end < 0) throw new Error("opening shuttle hook methods not found");
        const methodSource = sceneSource.slice(start, end).trim()
          .replace(/\\n        (?=(isOpening|opening|award|completeOpening|record|spawnOpening|resolve|publish))/g, ",\\n        ");
        const methods = Function(`return ({{${{methodSource}}}});`)();

        function makeHarness(activeGameplayPackIds) {{
          const scenarioRuntime = scenarioApi.create(project.metadata.systemScenarios, {{
            projectId: activeGameplayPackIds.length ? "webgl-demo-pack" : "webgl-demo-none",
            storage: null,
            restore: false,
            generatedGameplayCatalog: catalog,
            activeGameplayPackIds
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
            transportSequence: 2,
            gameOver: false,
            combatClockMs: 0,
            emitted: 0,
            characterAIPhase() {{
              return "shuttle";
            }},
            emitCombatState() {{
              this.emitted += 1;
            }}
          }};
          Object.assign(scene, methods);
          scene.openingShuttleEncounter = scene.createOpeningShuttleEncounterRuntimeState();
          return {{scenarioRuntime, scene}};
        }}

        function runOpeningCombat(harness, complete = false) {{
          const scene = harness.scene;
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
          let elite = null;
          if (spawned) {{
            elite = scene.aliens[0];
            scene.recordOpeningShuttleEncounterEvent(
              "alien-defeated",
              {{
                alienId: elite.id,
                spawnId: elite.spawnId,
                kills: 3,
                eliteWave: true,
                encounterRole: elite.encounterRole,
                actorArchetypeId: elite.actorArchetypeId
              }},
              2600
            );
          }}
          if (complete) {{
            scene.completeOpeningShuttleEncounterAtDestination(
              4200,
              {{locationId: "bay.shuttle", reason: "smoke-proof"}}
            );
          }}
          const snapshot = scene.openingShuttleEncounterSnapshot();
          harness.scenarioRuntime.setOpeningShuttleEncounterBridgeSnapshot(snapshot, {{
            source: "opening-shuttle-gameplay-pack-smoke"
          }});
          const diagnostic = harness.scenarioRuntime.openingShuttleEncounterBridgeDiagnostic({{
            scenarioId,
            source: "opening-shuttle-gameplay-pack-smoke"
          }});
          return {{
            spawned,
            elite,
            snapshot,
            diagnostic,
            config: harness.scenarioRuntime.openingShuttleGameplayPackConfig({{baseHostileCount: 2}}),
            selection: harness.scenarioRuntime.activeGameplayPackSelection()
          }};
        }}

        const noneHarness = makeHarness([]);
        const selectedHarness = makeHarness([pluginId]);

        const none = runOpeningCombat(noneHarness, false);
        const selected = runOpeningCombat(selectedHarness, true);
        const preview = selectedHarness.scenarioRuntime.generatedScenarioStartPreview(scenarioId);
        const summary = selectedHarness.scenarioRuntime.summary();

        console.log(JSON.stringify({{none, selected, preview, summary}}));
        """,
        SCENARIO_RUNTIME,
        SCENE_VIEWER,
        catalog_path,
        project_path,
    )

    none = result["none"]
    assert none["selection"]["mode"] == "none"
    assert none["selection"]["activePluginIds"] == []
    assert none["config"]["active"] is False
    assert none["config"]["extraHostileCount"] == 0
    assert none["config"]["eliteWave"]["enabled"] is False
    assert none["spawned"] is False
    assert none["elite"] is None
    assert none["snapshot"]["eliteWave"]["enabled"] is False
    assert none["snapshot"]["eliteWave"]["packConfigured"] is False
    assert none["snapshot"]["counters"]["defeated"] == 2
    assert none["snapshot"]["counters"]["eliteSpawned"] == 0
    assert none["snapshot"]["execution"]["additionalSpawnRequested"] is False
    assert none["diagnostic"]["coverage"]["generatedPreviewAvailable"] is True
    assert none["diagnostic"]["coverage"]["authoredHostileCount"] == 3
    assert none["diagnostic"]["coverage"]["liveDefeats"] == 2
    assert none["diagnostic"]["coverage"]["hostileDefeatsSatisfied"] is False

    selected = result["selected"]
    assert selected["selection"]["mode"] == "selected"
    assert selected["selection"]["activePluginIds"] == [PLUGIN_ID]
    assert selected["config"]["available"] is True
    assert selected["config"]["active"] is True
    assert selected["config"]["pluginId"] == PLUGIN_ID
    assert selected["config"]["scenarioId"] == SCENARIO_ID
    assert selected["config"]["encounterId"] == ENCOUNTER_ID
    assert selected["config"]["hostileCount"] == 3
    assert selected["config"]["extraHostileCount"] == 1
    assert selected["config"]["hostileHealthMultiplier"] == 3
    assert selected["config"]["eliteWave"] == {
        "enabled": True,
        "triggerDefeats": 2,
        "count": 1,
        "actorArchetypeId": "actor-archetype.shuttle-raider",
        "source": PLUGIN_ID,
        "scenarioId": SCENARIO_ID,
        "encounterId": ENCOUNTER_ID,
        "displayName": "Elite Boarding Leader",
        "objectiveLabel": "Defeat the 3x-health elite boarding leader",
        "alert": "Opening Shuttle Ambush Elite Wave: elite boarding leader inbound — 3x hostile health confirmed",
        "healthMultiplier": 3,
    }
    assert selected["spawned"] is True
    assert selected["elite"]["id"] == "boarding-elite-raider-3"
    assert selected["elite"]["eliteWave"] is True
    assert selected["elite"]["health"] == 180
    assert selected["elite"]["maxHealth"] == 180
    assert selected["elite"]["healthMultiplier"] == 3
    assert selected["snapshot"]["status"] == "completed"
    assert selected["snapshot"]["eliteWave"]["packConfigured"] is True
    assert selected["snapshot"]["eliteWave"]["activePluginIds"] == [PLUGIN_ID]
    assert selected["snapshot"]["counters"]["defeated"] == 3
    assert selected["snapshot"]["counters"]["eliteSpawned"] == 1
    assert selected["snapshot"]["counters"]["eliteDefeated"] == 1
    assert selected["snapshot"]["completion"]["completed"] is True
    assert selected["snapshot"]["completion"]["destinationReached"] is True
    assert selected["snapshot"]["execution"] == {
        "generatedPluginExecution": False,
        "generatedTemplateExecution": False,
        "rendererHandoff": False,
        "saveStateMutated": False,
        "projectJsonModified": False,
        "additionalSpawnRequested": True,
    }

    diagnostic = selected["diagnostic"]
    assert diagnostic["aligned"] is True
    assert diagnostic["alignmentStatus"] == "aligned-with-generated-preview"
    assert diagnostic["generatedPreview"]["scenarioId"] == SCENARIO_ID
    assert diagnostic["generatedPreview"]["hostileCount"] == 3
    assert diagnostic["coverage"]["authoredHostileCount"] == 3
    assert diagnostic["coverage"]["liveDefeats"] == 3
    assert diagnostic["coverage"]["hostileDefeatsSatisfied"] is True
    assert diagnostic["coverage"]["liveDestinationReached"] is True
    assert diagnostic["coverage"]["completionCoverageSatisfied"] is True
    assert diagnostic["safety"]["generatedPluginExecution"] is False
    assert diagnostic["safety"]["generatedTemplateExecution"] is False
    assert diagnostic["safety"]["rendererHandoff"] is False
    assert diagnostic["safety"]["saveStateMutated"] is False
    assert diagnostic["safety"]["projectJsonModified"] is False

    assert result["preview"]["scenarioId"] == SCENARIO_ID
    assert result["preview"]["canPreviewStart"] is True
    assert result["preview"]["startable"] is False
    assert result["summary"]["activeGameplayPackSelection"]["activePluginIds"] == [PLUGIN_ID]
    assert result["summary"]["openingShuttleGameplayPackConfig"]["extraHostileCount"] == 1


def test_reload_pack_selection_allows_explicit_none_to_override_metadata() -> None:
    """The reload selector must let the player choose None even if project metadata enables a pack."""

    desktop = WEBGL_DESKTOP.read_text(encoding="utf-8")
    storage_read = desktop.index("window.localStorage?.getItem?.(WEBGL_ACTIVE_GAMEPLAY_PACKS_KEY)")
    query_read = desktop.index('new URLSearchParams(window.location?.search || "")')
    metadata_read = desktop.index("const configured = metadata.activeGameplayPackIds")

    assert metadata_read < storage_read < query_read
    assert "selected = webglNormalizeGameplayPackIds(JSON.parse(raw));" in desktop
    assert "selected = webglNormalizeGameplayPackIds(raw);" in desktop
    assert 'params.has("gameplayPack")' in desktop
    assert 'params.has("gameplayPacks")' in desktop
    assert "selected = webglNormalizeGameplayPackIds(queryValue);" in desktop
    assert 'selectionSource = "query-param";' in desktop
