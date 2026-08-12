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
WEBGL_DESKTOP = SCRIPT_ROOT / "webgl-desktop.js"
GAMEPLAY_PACK_RUNTIME = SCRIPT_ROOT / "gameplay-pack-runtime.js"
PACK_JS = ROOT / "game_projects" / "webgl-demo" / "gameplay_packs" / "main_ship_bay_boarders" / "pack.js"
GAME_ROUTES = ROOT / "main_computer" / "viewport_routes_game.py"
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"


class MainShipBayBoardersPackJsSceneAdapterTests(unittest.TestCase):
    """Patch AC: wire the main-ship bay boarders JS pack to the live scene seam."""

    def run_node(self, script: str, *args: Path) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is required for main-ship pack scene adapter tests")
        result = subprocess.run(
            ["node", "-e", textwrap.dedent(script), *(str(arg) for arg in args)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_server_and_selector_expose_main_ship_js_pack(self) -> None:
        routes = GAME_ROUTES.read_text(encoding="utf-8")
        html = APPLICATIONS_HTML.read_text(encoding="utf-8")
        desktop = WEBGL_DESKTOP.read_text(encoding="utf-8")

        self.assertIn('"pack.main-ship.bay-boarders"', routes)
        self.assertIn('"main_ship_bay_boarders"', routes)
        self.assertIn('"pack.main-ship.bay-boarders"', html)
        self.assertIn("Main Ship: Bay Boarders", html)
        self.assertIn("WEBGL_MAIN_SHIP_BAY_BOARDERS_JS_PACK_ID", desktop)
        self.assertIn("mainShipInstallGameplayPackSource", desktop)
        self.assertIn("WEBGL_JS_GAMEPLAY_PACK_IDS.has(canonicalPluginId)", desktop)

    def test_scene_surface_runtime_exposes_main_ship_pack_adapter(self) -> None:
        scene = SCENE_VIEWER.read_text(encoding="utf-8")

        self.assertIn("mainShipInstallGameplayPackSource(source, packOptions = {}, nowMs)", scene)
        self.assertIn("container.__mainComputerShuttle3dRenderer?.mainShipInstallGameplayPackSource", scene)
        self.assertIn("mainShipClearGameplayPackCommandHarness(options = {}, nowMs)", scene)
        self.assertIn("container.__mainComputerShuttle3dRenderer?.mainShipClearGameplayPackCommandHarness", scene)
        self.assertIn("mainShipDispatchGameplayPackSectionEvent(eventType, detail = {}, nowMs)", scene)
        self.assertIn("container.__mainComputerShuttle3dRenderer?.mainShipDispatchGameplayPackSectionEvent", scene)
        self.assertIn("mainShipGameplayPackSnapshot()", scene)
        self.assertIn("container.__mainComputerShuttle3dRenderer?.mainShipGameplayPackSnapshot", scene)

    def test_reload_selection_installs_main_ship_pack_source_not_shuttle_source(self) -> None:
        result = self.run_node(
            """
            const fs = require("fs");
            const desktopSource = fs.readFileSync(process.argv[1], "utf8");
            const packSource = fs.readFileSync(process.argv[2], "utf8");

            const start = desktopSource.indexOf("    const webglProjectState");
            const end = desktopSource.indexOf("    function ensureWebglSystemScenarioRuntime", start);
            if (start < 0 || end < 0) throw new Error("webgl helper block not found");
            const helperSource = desktopSource.slice(start, end).replace(/^    /gm, "");

            let fetchedPayload = null;
            const fakeFetch = async (path, options) => {
              fetchedPayload = JSON.parse(options.body || "{}");
              return {
                ok: true,
                status: 200,
                async json() {
                  return {
                    ok: true,
                    source: packSource,
                    title: "Main Ship: Bay Boarders",
                    packId: "pack.main-ship.bay-boarders"
                  };
                }
              };
            };

            const fakeWindow = {
              location: {search: ""},
              localStorage: {
                getItem(key) {
                  if (key !== "main-computer.webgl.active-gameplay-packs.v1") return null;
                  return JSON.stringify({
                    mode: "selected",
                    activeGameplayPackIds: ["pack.main-ship.bay-boarders"]
                  });
                }
              }
            };
            const fakeDocument = {
              getElementById() { return null; },
              querySelector() { return null; },
              createElement() {
                return {
                  dataset: {},
                  appendChild() {},
                  addEventListener() {},
                  set textContent(value) { this._textContent = value; },
                  get textContent() { return this._textContent || ""; }
                };
              }
            };

            const helpers = Function("window", "document", "fetch", `${helperSource}
              return {
                webglAvailableGameplayPacks,
                webglReloadGameplayPackSelection,
                webglSelectedJsGameplayPackId,
                webglSelectedJsGameplayPackIds,
                installSelectedWebglJsGameplayPack
              };
            `)(fakeWindow, fakeDocument, fakeFetch);

            let openingInstalled = false;
            let mainShipInstalledSource = "";
            let mainShipInstalledOptions = null;
            const runtime = {
              openingShuttleInstallGameplayPackSource() {
                openingInstalled = true;
                return null;
              },
              mainShipInstallGameplayPackSource(source, options) {
                mainShipInstalledSource = source;
                mainShipInstalledOptions = options;
                return {
                  installed: true,
                  title: "Main Ship: Bay Boarders",
                  packId: "pack.main-ship.bay-boarders",
                  commandsApplied: 0
                };
              }
            };

            (async () => {
              const selection = helpers.webglReloadGameplayPackSelection({metadata: {}});
              const available = helpers.webglAvailableGameplayPacks({metadata: {}});
              const installResult = await helpers.installSelectedWebglJsGameplayPack(runtime, {metadata: {}});
              console.log(JSON.stringify({
                selection,
                selectedJsPackId: helpers.webglSelectedJsGameplayPackId(selection),
                selectedJsPackIds: helpers.webglSelectedJsGameplayPackIds(selection),
                hasMainPack: available.some((pack) => pack.pluginId === "pack.main-ship.bay-boarders"),
                fetchedPayload,
                openingInstalled,
                mainShipInstalledSourceIncludesSection: mainShipInstalledSource.includes("pack.section("),
                mainShipInstalledOptions,
                installResult
              }));
            })().catch((error) => {
              console.error(error && error.stack ? error.stack : String(error));
              process.exit(1);
            });
            """,
            WEBGL_DESKTOP,
            PACK_JS,
        )

        self.assertEqual(result["selection"]["mode"], "selected")
        self.assertEqual(result["selectedJsPackId"], "pack.main-ship.bay-boarders")
        self.assertEqual(result["selectedJsPackIds"], ["pack.main-ship.bay-boarders"])
        self.assertTrue(result["hasMainPack"])
        self.assertEqual(result["fetchedPayload"]["pack_id"], "pack.main-ship.bay-boarders")
        self.assertFalse(result["openingInstalled"])
        self.assertTrue(result["mainShipInstalledSourceIncludesSection"])
        self.assertEqual(
            result["mainShipInstalledOptions"]["selectedGameplayPackId"],
            "pack.main-ship.bay-boarders",
        )
        self.assertTrue(result["installResult"]["installed"])
        self.assertEqual(result["installResult"]["packId"], "pack.main-ship.bay-boarders")

    def test_cutscene_resolved_spawns_live_main_ship_boarders_and_all_clear_completes(self) -> None:
        result = self.run_node(
            """
            const fs = require("fs");
            const sceneSource = fs.readFileSync(process.argv[1], "utf8");
            const packApi = require(process.argv[2]);
            const packSource = fs.readFileSync(process.argv[3], "utf8");

            const start = sceneSource.indexOf("        mainShipGameplayPackClone");
            const end = sceneSource.indexOf("        createOpeningShuttleEncounterRuntimeState", start);
            if (start < 0 || end < 0) throw new Error("main ship gameplay pack methods not found");
            const methodSource = sceneSource.slice(start, end).trim()
              .replace(new RegExp("\\n        (?=mainShip|createMainShip|damageMainShip|updateMainShip)", "g"), ",\\n        ");
            const methods = Function(`return ({${methodSource}});`)();

            globalThis.MainComputerGameplayPackRuntime = packApi;

            const runtime = {
              combat: {
                phaser: {damage: 120}
              },
              camera: [0, 0.9, 0],
              playerHealth: 100,
              gameOver: false,
              kills: 0,
              combatClockMs: 0,
              shipState: {
                objectiveId: "objective.bay-ops",
                flags: {boardersPausedAfterDocking: true},
                lastInteractionStatus: ""
              },
              characterAIPhase() { return "mother-ship"; },
              isShuttleBayPlayerControlActive() { return false; },
              setShipInteractionStatus(message) {
                this.shipState.lastInteractionStatus = String(message || "");
              },
              createShipState() {
                return {objectiveId: "objective.bay-ops", flags: {}, lastInteractionStatus: ""};
              },
              emitShipState() { this.shipEmitted = (this.shipEmitted || 0) + 1; },
              emitCombatState() { this.combatEmitted = (this.combatEmitted || 0) + 1; },
              emitCharacterAIState() { this.characterEmitted = (this.characterEmitted || 0) + 1; }
            };
            Object.assign(runtime, methods);

            const installed = runtime.mainShipInstallGameplayPackSource(
              packSource,
              {source: "test.main-ship-scene-adapter", selectedGameplayPackId: "pack.main-ship.bay-boarders"},
              10
            );

            const wrongCutscene = runtime.mainShipDispatchGameplayPackSectionEvent(
              "cutscene-resolved",
              {sectionId: "main-ship-bay", cutsceneId: "wrong-cutscene"},
              20
            );
            const bayEntry = runtime.mainShipDispatchGameplayPackSectionEvent(
              "cutscene-resolved",
              {sectionId: "main-ship-bay", cutsceneId: "main-ship-bay-entry"},
              30
            );
            const duplicate = runtime.mainShipDispatchGameplayPackSectionEvent(
              "cutscene-resolved",
              {sectionId: "main-ship-bay", cutsceneId: "main-ship-bay-entry"},
              40
            );

            const spawned = runtime.mainShipGameplayPackSnapshot();
            runtime.mainShipVisibleGameplayPackBoarders().forEach((boarder) => {
              runtime.damageMainShipGameplayPackBoarder(boarder.id, 999, 100);
            });
            const cleared = runtime.mainShipGameplayPackSnapshot();

            console.log(JSON.stringify({
              installed,
              wrongCutscene,
              bayEntry,
              duplicate,
              spawned,
              cleared,
              objectiveId: runtime.shipState.objectiveId,
              objective: runtime.shipState.packObjective,
              interactionStatus: runtime.shipState.lastInteractionStatus,
              boardersPausedAfterDocking: runtime.shipState.flags.boardersPausedAfterDocking,
              kills: runtime.kills,
              emitted: {
                ship: runtime.shipEmitted || 0,
                combat: runtime.combatEmitted || 0,
                character: runtime.characterEmitted || 0
              }
            }));
            """,
            SCENE_VIEWER,
            GAMEPLAY_PACK_RUNTIME,
            PACK_JS,
        )

        self.assertTrue(result["installed"]["installed"])
        self.assertEqual(result["installed"]["packId"], "pack.main-ship.bay-boarders")
        self.assertEqual(result["wrongCutscene"], [])
        self.assertEqual([entry["type"] for entry in result["bayEntry"]], [
            "show-hud-message",
            "set-objective",
            "spawn-wave",
        ])
        self.assertEqual(result["duplicate"], [])

        spawned = result["spawned"]
        self.assertTrue(spawned["cutsceneResolved"])
        self.assertTrue(spawned["boarders"]["spawned"])
        self.assertEqual(spawned["boarders"]["spawnedCount"], 3)
        self.assertEqual(spawned["boarders"]["activeCount"], 3)
        self.assertEqual(spawned["boarders"]["healthMultiplier"], 1.5)
        self.assertEqual(spawned["currentObjective"]["id"], "clear-main-bay-boarders")
        self.assertEqual(spawned["currentObjective"]["label"], "Clear the boarders from the main bay")
        self.assertEqual(result["objectiveId"], "secure-main-bay")
        self.assertEqual(result["objective"]["label"], "Main bay secured")
        self.assertFalse(result["boardersPausedAfterDocking"])

        cleared = result["cleared"]
        self.assertEqual(cleared["boarders"]["activeCount"], 0)
        self.assertEqual(cleared["boarders"]["defeatedCount"], 3)
        self.assertEqual(cleared["status"], "completed")
        self.assertTrue(cleared["completion"]["completed"])
        self.assertIn("receipt.main-ship.bay-boarders-cleared", cleared["completion"]["receiptIds"])
        self.assertEqual(result["kills"], 3)
        self.assertGreater(result["emitted"]["ship"], 0)
        self.assertGreater(result["emitted"]["combat"], 0)

    def test_scene_dispatches_pack_cutscene_resolved_on_bay_control_handoff(self) -> None:
        source = SCENE_VIEWER.read_text(encoding="utf-8")
        self.assertIn('mainShipDispatchGameplayPackSectionEvent?.("cutscene-resolved"', source)
        self.assertIn('cutsceneId: "main-ship-bay-entry"', source)
        self.assertIn('reason: force ? "cutscene-skipped-or-forced" : "cutscene-completed"', source)


if __name__ == "__main__":
    unittest.main()
