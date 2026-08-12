from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = ROOT / "main_computer" / "web" / "applications" / "scripts"
PACK_RUNTIME = SCRIPT_ROOT / "gameplay-pack-runtime.js"
PACK_ROOT = (
    ROOT
    / "game_projects"
    / "webgl-demo"
    / "gameplay_packs"
    / "main_ship_bay_boarders"
)
MANIFEST = PACK_ROOT / "manifest.json"
PACK_JS = PACK_ROOT / "pack.js"
README = PACK_ROOT / "README.md"


class MainShipBayBoardersPackJsLanguageTargetTests(unittest.TestCase):
    """Patch AA: real-JS target pack for main-ship bay boarders.

    This is intentionally a target fixture only. It defines the next authoring
    surface after the working opening-shuttle pack: a section pack that triggers
    when the main-ship bay-entry cutscene resolves, whether completion is natural
    or user-skipped. Runtime support begins with the section command harness.
    """

    def pack_source(self) -> str:
        return PACK_JS.read_text(encoding="utf-8")

    def run_node(self, script: str) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is required for main-ship gameplay pack JS tests")
        result = subprocess.run(
            ["node", "-e", textwrap.dedent(script), str(PACK_RUNTIME), str(PACK_JS)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_pack_target_files_exist(self) -> None:
        self.assertTrue(MANIFEST.exists())
        self.assertTrue(PACK_JS.exists())
        self.assertTrue(README.exists())

    def test_manifest_declares_main_ship_section_target_and_safety_contract(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["schema"], "game.gameplayPackJsManifest.v1")
        self.assertEqual(manifest["kind"], "gameplay-pack-js")
        self.assertEqual(manifest["id"], "pack.main-ship.bay-boarders")
        self.assertEqual(manifest["title"], "Main Ship: Bay Boarders")
        self.assertEqual(manifest["entry"], "pack.js")
        self.assertEqual(manifest["authoringModel"]["language"], "javascript")
        self.assertEqual(manifest["authoringModel"]["sdk"], "defineGameplayPack")
        self.assertEqual(manifest["authoringModel"]["runtimeStatus"], "command-harness-supported")

        self.assertEqual(manifest["targets"]["scene"], "mother-ship")
        self.assertEqual(manifest["targets"]["section"], "main-ship-bay")
        self.assertEqual(manifest["targets"]["template"], "ship-interior-boarder-response")
        self.assertEqual(manifest["targets"]["cutsceneId"], "main-ship-bay-entry")
        self.assertEqual(manifest["targets"]["locations"], ["bay.shuttle"])

        self.assertIn("onCutsceneResolved", manifest["contracts"]["registersSectionHandlers"])
        self.assertIn("onAllHostilesDefeated", manifest["contracts"]["registersSectionHandlers"])
        self.assertIn("spawn-wave", manifest["contracts"]["emitsCommands"])
        self.assertIn("complete-section", manifest["contracts"]["emitsCommands"])

        permissions = manifest["permissions"]
        self.assertIs(permissions["spawnActors"], True)
        self.assertIs(permissions["showHudMessages"], True)
        self.assertIs(permissions["setObjectives"], True)
        self.assertIs(permissions["grantReceipts"], True)
        self.assertIs(permissions["completeSection"], True)
        self.assertIs(permissions["writeSaveState"], False)
        self.assertIs(permissions["accessNetwork"], False)
        self.assertIs(permissions["accessFilesystem"], False)
        self.assertIs(permissions["accessDom"], False)
        self.assertIs(permissions["rawRendererAccess"], False)
        self.assertIs(permissions["rawRuntimeStateMutation"], False)

    def test_pack_js_is_valid_ecmascript_module_syntax(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required to syntax-check the main-ship pack module")

        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir) / "main-ship-pack-under-test.mjs"
            module_path.write_text(self.pack_source(), encoding="utf-8")
            result = subprocess.run(
                ["node", "--check", str(module_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_pack_js_uses_section_authoring_surface_and_cutscene_resolved_trigger(self) -> None:
        source = self.pack_source()

        self.assertIn("export default defineGameplayPack({", source)
        self.assertIn('const PACK_ID = "pack.main-ship.bay-boarders";', source)
        self.assertIn('const SECTION_ID = "main-ship-bay";', source)
        self.assertIn('const CUTSCENE_ID = "main-ship-bay-entry";', source)
        self.assertRegex(source, r"setup\s*\(\s*pack\s*\)\s*\{")
        self.assertIn("pack.section(SECTION_ID, (section) =>", source)
        self.assertIn("section.onCutsceneResolved(CUTSCENE_ID, () =>", source)
        self.assertIn("section.onAllHostilesDefeated(() =>", source)
        self.assertIn("section.onPlayerDefeated(() =>", source)

    def test_pack_js_authors_main_bay_boarder_behavior_explicitly(self) -> None:
        source = self.pack_source()

        self.assertIn('const BOARDER_ARCHETYPE = "ship-boarder";', source)
        self.assertIn("const BOARDER_COUNT = 3;", source)
        self.assertIn("const BOARDER_HEALTH_MULTIPLIER = 1.5;", source)
        self.assertIn("Bay Boarders pack active: boarders detected in the main bay.", source)
        self.assertIn('id: "clear-main-bay-boarders"', source)
        self.assertIn('label: "Clear the boarders from the main bay"', source)
        self.assertIn('id: "main-ship-bay-boarders"', source)
        self.assertIn('type: "cutscene-resolved"', source)
        self.assertIn("cutsceneId: CUTSCENE_ID", source)
        self.assertIn("location: BAY_LOCATION", source)
        self.assertIn('displayName: "Main Ship Boarder"', source)
        self.assertIn("healthMultiplier: BOARDER_HEALTH_MULTIPLIER", source)
        self.assertIn("Hostile boarders are breaching the bay. Clear the deck.", source)
        self.assertIn('receipt.main-ship.bay-boarders-cleared', source)
        self.assertIn("Main bay boarders cleared.", source)

    def test_pack_js_loads_as_definition_but_runtime_does_not_support_sections_yet(self) -> None:
        result = self.run_node(
            """
            const fs = require("fs");
            const runtime = require(process.argv[1]);
            const source = fs.readFileSync(process.argv[2], "utf8");
            const validation = runtime.validateGameplayPackJsSource(source);
            const definition = runtime.loadGameplayPackDefinitionFromSource(source);
            const summary = runtime.gameplayPackDefinitionSummary(definition);
            console.log(JSON.stringify({
              validation,
              summary
            }));
            """
        )

        self.assertTrue(result["validation"]["ok"])
        self.assertEqual(result["summary"]["id"], "pack.main-ship.bay-boarders")
        self.assertEqual(result["summary"]["title"], "Main Ship: Bay Boarders")
        self.assertTrue(result["summary"]["setupAvailable"])
        self.assertTrue(result["summary"]["loadOnly"])
        self.assertFalse(result["summary"]["handlersRegistered"])
        self.assertFalse(result["summary"]["gameplayExecuted"])

    def test_pack_js_does_not_use_forbidden_browser_or_dynamic_code_surfaces(self) -> None:
        source = self.pack_source()
        without_string_literals = re.sub(
            r'(?:"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`)',
            '""',
            source,
        )
        forbidden_tokens = [
            "window",
            "document",
            "fetch",
            "XMLHttpRequest",
            "localStorage",
            "sessionStorage",
            "eval",
            "Function",
        ]

        for token in forbidden_tokens:
            self.assertNotRegex(
                without_string_literals,
                rf"\b{re.escape(token)}\b",
                f"pack.js should not reference forbidden global {token}",
            )

        self.assertNotIn("import(", without_string_literals)
        self.assertNotRegex(without_string_literals, r"(^|\n)\s*import\s+")

    def test_readme_explains_cutscene_resolution_and_runtime_boundary(self) -> None:
        readme = README.read_text(encoding="utf-8")

        self.assertIn("main-ship-bay-entry", readme)
        self.assertIn("normal cutscene completion and user skip", readme)
        self.assertIn("pack.section", readme)
        self.assertIn("section.onCutsceneResolved", readme)
        self.assertIn("not wired into live gameplay yet", readme)
        self.assertIn("section command harness", readme)


if __name__ == "__main__":
    unittest.main()
