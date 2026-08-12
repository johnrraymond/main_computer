from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENE_VIEWER = ROOT / "main_computer" / "web" / "applications" / "scripts" / "scene-viewer.js"
WEBGL_DESKTOP = ROOT / "main_computer" / "web" / "applications" / "scripts" / "webgl-desktop.js"


class MainShipBayBoardersPackVisibilityTests(unittest.TestCase):
    """Patch AD: make the live main-ship boarder pack obvious in the HUD."""

    def test_main_ship_pack_has_explicit_runtime_status_copy(self) -> None:
        desktop = WEBGL_DESKTOP.read_text(encoding="utf-8")

        self.assertIn("triggers after the bay-entry cutscene", desktop)
        self.assertIn("watch SHIP and CHARACTER AI HUD", desktop)
        self.assertIn("WEBGL_MAIN_SHIP_BAY_BOARDERS_JS_PACK_ID", desktop)

    def test_ship_hud_calls_out_armed_active_and_cleared_pack_states(self) -> None:
        scene = SCENE_VIEWER.read_text(encoding="utf-8")

        self.assertIn("PACK: ${packTitle} ARMED — waits for bay-entry cutscene", scene)
        self.assertIn("PACK: ${packTitle} — BOARDERS ${active}/${spawned} ACTIVE", scene)
        self.assertIn("PACK: ${packTitle} — BOARDERS CLEARED", scene)
        self.assertIn("mainShipGameplayPackStatus", scene)

    def test_character_ai_hud_labels_pack_boarders_separately_from_base_hostiles(self) -> None:
        scene = SCENE_VIEWER.read_text(encoding="utf-8")

        self.assertIn('const packBoarder = character.gameplayPackBoarder === true;', scene)
        self.assertIn('? "PACK BOARDER"', scene)
        self.assertIn('"main-ship-pack-boarder"', scene)


if __name__ == "__main__":
    unittest.main()
