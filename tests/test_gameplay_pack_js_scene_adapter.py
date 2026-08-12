from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = ROOT / "main_computer" / "web" / "applications" / "scripts"
SCENE_VIEWER = SCRIPT_ROOT / "scene-viewer.js"
GAMEPLAY_PACK_RUNTIME = SCRIPT_ROOT / "gameplay-pack-runtime.js"
PACK_JS = ROOT / "game_projects" / "webgl-demo" / "gameplay_packs" / "opening_shuttle_elite_boarders" / "pack.js"
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"


class GameplayPackJsSceneAdapterTests(unittest.TestCase):
    """Patch W: apply the current JS gameplay-pack commands to the live shuttle adapter."""

    def run_node(self, script: str) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is required for the JS gameplay-pack scene adapter tests")
        result = subprocess.run(
            [
                "node",
                "-e",
                textwrap.dedent(script),
                str(SCENE_VIEWER),
                str(GAMEPLAY_PACK_RUNTIME),
                str(PACK_JS),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_application_includes_gameplay_pack_runtime_before_scene_viewer(self) -> None:
        html = APPLICATIONS_HTML.read_text(encoding="utf-8")
        pack_runtime = html.index("applications/scripts/gameplay-pack-runtime.js")
        scene_viewer = html.index("applications/scripts/scene-viewer.js")
        self.assertLess(pack_runtime, scene_viewer)

    def test_js_pack_commands_drive_shuttle_health_wave_and_completion(self) -> None:
        result = self.run_node(
            r"""
            const fs = require("fs");
            const sceneSource = fs.readFileSync(process.argv[1], "utf8");
            const packApi = require(process.argv[2]);
            const packSource = fs.readFileSync(process.argv[3], "utf8");

            const start = sceneSource.indexOf("        createOpeningShuttleEncounterRuntimeState");
            const end = sceneSource.indexOf("        canAlienOccupy(", start);
            if (start < 0 || end < 0) throw new Error("opening shuttle methods not found");
            const methodSource = sceneSource.slice(start, end).trim()
              .replace(/\n        (?=(isOpening|opening|award|completeOpening|record|spawnOpening|resolve|combatSnapshot|publish|emitCombatState|spawnAlien))/g, ",\n        ");
            const methods = Function(`return ({${methodSource}});`)();

            globalThis.MainComputerGameplayPackRuntime = packApi;
            globalThis.MainComputerSystemScenarioRuntime = {
              current() {
                return {
                  openingShuttleGameplayPackConfig() {
                    return {
                      available: false,
                      active: false,
                      packLabel: "None — base game",
                      extraHostileCount: 0,
                      eliteWave: {enabled: false, healthMultiplier: 1}
                    };
                  },
                  setOpeningShuttleEncounterBridgeSnapshot(snapshot) {
                    return snapshot;
                  },
                  openingShuttleEncounterBridgeDiagnostic() {
                    return {kind: "diagnostic", available: true, safety: {}, coverage: {}};
                  }
                };
              }
            };

            const runtime = {
              combat: {
                enabled: true,
                transport: {
                  maxAlive: 6,
                  beamDurationMs: 900,
                  spawnPoints: [
                    {id: "port-aft-pad", position: [-2.9, -0.55, 2.55]},
                    {id: "starboard-aft-pad", position: [2.9, -0.55, 2.55]},
                    {id: "center-pad", position: [0, -0.55, 0.3]}
                  ]
                },
                alien: {maxHealth: 60}
              },
              aliens: [],
              transportSequence: 0,
              gameOver: false,
              combatClockMs: 0,
              emitted: 0,
              characterAIPhase() { return "shuttle"; },
              emitCombatState() { this.emitted += 1; }
            };
            Object.assign(runtime, methods);
            runtime.openingShuttleEncounter = runtime.createOpeningShuttleEncounterRuntimeState();

            const harness = packApi.loadGameplayPackCommandHarnessFromSource(packSource);
            const installed = runtime.openingShuttleInstallGameplayPackCommandHarness(harness, 100);
            runtime.spawnAlien(200);
            const firstAlien = runtime.aliens[0];

            runtime.recordOpeningShuttleEncounterEvent(
              "alien-defeated",
              {alienId: "boarding-alien-1", spawnId: "port-aft-pad", kills: 1},
              1200
            );
            runtime.recordOpeningShuttleEncounterEvent(
              "alien-defeated",
              {alienId: "boarding-alien-2", spawnId: "starboard-aft-pad", kills: 2},
              1800
            );
            const elite = runtime.aliens.find((alien) => alien.eliteWave === true);

            runtime.recordOpeningShuttleEncounterEvent(
              "alien-defeated",
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

            runtime.completeOpeningShuttleEncounterAtDestination(
              4200,
              {locationId: "bay.shuttle", reason: "js-pack-smoke"}
            );

            const snapshot = runtime.openingShuttleEncounterSnapshot();
            console.log(JSON.stringify({
              installed,
              firstAlien,
              elite,
              snapshot,
              harnessSummary: packApi.gameplayPackCommandHarnessSummary(harness),
              emitted: runtime.emitted
            }));
            """
        )

        first_alien = result["firstAlien"]
        assert first_alien["health"] == 180
        assert first_alien["maxHealth"] == 180
        assert first_alien["healthMultiplier"] == 3
        assert first_alien["packPowered"] is True
        assert first_alien["sourcePluginId"] == "pack.opening-shuttle.elite-boarders"

        elite = result["elite"]
        assert elite["displayName"] == "Elite Boarding Leader"
        assert elite["health"] == 180
        assert elite["maxHealth"] == 180
        assert elite["healthMultiplier"] == 3
        assert elite["sourcePluginId"] == "pack.opening-shuttle.elite-boarders"
        assert elite["actorArchetypeId"] == "actor-archetype.shuttle-raider"

        snapshot = result["snapshot"]
        assert snapshot["jsGameplayPack"]["installed"] is True
        assert snapshot["jsGameplayPack"]["packId"] == "pack.opening-shuttle.elite-boarders"
        assert snapshot["jsGameplayPack"]["title"] == "Opening Shuttle: Elite Boarders"
        assert snapshot["jsGameplayPack"]["setupRun"] is True
        assert snapshot["jsGameplayPack"]["handlersRegistered"] is True
        assert snapshot["jsGameplayPack"]["commandsApplied"] >= 7
        assert snapshot["eliteWave"]["enabled"] is True
        assert snapshot["eliteWave"]["requested"] is True
        assert snapshot["eliteWave"]["spawned"] is True
        assert snapshot["eliteWave"]["cleared"] is True
        assert snapshot["eliteWave"]["healthMultiplier"] == 3
        assert snapshot["eliteWave"]["pluginId"] == "pack.opening-shuttle.elite-boarders"
        assert snapshot["status"] == "completed"
        assert "receipt.opening-shuttle.elite-boarders-cleared" in snapshot["receiptIds"]
        assert snapshot["packLine"] == (
            "Gameplay Pack: Opening Shuttle: Elite Boarders • Elite Boarding Leader cleared "
            "• +1 hostile • 3x hostile health"
        )
        assert snapshot["lastMessage"] == "Elite boarders defeated. Shuttle route secured."
        assert result["harnessSummary"]["commandCount"] >= 7

    def test_without_installed_js_harness_scene_remains_base_health(self) -> None:
        result = self.run_node(
            r"""
            const fs = require("fs");
            const sceneSource = fs.readFileSync(process.argv[1], "utf8");
            const start = sceneSource.indexOf("        createOpeningShuttleEncounterRuntimeState");
            const end = sceneSource.indexOf("        canAlienOccupy(", start);
            if (start < 0 || end < 0) throw new Error("opening shuttle combat methods not found");
            const methodSource = sceneSource.slice(start, end).trim()
              .replace(/\n        (?=(isOpening|opening|award|completeOpening|record|spawnOpening|resolve|combatSnapshot|publish|emitCombatState|spawnAlien))/g, ",\n        ");
            const methods = Function(`return ({${methodSource}});`)();

            globalThis.MainComputerSystemScenarioRuntime = {
              current() {
                return {
                  openingShuttleGameplayPackConfig() {
                    return {
                      available: false,
                      active: false,
                      packLabel: "None — base game",
                      extraHostileCount: 0,
                      eliteWave: {enabled: false, healthMultiplier: 1}
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
                  spawnPoints: [{id: "center-pad", position: [0, -0.55, 0.3]}]
                },
                alien: {maxHealth: 60}
              },
              aliens: [],
              transportSequence: 0,
              gameOver: false,
              combatClockMs: 0,
              characterAIPhase() { return "shuttle"; },
              emitCombatState() {}
            };
            Object.assign(runtime, methods);
            runtime.openingShuttleEncounter = runtime.createOpeningShuttleEncounterRuntimeState();
            runtime.spawnAlien(100);
            const snapshot = runtime.openingShuttleEncounterSnapshot();
            console.log(JSON.stringify({alien: runtime.aliens[0], snapshot}));
            """
        )

        assert result["alien"]["health"] == 60
        assert result["alien"]["maxHealth"] == 60
        assert result["alien"]["healthMultiplier"] == 1
        assert result["alien"]["packPowered"] is False
        assert result["snapshot"]["jsGameplayPack"]["installed"] is False
        assert result["snapshot"]["packLine"] == "Gameplay Pack: None — base game"


if __name__ == "__main__":
    unittest.main()
