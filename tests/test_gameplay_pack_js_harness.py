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


class GameplayPackJsHarnessTests(unittest.TestCase):
    """Patch V: minimal shuttle-only pack harness and command emitter.

    This deliberately stops before scene execution. It proves the real JS pack
    can register handlers and that handlers emit validated command records.
    """

    def run_node(self, script: str) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is required for gameplay pack JS harness tests")
        result = subprocess.run(
            ["node", "-e", textwrap.dedent(script), str(PACK_RUNTIME), str(PACK_JS)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_runtime_exports_minimal_command_harness_api(self) -> None:
        result = self.run_node(
            """
            const runtime = require(process.argv[1]);
            console.log(JSON.stringify({
              hasCreateHarness: typeof runtime.createGameplayPackCommandHarness === "function",
              hasHarnessSummary: typeof runtime.gameplayPackCommandHarnessSummary === "function",
              hasDispatch: typeof runtime.dispatchGameplayPackEncounterEvent === "function",
              hasLoadHarness: typeof runtime.loadGameplayPackCommandHarnessFromSource === "function"
            }));
            """
        )

        self.assertTrue(result["hasCreateHarness"])
        self.assertTrue(result["hasHarnessSummary"])
        self.assertTrue(result["hasDispatch"])
        self.assertTrue(result["hasLoadHarness"])

    def test_pack_setup_registers_shuttle_handlers_without_running_gameplay(self) -> None:
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
        self.assertEqual(result["packId"], "pack.opening-shuttle.elite-boarders")
        self.assertTrue(result["setupRun"])
        self.assertTrue(result["handlersRegistered"])
        self.assertFalse(result["gameplayExecuted"])
        self.assertFalse(result["appliedToScene"])
        self.assertEqual(result["encounterIds"], ["opening-shuttle-ambush"])
        self.assertEqual(result["handlerCount"], 5)
        self.assertEqual(
            [(handler["event"], handler["match"]) for handler in result["handlers"]],
            [
                ("start", {}),
                ("hostiles-defeated", {"count": 2}),
                ("all-hostiles-defeated", {}),
                (
                    "destination-reached",
                    {"destinationId": "destination.solace-reach.haven-orbit"},
                ),
                ("player-defeated", {}),
            ],
        )
        self.assertEqual(result["commandCount"], 0)

    def test_on_start_emits_health_hud_and_objective_commands(self) -> None:
        result = self.run_node(
            """
            const fs = require("fs");
            const runtime = require(process.argv[1]);
            const source = fs.readFileSync(process.argv[2], "utf8");
            const harness = runtime.loadGameplayPackCommandHarnessFromSource(source);
            const commands = runtime.dispatchGameplayPackEncounterEvent(
              harness,
              "opening-shuttle-ambush",
              {type: "start"}
            );
            console.log(JSON.stringify({commands}));
            """
        )

        commands = result["commands"]
        self.assertEqual(
            [command["type"] for command in commands],
            ["set-hostile-health-multiplier", "set-objective", "show-hud-message"],
        )
        self.assertEqual(commands[0]["payload"], {"multiplier": 3})
        self.assertEqual(
            commands[1]["payload"],
            {
                "id": "survive-triple-strength-boarders",
                "label": "Survive the triple-strength boarding party",
                "required": True,
            },
        )
        self.assertIn("3x health", commands[2]["payload"]["message"])
        for command in commands:
            self.assertEqual(command["sourcePackId"], "pack.opening-shuttle.elite-boarders")
            self.assertEqual(command["encounterId"], "opening-shuttle-ambush")
            self.assertFalse(command["applied"])
            self.assertFalse(command["gameplayExecuted"])

    def test_hostile_defeat_threshold_emits_elite_spawn_wave_command(self) -> None:
        result = self.run_node(
            """
            const fs = require("fs");
            const runtime = require(process.argv[1]);
            const source = fs.readFileSync(process.argv[2], "utf8");
            const harness = runtime.loadGameplayPackCommandHarnessFromSource(source);
            const belowThreshold = runtime.dispatchGameplayPackEncounterEvent(
              harness,
              "opening-shuttle-ambush",
              {type: "hostiles-defeated", count: 1}
            );
            const atThreshold = runtime.dispatchGameplayPackEncounterEvent(
              harness,
              "opening-shuttle-ambush",
              {type: "hostiles-defeated", count: 2}
            );
            console.log(JSON.stringify({belowThreshold, atThreshold}));
            """
        )

        self.assertEqual(result["belowThreshold"], [])
        commands = result["atThreshold"]
        self.assertEqual([command["type"] for command in commands], ["spawn-wave", "set-objective"])
        self.assertEqual(commands[0]["payload"]["id"], "elite-boarding-leader")
        self.assertEqual(commands[0]["payload"]["trigger"], {"type": "after-hostile-defeats", "count": 2})
        self.assertEqual(
            commands[0]["payload"]["actors"],
            [
                {
                    "archetype": "shuttle-raider",
                    "count": 1,
                    "displayName": "Elite Boarding Leader",
                    "healthMultiplier": 3,
                }
            ],
        )
        self.assertEqual(commands[0]["payload"]["hudMessage"], "Elite Boarding Leader inbound.")
        self.assertEqual(commands[1]["payload"]["id"], "defeat-elite-boarding-leader")

    def test_completion_and_failure_handlers_emit_terminal_commands(self) -> None:
        result = self.run_node(
            """
            const fs = require("fs");
            const runtime = require(process.argv[1]);
            const source = fs.readFileSync(process.argv[2], "utf8");
            const harness = runtime.loadGameplayPackCommandHarnessFromSource(source);
            const allClear = runtime.dispatchGameplayPackEncounterEvent(
              harness,
              "opening-shuttle-ambush",
              {type: "all-hostiles-defeated"}
            );
            const wrongDestination = runtime.dispatchGameplayPackEncounterEvent(
              harness,
              "opening-shuttle-ambush",
              {type: "destination-reached", destinationId: "destination.other"}
            );
            const havenReached = runtime.dispatchGameplayPackEncounterEvent(
              harness,
              "opening-shuttle-ambush",
              {type: "destination-reached", destinationId: "destination.solace-reach.haven-orbit"}
            );
            const playerDefeated = runtime.dispatchGameplayPackEncounterEvent(
              harness,
              "opening-shuttle-ambush",
              {type: "player-defeated"}
            );
            console.log(JSON.stringify({allClear, wrongDestination, havenReached, playerDefeated}));
            """
        )

        self.assertEqual([command["type"] for command in result["allClear"]], ["set-objective"])
        self.assertEqual(result["allClear"][0]["payload"]["id"], "reach-haven-orbit")

        self.assertEqual(result["wrongDestination"], [])

        self.assertEqual([command["type"] for command in result["havenReached"]], ["complete-encounter"])
        self.assertEqual(
            result["havenReached"][0]["payload"],
            {
                "receipt": "receipt.opening-shuttle.elite-boarders-cleared",
                "message": "Elite boarders defeated. Shuttle route secured.",
            },
        )

        self.assertEqual([command["type"] for command in result["playerDefeated"]], ["fail-encounter"])
        self.assertEqual(result["playerDefeated"][0]["payload"]["reason"], "shuttle-overrun")
        self.assertIn("overwhelms", result["playerDefeated"][0]["payload"]["message"])


if __name__ == "__main__":
    unittest.main()
