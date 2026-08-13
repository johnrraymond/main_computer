from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACK_DIR = ROOT / "game_projects" / "webgl-demo" / "gameplay_packs" / "experimental" / "large-random-shuttle-boarder"


class ExperimentalLargeRandomShuttleBoarderPackTests(unittest.TestCase):
    def test_reviewed_plan_is_additive_only(self) -> None:
        plan = json.loads((PACK_DIR / "plan.json").read_text())

        self.assertEqual(plan["scenario"]["id"], "opening-shuttle-ambush")
        self.assertEqual(plan["scenario"]["kind"], "encounter")
        self.assertEqual(len(plan["steps"]), 1)
        self.assertEqual(plan["steps"][0]["event"], "onStart")
        self.assertEqual(plan["steps"][0]["commands"], ["spawnWave", "showHudMessage"])
        self.assertNotIn("setHostileHealthMultiplier", json.dumps(plan))

    def test_pack_spawns_one_random_large_boarder_without_global_multiplier(self) -> None:
        source = (PACK_DIR / "pack.js").read_text()

        self.assertIn('pack.encounter("opening-shuttle-ambush"', source)
        self.assertIn("encounter.onStart", source)
        self.assertIn("chooseShuttleBoarderLocation", source)
        self.assertIn("Math.random", source)
        self.assertIn("location,", source)
        self.assertIn('displayName: "Large Shuttle Boarder"', source)
        self.assertIn("healthMultiplier: 2.5", source)
        self.assertNotIn("setHostileHealthMultiplier", source)
        self.assertNotIn("getMetadata", source)

    def test_pack_is_javascript_parseable(self) -> None:
        result = subprocess.run(
            ["node", "--check", str(PACK_DIR / "pack.js")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
