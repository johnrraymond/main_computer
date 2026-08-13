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
OPENING_PACK = ROOT / "game_projects" / "webgl-demo" / "gameplay_packs" / "opening_shuttle_elite_boarders" / "pack.js"
MAIN_SHIP_PACK = ROOT / "game_projects" / "webgl-demo" / "gameplay_packs" / "main_ship_bay_boarders" / "pack.js"
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"


class WebglGameplayPackLobbyMultiSelectTests(unittest.TestCase):
    """Patch AG: the pre-game lobby can save and install multiple JS gameplay packs."""

    def run_node(self, script: str) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is required for WebGL gameplay pack lobby tests")
        result = subprocess.run(
            [
                "node",
                "-e",
                textwrap.dedent(script),
                str(WEBGL_DESKTOP),
                str(OPENING_PACK),
                str(MAIN_SHIP_PACK),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_lobby_markup_uses_checkboxes_and_start_game(self) -> None:
        html = APPLICATIONS_HTML.read_text(encoding="utf-8")

        self.assertIn("Gameplay Packs", html)
        self.assertIn('id="webgl-gameplay-pack-checklist"', html)
        self.assertGreaterEqual(html.count("data-webgl-gameplay-pack-checkbox"), 2)
        self.assertIn("pack.opening-shuttle.elite-boarders", html)
        self.assertIn("pack.main-ship.bay-boarders", html)
        self.assertIn("START GAME", html)

    def test_default_enabled_packs_are_selected_without_saved_local_state(self) -> None:
        result = self.run_node(
            r'''
            const fs = require("fs");
            const desktopSource = fs.readFileSync(process.argv[1], "utf8");
            const start = desktopSource.indexOf("    const webglProjectState");
            const end = desktopSource.indexOf("    function ensureWebglSystemScenarioRuntime", start);
            const helperSource = desktopSource.slice(start, end).replace(/^    /gm, "");

            const fakeWindow = {
              location: {search: ""},
              localStorage: {getItem() { return null; }, setItem() {}}
            };
            const fakeDocument = {
              getElementById() { return null; },
              querySelector() { return null; },
              createElement() { return {dataset: {}, appendChild() {}, addEventListener() {}, setAttribute() {}}; }
            };

            const helpers = Function("window", "document", "fetch", `${helperSource}
              return {
                webglReloadGameplayPackSelection,
                webglDefaultGameplayPackIds,
                webglGameplayPackSelectionStorageValue
              };
            `)(fakeWindow, fakeDocument, async () => ({ok: true, status: 200, async json() { return {ok: true}; }}));

            const defaults = helpers.webglDefaultGameplayPackIds({metadata: {}});
            const selection = helpers.webglReloadGameplayPackSelection({metadata: {}});
            const stored = helpers.webglGameplayPackSelectionStorageValue(defaults);

            console.log(JSON.stringify({defaults, selection, stored}));
            '''
        )

        self.assertEqual(
            result["defaults"],
            ["pack.opening-shuttle.elite-boarders", "pack.main-ship.bay-boarders"],
        )
        self.assertEqual(result["selection"]["source"], "default-enabled")
        self.assertEqual(result["selection"]["activeGameplayPackIds"], result["defaults"])
        self.assertEqual(result["stored"]["schema"], "game.reloadGameplayPackSelection.v2")
        self.assertEqual(result["stored"]["enabledPackIds"], result["defaults"])

    def test_v2_local_selection_installs_opening_and_main_ship_packs_together(self) -> None:
        result = self.run_node(
            r'''
            const fs = require("fs");
            const desktopSource = fs.readFileSync(process.argv[1], "utf8");
            const openingSource = fs.readFileSync(process.argv[2], "utf8");
            const mainShipSource = fs.readFileSync(process.argv[3], "utf8");
            const start = desktopSource.indexOf("    const webglProjectState");
            const end = desktopSource.indexOf("    function ensureWebglSystemScenarioRuntime", start);
            const helperSource = desktopSource.slice(start, end).replace(/^    /gm, "");

            const fetchedPayloads = [];
            const fakeFetch = async (path, options) => {
              const payload = JSON.parse(options.body || "{}");
              fetchedPayloads.push(payload);
              const isMainShip = payload.pack_id === "pack.main-ship.bay-boarders";
              return {
                ok: true,
                status: 200,
                async json() {
                  return {
                    ok: true,
                    source: isMainShip ? mainShipSource : openingSource,
                    title: isMainShip ? "Main Ship: Bay Boarders" : "Opening Shuttle: Elite Boarders",
                    packId: payload.pack_id
                  };
                }
              };
            };

            const fakeWindow = {
              location: {search: ""},
              localStorage: {
                getItem(key) {
                  if (key !== "main-computer.webgl.enabled-gameplay-packs.v2") return null;
                  return JSON.stringify({
                    schema: "game.reloadGameplayPackSelection.v2",
                    mode: "selected",
                    enabledPackIds: [
                      "pack.opening-shuttle.elite-boarders",
                      "pack.main-ship.bay-boarders"
                    ]
                  });
                },
                setItem() {}
              }
            };
            const fakeDocument = {
              getElementById() { return null; },
              querySelector() { return null; },
              createElement() { return {dataset: {}, appendChild() {}, addEventListener() {}, setAttribute() {}}; }
            };

            const helpers = Function("window", "document", "fetch", `${helperSource}
              return {
                webglReloadGameplayPackSelection,
                webglSelectedJsGameplayPackIds,
                installSelectedWebglJsGameplayPack,
                webglGameplayPackInstallStatusMessage
              };
            `)(fakeWindow, fakeDocument, fakeFetch);

            const installed = [];
            const runtime = {
              openingShuttleInstallGameplayPackSource(source, options) {
                installed.push({target: "opening", sourceIncludesEncounter: source.includes("pack.encounter("), options});
                return {
                  jsGameplayPack: {
                    installed: true,
                    title: "Opening Shuttle: Elite Boarders",
                    commandsApplied: 1
                  }
                };
              },
              mainShipInstallGameplayPackSource(source, options) {
                installed.push({target: "main-ship", sourceIncludesSection: source.includes("pack.section("), options});
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
              const installResult = await helpers.installSelectedWebglJsGameplayPack(runtime, {metadata: {}});
              const message = helpers.webglGameplayPackInstallStatusMessage(installResult);
              console.log(JSON.stringify({
                selection,
                selectedJsPackIds: helpers.webglSelectedJsGameplayPackIds(selection),
                fetchedPayloads,
                installed,
                installResult,
                message
              }));
            })().catch((error) => {
              console.error(error && error.stack ? error.stack : String(error));
              process.exit(1);
            });
            '''
        )

        self.assertEqual(result["selection"]["source"], "local-storage")
        self.assertEqual(
            result["selectedJsPackIds"],
            ["pack.opening-shuttle.elite-boarders", "pack.main-ship.bay-boarders"],
        )
        self.assertEqual(
            [payload["pack_id"] for payload in result["fetchedPayloads"]],
            ["pack.opening-shuttle.elite-boarders", "pack.main-ship.bay-boarders"],
        )
        self.assertEqual([entry["target"] for entry in result["installed"]], ["opening", "main-ship"])
        self.assertTrue(result["installed"][0]["sourceIncludesEncounter"])
        self.assertTrue(result["installed"][1]["sourceIncludesSection"])
        self.assertTrue(result["installResult"]["installed"])
        self.assertEqual(result["installResult"]["packIds"], result["selectedJsPackIds"])
        self.assertIn("Opening Shuttle: Elite Boarders", result["message"])
        self.assertIn("Main Ship: Bay Boarders", result["message"])


if __name__ == "__main__":
    unittest.main()
