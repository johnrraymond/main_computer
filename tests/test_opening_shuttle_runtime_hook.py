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
        self.assertIn("openingShuttleApplyGameplayPackCommand", hook_source)
        self.assertNotIn("openingShuttleActiveGameplayPackConfig", hook_source)
        self.assertNotIn("openingShuttleGameplayPackConfig", hook_source)
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

    def test_js_pack_spawn_wave_command_resolves_to_one_elite_raider(self) -> None:
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
                    throw new Error("legacy generated config must not be consulted by scene-viewer");
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
            const applied = runtime.openingShuttleApplyGameplayPackCommand({
              type: "spawn-wave",
              sourcePackId: "pack.opening-shuttle.elite-boarders",
              payload: {
                id: "elite-boarding-leader",
                trigger: {type: "after-hostile-defeats", count: 2},
                actors: [{
                  archetype: "shuttle-raider",
                  count: 1,
                  displayName: "Elite Boarding Leader",
                  healthMultiplier: 3
                }],
                hudMessage: "Elite Boarding Leader inbound."
              }
            }, 1810);
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

            console.log(JSON.stringify({applied, blockedDuplicate, elite, snapshot, emitted: runtime.emitted}));
            """
        )

        assert result["applied"] is True
        assert result["blockedDuplicate"] is False
        elite = result["elite"]
        assert elite["id"] == "boarding-elite-raider-3"
        assert elite["spawnId"] == "center-pad"
        assert elite["health"] == 180
        assert elite["maxHealth"] == 180
        assert elite["healthMultiplier"] == 3
        assert elite["packPowered"] is True
        assert elite["eliteWave"] is True
        assert elite["displayName"] == "Elite Boarding Leader"
        assert elite["sourcePluginId"] == "pack.opening-shuttle.elite-boarders"
        assert elite["actorArchetypeId"] == "actor-archetype.shuttle-raider"
        snapshot = result["snapshot"]
        assert snapshot["counters"]["eliteSpawned"] == 1
        assert snapshot["counters"]["eliteDefeated"] == 1
        assert snapshot["eliteWave"]["requested"] is True
        assert snapshot["eliteWave"]["spawned"] is True
        assert snapshot["eliteWave"]["cleared"] is True
        assert snapshot["eliteWave"]["healthMultiplier"] == 3
        assert snapshot["eliteWave"]["pluginId"] == "pack.opening-shuttle.elite-boarders"
        assert snapshot["execution"]["additionalSpawnRequested"] is True

    def test_legacy_generated_pack_config_no_longer_triples_raiders_without_js_commands(self) -> None:
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

            globalThis.MainComputerSystemScenarioRuntime = {
              current() {
                return {
                  openingShuttleGameplayPackConfig() {
                    return {
                      available: true,
                      active: true,
                      pluginId: "plugin.hand-authored.opening-shuttle-ambush.001",
                      extraHostileCount: 1,
                      hostileHealthMultiplier: 3,
                      eliteWave: {
                        enabled: true,
                        triggerDefeats: 2,
                        count: 1,
                        actorArchetypeId: "actor-archetype.shuttle-raider",
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

            runtime.spawnAlien(100);
            const alien = runtime.aliens[0];
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
            const snapshot = runtime.openingShuttleEncounterSnapshot();

            console.log(JSON.stringify({alien, spawned, snapshot}));
            """
        )

        assert result["alien"]["health"] == 60
        assert result["alien"]["maxHealth"] == 60
        assert result["alien"]["healthMultiplier"] == 1
        assert result["alien"]["packPowered"] is False
        assert result["spawned"] is False
        assert result["snapshot"]["eliteWave"]["enabled"] is False
        assert result["snapshot"]["eliteWave"]["packConfigured"] is False
        assert result["snapshot"]["jsGameplayPack"]["installed"] is False
        assert result["snapshot"]["packLine"] == "Gameplay Pack: None — base game"

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
                    throw new Error("legacy generated config must not be consulted by scene-viewer");
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
            runtime.openingShuttleApplyGameplayPackCommand({
              type: "spawn-wave",
              sourcePackId: "pack.opening-shuttle.elite-boarders",
              payload: {
                id: "elite-boarding-leader",
                trigger: {type: "after-hostile-defeats", count: 2},
                actors: [{
                  archetype: "shuttle-raider",
                  count: 1,
                  displayName: "Elite Boarding Leader",
                  healthMultiplier: 3
                }],
                hudMessage: "Elite Boarding Leader inbound."
              }
            }, 1810);
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
        assert snapshot["completion"]["completed"] is True
        assert snapshot["completion"]["destinationReached"] is True
        assert snapshot["completion"]["receiptIds"] == [
            "receipt.solace-reach.opening-shuttle-ambush.first-raider-defeated",
            "receipt.solace-reach.opening-shuttle-ambush.elite-wave-spawned",
            "receipt.solace-reach.opening-shuttle-ambush.elite-wave-cleared",
            "receipt.solace-reach.opening-shuttle-ambush.destination-reached",
            "receipt.solace-reach.opening-shuttle-ambush.completed",
        ]
        assert snapshot["receiptIds"] == snapshot["completion"]["receiptIds"]
        assert snapshot["events"][-1]["type"] == "destination-reached"
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
        self.assertNotIn("MainComputerSystemScenarioRuntime", hook_source)
        self.assertNotIn("openingShuttleGameplayPackConfig", hook_source)
        self.assertIn("openingShuttleApplyGameplayPackCommand", hook_source)
        self.assertIn("spawnOpeningShuttleEliteRaider", hook_source)
        self.assertIn("completeOpeningShuttleEncounterAtDestination", hook_source)
        self.assertIn("publishOpeningShuttleEncounterBridge", source)
        self.assertIn("setOpeningShuttleEncounterBridgeSnapshot", source)
        self.assertIn("this.aliens.push(alien);", hook_source)
        self.assertIn("this.resolveOpeningShuttleEliteWave?.(nowMs);", source)
        self.assertIn("this.completeOpeningShuttleEncounterAtDestination?.", source)


if __name__ == "__main__":
    unittest.main()
