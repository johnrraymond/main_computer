from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = ROOT / "main_computer" / "web" / "applications" / "scripts"
PACK_RUNTIME = SCRIPT_ROOT / "gameplay-pack-runtime.js"
PACK_JS = (
    ROOT
    / "game_projects"
    / "webgl-demo"
    / "gameplay_packs"
    / "opening_shuttle_elite_boarders"
    / "pack.js"
)


class GameplayPackJsLoaderTests(unittest.TestCase):
    """Patch U: minimal real-JS gameplay pack loader.

    This is intentionally load-only. It validates the pack source and reads the
    definition object without registering handlers or executing gameplay.
    """

    def run_node(self, script: str) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is required for gameplay pack JS loader tests")
        result = subprocess.run(
            ["node", "-e", textwrap.dedent(script), str(PACK_RUNTIME), str(PACK_JS)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_loader_module_exports_minimal_pack_runtime_api(self) -> None:
        result = self.run_node(
            """
            const runtime = require(process.argv[1]);
            console.log(JSON.stringify({
              schema: runtime.GAMEPLAY_PACK_JS_RUNTIME_SCHEMA,
              hasDefine: typeof runtime.defineGameplayPack === "function",
              hasValidate: typeof runtime.validateGameplayPackJsSource === "function",
              hasLoad: typeof runtime.loadGameplayPackDefinitionFromSource === "function",
              hasSummary: typeof runtime.gameplayPackDefinitionSummary === "function",
              forbiddenGlobals: runtime.GAMEPLAY_PACK_JS_FORBIDDEN_GLOBALS
            }));
            """
        )

        self.assertEqual(result["schema"], "game.gameplayPackJsRuntime.v1")
        self.assertTrue(result["hasDefine"])
        self.assertTrue(result["hasValidate"])
        self.assertTrue(result["hasLoad"])
        self.assertTrue(result["hasSummary"])
        self.assertIn("window", result["forbiddenGlobals"])
        self.assertIn("fetch", result["forbiddenGlobals"])
        self.assertIn("Function", result["forbiddenGlobals"])

    def test_current_opening_shuttle_pack_loads_as_definition_without_running_setup(self) -> None:
        result = self.run_node(
            """
            const fs = require("fs");
            const runtime = require(process.argv[1]);
            const source = fs.readFileSync(process.argv[2], "utf8");
            globalThis.__gameplayPackSetupRan = false;
            const validation = runtime.validateGameplayPackJsSource(source);
            const definition = runtime.loadGameplayPackDefinitionFromSource(source);
            const summary = runtime.gameplayPackDefinitionSummary(definition);
            console.log(JSON.stringify({
              validation,
              summary,
              setupType: typeof definition.setup,
              setupRan: globalThis.__gameplayPackSetupRan
            }));
            """
        )

        self.assertTrue(result["validation"]["ok"], result["validation"])
        self.assertEqual(result["summary"]["id"], "pack.opening-shuttle.elite-boarders")
        self.assertEqual(result["summary"]["title"], "Opening Shuttle: Elite Boarders")
        self.assertEqual(result["summary"]["version"], "0.1.0")
        self.assertEqual(result["summary"]["targets"]["encounter"], "opening-shuttle-ambush")
        self.assertEqual(result["summary"]["targets"]["template"], "shuttle-ambush")
        self.assertTrue(result["summary"]["permissions"]["spawnActors"])
        self.assertTrue(result["summary"]["permissions"]["modifyCombatStats"])
        self.assertFalse(result["summary"]["permissions"]["writeSaveState"])
        self.assertEqual(result["setupType"], "function")
        self.assertTrue(result["summary"]["setupAvailable"])
        self.assertTrue(result["summary"]["loadOnly"])
        self.assertFalse(result["summary"]["handlersRegistered"])
        self.assertFalse(result["summary"]["gameplayExecuted"])
        self.assertFalse(result["setupRan"])

    def test_loader_does_not_execute_setup_body_while_loading(self) -> None:
        result = self.run_node(
            """
            const runtime = require(process.argv[1]);
            const source = `
              export default defineGameplayPack({
                id: "pack.test.setup-not-run",
                title: "Setup Not Run",
                version: "0.1.0",
                setup(pack) {
                  throw new Error("setup should not execute during load");
                }
              });
            `;
            const definition = runtime.loadGameplayPackDefinitionFromSource(source);
            console.log(JSON.stringify({
              id: definition.id,
              setupType: typeof definition.setup,
              setupAvailable: definition.setupAvailable,
              gameplayExecuted: definition.gameplayExecuted
            }));
            """
        )

        self.assertEqual(result["id"], "pack.test.setup-not-run")
        self.assertEqual(result["setupType"], "function")
        self.assertTrue(result["setupAvailable"])
        self.assertFalse(result["gameplayExecuted"])

    def test_forbidden_globals_are_rejected_before_loading(self) -> None:
        result = self.run_node(
            """
            const runtime = require(process.argv[1]);
            const source = `
              export default defineGameplayPack({
                id: "pack.bad.window",
                title: "Bad Window Pack",
                version: "0.1.0",
                setup(pack) {
                  window.alert("nope");
                  fetch("/nope");
                }
              });
            `;
            const validation = runtime.validateGameplayPackJsSource(source);
            let loadError = "";
            try {
              runtime.loadGameplayPackDefinitionFromSource(source);
            } catch (error) {
              loadError = String(error.message || error);
            }
            console.log(JSON.stringify({
              ok: validation.ok,
              forbiddenGlobals: validation.forbiddenGlobals,
              loadError
            }));
            """
        )

        self.assertFalse(result["ok"])
        self.assertIn("window", result["forbiddenGlobals"])
        self.assertIn("fetch", result["forbiddenGlobals"])
        self.assertIn("forbidden global window", result["loadError"])
        self.assertIn("forbidden global fetch", result["loadError"])

    def test_forbidden_global_words_in_strings_and_comments_do_not_block_loading(self) -> None:
        result = self.run_node(
            """
            const runtime = require(process.argv[1]);
            const source = `
              // This comment mentions window and fetch as forbidden examples.
              export default defineGameplayPack({
                id: "pack.safe.words-only",
                title: "Words Only",
                version: "0.1.0",
                description: "The words window fetch document are only text.",
                setup(pack) {}
              });
            `;
            const validation = runtime.validateGameplayPackJsSource(source);
            const definition = runtime.loadGameplayPackDefinitionFromSource(source);
            console.log(JSON.stringify({
              ok: validation.ok,
              problems: validation.problems,
              id: definition.id
            }));
            """
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["problems"], [])
        self.assertEqual(result["id"], "pack.safe.words-only")


if __name__ == "__main__":
    unittest.main()
