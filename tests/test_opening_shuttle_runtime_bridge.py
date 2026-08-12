from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = ROOT / "main_computer" / "web" / "applications" / "scripts"
SCENARIO_RUNTIME = SCRIPT_ROOT / "system-scenario-runtime.js"
SCENE_VIEWER = SCRIPT_ROOT / "scene-viewer.js"
PROJECT_PATH = ROOT / "game_projects" / "webgl-demo" / "project.json"


class OpeningShuttleRuntimeBridgeTests(unittest.TestCase):
    """Patch I: mirror opening-shuttle completion state into scenario runtime read-only metadata."""

    def run_node(self, script: str) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is required for the opening shuttle runtime bridge smoke")
        result = subprocess.run(
            ["node", "-e", textwrap.dedent(script), str(SCENARIO_RUNTIME), str(PROJECT_PATH)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_system_scenario_runtime_accepts_read_only_opening_shuttle_bridge_without_persisting(self) -> None:
        result = self.run_node(
            r"""
            const fs = require("fs");
            const scenarioApi = require(process.argv[1]);
            const project = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
            let writes = 0;
            const storage = {
              getItem() { return null; },
              setItem() { writes += 1; }
            };
            const runtime = scenarioApi.create(project.metadata.systemScenarios, {
              projectId: project.id,
              storage,
              restore: false
            });
            const writesAfterCreate = writes;
            const bridge = runtime.setOpeningShuttleEncounterBridgeSnapshot({
              schema: "game.openingShuttleEncounterRuntime.v1",
              encounterId: "encounter.solace-reach.opening-shuttle-ambush",
              templateId: "encounter-template.shuttle-ambush",
              systemId: "system.solace-reach",
              destinationId: "destination.solace-reach.haven-orbit",
              builtInConsumerId: "built-in.solace-reach.opening-shuttle-ambush",
              status: "completed",
              objectiveLine: "Reach Haven orbit • COMPLETED",
              objectiveSequence: [
                {
                  id: "survive-boarding",
                  type: "objective-type.survive",
                  label: "Survive the shuttle boarding",
                  required: true,
                  status: "completed",
                  progress: {playerDefeated: 0, playerDamaged: 1, encounterCompleted: true}
                },
                {
                  id: "clear-raiders",
                  type: "objective-type.clear-hostiles",
                  label: "Clear shuttle raiders",
                  required: true,
                  status: "completed",
                  progress: {
                    defeated: 3,
                    eliteRequested: true,
                    eliteSpawned: true,
                    eliteCleared: true
                  }
                },
                {
                  id: "reach-haven-orbit",
                  type: "objective-type.reach-destination",
                  label: "Reach Haven orbit",
                  required: true,
                  status: "completed",
                  progress: {
                    systemId: "system.solace-reach",
                    destinationId: "destination.solace-reach.haven-orbit",
                    destinationReached: true,
                    encounterCompleted: true
                  }
                }
              ],
              counters: {
                spawned: 2,
                activated: 2,
                defeated: 3,
                eliteSpawned: 1,
                eliteDefeated: 1,
                playerDamaged: 1,
                playerDefeated: 0,
                destinationReached: 1
              },
              eliteWave: {
                triggerDefeats: 2,
                requested: true,
                spawned: true,
                cleared: true,
                actorArchetypeId: "actor-archetype.shuttle-raider",
                source: "built-in.solace-reach.opening-shuttle-ambush"
              },
              receiptIds: [
                "receipt.solace-reach.opening-shuttle-ambush.elite-wave-cleared",
                "receipt.solace-reach.opening-shuttle-ambush.destination-reached",
                "receipt.solace-reach.opening-shuttle-ambush.completed"
              ],
              completion: {
                destinationReached: true,
                completed: true,
                failed: false,
                completedAtMs: 4200,
                failedAtMs: null,
                receiptIds: [
                  "receipt.solace-reach.opening-shuttle-ambush.elite-wave-cleared",
                  "receipt.solace-reach.opening-shuttle-ambush.destination-reached",
                  "receipt.solace-reach.opening-shuttle-ambush.completed"
                ]
              },
              execution: {
                generatedPluginExecution: false,
                generatedTemplateExecution: false,
                rendererHandoff: false,
                saveStateMutated: false,
                projectJsonModified: false,
                additionalSpawnRequested: true
              }
            }, {
              source: "scene-viewer.openingShuttleEncounter",
              nowMs: 4200,
              emit: false
            });
            const summary = runtime.summary().openingShuttleEncounterBridge;
            const current = runtime.currentOpeningShuttleEncounterBridge();
            const clearResult = runtime.clearOpeningShuttleEncounterBridge("test-clear", {emit: false});

            console.log(JSON.stringify({
              bridge,
              summary,
              current,
              clearResult,
              afterClear: runtime.currentOpeningShuttleEncounterBridge(),
              writesAfterCreate,
              writesAfterBridge: writes
            }));
            """
        )

        assert result["writesAfterBridge"] == result["writesAfterCreate"]

        bridge = result["bridge"]
        assert bridge == result["summary"]
        assert bridge == result["current"]
        assert bridge["schema"] == "game.openingShuttleEncounterBridge.v1"
        assert bridge["kind"] == "opening-shuttle-encounter-runtime-bridge"
        assert bridge["source"] == "scene-viewer.openingShuttleEncounter"
        assert bridge["runtimeLocal"] is True
        assert bridge["readOnly"] is True
        assert bridge["persisted"] is False
        assert bridge["generated"] is False
        assert bridge["encounterId"] == "encounter.solace-reach.opening-shuttle-ambush"
        assert bridge["templateId"] == "encounter-template.shuttle-ambush"
        assert bridge["status"] == "completed"
        assert bridge["objectiveSequence"][1]["progress"] == {
            "defeated": 3,
            "eliteRequested": True,
            "eliteSpawned": True,
            "eliteCleared": True,
        }
        assert bridge["counters"] == {
            "spawned": 2,
            "activated": 2,
            "defeated": 3,
            "eliteSpawned": 1,
            "eliteDefeated": 1,
            "playerDamaged": 1,
            "playerDefeated": 0,
            "destinationReached": 1,
        }
        assert bridge["completion"] == {
            "destinationReached": True,
            "completed": True,
            "failed": False,
            "completedAtMs": 4200,
            "failedAtMs": None,
            "receiptIds": [
                "receipt.solace-reach.opening-shuttle-ambush.elite-wave-cleared",
                "receipt.solace-reach.opening-shuttle-ambush.destination-reached",
                "receipt.solace-reach.opening-shuttle-ambush.completed",
            ],
        }
        assert bridge["execution"] == {
            "generatedPluginExecution": False,
            "generatedTemplateExecution": False,
            "rendererHandoff": False,
            "saveStateMutated": False,
            "projectJsonModified": False,
            "additionalSpawnRequested": True,
        }
        assert bridge["bridgedAtMs"] == 4200
        assert result["clearResult"]["cleared"] is True
        assert result["clearResult"]["reason"] == "test-clear"
        assert result["afterClear"] is None

    def test_scene_viewer_publishes_bridge_through_current_system_scenario_runtime_only(self) -> None:
        source = SCENE_VIEWER.read_text(encoding="utf-8")
        assert "publishOpeningShuttleEncounterBridge(snapshot = null, nowMs = this.combatClockMs)" in source
        assert "globalThis.MainComputerSystemScenarioRuntime?.current?.()" in source
        assert "setOpeningShuttleEncounterBridgeSnapshot" in source
        assert 'source: "scene-viewer.openingShuttleEncounter"' in source
        assert "this.publishOpeningShuttleEncounterBridge?.(this.openingShuttleEncounterSnapshot(), nowMs);" in source
        assert "recordPlayerAction" not in source[
            source.index("        publishOpeningShuttleEncounterBridge"):
            source.index("        emitCombatState", source.index("        publishOpeningShuttleEncounterBridge"))
        ]


    def test_runtime_reports_opening_shuttle_bridge_alignment_with_generated_authoring_preview(self) -> None:
        result = self.run_node(
            r"""
            const fs = require("fs");
            const scenarioApi = require(process.argv[1]);
            const project = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
            const generatedCatalog = {
              schema: scenarioApi.GENERATED_GAMEPLAY_CATALOG_SCHEMA,
              kind: scenarioApi.GENERATED_GAMEPLAY_CATALOG_KIND,
              status: "ready",
              runtimeStatus: scenarioApi.GENERATED_GAMEPLAY_CATALOG_RUNTIME_STATUS,
              exported: true,
              runtimeLoaded: false,
              projectJsonModified: false,
              activatedInRuntime: false,
              enabledPluginIds: ["plugin.hand-authored.opening-shuttle-ambush.001"],
              entryPoints: ["scenario.plugin.opening-shuttle-ambush.elite-wave"],
              scenarioIds: ["scenario.plugin.opening-shuttle-ambush.elite-wave"],
              encounterIds: ["encounter.plugin.opening-shuttle-ambush.elite-wave"],
              receiptIds: ["receipt.plugin.opening-shuttle-ambush.elite-wave-cleared"],
              consequenceIds: ["consequence.plugin.opening-shuttle-ambush.mark-solace-reach"],
              documents: [
                {
                  pluginId: "plugin.hand-authored.opening-shuttle-ambush.001",
                  kind: "scenario",
                  id: "scenario.plugin.opening-shuttle-ambush.elite-wave",
                  title: "Opening Shuttle Ambush Elite Wave",
                  path: "generated/gameplay-plugins/plugin.hand-authored.opening-shuttle-ambush.001/content/scenarios/opening-shuttle-ambush-elite-wave.json",
                  stageIds: ["boarding-survival", "elite-wave", "haven-orbit"],
                  encounterIds: ["encounter.plugin.opening-shuttle-ambush.elite-wave"],
                  receiptIds: ["receipt.plugin.opening-shuttle-ambush.elite-wave-cleared"],
                  consequenceTypes: ["consequence-type.record-receipt", "consequence-type.mark-system"]
                },
                {
                  pluginId: "plugin.hand-authored.opening-shuttle-ambush.001",
                  kind: "encounter",
                  id: "encounter.plugin.opening-shuttle-ambush.elite-wave",
                  title: "Opening Shuttle Ambush Elite Wave",
                  path: "generated/gameplay-plugins/plugin.hand-authored.opening-shuttle-ambush.001/content/encounters/opening-shuttle-ambush-elite-wave.json",
                  template: "encounter-template.shuttle-ambush",
                  objectiveTypes: [
                    "objective-type.survive",
                    "objective-type.clear-hostiles",
                    "objective-type.reach-destination"
                  ],
                  actorArchetypes: ["actor-archetype.shuttle-raider"],
                  objectives: [
                    {
                      id: "survive-boarding",
                      type: "objective-type.survive",
                      label: "Survive the shuttle boarding",
                      required: true
                    },
                    {
                      id: "clear-raiders",
                      type: "objective-type.clear-hostiles",
                      label: "Clear shuttle raiders",
                      required: true
                    },
                    {
                      id: "reach-haven-orbit",
                      type: "objective-type.reach-destination",
                      label: "Reach Haven orbit",
                      required: true
                    }
                  ],
                  participants: [
                    {
                      role: "hostile",
                      actorArchetypeId: "actor-archetype.shuttle-raider",
                      count: 3
                    }
                  ],
                  location: {
                    systemId: "system.solace-reach",
                    destinationId: "destination.solace-reach.haven-orbit"
                  },
                  receiptIds: ["receipt.plugin.opening-shuttle-ambush.elite-wave-cleared"],
                  consequenceTypes: ["consequence-type.record-receipt", "consequence-type.mark-system"]
                }
              ],
              scenarios: [],
              encounters: [],
              plugins: [],
              problems: []
            };
            let writes = 0;
            const runtime = scenarioApi.create(project.metadata.systemScenarios, {
              projectId: project.id,
              storage: {
                getItem() { return null; },
                setItem() { writes += 1; }
              },
              restore: false,
              generatedGameplayCatalog: generatedCatalog
            });
            const writesAfterCreate = writes;
            runtime.setOpeningShuttleEncounterBridgeSnapshot({
              status: "completed",
              objectiveLine: "Reach Haven orbit • COMPLETED",
              objectiveSequence: [
                {
                  id: "survive-boarding",
                  type: "objective-type.survive",
                  label: "Survive the shuttle boarding",
                  required: true,
                  status: "completed"
                },
                {
                  id: "clear-raiders",
                  type: "objective-type.clear-hostiles",
                  label: "Clear shuttle raiders",
                  required: true,
                  status: "completed"
                },
                {
                  id: "reach-haven-orbit",
                  type: "objective-type.reach-destination",
                  label: "Reach Haven orbit",
                  required: true,
                  status: "completed"
                }
              ],
              counters: {
                spawned: 2,
                activated: 2,
                defeated: 3,
                eliteSpawned: 1,
                eliteDefeated: 1,
                playerDamaged: 0,
                playerDefeated: 0,
                destinationReached: 1
              },
              eliteWave: {
                triggerDefeats: 2,
                requested: true,
                spawned: true,
                cleared: true,
                actorArchetypeId: "actor-archetype.shuttle-raider",
                source: "built-in.solace-reach.opening-shuttle-ambush"
              },
              receiptIds: [
                "receipt.solace-reach.opening-shuttle-ambush.elite-wave-cleared",
                "receipt.solace-reach.opening-shuttle-ambush.destination-reached",
                "receipt.solace-reach.opening-shuttle-ambush.completed"
              ],
              completion: {
                destinationReached: true,
                completed: true,
                failed: false
              },
              execution: {
                generatedPluginExecution: false,
                generatedTemplateExecution: false,
                rendererHandoff: false,
                saveStateMutated: false,
                projectJsonModified: false,
                additionalSpawnRequested: true
              }
            }, {
              nowMs: 99,
              emit: false
            });

            console.log(JSON.stringify({
              diagnostic: runtime.openingShuttleEncounterBridgeDiagnostic({
                generatedScenarioId: "scenario.plugin.opening-shuttle-ambush.elite-wave"
              }),
              summaryDiagnostic: runtime.summary().openingShuttleEncounterBridgeDiagnostic,
              missingDiagnostic: scenarioApi.openingShuttleEncounterBridgeDiagnostic(null),
              writesAfterCreate,
              writesAfterDiagnostic: writes
            }));
            """
        )

        assert result["writesAfterDiagnostic"] == result["writesAfterCreate"]

        diagnostic = result["diagnostic"]
        assert diagnostic["schema"] == "game.openingShuttleEncounterBridgeDiagnostic.v1"
        assert diagnostic["kind"] == "opening-shuttle-encounter-bridge-diagnostic"
        assert diagnostic["available"] is True
        assert diagnostic["aligned"] is True
        assert diagnostic["alignmentStatus"] == "aligned-with-generated-preview"
        assert diagnostic["readOnly"] is True
        assert diagnostic["runtimeLocal"] is True
        assert diagnostic["persisted"] is False
        assert diagnostic["saveStateMutated"] is False
        assert diagnostic["projectJsonModified"] is False
        assert diagnostic["problems"] == []
        assert diagnostic["bridge"]["templateId"] == "encounter-template.shuttle-ambush"
        assert diagnostic["bridge"]["status"] == "completed"
        assert diagnostic["bridge"]["objectiveStatuses"] == [
            {
                "id": "survive-boarding",
                "type": "objective-type.survive",
                "status": "completed",
                "required": True,
            },
            {
                "id": "clear-raiders",
                "type": "objective-type.clear-hostiles",
                "status": "completed",
                "required": True,
            },
            {
                "id": "reach-haven-orbit",
                "type": "objective-type.reach-destination",
                "status": "completed",
                "required": True,
            },
        ]
        assert diagnostic["bridge"]["counters"]["defeated"] == 3
        assert diagnostic["bridge"]["eliteWave"]["cleared"] is True
        assert diagnostic["bridge"]["completion"]["completed"] is True
        assert diagnostic["generatedPreview"]["scenarioId"] == "scenario.plugin.opening-shuttle-ambush.elite-wave"
        assert diagnostic["generatedPreview"]["canPreviewStart"] is True
        assert diagnostic["generatedPreview"]["startable"] is False
        assert diagnostic["generatedPreview"]["primaryTemplateId"] == "encounter-template.shuttle-ambush"
        assert diagnostic["generatedPreview"]["templateMatches"] is True
        assert diagnostic["generatedPreview"]["locationMatches"] is True
        assert diagnostic["generatedPreview"]["objectiveTypesCovered"] is True
        assert diagnostic["generatedPreview"]["hostileCount"] == 3
        assert diagnostic["generatedPreview"]["receiptIds"] == [
            "receipt.plugin.opening-shuttle-ambush.elite-wave-cleared"
        ]
        assert diagnostic["coverage"] == {
            "generatedPreviewAvailable": True,
            "authoredHostileCount": 3,
            "liveDefeats": 3,
            "liveEliteDefeats": 1,
            "hostileDefeatsSatisfied": True,
            "liveDestinationReached": True,
            "livePlayerSurvived": True,
            "completionReported": True,
            "completionCoverageSatisfied": True,
            "authoredObjectiveTypes": [
                "objective-type.survive",
                "objective-type.clear-hostiles",
                "objective-type.reach-destination",
            ],
            "liveObjectiveTypes": [
                "objective-type.survive",
                "objective-type.clear-hostiles",
                "objective-type.reach-destination",
            ],
            "objectiveTypesCovered": True,
            "authoredReceiptIds": [
                "receipt.plugin.opening-shuttle-ambush.elite-wave-cleared"
            ],
            "liveReceiptIds": [
                "receipt.solace-reach.opening-shuttle-ambush.elite-wave-cleared",
                "receipt.solace-reach.opening-shuttle-ambush.destination-reached",
                "receipt.solace-reach.opening-shuttle-ambush.completed",
            ],
        }
        assert diagnostic["generatedPreview"]["rendererHandoff"] is False
        assert diagnostic["generatedPreview"]["gameplayTemplateExecution"] is False
        assert diagnostic["generatedPreview"]["saveStateMutated"] is False
        assert diagnostic["safety"] == {
            "executionSafe": True,
            "generatedPluginExecution": False,
            "generatedTemplateExecution": False,
            "rendererHandoff": False,
            "saveStateMutated": False,
            "projectJsonModified": False,
            "additionalSpawnRequested": True,
        }

        assert result["summaryDiagnostic"]["alignmentStatus"] == "bridge-only"
        assert result["summaryDiagnostic"]["generatedPreview"] is None
        assert result["missingDiagnostic"]["available"] is False
        assert result["missingDiagnostic"]["aligned"] is False
        assert result["missingDiagnostic"]["problems"] == ["missing-opening-shuttle-bridge"]


    def test_scene_viewer_exposes_read_only_authoring_alignment_snapshot_for_hud(self) -> None:
        result = self.run_node(
            r"""
            const fs = require("fs");
            const sceneSource = fs.readFileSync("main_computer/web/applications/scripts/scene-viewer.js", "utf8");
            const start = sceneSource.indexOf("        openingShuttleGeneratedAuthoringScenarioId()");
            const end = sceneSource.indexOf("        emitCombatState(", start);
            if (start < 0 || end < 0) throw new Error("opening shuttle authoring alignment methods not found");
            const methodSource = sceneSource.slice(start, end).trim()
              .replace(/\n        (?=(openingShuttle|publishOpening))/g, ",\n        ");
            const methods = Function(`return ({${methodSource}});`)();

            const scenarioId = "scenario.plugin.opening-shuttle-ambush.elite-wave";
            const scenarioRuntime = {
              bridge: null,
              setOpeningShuttleEncounterBridgeSnapshot(snapshot, options) {
                this.bridge = {
                  kind: "opening-shuttle-encounter-runtime-bridge",
                  status: snapshot.status || "active",
                  objectiveLine: snapshot.objectiveLine || "Survive the shuttle boarding • ACTIVE",
                  ...snapshot,
                  options
                };
                return this.bridge;
              },
              generatedGameplayCatalog() {
                return {
                  ready: true,
                  scenarioIds: [scenarioId]
                };
              },
              openingShuttleEncounterBridgeDiagnostic(options) {
                return {
                  kind: "opening-shuttle-encounter-bridge-diagnostic",
                  available: Boolean(this.bridge),
                  aligned: options.generatedScenarioId === scenarioId,
                  alignmentStatus: options.generatedScenarioId === scenarioId
                    ? "aligned-with-generated-preview"
                    : "bridge-only",
                  bridge: {
                    status: this.bridge?.status || "active",
                    objectiveLine: this.bridge?.objectiveLine || "",
                    templateId: this.bridge?.templateId || "encounter-template.shuttle-ambush"
                  },
                  generatedPreview: options.generatedScenarioId === scenarioId
                    ? {
                      primaryTemplateId: "encounter-template.shuttle-ambush",
                      hostileCount: 3
                    }
                    : null,
                  coverage: {
                    generatedPreviewAvailable: true,
                    authoredHostileCount: 3,
                    liveDefeats: 3,
                    liveEliteDefeats: 1,
                    hostileDefeatsSatisfied: true,
                    liveDestinationReached: true,
                    livePlayerSurvived: true,
                    completionReported: true,
                    completionCoverageSatisfied: true,
                    objectiveTypesCovered: true
                  },
                  safety: {
                    executionSafe: true,
                    generatedPluginExecution: false,
                    generatedTemplateExecution: false,
                    rendererHandoff: false,
                    saveStateMutated: false,
                    projectJsonModified: false,
                    additionalSpawnRequested: true
                  },
                  problems: []
                };
              }
            };
            globalThis.MainComputerSystemScenarioRuntime = {current: () => scenarioRuntime};

            const viewer = {
              openingShuttleEncounterAuthoringAlignment: null,
              openingShuttleEncounterSnapshot() {
                return {
                  status: "completed",
                  objectiveLine: "Reach Haven orbit • COMPLETED",
                  templateId: "encounter-template.shuttle-ambush"
                };
              }
            };
            Object.assign(viewer, methods);

            const bridge = viewer.publishOpeningShuttleEncounterBridge(
              viewer.openingShuttleEncounterSnapshot(),
              1234
            );
            const alignment = viewer.openingShuttleEncounterAuthoringAlignment;

            console.log(JSON.stringify({bridge, alignment}));
            """
        )

        alignment = result["alignment"]
        assert result["bridge"]["options"]["source"] == "scene-viewer.openingShuttleEncounter"
        assert alignment["schema"] == "game.openingShuttleAuthoringAlignment.v1"
        assert alignment["kind"] == "opening-shuttle-authoring-alignment"
        assert alignment["readOnly"] is True
        assert alignment["runtimeLocal"] is True
        assert alignment["persisted"] is False
        assert alignment["generated"] is False
        assert alignment["scenarioId"] == "scenario.plugin.opening-shuttle-ambush.elite-wave"
        assert alignment["alignmentStatus"] == "aligned-with-generated-preview"
        assert alignment["aligned"] is True
        assert alignment["authoringPreviewAvailable"] is True
        assert alignment["hostileCount"] == 3
        assert alignment["requiredHostileDefeats"] == 3
        assert alignment["liveDefeats"] == 3
        assert alignment["liveEliteDefeats"] == 1
        assert alignment["hostileDefeatsSatisfied"] is True
        assert alignment["destinationReached"] is True
        assert alignment["playerSurvived"] is True
        assert alignment["completionReported"] is True
        assert alignment["completionAligned"] is True
        assert alignment["coverage"] == {
            "generatedPreviewAvailable": True,
            "authoredHostileCount": 3,
            "liveDefeats": 3,
            "liveEliteDefeats": 1,
            "hostileDefeatsSatisfied": True,
            "liveDestinationReached": True,
            "livePlayerSurvived": True,
            "completionReported": True,
            "completionCoverageSatisfied": True,
            "objectiveTypesCovered": True,
        }
        assert alignment["generatedPluginExecution"] is False
        assert alignment["generatedTemplateExecution"] is False
        assert alignment["rendererHandoff"] is False
        assert alignment["saveStateMutated"] is False
        assert alignment["projectJsonModified"] is False
        assert alignment["safety"]["executionSafe"] is True
        assert alignment["safety"]["additionalSpawnRequested"] is True


if __name__ == "__main__":
    unittest.main()
