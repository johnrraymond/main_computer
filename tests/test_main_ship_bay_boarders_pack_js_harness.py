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
    / "main_ship_bay_boarders"
    / "pack.js"
)


class MainShipBayBoardersPackJsHarnessTests(unittest.TestCase):
    """Patch AB: minimal section command harness for the main-ship bay pack.

    This deliberately stops before live scene execution. It proves the real JS
    pack can register section handlers and that section events emit whitelisted
    commands for the main-ship bay boarder behavior.
    """

    def run_node(self, script: str) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is required for main-ship gameplay pack JS harness tests")
        result = subprocess.run(
            ["node", "-e", textwrap.dedent(script), str(PACK_RUNTIME), str(PACK_JS)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_runtime_exports_section_dispatch_api(self) -> None:
        result = self.run_node(
            """
            const runtime = require(process.argv[1]);
            console.log(JSON.stringify({
              hasSectionDispatch: typeof runtime.dispatchGameplayPackSectionEvent === "function",
              hasEncounterDispatch: typeof runtime.dispatchGameplayPackEncounterEvent === "function",
              hasLoadHarness: typeof runtime.loadGameplayPackCommandHarnessFromSource === "function"
            }));
            """
        )

        self.assertTrue(result["hasSectionDispatch"])
        self.assertTrue(result["hasEncounterDispatch"])
        self.assertTrue(result["hasLoadHarness"])

    def test_pack_setup_registers_main_ship_section_handlers_without_scene_execution(self) -> None:
        result = self.run_node(
            """
            const fs = require("fs");
            const runtime = require(process.argv[1]);
            const source = fs.readFileSync(process.argv[2], "utf8");
            const harness = runtime.loadGameplayPackCommandHarnessFromSource(source);
            console.log(JSON.stringify(runtime.gameplayPackCommandHarnessSummary(harness)));
            """
        )

        self.assertEqual(result["kind"], "gameplay-pack-js-command-harness-summary")
        self.assertEqual(result["packId"], "pack.main-ship.bay-boarders")
        self.assertTrue(result["setupRun"])
        self.assertTrue(result["handlersRegistered"])
        self.assertFalse(result["gameplayExecuted"])
        self.assertFalse(result["appliedToScene"])
        self.assertEqual(result["encounterIds"], [])
        self.assertEqual(result["sectionIds"], ["main-ship-bay"])
        self.assertEqual(result["handlerCount"], 3)
        self.assertEqual(
            [(handler.get("sectionId"), handler["event"], handler["match"]) for handler in result["handlers"]],
            [
                ("main-ship-bay", "cutscene-resolved", {"cutsceneId": "main-ship-bay-entry"}),
                ("main-ship-bay", "all-hostiles-defeated", {}),
                ("main-ship-bay", "player-defeated", {}),
            ],
        )
        self.assertEqual(result["commandCount"], 0)

    def test_cutscene_resolved_emits_bay_boarder_spawn_commands_only_for_matching_cutscene(self) -> None:
        result = self.run_node(
            """
            const fs = require("fs");
            const runtime = require(process.argv[1]);
            const source = fs.readFileSync(process.argv[2], "utf8");
            const harness = runtime.loadGameplayPackCommandHarnessFromSource(source);
            const wrongCutscene = runtime.dispatchGameplayPackSectionEvent(
              harness,
              "main-ship-bay",
              {type: "cutscene-resolved", cutsceneId: "other-cutscene"}
            );
            const bayEntryResolved = runtime.dispatchGameplayPackSectionEvent(
              harness,
              "main-ship-bay",
              {type: "cutscene-resolved", cutsceneId: "main-ship-bay-entry"}
            );
            console.log(JSON.stringify({wrongCutscene, bayEntryResolved}));
            """
        )

        self.assertEqual(result["wrongCutscene"], [])

        commands = result["bayEntryResolved"]
        self.assertEqual(
            [command["type"] for command in commands],
            ["show-hud-message", "set-objective", "spawn-wave"],
        )
        for command in commands:
            self.assertEqual(command["sourcePackId"], "pack.main-ship.bay-boarders")
            self.assertEqual(command["sectionId"], "main-ship-bay")
            self.assertNotIn("encounterId", command)
            self.assertFalse(command["applied"])
            self.assertFalse(command["gameplayExecuted"])

        self.assertIn("boarders detected", commands[0]["payload"]["message"])
        self.assertEqual(
            commands[1]["payload"],
            {
                "id": "clear-main-bay-boarders",
                "label": "Clear the boarders from the main bay",
                "required": True,
            },
        )
        self.assertEqual(commands[2]["payload"]["id"], "main-ship-bay-boarders")
        self.assertEqual(
            commands[2]["payload"]["trigger"],
            {"type": "cutscene-resolved", "cutsceneId": "main-ship-bay-entry"},
        )
        self.assertEqual(commands[2]["payload"]["location"], "bay.shuttle")
        self.assertEqual(
            commands[2]["payload"]["actors"],
            [
                {
                    "archetype": "ship-boarder",
                    "count": 3,
                    "displayName": "Main Ship Boarder",
                    "healthMultiplier": 1.5,
                }
            ],
        )
        self.assertIn("Clear the deck", commands[2]["payload"]["hudMessage"])

    def test_all_clear_and_player_defeated_emit_section_terminal_commands(self) -> None:
        result = self.run_node(
            """
            const fs = require("fs");
            const runtime = require(process.argv[1]);
            const source = fs.readFileSync(process.argv[2], "utf8");
            const harness = runtime.loadGameplayPackCommandHarnessFromSource(source);
            const allClear = runtime.dispatchGameplayPackSectionEvent(
              harness,
              "main-ship-bay",
              {type: "all-hostiles-defeated"}
            );
            const playerDefeated = runtime.dispatchGameplayPackSectionEvent(
              harness,
              "main-ship-bay",
              {type: "player-defeated"}
            );
            console.log(JSON.stringify({allClear, playerDefeated}));
            """
        )

        self.assertEqual([command["type"] for command in result["allClear"]], ["set-objective", "complete-section"])
        self.assertEqual(
            result["allClear"][0]["payload"],
            {
                "id": "secure-main-bay",
                "label": "Main bay secured",
                "required": True,
                "status": "complete",
            },
        )
        self.assertEqual(
            result["allClear"][1]["payload"],
            {
                "receipt": "receipt.main-ship.bay-boarders-cleared",
                "message": "Main bay boarders cleared.",
            },
        )

        self.assertEqual([command["type"] for command in result["playerDefeated"]], ["fail-section"])
        self.assertEqual(result["playerDefeated"][0]["payload"]["reason"], "main-bay-overrun")
        self.assertIn("overran", result["playerDefeated"][0]["payload"]["message"])


if __name__ == "__main__":
    unittest.main()
