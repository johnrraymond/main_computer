from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = ROOT / "main_computer" / "web" / "applications" / "scripts"
WEBGL_DESKTOP = SCRIPT_ROOT / "webgl-desktop.js"
SCENE_VIEWER = SCRIPT_ROOT / "scene-viewer.js"
GAMEPLAY_PACK_RUNTIME = SCRIPT_ROOT / "gameplay-pack-runtime.js"
PACK_JS = ROOT / "game_projects" / "webgl-demo" / "gameplay_packs" / "opening_shuttle_elite_boarders" / "pack.js"


class GameplayPackJsFinalCleanupTests(unittest.TestCase):
    '''Patch Z: final stabilization and observability for the JS shuttle pack path.'''

    def run_node(self, script: str, *args: Path) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is required for JS gameplay-pack cleanup tests")
        result = subprocess.run(
            ["node", "-e", textwrap.dedent(script), *(str(arg) for arg in args)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_webgl_records_js_pack_install_result_and_none_clears_runtime(self) -> None:
        result = self.run_node(
            r'''
            const fs = require("fs");
            const source = fs.readFileSync(process.argv[1], "utf8");
            const start = source.indexOf("    const webglProjectState");
            const end = source.indexOf("    function ensureWebglSystemScenarioRuntime", start);
            if (start < 0 || end < 0) throw new Error("webgl helper block not found");
            const helperSource = source.slice(start, end).replace(/^    /gm, "");
            const statusNode = {dataset: {}, textContent: ""};
            const panelNode = {dataset: {}};
            const fakeDocument = {
              getElementById(id) {
                if (id === "webgl-gameplay-pack-status") return statusNode;
                if (id === "webgl-gameplay-pack-selector") return panelNode;
                return null;
              },
              querySelector() { return null; },
              createElement() { return {dataset: {}, appendChild() {}, addEventListener() {}}; }
            };
            const fakeWindow = {
              location: {search: ""},
              localStorage: {getItem() { return JSON.stringify({mode: "none", activeGameplayPackIds: []}); }}
            };
            const helpers = Function("window", "document", "fetch", `${helperSource}
              return {
                installSelectedWebglJsGameplayPack,
                webglRecordJsGameplayPackInstallResult,
                webglGameplayPackInstallStatusMessage,
                webglProjectState
              };
            `)(fakeWindow, fakeDocument, async () => ({ok: true, status: 200, json: async () => ({ok: true})}));

            let cleared = false;
            const runtime = {
              openingShuttleClearGameplayPackCommandHarness(options) {
                cleared = options.reason === "no-active-gameplay-pack";
                return {jsGameplayPack: {installed: false}};
              }
            };

            (async () => {
              const noneResult = await helpers.installSelectedWebglJsGameplayPack(runtime, {metadata: {}});
              helpers.webglRecordJsGameplayPackInstallResult({
                mode: "selected",
                installed: true,
                packId: "pack.opening-shuttle.elite-boarders",
                title: "Opening Shuttle: Elite Boarders",
                commandsApplied: 3
              });
              const selectedStatus = {
                text: statusNode.textContent,
                mode: statusNode.dataset.mode,
                panel: {...panelNode.dataset},
                state: helpers.webglProjectState.jsGameplayPackInstallResult
              };
              helpers.webglRecordJsGameplayPackInstallResult(noneResult);
              console.log(JSON.stringify({
                noneResult,
                cleared,
                selectedStatus,
                noneStatus: {
                  text: statusNode.textContent,
                  mode: statusNode.dataset.mode,
                  panel: {...panelNode.dataset},
                  state: helpers.webglProjectState.jsGameplayPackInstallResult
                },
                errorMessage: helpers.webglGameplayPackInstallStatusMessage({mode: "error", error: "boom"})
              }));
            })().catch((error) => {
              console.error(error && error.stack ? error.stack : String(error));
              process.exit(1);
            });
            ''',
            WEBGL_DESKTOP,
        )

        self.assertTrue(result["cleared"])
        self.assertEqual(result["noneResult"]["mode"], "none")
        self.assertTrue(result["noneResult"]["cleared"])
        self.assertEqual(result["selectedStatus"]["text"], "Gameplay Pack runtime: Opening Shuttle: Elite Boarders installed")
        self.assertEqual(result["selectedStatus"]["mode"], "selected")
        self.assertEqual(result["selectedStatus"]["panel"]["jsGameplayPackInstalled"], "true")
        self.assertEqual(result["selectedStatus"]["panel"]["jsGameplayPackId"], "pack.opening-shuttle.elite-boarders")
        self.assertEqual(result["selectedStatus"]["state"]["commandsApplied"], 3)
        self.assertEqual(result["noneStatus"]["text"], "Gameplay Pack runtime: None — base game")
        self.assertEqual(result["noneStatus"]["mode"], "none")
        self.assertEqual(result["noneStatus"]["panel"]["jsGameplayPackInstalled"], "false")
        self.assertEqual(result["noneStatus"]["state"]["mode"], "none")
        self.assertEqual(result["errorMessage"], "Gameplay Pack runtime error: boom")

    def test_scene_snapshot_exposes_pack_debug_state_and_clear_returns_base_path(self) -> None:
        result = self.run_node(
            r'''
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
              emitCombatState() { this.emitted += 1; },
              publishOpeningShuttleEncounterBridge(snapshot) { this.lastBridge = snapshot; return snapshot; }
            };
            Object.assign(runtime, methods);
            runtime.openingShuttleEncounter = runtime.createOpeningShuttleEncounterRuntimeState();

            const installed = runtime.openingShuttleInstallGameplayPackSource(
              packSource,
              {source: "test.final-cleanup", selectedGameplayPackId: "pack.opening-shuttle.elite-boarders"},
              100
            );
            const cleared = runtime.openingShuttleClearGameplayPackCommandHarness({source: "test.final-cleanup", reason: "none-selected"}, 200);
            runtime.spawnAlien(300);
            const afterClearAlien = runtime.aliens[runtime.aliens.length - 1] || null;
            console.log(JSON.stringify({
              installed: installed.jsGameplayPack,
              cleared: cleared.jsGameplayPack,
              packLine: cleared.packLine,
              eliteWave: cleared.eliteWave,
              afterClearAlien,
              emitted: runtime.emitted
            }));
            ''',
            SCENE_VIEWER,
            GAMEPLAY_PACK_RUNTIME,
            PACK_JS,
        )

        installed = result["installed"]
        self.assertTrue(installed["installed"])
        self.assertEqual(installed["installStatus"], "installed")
        self.assertEqual(installed["installedAtMs"], 100)
        self.assertEqual(installed["installSource"], "test.final-cleanup")
        self.assertEqual(installed["selectedGameplayPackId"], "pack.opening-shuttle.elite-boarders")
        self.assertGreaterEqual(installed["commandApplyCount"], 3)
        self.assertTrue(installed["lastCommands"])

        cleared = result["cleared"]
        self.assertFalse(cleared["installed"])
        self.assertEqual(cleared["installStatus"], "none")
        self.assertEqual(cleared["uninstalledAtMs"], 200)
        self.assertEqual(result["packLine"], "Gameplay Pack: None — base game")
        self.assertFalse(result["eliteWave"]["enabled"])
        self.assertFalse(result["eliteWave"]["packConfigured"])
        self.assertEqual(result["afterClearAlien"]["health"], 60)


if __name__ == "__main__":
    unittest.main()
