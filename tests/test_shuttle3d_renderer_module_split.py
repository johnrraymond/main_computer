from __future__ import annotations

import unittest
from pathlib import Path

from main_computer.viewport import APPLICATIONS_INDEX_HTML


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "main_computer" / "web"
SCRIPT_ROOT = WEB_ROOT / "applications" / "scripts"


class Shuttle3DRendererModuleSplitTests(unittest.TestCase):
    """Patch S: renderer passes can move out of scene-viewer.js without changing load order."""

    def test_renderer_modules_are_included_before_scene_viewer(self) -> None:
        html = (WEB_ROOT / "applications.html").read_text(encoding="utf-8")
        registry = "<!-- @include applications/scripts/shuttle3d-renderer-modules.js -->"
        room_geometry = "<!-- @include applications/scripts/shuttle3d-render-room-geometry.js -->"
        viewscreens = "<!-- @include applications/scripts/shuttle3d-render-viewscreens.js -->"
        scene_viewer = "<!-- @include applications/scripts/scene-viewer.js -->"

        for include in (registry, room_geometry, viewscreens, scene_viewer):
            self.assertIn(include, html)

        self.assertLess(html.index(registry), html.index(room_geometry))
        self.assertLess(html.index(room_geometry), html.index(viewscreens))
        self.assertLess(html.index(viewscreens), html.index(scene_viewer))

    def test_served_application_bundle_contains_renderer_modules(self) -> None:
        self.assertIn("Patch S: browser-safe renderer module registry", APPLICATIONS_INDEX_HTML)
        self.assertIn("MainComputerShuttle3DRendererModules", APPLICATIONS_INDEX_HTML)
        self.assertIn('modules.register("roomGeometry"', APPLICATIONS_INDEX_HTML)
        self.assertIn('modules.register("viewscreens"', APPLICATIONS_INDEX_HTML)
        self.assertIn("appendMotherShipRoomGeometry(builder, nowMs = 0)", APPLICATIONS_INDEX_HTML)
        self.assertIn("appendMotherShipViewscreenDisplay(builder, prop, nowMs = 0)", APPLICATIONS_INDEX_HTML)
        self.assertIn("appendEnemyShipTacticalDisplay(builder, prop, nowMs = 0)", APPLICATIONS_INDEX_HTML)

    def test_scene_viewer_delegates_extracted_render_passes(self) -> None:
        scene_viewer = (SCRIPT_ROOT / "scene-viewer.js").read_text(encoding="utf-8")
        room_geometry = (SCRIPT_ROOT / "shuttle3d-render-room-geometry.js").read_text(encoding="utf-8")
        viewscreens = (SCRIPT_ROOT / "shuttle3d-render-viewscreens.js").read_text(encoding="utf-8")

        self.assertIn('"roomGeometry"', scene_viewer)
        self.assertIn('"viewscreens"', scene_viewer)
        self.assertIn("MainComputerShuttle3DRendererModules?.call", scene_viewer)
        self.assertIn("Patch O renders room shell/wall/opening geometry from rooms[].geometry", room_geometry)
        self.assertIn("Patch P renders content-defined viewscreens/displays from prop.display metadata", viewscreens)

        # The heavy implementation should live in the module file; scene-viewer keeps only the delegating seam.
        self.assertNotIn("const doorStateColor = (doorId) =>", scene_viewer)
        self.assertIn("const doorStateColor = (doorId) =>", room_geometry)
        self.assertNotIn("const tacticalGrid = builder.color", scene_viewer)
        self.assertIn("const tacticalGrid = builder.color", viewscreens)


if __name__ == "__main__":
    unittest.main()
