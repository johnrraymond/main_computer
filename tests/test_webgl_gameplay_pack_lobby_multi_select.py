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
EXPERIMENTAL_PACK = ROOT / "game_projects" / "webgl-demo" / "gameplay_packs" / "experimental" / "large-random-shuttle-boarder" / "pack.js"
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
WEBGL_APP_HTML = ROOT / "main_computer" / "web" / "applications" / "apps" / "webgl.html"
GAME_EDITOR_CSS = ROOT / "main_computer" / "web" / "applications" / "styles" / "game-editor.css"


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
                str(EXPERIMENTAL_PACK),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_lobby_markup_uses_checkboxes_and_start_new_game(self) -> None:
        html = APPLICATIONS_HTML.read_text(encoding="utf-8")

        self.assertIn("Gameplay Packs", html)
        self.assertIn('id="webgl-gameplay-pack-checklist"', html)
        self.assertGreaterEqual(html.count("data-webgl-gameplay-pack-checkbox"), 2)
        self.assertIn("pack.opening-shuttle.elite-boarders", html)
        self.assertIn("pack.main-ship.bay-boarders", html)
        self.assertIn("START NEW GAME", html)
        self.assertNotIn(">START GAME</button>", html)
        self.assertIn('id="webgl-autosave-status"', html)
        self.assertIn("Autosave: none yet.", html)

    def test_lobby_mounts_into_main_scene_view_before_start(self) -> None:
        app_html = WEBGL_APP_HTML.read_text(encoding="utf-8")
        desktop = WEBGL_DESKTOP.read_text(encoding="utf-8")
        css = GAME_EDITOR_CSS.read_text(encoding="utf-8")

        self.assertIn('id="webgl-demo"', app_html)
        self.assertIn("webglMountGameplayPackLobbyInMainView", desktop)
        self.assertIn("webglSetGameplayPackLobbyVisible(!webglProjectState.gameStarted)", desktop)
        self.assertIn("webglSetGameplayPackLobbyVisible(false)", desktop)
        self.assertIn("webgl-gameplay-pack-lobby-stage", desktop)
        self.assertIn(".webgl-gameplay-pack-lobby-stage", css)
        self.assertIn('setAttribute?.("data-webgl-gameplay-pack-main-view", "true")', desktop)
        self.assertIn("START NEW GAME failed", desktop)
        self.assertIn("WEBGL_AUTOSAVE_KEY", desktop)
        self.assertIn("main-computer.webgl.autosave.v1", desktop)
        self.assertIn("webglUpdateAutosaveStatus", desktop)
        self.assertIn(".webgl-autosave-status", css)
        self.assertIn(".webgl-autosave-toast", css)
        self.assertIn("webglShowAutosaveToast", desktop)

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

    def test_start_game_writes_local_autosave_shell_with_selected_packs(self) -> None:
        result = self.run_node(
            r'''
            const fs = require("fs");
            const desktopSource = fs.readFileSync(process.argv[1], "utf8");
            const start = desktopSource.indexOf("    const webglProjectState");
            const end = desktopSource.indexOf("    function ensureWebglSystemScenarioRuntime", start);
            const helperSource = desktopSource.slice(start, end).replace(/^    /gm, "");

            const store = {};
            const selectedCheckboxes = [
              {
                checked: true,
                value: "pack.opening-shuttle.elite-boarders",
                dataset: {gameplayPackId: "pack.opening-shuttle.elite-boarders"}
              },
              {
                checked: true,
                value: "pack.experimental.large-random-shuttle-boarder",
                dataset: {gameplayPackId: "pack.experimental.large-random-shuttle-boarder"}
              }
            ];
            const startButton = {dataset: {}, disabled: false, addEventListener() {}};
            const statusNode = {dataset: {}, textContent: ""};
            const autosaveNode = {dataset: {}, textContent: ""};
            const panel = {dataset: {}, querySelector() { return null; }};
            const checklist = {
              dataset: {},
              querySelectorAll(selector) {
                if (selector === "[data-webgl-gameplay-pack-checkbox]") return selectedCheckboxes;
                return [];
              }
            };

            const fakeWindow = {
              location: {search: ""},
              setTimeout(callback) { callback(); return 1; },
              clearTimeout() {},
              localStorage: {
                getItem(key) { return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null; },
                setItem(key, value) { store[key] = String(value); }
              }
            };
            const createdNodes = {};
            const canvasHost = {
              children: [],
              appendChild(node) {
                this.children.push(node);
                if (node.id) createdNodes[node.id] = node;
              }
            };
            const sceneSurface = {parentElement: canvasHost};
            const fakeDocument = {
              getElementById(id) {
                if (id === "webgl-gameplay-pack-selector") return panel;
                if (id === "webgl-gameplay-pack-checklist") return checklist;
                if (id === "webgl-gameplay-pack-start" || id === "webgl-gameplay-pack-apply") return startButton;
                if (id === "webgl-gameplay-pack-status") return statusNode;
                if (id === "webgl-autosave-status") return autosaveNode;
                if (id === "webgl-demo") return sceneSurface;
                if (createdNodes[id]) return createdNodes[id];
                return null;
              },
              querySelector() { return null; },
              createElement() {
                return {
                  id: "",
                  className: "",
                  dataset: {},
                  hidden: false,
                  textContent: "",
                  appendChild() {},
                  addEventListener() {},
                  setAttribute() {}
                };
              }
            };

            const helpers = Function("window", "document", "fetch", `${helperSource}
              const initWebgl = async () => ({ok: true});
              return {
                startWebglGameFromGameplayPackLobby,
                webglReadAutosave,
                webglAutosaveStatusMessage,
                WEBGL_AUTOSAVE_KEY
              };
            `)(fakeWindow, fakeDocument, async () => ({ok: true, status: 200, async json() { return {ok: true}; }}));

            (async () => {
              const savedSelection = await helpers.startWebglGameFromGameplayPackLobby();
              const rawAutosave = store["main-computer.webgl.autosave.v1"];
              const autosave = helpers.webglReadAutosave();
              console.log(JSON.stringify({
                savedSelection,
                rawAutosave: JSON.parse(rawAutosave),
                autosave,
                autosaveStatusText: autosaveNode.textContent,
                autosaveStatusDataset: autosaveNode.dataset,
                autosaveToastText: createdNodes["webgl-autosave-toast"]?.textContent || "",
                autosaveToastHidden: Boolean(createdNodes["webgl-autosave-toast"]?.hidden),
                storageKeys: Object.keys(store).sort()
              }));
            })().catch((error) => {
              console.error(error && error.stack ? error.stack : String(error));
              process.exit(1);
            });
            '''
        )

        self.assertIn("main-computer.webgl.autosave.v1", result["storageKeys"])
        self.assertEqual(result["rawAutosave"]["schema"], "game.webglAutosave.v1")
        self.assertEqual(result["rawAutosave"]["checkpoint"]["id"], "new-game-start")
        self.assertEqual(result["rawAutosave"]["checkpoint"]["label"], "New Game Start")
        self.assertEqual(
            result["rawAutosave"]["enabledPackIds"],
            ["pack.opening-shuttle.elite-boarders", "pack.experimental.large-random-shuttle-boarder"],
        )
        self.assertEqual(result["autosave"]["checkpoint"]["label"], "New Game Start")
        self.assertIn("Autosave saved: New Game Start", result["autosaveStatusText"])
        self.assertIn("Autosave saved: New Game Start", result["autosaveToastText"])
        self.assertTrue(result["autosaveToastHidden"])
        self.assertEqual(result["autosaveStatusDataset"]["hasAutosave"], "true")
        self.assertEqual(result["autosaveStatusDataset"]["mode"], "saved")
        self.assertEqual(result["autosaveStatusDataset"]["checkpointId"], "new-game-start")
        self.assertEqual(result["autosaveStatusDataset"]["packCount"], "2")

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


    def test_experimental_js_packs_from_project_metadata_are_listed_and_loadable(self) -> None:
        result = self.run_node(
            r"""
            const fs = require("fs");
            const desktopSource = fs.readFileSync(process.argv[1], "utf8");
            const openingSource = fs.readFileSync(process.argv[2], "utf8");
            const experimentalSource = fs.readFileSync(process.argv[4], "utf8");
            const start = desktopSource.indexOf("    const webglProjectState");
            const end = desktopSource.indexOf("    function ensureWebglSystemScenarioRuntime", start);
            const helperSource = desktopSource.slice(start, end).replace(/^    /gm, "");

            const experimentalPackId = "pack.experimental.large-random-shuttle-boarder";
            const project = {
              metadata: {
                availableGameplayPacks: {
                  packs: [
                    {
                      id: experimentalPackId,
                      pluginId: experimentalPackId,
                      label: "Large Shuttle Boarder Ambush",
                      description: "Experimental generated pack.",
                      sourceKind: "js-gameplay-pack",
                      packageRoot: "gameplay_packs/experimental/large-random-shuttle-boarder",
                      entry: "pack.js",
                      status: "experimental",
                      loadable: true,
                      defaultEnabled: false,
                      experimental: true,
                      generated: true,
                      hiddenFromLobby: true,
                      encounterIds: ["opening-shuttle-ambush"]
                    }
                  ]
                }
              }
            };

            const fetchedPayloads = [];
            const fakeFetch = async (path, options) => {
              const payload = JSON.parse(options.body || "{}");
              fetchedPayloads.push(payload);
              return {
                ok: true,
                status: 200,
                async json() {
                  return {
                    ok: true,
                    source: payload.pack_id === experimentalPackId ? experimentalSource : openingSource,
                    title: payload.pack_id === experimentalPackId ? "Large Shuttle Boarder Ambush" : "Opening Shuttle: Elite Boarders",
                    packId: payload.pack_id,
                    sourceKind: "js-gameplay-pack",
                    targets: {encounter: "opening-shuttle-ambush"},
                    experimental: payload.pack_id === experimentalPackId,
                    generated: payload.pack_id === experimentalPackId
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
                    enabledPackIds: [experimentalPackId]
                  });
                },
                setItem() {}
              }
            };
            const fakeDocument = {
              getElementById() { return null; },
              querySelector() { return null; },
              createElement(tag) {
                return {
                  tag,
                  dataset: {},
                  children: [],
                  textContent: "",
                  className: "",
                  appendChild(child) { this.children.push(child); },
                  addEventListener() {},
                  setAttribute() {}
                };
              }
            };

            const helpers = Function("window", "document", "fetch", `${helperSource}
              return {
                webglAvailableGameplayPacks,
                webglDefaultGameplayPackIds,
                webglSelectedJsGameplayPackIds,
                webglBuildGameplayPackCheckbox,
                installSelectedWebglJsGameplayPack
              };
            `)(fakeWindow, fakeDocument, fakeFetch);

            const packs = helpers.webglAvailableGameplayPacks(project);
            const defaults = helpers.webglDefaultGameplayPackIds(project);
            const selection = {activeGameplayPackIds: [experimentalPackId]};
            const selectedJs = helpers.webglSelectedJsGameplayPackIds(selection, project);
            const experimentalPack = packs.find((pack) => pack.pluginId === experimentalPackId);
            const checkbox = helpers.webglBuildGameplayPackCheckbox(experimentalPack, [experimentalPackId]);

            const installed = [];
            const runtime = {
              openingShuttleInstallGameplayPackSource(source, options) {
                installed.push({sourceIncludesEncounter: source.includes("pack.encounter("), options});
                return {
                  jsGameplayPack: {
                    installed: true,
                    title: "Large Shuttle Boarder Ambush",
                    commandsApplied: 2
                  }
                };
              }
            };

            (async () => {
              const installResult = await helpers.installSelectedWebglJsGameplayPack(runtime, project);
              console.log(JSON.stringify({
                packs,
                defaults,
                selectedJs,
                checkbox: {
                  checked: checkbox.children[0].checked,
                  meta: checkbox.children[1].children[1].textContent
                },
                fetchedPayloads,
                installed,
                installResult
              }));
            })().catch((error) => {
              console.error(error && error.stack ? error.stack : String(error));
              process.exit(1);
            });
            """
        )

        experimental = next(
            pack
            for pack in result["packs"]
            if pack["pluginId"] == "pack.experimental.large-random-shuttle-boarder"
        )
        self.assertTrue(experimental["experimental"])
        self.assertTrue(experimental["generated"])
        self.assertTrue(experimental["hiddenFromLobby"])
        self.assertEqual(experimental["status"], "js-pack")
        self.assertNotIn("pack.experimental.large-random-shuttle-boarder", result["defaults"])
        self.assertEqual(result["selectedJs"], ["pack.experimental.large-random-shuttle-boarder"])
        self.assertTrue(result["checkbox"]["checked"])
        self.assertIn("experimental", result["checkbox"]["meta"])
        self.assertEqual(result["fetchedPayloads"][0]["pack_id"], "pack.experimental.large-random-shuttle-boarder")
        self.assertTrue(result["installed"][0]["sourceIncludesEncounter"])
        self.assertTrue(result["installResult"]["installed"])
        self.assertEqual(result["installResult"]["packIds"], ["pack.experimental.large-random-shuttle-boarder"])



if __name__ == "__main__":
    unittest.main()
