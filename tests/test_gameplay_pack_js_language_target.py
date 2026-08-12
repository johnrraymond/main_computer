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
PACK_ROOT = (
    ROOT
    / "game_projects"
    / "webgl-demo"
    / "gameplay_packs"
    / "opening_shuttle_elite_boarders"
)
MANIFEST = PACK_ROOT / "manifest.json"
PACK_JS = PACK_ROOT / "pack.js"
README = PACK_ROOT / "README.md"


class GameplayPackJsLanguageTargetTests(unittest.TestCase):
    """Patch T/Z: real-JS opening shuttle gameplay pack authoring surface.

    The pack started as a target fixture and now runs through the narrow YAGNI
    command harness for the opening shuttle encounter.
    """

    def pack_source(self) -> str:
        return PACK_JS.read_text(encoding="utf-8")

    def test_pack_target_files_exist(self) -> None:
        self.assertTrue(MANIFEST.exists())
        self.assertTrue(PACK_JS.exists())
        self.assertTrue(README.exists())

    def test_manifest_declares_real_js_pack_entry_and_sandbox_contract(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["schema"], "game.gameplayPackJsManifest.v1")
        self.assertEqual(manifest["kind"], "gameplay-pack-js")
        self.assertEqual(manifest["id"], "pack.opening-shuttle.elite-boarders")
        self.assertEqual(manifest["entry"], "pack.js")
        self.assertEqual(manifest["authoringModel"]["language"], "javascript")
        self.assertEqual(manifest["authoringModel"]["sdk"], "defineGameplayPack")
        self.assertEqual(manifest["authoringModel"]["runtimeStatus"], "yagni-runtime-active")

        self.assertEqual(manifest["targets"]["encounter"], "opening-shuttle-ambush")
        self.assertEqual(manifest["targets"]["template"], "shuttle-ambush")
        self.assertEqual(manifest["targets"]["destinationId"], "destination.solace-reach.haven-orbit")

        permissions = manifest["permissions"]
        self.assertIs(permissions["spawnActors"], True)
        self.assertIs(permissions["modifyCombatStats"], True)
        self.assertIs(permissions["showHudMessages"], True)
        self.assertIs(permissions["grantReceipts"], True)
        self.assertIs(permissions["completeEncounter"], True)
        self.assertIs(permissions["writeSaveState"], False)
        self.assertIs(permissions["accessNetwork"], False)
        self.assertIs(permissions["accessFilesystem"], False)
        self.assertIs(permissions["accessDom"], False)
        self.assertIs(permissions["rawRendererAccess"], False)
        self.assertIs(permissions["rawRuntimeStateMutation"], False)

        self.assertIn("set-hostile-health-multiplier", manifest["contracts"]["emitsCommands"])
        self.assertIn("spawn-wave", manifest["contracts"]["emitsCommands"])
        self.assertIn("complete-encounter", manifest["contracts"]["emitsCommands"])

    def test_pack_js_is_real_ecmascript_module_syntax(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required to syntax-check the target pack module")

        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir) / "pack-under-test.mjs"
            module_path.write_text(PACK_JS.read_text(encoding="utf-8"), encoding="utf-8")
            result = subprocess.run(
                ["node", "--check", str(module_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_pack_js_uses_the_desired_authoring_sdk_shape(self) -> None:
        source = self.pack_source()

        self.assertIn("export default defineGameplayPack({", source)
        self.assertIn('id: PACK_ID', source)
        self.assertIn('title: "Opening Shuttle: Elite Boarders"', source)
        self.assertRegex(source, r"setup\s*\(\s*pack\s*\)\s*\{")
        self.assertIn('pack.encounter("opening-shuttle-ambush"', source)
        self.assertIn("encounter.onStart(() =>", source)
        self.assertIn(
            "encounter.onHostilesDefeated(BASE_RAIDER_DEFEATS_BEFORE_LEADER, () =>",
            source,
        )
        self.assertIn("encounter.onAllHostilesDefeated(() =>", source)
        self.assertIn("encounter.onDestinationReached(HAVEN_ORBIT_DESTINATION, () =>", source)
        self.assertIn("encounter.onPlayerDefeated(() =>", source)

    def test_pack_js_authors_the_shuttle_gameplay_delta_explicitly(self) -> None:
        source = self.pack_source()

        self.assertIn("const HEALTH_MULTIPLIER = 3;", source)
        self.assertIn("const BASE_RAIDER_DEFEATS_BEFORE_LEADER = 2;", source)
        self.assertIn("encounter.setHostileHealthMultiplier(HEALTH_MULTIPLIER);", source)
        self.assertIn("Elite Boarders pack active: shuttle raiders have 3x health.", source)
        self.assertIn('id: "elite-boarding-leader"', source)
        self.assertIn('type: "after-hostile-defeats"', source)
        self.assertIn("count: BASE_RAIDER_DEFEATS_BEFORE_LEADER", source)
        self.assertIn('displayName: "Elite Boarding Leader"', source)
        self.assertIn("healthMultiplier: HEALTH_MULTIPLIER", source)
        self.assertIn("Elite Boarding Leader inbound.", source)
        self.assertIn('receipt.opening-shuttle.elite-boarders-cleared', source)
        self.assertIn("Elite boarders defeated. Shuttle route secured.", source)

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

    def test_readme_marks_pack_js_as_the_working_yagni_runtime_surface(self) -> None:
        readme = README.read_text(encoding="utf-8")

        self.assertIn("working real-JavaScript gameplay-pack authoring surface", readme)
        self.assertIn("current YAGNI runtime loads this pack", readme)
        self.assertIn("defineGameplayPack", readme)
        self.assertIn("command harness", readme)
        self.assertIn("shuttle scene adapter", readme)


if __name__ == "__main__":
    unittest.main()
