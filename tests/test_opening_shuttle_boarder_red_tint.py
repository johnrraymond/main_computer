from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENE_VIEWER = ROOT / "main_computer" / "web" / "applications" / "scripts" / "scene-viewer.js"


class OpeningShuttleBoarderRedTintTests(unittest.TestCase):
    """Patch AF: opening-shuttle boarders should use hostile red visuals."""

    def test_opening_shuttle_boarder_dynamic_colors_are_red_not_green(self) -> None:
        scene = SCENE_VIEWER.read_text(encoding="utf-8")

        self.assertIn('const transportGlow = builder.color("#ff2d2d", true);', scene)
        self.assertIn('const alienBody = builder.color("#991b1b");', scene)
        self.assertIn('const alienArmor = builder.color("#450a0a");', scene)
        self.assertIn('const alienEyes = builder.color("#fef2f2", true);', scene)
        self.assertIn('const healthFill = builder.color("#f87171", true);', scene)

        dynamic_block = scene[
            scene.index("        buildDynamicGeometry(nowMs) {"):
            scene.index("        openingShuttleDefaultEliteWaveConfig() {")
        ]
        self.assertNotIn('const transportGlow = builder.color("#84cc16", true);', dynamic_block)
        self.assertNotIn('const alienBody = builder.color("#365314");', dynamic_block)
        self.assertNotIn('const alienArmor = builder.color("#1a2e05");', dynamic_block)
        self.assertNotIn('const healthFill = builder.color("#84cc16", true);', dynamic_block)

    def test_opening_shuttle_and_main_ship_boarders_share_red_hostile_palette(self) -> None:
        scene = SCENE_VIEWER.read_text(encoding="utf-8")

        self.assertIn('const body = builder.color(enemy ? "#991b1b" : "#1d4ed8");', scene)
        self.assertIn('const armor = builder.color(enemy ? "#450a0a" : "#0f172a");', scene)
        self.assertIn('const alienBody = builder.color("#991b1b");', scene)
        self.assertIn('const alienArmor = builder.color("#450a0a");', scene)


if __name__ == "__main__":
    unittest.main()
