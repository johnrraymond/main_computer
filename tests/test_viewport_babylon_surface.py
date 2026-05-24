from __future__ import annotations

import unittest

from main_computer.viewport import APPLICATIONS_INDEX_HTML


class ViewportSceneSurfaceTests(unittest.TestCase):
    def test_game_surface_tab_renders_isometric_sprite_surface(self) -> None:
        self.assertIn("Game Surface", APPLICATIONS_INDEX_HTML)
        self.assertIn("Arcstorm finale sprite/particle showcase", APPLICATIONS_INDEX_HTML)
        self.assertIn("Arcstorm finale surface is ready.", APPLICATIONS_INDEX_HTML)
        self.assertIn("sprite-actor", APPLICATIONS_INDEX_HTML)
        self.assertIn("scene-object--sprite-actor", APPLICATIONS_INDEX_HTML)
        self.assertIn("scene-sprite-frame", APPLICATIONS_INDEX_HTML)
        self.assertIn("scene-sprite-rig-layer", APPLICATIONS_INDEX_HTML)
        self.assertIn("scene-particle-orbit", APPLICATIONS_INDEX_HTML)
        self.assertIn("spell-swirl", APPLICATIONS_INDEX_HTML)
        self.assertIn("spell-bolt", APPLICATIONS_INDEX_HTML)
        self.assertIn("impact-burst", APPLICATIONS_INDEX_HTML)
        self.assertIn("linkedParticleProjection", APPLICATIONS_INDEX_HTML)
        self.assertIn("scene-particle-bolt-run", APPLICATIONS_INDEX_HTML)
        self.assertIn("parentId", APPLICATIONS_INDEX_HTML)
        self.assertIn("particle-emitter", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="webgl-particle-density"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="game-editor-particle-density"', APPLICATIONS_INDEX_HTML)
        self.assertIn("sceneVfxSettings", APPLICATIONS_INDEX_HTML)
        self.assertIn("particleMultiplier", APPLICATIONS_INDEX_HTML)
        self.assertIn("phase-4-finale-showcase", APPLICATIONS_INDEX_HTML)
        self.assertIn("enableClickMovement", APPLICATIONS_INDEX_HTML)
        self.assertIn("screenPointToIsoWorld", APPLICATIONS_INDEX_HTML)
        self.assertIn("scene-movement-marker", APPLICATIONS_INDEX_HTML)
        self.assertIn("Left-click ground to move", APPLICATIONS_INDEX_HTML)
        self.assertIn("removeEventListener(\"pointerdown\", container.__mainComputerClickMovementHandler)", APPLICATIONS_INDEX_HTML)
        self.assertIn("startSceneMovement", APPLICATIONS_INDEX_HTML)
        self.assertIn("movementSpeed(scene)", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("scene-click-move", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("player-capsule", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("mesh-actor", APPLICATIONS_INDEX_HTML)
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
