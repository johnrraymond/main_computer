from __future__ import annotations

import tempfile
import threading
import urllib.request
import unittest
from pathlib import Path

from main_computer.config import MainComputerConfig
from main_computer.viewport import ViewportServer


class GameEditorFunctionalSceneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=self.root), verbose=False)
        self.server.debug_root = self.root
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def get_text(self, path: str) -> str:
        with urllib.request.urlopen(self.base_url + path, timeout=5) as response:
            return response.read().decode("utf-8")

    def test_game_surface_route_renders_scene_aware_surface(self) -> None:
        html = self.get_text("/applications/webgl")
        self.assertIn('id="webgl-demo"', html)
        self.assertIn('aria-label="Scene-aware game surface"', html)
        self.assertIn('data-scene-id="default-empty-scene"', html)
        self.assertIn("MainComputerSceneViewer", html)
        self.assertIn("main-computer-game-editor-scene-change", html)
        self.assertIn("loadWebglProject", html)
        self.assertIn("Vertex-built shuttle walk-around surface", html)
        self.assertIn("sprite-actor", html)
        self.assertIn("scene-object--sprite-actor", html)
        self.assertIn("scene-sprite-frame", html)
        self.assertIn("scene-sprite-rig-layer", html)
        self.assertIn("scene-particle-orbit", html)
        self.assertIn("spell-swirl", html)
        self.assertIn("spell-bolt", html)
        self.assertIn("impact-burst", html)
        self.assertIn("linkedParticleProjection", html)
        self.assertIn("parentId", html)
        self.assertIn("particle-emitter", html)
        self.assertIn('id="webgl-particle-density"', html)
        self.assertIn("particleMultiplier", html)
        self.assertIn("renderShuttle3dScene", html)
        self.assertIn("bindShuttle3dLookaround", html)
        self.assertIn("class Shuttle3dVertexRenderer", html)
        self.assertIn("class Shuttle3dGeometryWriter", html)
        self.assertIn("shuttle3dBoundsVertices", html)
        self.assertIn("scene-shuttle3d-canvas", html)
        self.assertIn("gl.drawArrays(gl.TRIANGLES", html)
        self.assertIn('container.dataset.shuttle3d = "webgl-vertex-mesh"', html)
        self.assertNotIn("scene-shuttle3d-wall--forward", html)
        self.assertIn("phase-4-shuttle-first-person-movement", html)
        self.assertIn("shuttle3dMovementConfig", html)
        self.assertIn("setMovementKey(code, active)", html)
        self.assertIn("updateMovement(deltaSeconds)", html)
        self.assertIn('container.addEventListener("keyup", keyUp)', html)
        self.assertIn("W/A/S/D", html)
        self.assertIn("enableClickMovement", html)
        self.assertIn("screenPointToIsoWorld", html)
        self.assertIn("scene-movement-marker", html)
        self.assertIn("W/A/S/D to walk", html)
        self.assertIn("removeEventListener(\"pointerdown\", container.__mainComputerClickMovementHandler)", html)
        self.assertIn("startSceneMovement", html)
        self.assertIn("movementSpeed(scene)", html)
        self.assertNotIn("scene-click-move", html)
        self.assertNotIn("player-capsule", html)
        self.assertNotIn("mesh-actor", html)
        self.assertNotIn("MainComputerBabylonPreview", html)
        self.assertNotIn("MeshBuilder", html)
        self.assertNotIn("SceneLoader", html)

    def test_game_editor_route_renders_project_backed_scene_builder(self) -> None:
        html = self.get_text("/applications/game-editor")
        self.assertIn('data-app="game-editor"', html)
        self.assertIn('id="game-editor-app"', html)
        self.assertIn("Project-backed scene editor is ready.", html)
        self.assertIn("function initGameEditorApp()", html)
        self.assertIn("gameEditorApi", html)
        self.assertIn("main-computer-game-editor-scene-change", html)
        self.assertIn("syncGameEditorSceneStore({reason: message})", html)
        self.assertIn("MainComputerSceneStore", html)
        self.assertIn('id="game-editor-preview"', html)
        self.assertIn('id="game-editor-webgl-canvas"', html)
        self.assertIn('id="game-editor-scene-select"', html)
        self.assertIn('aria-label="Select project scene"', html)
        self.assertIn('id="game-editor-new-scene"', html)
        self.assertIn('id="game-editor-archive-scene"', html)
        self.assertIn("function selectGameEditorScene", html)
        self.assertIn("function createGameEditorProjectScene", html)
        self.assertIn("function archiveGameEditorProjectScene", html)
        self.assertIn("project.archivedScenes", html)
        self.assertIn('url.searchParams.set("scene"', html)
        self.assertIn('id="game-editor-chat-toggle"', html)
        self.assertIn('aria-controls="game-editor-chat-popout"', html)
        self.assertIn('id="game-editor-chat-popout"', html)
        self.assertIn('id="game-editor-chat-panel"', html)
        self.assertIn('data-chat-console-embed="game-editor"', html)
        self.assertIn('data-chat-console-layout="full"', html)
        self.assertIn('data-chat-console-show-thread-rail="1"', html)
        self.assertIn('layout: "full"', html)
        self.assertIn("showThreadRail: true", html)
        self.assertIn("width: min(980px, calc(100% - 28px));", html)
        self.assertIn("Game Assistant", html)
        self.assertIn("function setGameEditorChatOpen", html)
        self.assertIn("setGameEditorChatOpen(!gameEditorState.chatOpen)", html)
        self.assertIn('nodes.chatPopout?.addEventListener("click", (event) => {', html)
        self.assertIn("event.stopPropagation();", html)
        self.assertIn("window.MainComputerGameEditorContext", html)
        self.assertIn("getEmbeddedContext: gameEditorChatContextSnapshot", html)
        self.assertIn('id: "game-editor-edit"', html)
        self.assertIn('label: "Edit this game"', html)
        self.assertIn("defaultEnabled: true", html)
        self.assertIn('pathway: "game-editor-rag-edit-smoke"', html)
        self.assertIn("/api/applications/game-editor/project/write", html)
        self.assertIn("/api/applications/game-editor/asset/upload", html)
        self.assertIn('id="game-editor-particle-density"', html)
        self.assertIn('data-vfx-preset="4"', html)


if __name__ == "__main__":
    unittest.main()
