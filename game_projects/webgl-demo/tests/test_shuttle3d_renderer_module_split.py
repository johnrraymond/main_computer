from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
GAME_ROOT = ROOT / "game_projects" / "webgl-demo"
SCRIPT_ROOT = GAME_ROOT / "web" / "scripts"


class Shuttle3DRendererModuleSplitTests(unittest.TestCase):
    """Renderer extraction remains modular after the legacy viewscreen module is removed."""

    def test_room_geometry_registry_and_new_viewscreen_renderer_load_before_scene_viewer(self) -> None:
        game = json.loads((GAME_ROOT / "game.json").read_text(encoding="utf-8"))
        scripts = game["web"]["bundles"]["runtime-before-routing"]
        registry = "web/scripts/shuttle3d-renderer-modules.js"
        room_geometry = "web/scripts/shuttle3d-render-room-geometry.js"
        presentation = "web/scripts/bridge-viewscreen-presentation.js"
        renderer = "web/scripts/bridge-viewscreen-renderer.js"
        legacy = "web/scripts/shuttle3d-render-viewscreens.js"
        scene_viewer = "web/scripts/scene-viewer.js"

        for script in (presentation, renderer, registry, room_geometry, scene_viewer):
            self.assertIn(script, scripts)
        self.assertNotIn(legacy, scripts)
        self.assertLess(scripts.index(presentation), scripts.index(renderer))
        self.assertLess(scripts.index(renderer), scripts.index(scene_viewer))
        self.assertLess(scripts.index(registry), scripts.index(room_geometry))
        self.assertLess(scripts.index(room_geometry), scripts.index(scene_viewer))

    def test_legacy_viewscreen_module_is_physically_deleted(self) -> None:
        self.assertFalse((SCRIPT_ROOT / "shuttle3d-render-viewscreens.js").exists())

    def test_scene_viewer_has_no_legacy_viewscreen_delegating_seams(self) -> None:
        scene_viewer = (SCRIPT_ROOT / "scene-viewer.js").read_text(encoding="utf-8")
        room_geometry = (SCRIPT_ROOT / "shuttle3d-render-room-geometry.js").read_text(encoding="utf-8")
        renderer = (SCRIPT_ROOT / "bridge-viewscreen-renderer.js").read_text(encoding="utf-8")

        self.assertIn('"roomGeometry"', scene_viewer)
        self.assertIn("MainComputerShuttle3DRendererModules?.call", scene_viewer)
        self.assertIn("Patch O renders room shell/wall/opening geometry from rooms[].geometry", room_geometry)
        self.assertIn("MainComputerBridgeViewscreenRenderer", scene_viewer)
        self.assertIn('case "planet"', renderer)
        self.assertIn('case "warp-transit"', renderer)
        self.assertIn('case "astrometric"', renderer)
        self.assertIn('case "idle"', renderer)

        for legacy in (
            "appendMotherShipViewscreenDisplay",
            "appendSystemPlanetDisplay",
            "appendAstrometricSystemDisplay",
            "appendWarpTransitDisplay",
            "appendEnemyShipTacticalDisplay",
        ):
            self.assertNotIn(legacy, scene_viewer)
        self.assertNotIn('"viewscreens"', scene_viewer)


if __name__ == "__main__":
    unittest.main()
