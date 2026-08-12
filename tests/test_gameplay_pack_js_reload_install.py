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
GAME_ROUTES = ROOT / "main_computer" / "viewport_routes_game.py"
PACK_JS = ROOT / "game_projects" / "webgl-demo" / "gameplay_packs" / "opening_shuttle_elite_boarders" / "pack.js"


class GameplayPackJsReloadInstallTests(unittest.TestCase):
    """Patch Y: selected JS shuttle pack installs on reload instead of using JSON inference."""

    def run_node(self, script: str) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is required for the JS gameplay-pack reload install tests")
        result = subprocess.run(
            [
                "node",
                "-e",
                textwrap.dedent(script),
                str(WEBGL_DESKTOP),
                str(PACK_JS),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_server_exposes_js_pack_source_endpoint(self) -> None:
        source = GAME_ROUTES.read_text(encoding="utf-8")
        self.assertIn('"/api/applications/game-editor/gameplay-pack/js-source"', source)
        self.assertIn("def _game_gameplay_pack_js_source_payload", source)
        self.assertIn('"pack.opening-shuttle.elite-boarders"', source)
        self.assertIn('"plugin.hand-authored.opening-shuttle-ambush.001"', source)
        self.assertIn('"source": source_path.read_text(encoding="utf-8")', source)
        self.assertIn('"saveStateMutated": False', source)
        self.assertIn('"projectJsonModified": False', source)

    def test_scene_surface_runtime_exposes_js_pack_installer(self) -> None:
        source = SCENE_VIEWER.read_text(encoding="utf-8")
        self.assertIn("openingShuttleInstallGameplayPackSource(source, packOptions = {}, nowMs)", source)
        self.assertIn("container.__mainComputerShuttle3dRenderer?.openingShuttleInstallGameplayPackSource", source)
        self.assertIn("this.emitCombatState?.(true);", source)

    def test_reload_selection_aliases_legacy_pack_to_js_pack_and_installs_source(self) -> None:
        result = self.run_node(
            r'''
            const fs = require("fs");
            const desktopSource = fs.readFileSync(process.argv[1], "utf8");
            const packSource = fs.readFileSync(process.argv[2], "utf8");

            const start = desktopSource.indexOf("    const webglProjectState");
            const end = desktopSource.indexOf("    function ensureWebglSystemScenarioRuntime", start);
            if (start < 0 || end < 0) throw new Error("webgl gameplay pack helper block not found");
            const helperSource = desktopSource.slice(start, end).replace(/^    /gm, "");

            let fetchedPath = "";
            let fetchedPayload = null;
            const fakeFetch = async (path, options) => {
              fetchedPath = path;
              fetchedPayload = JSON.parse(options.body || "{}");
              return {
                ok: true,
                status: 200,
                async json() {
                  return {
                    ok: true,
                    source: packSource,
                    title: "Opening Shuttle: Elite Boarders",
                    packId: "pack.opening-shuttle.elite-boarders"
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
                    activeGameplayPackIds: ["plugin.hand-authored.opening-shuttle-ambush.001"]
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
                webglNormalizeGameplayPackIds,
                webglAvailableGameplayPacks,
                webglReloadGameplayPackSelection,
                webglSelectedJsGameplayPackId,
                installSelectedWebglJsGameplayPack
              };
            `)(fakeWindow, fakeDocument, fakeFetch);

            const project = {
              metadata: {
                availableGameplayPacks: {
                  packs: [{
                    pluginId: "plugin.hand-authored.opening-shuttle-ambush.001",
                    label: "Opening Shuttle Ambush Elite Wave"
                  }]
                }
              }
            };
            let installedSource = "";
            let installedOptions = null;
            const runtime = {
              openingShuttleInstallGameplayPackSource(source, options) {
                installedSource = source;
                installedOptions = options;
                return {
                  jsGameplayPack: {
                    installed: true,
                    title: "Opening Shuttle: Elite Boarders"
                  }
                };
              }
            };

            (async () => {
              const selection = helpers.webglReloadGameplayPackSelection(project);
              const available = helpers.webglAvailableGameplayPacks(project);
              const installResult = await helpers.installSelectedWebglJsGameplayPack(runtime, project);

              console.log(JSON.stringify({
                normalizedLegacy: helpers.webglNormalizeGameplayPackIds("plugin.hand-authored.opening-shuttle-ambush.001"),
                selection,
                selectedJsPackId: helpers.webglSelectedJsGameplayPackId(selection),
                available,
                fetchedPath,
                fetchedPayload,
                installedSourceIncludesDefine: installedSource.includes("defineGameplayPack"),
                installedOptions,
                installResult
              }));
            })().catch((error) => {
              console.error(error && error.stack ? error.stack : String(error));
              process.exit(1);
            });
            '''
        )

        self.assertEqual(result["normalizedLegacy"], ["pack.opening-shuttle.elite-boarders"])
        self.assertEqual(result["selection"]["mode"], "selected")
        self.assertEqual(result["selection"]["activeGameplayPackIds"], ["pack.opening-shuttle.elite-boarders"])
        self.assertEqual(result["selectedJsPackId"], "pack.opening-shuttle.elite-boarders")
        self.assertEqual(result["available"][0]["pluginId"], "pack.opening-shuttle.elite-boarders")
        self.assertEqual(result["available"][0]["label"], "Opening Shuttle: Elite Boarders")
        self.assertEqual(result["fetchedPath"], "/api/applications/game-editor/gameplay-pack/js-source")
        self.assertEqual(result["fetchedPayload"]["pack_id"], "pack.opening-shuttle.elite-boarders")
        self.assertTrue(result["installedSourceIncludesDefine"])
        self.assertEqual(result["installedOptions"]["selectedGameplayPackId"], "pack.opening-shuttle.elite-boarders")
        self.assertTrue(result["installResult"]["installed"])


if __name__ == "__main__":
    unittest.main()
