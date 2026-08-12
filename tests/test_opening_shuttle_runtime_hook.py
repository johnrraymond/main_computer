from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENE_VIEWER = ROOT / "main_computer" / "web" / "applications" / "scripts" / "scene-viewer.js"


class OpeningShuttleRuntimeHookTests(unittest.TestCase):
    """Patch H/I: expose opening shuttle completion receipts and a read-only bridge."""

    def run_node(self, script: str) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is required for the opening shuttle runtime hook smoke")
        result = subprocess.run(
            ["node", "-e", textwrap.dedent(script), str(SCENE_VIEWER)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_hook_state_is_runtime_local_and_observation_only(self) -> None:
        source = SCENE_VIEWER.read_text(encoding="utf-8")
        start = source.index("        createOpeningShuttleEncounterRuntimeState")
        end = source.index("        combatSnapshot(", start)
        hook_source = source[start:end]

        self.assertIn("game.openingShuttleEncounterRuntime.v1", hook_source)
        self.assertIn("encounter.solace-reach.opening-shuttle-ambush", hook_source)
        self.assertIn("encounter-template.shuttle-ambush", hook_source)
        self.assertIn("system.solace-reach", hook_source)
        self.assertIn("destination.solace-reach.haven-orbit", hook_source)
        self.assertIn("built-in.solace-reach.opening-shuttle-ambush", hook_source)
        self.assertIn("actor-archetype.shuttle-raider", hook_source)
        self.assertIn("eliteWave", hook_source)
        self.assertIn("openingShuttleEncounterObjectiveSnapshot", hook_source)
        self.assertIn("objective-type.survive", hook_source)
        self.assertIn("objective-type.clear-hostiles", hook_source)
        self.assertIn("objective-type.reach-destination", hook_source)
        self.assertIn("spawnOpeningShuttleEliteRaider", hook_source)
        self.assertIn("resolveOpeningShuttleEliteWave", hook_source)
        self.assertIn("generatedPluginExecution: false", hook_source)
        self.assertIn("generatedTemplateExecution: false", hook_source)
        self.assertIn("rendererHandoff: false", hook_source)
        self.assertIn("saveStateMutated: false", hook_source)
        self.assertIn("projectJsonModified: false", hook_source)
        self.assertIn("additionalSpawnRequested: false", hook_source)
        self.assertIn("Elite Boarding Leader", hook_source)
        self.assertIn("healthMultiplier", hook_source)
        self.assertIn("openingShuttleEncounterPackLine", hook_source)
        self.assertIn("openingShuttleActiveGameplayPackConfig", hook_source)
        self.assertIn("openingShuttleGameplayPackConfig", hook_source)
        self.assertNotIn("generatedScenarioStartCommandGate", hook_source)

    def test_hook_records_shuttle_phase_events_receipts_and_safety_snapshot(self) -> None:
        result = self.run_node(
            """
            const fs = require('fs');
            const source = fs.readFileSync(process.argv[1], 'utf8');
            const start = source.indexOf('        createOpeningShuttleEncounterRuntimeState');
            const end = source.indexOf('        combatSnapshot(', start);
            if (start < 0 || end < 0) throw new Error('opening shuttle hook methods not found');
            const methodSource = source.slice(start, end).trim()
              .replace(/\\n        (?=(isOpening|opening|award|completeOpening|record|spawnOpening|resolve))/g, ',\\n        ');
            const methods = Function(`return ({${methodSource}});`)();
            const runtime = {
              combat: {enabled: true},
              combatClockMs: 0,
              characterAIPhase() { return 'shuttle'; }
            };
            Object.assign(runtime, methods);
            runtime.openingShuttleEncounter = runtime.createOpeningShuttleEncounterRuntimeState();

            runtime.recordOpeningShuttleEncounterEvent(
              'alien-spawned',
              {alienId: 'boarding-alien-1', spawnId: 'port-aft-pad'},
              100
            );
            runtime.recordOpeningShuttleEncounterEvent(
              'alien-activated',
              {alienId: 'boarding-alien-1', spawnId: 'port-aft-pad'},
              700
            );
            runtime.recordOpeningShuttleEncounterEvent(
              'alien-defeated',
              {alienId: 'boarding-alien-1', spawnId: 'port-aft-pad', kills: 1},
              1200
            );
            runtime.recordOpeningShuttleEncounterEvent(
              'alien-defeated',
              {alienId: 'boarding-alien-2', spawnId: 'starboard-aft-pad', kills: 2},
              1800
            );
            runtime.recordOpeningShuttleEncounterEvent(
              'player-damaged',
              {alienId: 'boarding-alien-3', damage: 8, health: 92},
              2200
            );
            const snapshot = runtime.openingShuttleEncounterSnapshot();

            runtime.characterAIPhase = () => 'mother-ship';
            const blocked = runtime.recordOpeningShuttleEncounterEvent(
              'alien-spawned',
              {alienId: 'should-not-record'},
              3000
            );
            const afterBlocked = runtime.openingShuttleEncounterSnapshot();

            console.log(JSON.stringify({snapshot, blocked, afterBlocked}));
            """
        )

        snapshot = result["snapshot"]
        assert snapshot["schema"] == "game.openingShuttleEncounterRuntime.v1"
        assert snapshot["encounterId"] == "encounter.solace-reach.opening-shuttle-ambush"
        assert snapshot["templateId"] == "encounter-template.shuttle-ambush"
        assert snapshot["systemId"] == "system.solace-reach"
        assert snapshot["destinationId"] == "destination.solace-reach.haven-orbit"
        assert snapshot["builtInConsumerId"] == "built-in.solace-reach.opening-shuttle-ambush"
        assert snapshot["status"] == "active"
        assert snapshot["completion"] == {
            "destinationReached": False,
            "completed": False,
            "failed": False,
            "completedAtMs": None,
            "failedAtMs": None,
            "receiptIds": [
                "receipt.solace-reach.opening-shuttle-ambush.boarding-contact",
                "receipt.solace-reach.opening-shuttle-ambush.first-raider-defeated",
            ],
        }
        assert snapshot["receiptIds"] == snapshot["completion"]["receiptIds"]
        assert snapshot["counters"] == {
            "spawned": 1,
            "activated": 1,
            "defeated": 2,
            "eliteSpawned": 0,
            "eliteDefeated": 0,
            "playerDamaged": 1,
            "playerDefeated": 0,
            "destinationReached": 0,
        }
        assert snapshot["eliteWave"] == {
            "enabled": False,
            "triggerDefeats": 2,
            "requested": False,
            "spawned": False,
            "cleared": False,
            "requiredExtraHostiles": 0,
            "spawnedCount": 0,
            "actorArchetypeId": "actor-archetype.shuttle-raider",
            "source": "none",
            "scenarioId": "",
            "encounterId": "",
            "pluginId": "",
            "packConfigAvailable": False,
            "packConfigured": False,
            "activePluginIds": [],
            "displayName": "",
            "objectiveLabel": "",
            "packLabel": "None — base game",
            "alert": "",
            "healthMultiplier": 1,
        }
        assert [objective["id"] for objective in snapshot["objectiveSequence"]] == [
            "survive-boarding",
            "clear-raiders",
            "reach-haven-orbit",
        ]
        assert [objective["type"] for objective in snapshot["objectiveSequence"]] == [
            "objective-type.survive",
            "objective-type.clear-hostiles",
            "objective-type.reach-destination",
        ]
        assert [objective["status"] for objective in snapshot["objectiveSequence"]] == [
            "active",
            "active",
            "pending",
        ]
        assert snapshot["objectiveSequence"][1]["progress"] == {
            "defeated": 2,
            "eliteRequested": False,
            "eliteSpawned": False,
            "eliteCleared": False,
            "requiredExtraHostiles": 0,
            "eliteDisplayName": "Elite Boarding Leader",
            "packConfigured": False,
        }
        assert snapshot["objectiveLine"] == "Survive the shuttle boarding • ACTIVE"
        assert [receipt["id"] for receipt in snapshot["receipts"]] == [
            "receipt.solace-reach.opening-shuttle-ambush.boarding-contact",
            "receipt.solace-reach.opening-shuttle-ambush.first-raider-defeated",
        ]
        assert snapshot["events"][-1]["type"] == "player-damaged"
        assert snapshot["lastMessage"] == "Cadet took boarding damage."
        assert snapshot["execution"] == {
            "generatedPluginExecution": False,
            "generatedTemplateExecution": False,
            "rendererHandoff": False,
            "saveStateMutated": False,
            "projectJsonModified": False,
            "additionalSpawnRequested": False,
        }

        assert result["blocked"] is None
        assert result["afterBlocked"]["counters"] == snapshot["counters"]

    def test_elite_wave_request_resolves_to_one_builtin_raider(self) -> None:
        result = self.run_node(
            """
            const fs = require('fs');
            const source = fs.readFileSync(process.argv[1], 'utf8');
            const start = source.indexOf('        createOpeningShuttleEncounterRuntimeState');
            const end = source.indexOf('        combatSnapshot(', start);
            if (start < 0 || end < 0) throw new Error('opening shuttle hook methods not found');
            const methodSource = source.slice(start, end).trim()
              .replace(/\\n        (?=(isOpening|opening|award|completeOpening|record|spawnOpening|resolve))/g, ',\\n        ');
            const methods = Function(`return ({${methodSource}});`)();
            const runtime = {
              combat: {
                enabled: true,
                transport: {
                  maxAlive: 4,
                  beamDurationMs: 900,
                  spawnPoints: [
                    {id: 'port-aft-pad', position: [-2.9, -0.55, 2.55]},
                    {id: 'starboard-aft-pad', position: [2.9, -0.55, 2.55]},
                    {id: 'center-pad', position: [0, -0.55, 0.3]}
                  ]
                },
                alien: {maxHealth: 60}
              },
              aliens: [],
              transportSequence: 2,
              gameOver: false,
              combatClockMs: 0,
              emitted: 0,
              characterAIPhase() { return 'shuttle'; },
              emitCombatState() { this.emitted += 1; }
            };
            globalThis.MainComputerSystemScenarioRuntime = {
              current() {
                return {
                  openingShuttleGameplayPackConfig() {
                    return {
                      available: true,
                      active: true,
                      pluginId: "plugin.hand-authored.opening-shuttle-ambush.001",
                      scenarioId: "scenario.plugin.opening-shuttle-ambush.elite-wave",
                      scenarioTitle: "Opening Shuttle Ambush Elite Wave",
                      packLabel: "Opening Shuttle Ambush Elite Wave",
                      encounterId: "encounter.plugin.opening-shuttle-ambush.elite-wave",
                      encounterTitle: "Opening Shuttle Ambush Elite Leader",
                      extraHostileCount: 1,
                      activePluginIds: ["plugin.hand-authored.opening-shuttle-ambush.001"],
                      eliteWave: {
                        enabled: true,
                        triggerDefeats: 2,
                        count: 1,
                        actorArchetypeId: "actor-archetype.shuttle-raider",
                        source: "plugin.hand-authored.opening-shuttle-ambush.001",
                        scenarioId: "scenario.plugin.opening-shuttle-ambush.elite-wave",
                        encounterId: "encounter.plugin.opening-shuttle-ambush.elite-wave",
                        displayName: "Elite Boarding Leader",
                        objectiveLabel: "Defeat the elite boarding leader",
                        alert: "Opening Shuttle Ambush Elite Wave: Elite Boarding Leader inbound — 3x hostile health confirmed",
                        healthMultiplier: 3
                      }
                    };
                  }
                };
              }
            };
            Object.assign(runtime, methods);
            runtime.openingShuttleEncounter = runtime.createOpeningShuttleEncounterRuntimeState();

            runtime.recordOpeningShuttleEncounterEvent(
              'alien-defeated',
              {alienId: 'boarding-alien-1', spawnId: 'port-aft-pad', kills: 1},
              1200
            );
            runtime.recordOpeningShuttleEncounterEvent(
              'alien-defeated',
              {alienId: 'boarding-alien-2', spawnId: 'starboard-aft-pad', kills: 2},
              1800
            );
            const spawned = runtime.resolveOpeningShuttleEliteWave(1810);
            const blockedDuplicate = runtime.resolveOpeningShuttleEliteWave(1820);
            const elite = runtime.aliens[0];
            runtime.recordOpeningShuttleEncounterEvent(
              'alien-defeated',
              {
                alienId: elite.id,
                spawnId: elite.spawnId,
                kills: 3,
                eliteWave: true,
                encounterRole: elite.encounterRole,
                actorArchetypeId: elite.actorArchetypeId
              },
              2600
            );
            const snapshot = runtime.openingShuttleEncounterSnapshot();

            console.log(JSON.stringify({spawned, blockedDuplicate, elite, snapshot, emitted: runtime.emitted}));
            """
        )

        assert result["spawned"] is True
        assert result["blockedDuplicate"] is False
        elite = result["elite"]
        assert elite["id"] == "boarding-elite-raider-3"
        assert elite["spawnId"] == "center-pad"
        assert elite["health"] == 180
        assert elite["maxHealth"] == 180
        assert elite["eliteWave"] is True
        assert elite["encounterRole"] == "elite-boarding-leader"
        assert elite["displayName"] == "Elite Boarding Leader"
        assert elite["label"] == "Elite Boarding Leader"
        assert elite["actorArchetypeId"] == "actor-archetype.shuttle-raider"
        assert result["emitted"] == 1

        snapshot = result["snapshot"]
        assert snapshot["counters"]["defeated"] == 3
        assert snapshot["counters"]["eliteSpawned"] == 1
        assert snapshot["counters"]["eliteDefeated"] == 1
        assert snapshot["counters"]["destinationReached"] == 0
        assert snapshot["eliteWave"] == {
            "enabled": True,
            "triggerDefeats": 2,
            "requested": True,
            "spawned": True,
            "cleared": True,
            "requiredExtraHostiles": 1,
            "spawnedCount": 1,
            "actorArchetypeId": "actor-archetype.shuttle-raider",
            "source": "plugin.hand-authored.opening-shuttle-ambush.001",
            "scenarioId": "scenario.plugin.opening-shuttle-ambush.elite-wave",
            "encounterId": "encounter.plugin.opening-shuttle-ambush.elite-wave",
            "pluginId": "plugin.hand-authored.opening-shuttle-ambush.001",
            "packConfigAvailable": True,
            "packConfigured": True,
            "activePluginIds": ["plugin.hand-authored.opening-shuttle-ambush.001"],
            "displayName": "Elite Boarding Leader",
            "objectiveLabel": "Defeat the elite boarding leader",
            "packLabel": "Opening Shuttle Ambush Elite Wave",
            "alert": "Opening Shuttle Ambush Elite Wave: Elite Boarding Leader inbound — 3x hostile health confirmed",
            "healthMultiplier": 3,
        }
        assert [objective["status"] for objective in snapshot["objectiveSequence"]] == [
            "active",
            "completed",
            "active",
        ]
        assert snapshot["objectiveSequence"][1]["progress"] == {
            "defeated": 3,
            "eliteRequested": True,
            "eliteSpawned": True,
            "eliteCleared": True,
            "requiredExtraHostiles": 1,
            "eliteDisplayName": "Elite Boarding Leader",
            "packConfigured": True,
        }
        assert snapshot["objectiveLine"] == "Reach Haven orbit with the leader neutralized • ACTIVE"
        assert snapshot["packLine"] == "Gameplay Pack: Opening Shuttle Ambush Elite Wave • Elite Boarding Leader cleared • +1 hostile • 3x hostile health"
        assert [receipt["id"] for receipt in snapshot["receipts"]] == [
            "receipt.solace-reach.opening-shuttle-ambush.first-raider-defeated",
            "receipt.solace-reach.opening-shuttle-ambush.elite-wave-requested",
            "receipt.solace-reach.opening-shuttle-ambush.elite-wave-spawned",
            "receipt.solace-reach.opening-shuttle-ambush.elite-wave-cleared",
        ]
        assert snapshot["execution"] == {
            "generatedPluginExecution": False,
            "generatedTemplateExecution": False,
            "rendererHandoff": False,
            "saveStateMutated": False,
            "projectJsonModified": False,
            "additionalSpawnRequested": True,
        }



    def test_pack_health_multiplier_triples_spawned_raiders_only_when_pack_active(self) -> None:
        result = self.run_node(
            """
            const fs = require('fs');
            const source = fs.readFileSync(process.argv[1], 'utf8');
            const start = source.indexOf('        createOpeningShuttleEncounterRuntimeState');
            const end = source.indexOf('        canAlienOccupy(', start);
            if (start < 0 || end < 0) throw new Error('opening shuttle combat methods not found');
            const methodSource = source.slice(start, end).trim()
              .replace(/\\n        (?=(isOpening|opening|award|completeOpening|record|spawnOpening|resolve|combatSnapshot|publish|emitCombatState|spawnAlien))/g, ',\\n        ');
            const methods = Function(`return ({${methodSource}});`)();

            function makeRuntime(packEnabled) {
              globalThis.MainComputerSystemScenarioRuntime = {
                current() {
                  return {
                    openingShuttleGameplayPackConfig() {
                      if (!packEnabled) {
                        return {
                          available: false,
                          active: false,
                          packLabel: "None — base game",
                          extraHostileCount: 0,
                          eliteWave: {enabled: false, healthMultiplier: 1}
                        };
                      }
                      return {
                        available: true,
                        active: true,
                        pluginId: "plugin.hand-authored.opening-shuttle-ambush.001",
                        scenarioId: "scenario.plugin.opening-shuttle-ambush.elite-wave",
                        scenarioTitle: "Opening Shuttle Ambush Elite Wave",
                        packLabel: "Opening Shuttle Ambush Elite Wave",
                        encounterId: "encounter.plugin.opening-shuttle-ambush.elite-wave",
                        encounterTitle: "Opening Shuttle Ambush Elite Leader",
                        extraHostileCount: 1,
                        activePluginIds: ["plugin.hand-authored.opening-shuttle-ambush.001"],
                        hostileHealthMultiplier: 3,
                        eliteWave: {
                          enabled: true,
                          triggerDefeats: 2,
                          count: 1,
                          actorArchetypeId: "actor-archetype.shuttle-raider",
                          source: "plugin.hand-authored.opening-shuttle-ambush.001",
                          scenarioId: "scenario.plugin.opening-shuttle-ambush.elite-wave",
                          encounterId: "encounter.plugin.opening-shuttle-ambush.elite-wave",
                          displayName: "Elite Boarding Leader",
                          objectiveLabel: "Defeat the 3x-health elite boarding leader",
                          alert: "Opening Shuttle Ambush Elite Wave: elite boarding leader inbound — 3x hostile health confirmed",
                          healthMultiplier: 3
                        }
                      };
                    }
                  };
                }
              };
              const runtime = {
                combat: {
                  enabled: true,
                  transport: {
                    maxAlive: 4,
                    beamDurationMs: 900,
                    spawnPoints: [
                      {id: 'port-aft-pad', position: [-2.9, -0.55, 2.55]},
                      {id: 'starboard-aft-pad', position: [2.9, -0.55, 2.55]},
                      {id: 'center-pad', position: [0, -0.55, 0.3]}
                    ]
                  },
                  alien: {maxHealth: 60}
                },
                aliens: [],
                transportSequence: 0,
                gameOver: false,
                combatClockMs: 0,
                emitted: 0,
                characterAIPhase() { return 'shuttle'; },
                emitCombatState() { this.emitted += 1; }
              };
              Object.assign(runtime, methods);
              runtime.openingShuttleEncounter = runtime.createOpeningShuttleEncounterRuntimeState();
              return runtime;
            }

            const none = makeRuntime(false);
            none.spawnAlien(100);
            const noneAlien = none.aliens[0];

            const selected = makeRuntime(true);
            selected.spawnAlien(100);
            const selectedAlien = selected.aliens[0];

            selected.recordOpeningShuttleEncounterEvent(
              'alien-defeated',
              {alienId: 'boarding-alien-1', spawnId: 'port-aft-pad', kills: 1},
              1200
            );
            selected.recordOpeningShuttleEncounterEvent(
              'alien-defeated',
              {alienId: 'boarding-alien-2', spawnId: 'starboard-aft-pad', kills: 2},
              1800
            );
            const eliteSpawned = selected.resolveOpeningShuttleEliteWave(1810);
            const elite = selected.aliens.find((alien) => alien.eliteWave === true);

            console.log(JSON.stringify({
              noneAlien,
              selectedAlien,
              eliteSpawned,
              elite,
              selectedSnapshot: selected.openingShuttleEncounterSnapshot()
            }));
            """
        )

        assert result["noneAlien"]["health"] == 60
        assert result["noneAlien"]["maxHealth"] == 60
        assert result["noneAlien"]["healthMultiplier"] == 1
        assert result["noneAlien"]["packPowered"] is False

        assert result["selectedAlien"]["health"] == 180
        assert result["selectedAlien"]["maxHealth"] == 180
        assert result["selectedAlien"]["healthMultiplier"] == 3
        assert result["selectedAlien"]["packPowered"] is True
        assert result["selectedAlien"]["sourcePluginId"] == "plugin.hand-authored.opening-shuttle-ambush.001"

        assert result["eliteSpawned"] is True
        assert result["elite"]["health"] == 180
        assert result["elite"]["maxHealth"] == 180
        assert result["elite"]["healthMultiplier"] == 3
        assert result["selectedSnapshot"]["eliteWave"]["healthMultiplier"] == 3
        assert result["selectedSnapshot"]["packLine"] == (
            "Gameplay Pack: Opening Shuttle Ambush Elite Wave • Elite Boarding Leader aboard "
            "• +1 hostile • 3x hostile health"
        )


    def test_destination_reached_records_completion_receipts_without_generated_execution(self) -> None:
        result = self.run_node(
            """
            const fs = require('fs');
            const source = fs.readFileSync(process.argv[1], 'utf8');
            const start = source.indexOf('        createOpeningShuttleEncounterRuntimeState');
            const end = source.indexOf('        combatSnapshot(', start);
            if (start < 0 || end < 0) throw new Error('opening shuttle hook methods not found');
            const methodSource = source.slice(start, end).trim()
              .replace(/\\n        (?=(isOpening|opening|award|completeOpening|record|spawnOpening|resolve))/g, ',\\n        ');
            const methods = Function(`return ({${methodSource}});`)();
            const runtime = {
              combat: {
                enabled: true,
                transport: {
                  maxAlive: 4,
                  beamDurationMs: 900,
                  spawnPoints: [{id: 'center-pad', position: [0, -0.55, 0.3]}]
                },
                alien: {maxHealth: 60}
              },
              aliens: [],
              transportSequence: 0,
              gameOver: false,
              combatClockMs: 0,
              emitted: 0,
              characterAIPhase() { return 'shuttle'; },
              emitCombatState() { this.emitted += 1; }
            };
            globalThis.MainComputerSystemScenarioRuntime = {
              current() {
                return {
                  openingShuttleGameplayPackConfig() {
                    return {
                      available: true,
                      active: true,
                      pluginId: "plugin.hand-authored.opening-shuttle-ambush.001",
                      scenarioId: "scenario.plugin.opening-shuttle-ambush.elite-wave",
                      encounterId: "encounter.plugin.opening-shuttle-ambush.elite-wave",
                      extraHostileCount: 1,
                      activePluginIds: ["plugin.hand-authored.opening-shuttle-ambush.001"],
                      eliteWave: {
                        enabled: true,
                        triggerDefeats: 2,
                        count: 1,
                        actorArchetypeId: "actor-archetype.shuttle-raider",
                        source: "plugin.hand-authored.opening-shuttle-ambush.001",
                        scenarioId: "scenario.plugin.opening-shuttle-ambush.elite-wave",
                        encounterId: "encounter.plugin.opening-shuttle-ambush.elite-wave"
                      }
                    };
                  }
                };
              }
            };
            Object.assign(runtime, methods);
            runtime.openingShuttleEncounter = runtime.createOpeningShuttleEncounterRuntimeState();

            runtime.recordOpeningShuttleEncounterEvent(
              'alien-defeated',
              {alienId: 'boarding-alien-1', spawnId: 'port-aft-pad', kills: 1},
              1200
            );
            runtime.recordOpeningShuttleEncounterEvent(
              'alien-defeated',
              {alienId: 'boarding-alien-2', spawnId: 'starboard-aft-pad', kills: 2},
              1800
            );
            runtime.resolveOpeningShuttleEliteWave(1810);
            const elite = runtime.aliens[0];
            runtime.recordOpeningShuttleEncounterEvent(
              'alien-defeated',
              {
                alienId: elite.id,
                spawnId: elite.spawnId,
                kills: 3,
                eliteWave: true,
                encounterRole: elite.encounterRole,
                actorArchetypeId: elite.actorArchetypeId
              },
              2600
            );
            const completed = runtime.completeOpeningShuttleEncounterAtDestination(
              4200,
              {locationId: 'bay.shuttle', reason: 'test'}
            );
            const duplicate = runtime.completeOpeningShuttleEncounterAtDestination(
              4300,
              {locationId: 'bay.shuttle', reason: 'duplicate'}
            );
            const snapshot = runtime.openingShuttleEncounterSnapshot();

            console.log(JSON.stringify({completed, duplicate, snapshot}));
            """
        )

        assert result["completed"] is True
        assert result["duplicate"] is False
        snapshot = result["snapshot"]
        assert snapshot["status"] == "completed"
        assert snapshot["counters"]["destinationReached"] == 1
        assert [objective["status"] for objective in snapshot["objectiveSequence"]] == [
            "completed",
            "completed",
            "completed",
        ]
        assert snapshot["objectiveSequence"][2]["progress"] == {
            "systemId": "system.solace-reach",
            "destinationId": "destination.solace-reach.haven-orbit",
            "destinationReached": True,
            "encounterCompleted": True,
            "packConfigured": True,
        }
        assert snapshot["completion"] == {
            "destinationReached": True,
            "completed": True,
            "failed": False,
            "completedAtMs": 4200,
            "failedAtMs": None,
            "receiptIds": [
                "receipt.solace-reach.opening-shuttle-ambush.first-raider-defeated",
                "receipt.solace-reach.opening-shuttle-ambush.elite-wave-requested",
                "receipt.solace-reach.opening-shuttle-ambush.elite-wave-spawned",
                "receipt.solace-reach.opening-shuttle-ambush.elite-wave-cleared",
                "receipt.solace-reach.opening-shuttle-ambush.destination-reached",
                "receipt.solace-reach.opening-shuttle-ambush.completed",
            ],
        }
        assert snapshot["receiptIds"] == snapshot["completion"]["receiptIds"]
        assert snapshot["events"][-1]["type"] == "destination-reached"
        assert snapshot["events"][-1]["detail"] == {
            "systemId": "system.solace-reach",
            "destinationId": "destination.solace-reach.haven-orbit",
            "locationId": "bay.shuttle",
            "reason": "test",
        }
        assert snapshot["lastMessage"] == "Opening shuttle ambush cleared at Haven orbit."
        assert snapshot["execution"] == {
            "generatedPluginExecution": False,
            "generatedTemplateExecution": False,
            "rendererHandoff": False,
            "saveStateMutated": False,
            "projectJsonModified": False,
            "additionalSpawnRequested": True,
        }


    def test_legacy_combat_loop_emits_hook_events_and_controlled_elite_spawn(self) -> None:
        source = SCENE_VIEWER.read_text(encoding="utf-8")

        self.assertIn("this.openingShuttleEncounter = this.createOpeningShuttleEncounterRuntimeState();", source)
        self.assertIn("const openingShuttleEncounter = this.openingShuttleEncounterSnapshot();", source)
        self.assertIn("openingShuttleEncounter,", source)
        self.assertIn("openingShuttleAuthoringAlignment", source)
        self.assertIn("scene-shuttle3d-encounter-line", source)
        self.assertIn("scene-shuttle3d-pack-line", source)
        self.assertIn("scene-shuttle3d-authoring-line", source)
        self.assertIn("OBJECTIVE: ${encounter.objectiveLine}", source)
        self.assertIn("GAMEPLAY PACK:", source)
        self.assertIn("AUTHORING ALIGNMENT: ${String(authoringAlignment.alignmentStatus).toUpperCase()}", source)
        self.assertIn("encounterLine.dataset.objectiveStatus", source)
        self.assertIn('"alien-spawned"', source)
        self.assertIn('"alien-activated"', source)
        self.assertIn('"player-damaged"', source)
        self.assertIn('"player-defeated"', source)
        self.assertIn('"alien-defeated"', source)
        self.assertIn('this.recordOpeningShuttleEncounterEvent?.("combat-reset", {}, nowMs);', source)
        self.assertIn("this.emitCharacterAIState?.(true);", source)

        hook_start = source.index("        createOpeningShuttleEncounterRuntimeState")
        hook_end = source.index("        combatSnapshot(", hook_start)
        hook_source = source[hook_start:hook_end]
        self.assertIn("MainComputerSystemScenarioRuntime", hook_source)
        self.assertIn("openingShuttleGameplayPackConfig", hook_source)
        self.assertIn("spawnOpeningShuttleEliteRaider", hook_source)
        self.assertIn("completeOpeningShuttleEncounterAtDestination", hook_source)
        self.assertIn("publishOpeningShuttleEncounterBridge", source)
        self.assertIn("setOpeningShuttleEncounterBridgeSnapshot", source)
        self.assertIn("this.aliens.push(alien);", hook_source)
        self.assertIn("this.resolveOpeningShuttleEliteWave?.(nowMs);", source)
        self.assertIn("this.completeOpeningShuttleEncounterAtDestination?.", source)


if __name__ == "__main__":
    unittest.main()
