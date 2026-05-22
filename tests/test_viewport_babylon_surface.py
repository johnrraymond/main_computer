from __future__ import annotations

import unittest

from main_computer.viewport import APPLICATIONS_INDEX_HTML


class ViewportSceneSurfaceTests(unittest.TestCase):
    def test_game_surface_tab_renders_isometric_sprite_surface(self) -> None:
        self.assertIn("Game Surface", APPLICATIONS_INDEX_HTML)
        self.assertIn("isometric sprite-and-particle scene surface", APPLICATIONS_INDEX_HTML)
        self.assertIn("Isometric surface is ready.", APPLICATIONS_INDEX_HTML)
        self.assertIn("sprite-actor", APPLICATIONS_INDEX_HTML)
        self.assertIn("scene-object--sprite-actor", APPLICATIONS_INDEX_HTML)
        self.assertIn("scene-sprite-frame", APPLICATIONS_INDEX_HTML)
        self.assertIn("particle-emitter", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="webgl-demo"', APPLICATIONS_INDEX_HTML)
        self.assertIn('aria-label="Scene-aware game surface"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-scene-id="default-empty-scene"', APPLICATIONS_INDEX_HTML)

    def test_game_surface_uses_shared_scene_viewer_not_engine_runtime(self) -> None:
        self.assertIn("function initWebgl(", APPLICATIONS_INDEX_HTML)
        self.assertIn("MainComputerSceneStore", APPLICATIONS_INDEX_HTML)
        self.assertIn("MainComputerSceneViewer", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("MainComputerBabylonPreview", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("BABYLON", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("MeshBuilder", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("SceneLoader", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("compileShader", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("createProgram", APPLICATIONS_INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
