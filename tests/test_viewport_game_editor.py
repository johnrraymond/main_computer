from __future__ import annotations

import base64
import copy
import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.viewport import APPLICATIONS_INDEX_HTML, ViewportServer, _application_route_target


ROOT = Path(__file__).resolve().parents[1]
WEBGL_PROJECT = json.loads((ROOT / "game_projects" / "webgl-demo" / "project.json").read_text(encoding="utf-8"))
STARTER_PROJECT = json.loads((ROOT / "game_projects" / "starter-game" / "project.json").read_text(encoding="utf-8"))


class ViewportGameEditorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        project_root = self.root / "game_projects" / "starter-game"
        for folder in ("assets", "scripts", "data", "builds"):
            (project_root / folder).mkdir(parents=True, exist_ok=True)
        (project_root / "project.json").write_text(json.dumps(STARTER_PROJECT, indent=2), encoding="utf-8")
        webgl_root = self.root / "game_projects" / "webgl-demo"
        for folder in ("assets", "scripts", "data", "builds"):
            (webgl_root / folder).mkdir(parents=True, exist_ok=True)
        (webgl_root / "project.json").write_text(json.dumps(WEBGL_PROJECT, indent=2), encoding="utf-8")
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

    def post(self, path: str, payload: dict | None = None) -> dict:
        request = Request(
            self.base_url + path,
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_error(self, path: str, payload: dict | None = None) -> HTTPError:
        with self.assertRaises(HTTPError) as raised:
            self.post(path, payload)
        return raised.exception

    def read_project(self) -> dict:
        return self.post("/api/applications/game-editor/project/read", {"project_id": "webgl-demo"})

    def test_projects_list_and_read_return_webgl_demo_default_with_hash(self) -> None:
        data = self.post("/api/applications/game-editor/projects")
        self.assertTrue(data["ok"])
        self.assertEqual(data["projects"][0]["id"], "webgl-demo")
        self.assertTrue(data["projects"][0]["default"])
        self.assertIn("webgl-demo", [project["id"] for project in data["projects"]])
        self.assertIn("starter-game", [project["id"] for project in data["projects"]])

        project = self.read_project()
        self.assertEqual(project["project"]["name"], "WebGL Demo")
        self.assertNotIn("engine", project["project"])
        self.assertEqual(project["project"]["activeSceneId"], "default-empty-scene")
        self.assertEqual(project["project"]["scenes"][0]["objects"][0]["type"], "sprite-actor")
        self.assertTrue(project["project"]["scenes"][0]["objects"][0]["props"]["spawn"])
        self.assertEqual(project["project"]["scenes"][0]["metadata"]["projection"], "isometric")
        self.assertEqual(project["project"]["scenes"][0]["metadata"]["rolloutPhase"], "phase-4-finale-showcase")
        self.assertEqual(project["project"]["scenes"][0]["metadata"]["characterModel"], "sprite-particle-rig")
        self.assertFalse(project["project"]["scenes"][0]["metadata"]["meshActorsEnabled"])
        self.assertEqual(project["project"]["scenes"][0]["metadata"]["controls"]["movement"], "left-click")
        self.assertEqual(project["project"]["scenes"][0]["metadata"]["controls"]["moveSpeed"], 3.15)
        self.assertTrue(project["project"]["scenes"][0]["metadata"]["controls"]["clickToMove"])
        self.assertFalse(project["project"]["scenes"][0]["metadata"]["controls"]["keyboardMovement"])
        self.assertEqual(project["project"]["scenes"][0]["metadata"]["movementBounds"]["maxX"], 10)
        self.assertTrue(project["project"]["scenes"][0]["metadata"]["parentedParticles"])
        self.assertTrue(project["project"]["scenes"][0]["metadata"]["linkedSpellProjectiles"])
        self.assertTrue(project["project"]["scenes"][0]["metadata"]["targetedParticles"])
        self.assertEqual(project["project"]["scenes"][0]["objects"][1]["parentId"], "hero-sprite")
        self.assertEqual(project["project"]["scenes"][0]["objects"][1]["props"]["motion"], "spell-swirl")
        self.assertEqual(project["project"]["scenes"][0]["objects"][3]["props"]["motion"], "nova-ring")
        self.assertEqual(project["project"]["scenes"][0]["objects"][4]["props"]["motion"], "shockwave-ring")
        self.assertEqual(project["project"]["scenes"][0]["objects"][9]["props"]["motion"], "spell-bolt")
        self.assertEqual(project["project"]["scenes"][0]["objects"][10]["props"]["motion"], "spell-bolt")
        self.assertEqual(project["project"]["scenes"][0]["objects"][11]["props"]["motion"], "impact-burst")
        self.assertEqual(project["project"]["scenes"][0]["objects"][12]["props"]["motion"], "impact-burst")
        self.assertEqual(project["project"]["scenes"][0]["objects"][13]["props"]["motion"], "starfall")
        self.assertEqual(project["project"]["scenes"][0]["metadata"]["choreography"]["title"], "Arcstorm Finale")
        self.assertEqual(project["project"]["scenes"][0]["metadata"]["vfx"]["particleMultiplier"], 2)
        self.assertEqual(project["project"]["scenes"][0]["metadata"]["vfx"]["effectMultiplier"], 2)
        self.assertTrue(project["project"]["scenes"][0]["metadata"]["uiParticleControls"])
        self.assertNotIn("mesh-actor", json.dumps(project["project"]))
        self.assertNotIn("player-capsule", json.dumps(project["project"]))
        self.assertNotIn("hero-mesh", json.dumps(project["project"]))
        self.assertRegex(project["content_hash"], r"^[0-9a-f]{64}$")

    def test_project_write_saves_and_rejects_stale_hash(self) -> None:
        current = self.read_project()
        project = copy.deepcopy(current["project"])
        project["name"] = "Saved Game"
        saved = self.post("/api/applications/game-editor/project/write", {"project_id": "webgl-demo", "expected_content_hash": current["content_hash"], "project": project})
        self.assertTrue(saved["ok"])
        self.assertEqual(self.read_project()["project"]["name"], "Saved Game")

        project["name"] = "Stale Save"
        self.assertEqual(self.post_error("/api/applications/game-editor/project/write", {"project_id": "webgl-demo", "expected_content_hash": current["content_hash"], "project": project}).code, 409)

    def test_project_create_duplicate_export_and_import(self) -> None:
        created = self.post("/api/applications/game-editor/project/create", {"project_id": "new-game", "name": "New Game"})
        self.assertTrue(created["ok"])
        for folder in ("assets", "scripts", "data", "builds"):
            self.assertTrue((self.root / "game_projects" / "new-game" / folder).exists())

        duplicate = self.post("/api/applications/game-editor/project/duplicate", {"project_id": "new-game", "new_project_id": "new-game-copy"})
        self.assertTrue(duplicate["ok"])

        exported = self.post("/api/applications/game-editor/project/export", {"project_id": "new-game"})
        exported_bytes = base64.b64decode(exported["content_base64"])
        with zipfile.ZipFile(io.BytesIO(exported_bytes)) as archive:
            self.assertIn("project.json", archive.namelist())

        imported = self.post("/api/applications/game-editor/project/import", {"project_id": "imported-game", "content_base64": exported["content_base64"]})
        self.assertTrue(imported["ok"])

        bad = io.BytesIO()
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("../escape.txt", "bad")
        self.assertEqual(self.post_error("/api/applications/game-editor/project/import", {"project_id": "bad-import", "content_base64": base64.b64encode(bad.getvalue()).decode("ascii")}).code, 400)

    def test_file_routes_read_write_delete_move_and_path_safety(self) -> None:
        written = self.post("/api/applications/game-editor/file/write", {"project_id": "starter-game", "path": "data/dialogue.txt", "content": "hello"})
        self.assertTrue(written["ok"])
        read = self.post("/api/applications/game-editor/file/read", {"project_id": "starter-game", "path": "data/dialogue.txt"})
        self.assertEqual(read["content"], "hello")
        encoded = self.post("/api/applications/game-editor/file/read", {"project_id": "starter-game", "path": "data/dialogue.txt", "mode": "base64"})
        self.assertEqual(base64.b64decode(encoded["content_base64"]), b"hello")
        self.assertEqual(self.post_error("/api/applications/game-editor/file/write", {"project_id": "starter-game", "path": "data/dialogue.txt", "content": "stale", "expected_content_hash": "0" * 64}).code, 409)
        moved = self.post("/api/applications/game-editor/file/move", {"project_id": "starter-game", "path": "data/dialogue.txt", "new_path": "data/dialogue-renamed.txt", "expected_content_hash": written["content_hash"]})
        self.assertTrue(moved["ok"])
        deleted = self.post("/api/applications/game-editor/file/delete", {"project_id": "starter-game", "path": "data/dialogue-renamed.txt", "expected_content_hash": moved["content_hash"]})
        self.assertTrue(deleted["deleted"])
        self.assertEqual(self.post_error("/api/applications/game-editor/file/read", {"project_id": "starter-game", "path": "../escape.txt"}).code, 400)
        self.assertEqual(self.post_error("/api/applications/game-editor/file/read", {"project_id": "starter-game", "path": str(self.root / "escape.txt")}).code, 400)

    def test_asset_routes_accept_any_extension_and_serve_nosniff(self) -> None:
        uploads = {
            "image.png": b"\x89PNG\r\n\x1a\n",
            "logic.js": b"console.log('ok')",
            "shape.svg": b"<svg></svg>",
            "tool.exe": b"MZ",
            "pack.zip": b"PK\x03\x04",
            "blob": b"\x00\x01",
            "thing.custom": b"custom",
            "models/crate.glb": b"glTF\x02\x00\x00\x00",
        }
        hashes: dict[str, str] = {}
        for name, data in uploads.items():
            result = self.post("/api/applications/game-editor/asset/upload", {"project_id": "starter-game", "path": name, "content_base64": base64.b64encode(data).decode("ascii")})
            self.assertTrue(result["ok"])
            hashes[name] = result["asset"]["content_hash"]
        listed = self.post("/api/applications/game-editor/assets", {"project_id": "starter-game"})
        self.assertEqual({asset["path"] for asset in listed["assets"]}, set(uploads))
        model_asset = next(asset for asset in listed["assets"] if asset["path"] == "models/crate.glb")
        self.assertEqual(model_asset["kind"], "model")
        self.assertFalse(model_asset["preview_supported"])
        self.assertIn("/api/applications/game-editor/asset/read", model_asset["url"])
        self.assertEqual(self.post_error("/api/applications/game-editor/asset/upload", {"project_id": "starter-game", "path": "../bad.png", "content": "bad"}).code, 400)
        self.assertEqual(self.post_error("/api/applications/game-editor/asset/upload", {"project_id": "starter-game", "path": str(self.root / "bad.png"), "content": "bad"}).code, 400)
        self.assertEqual(self.post_error("/api/applications/game-editor/asset/upload", {"project_id": "starter-game", "path": "huge.bin", "content_base64": base64.b64encode(b"1234").decode("ascii"), "max_bytes": 1}).code, 400)

        with urlopen(f"{self.base_url}/api/applications/game-editor/asset/read?project_id=starter-game&path=image.png", timeout=5) as response:
            self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
            self.assertEqual(response.read(), uploads["image.png"])

        stale = self.post_error("/api/applications/game-editor/asset/delete", {"project_id": "starter-game", "path": "logic.js", "expected_content_hash": "0" * 64})
        self.assertEqual(stale.code, 409)
        moved = self.post("/api/applications/game-editor/asset/move", {"project_id": "starter-game", "path": "logic.js", "new_path": "renamed.js", "expected_content_hash": hashes["logic.js"]})
        self.assertTrue(moved["ok"])
        deleted = self.post("/api/applications/game-editor/asset/delete", {"project_id": "starter-game", "path": "renamed.js", "expected_content_hash": moved["content_hash"]})
        self.assertTrue(deleted["deleted"])

    def test_frontend_static_hooks_and_routes(self) -> None:
        self.assertIn('data-app="game-editor"', APPLICATIONS_INDEX_HTML)
        self.assertIn("Game Editor", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="game-editor-app"', APPLICATIONS_INDEX_HTML)
        self.assertIn("Project-backed scene editor is ready.", APPLICATIONS_INDEX_HTML)
        self.assertIn("function initGameEditorApp()", APPLICATIONS_INDEX_HTML)
        self.assertIn("function disposeGameEditorSurface()", APPLICATIONS_INDEX_HTML)
        self.assertIn("gameEditorApi", APPLICATIONS_INDEX_HTML)
        self.assertIn("MainComputerSceneStore", APPLICATIONS_INDEX_HTML)
        self.assertIn("MainComputerSceneViewer", APPLICATIONS_INDEX_HTML)
        self.assertIn("main-computer-game-editor-scene-change", APPLICATIONS_INDEX_HTML)
        self.assertIn("main-computer-scene-change", APPLICATIONS_INDEX_HTML)
        self.assertIn("loadWebglProject", APPLICATIONS_INDEX_HTML)
        self.assertIn("selectedObjectId", APPLICATIONS_INDEX_HTML)
        self.assertIn("Game Surface mirror", APPLICATIONS_INDEX_HTML)
        self.assertIn("sprite-actor", APPLICATIONS_INDEX_HTML)
        self.assertIn("scene-object--sprite-actor", APPLICATIONS_INDEX_HTML)
        self.assertIn("scene-sprite-frame", APPLICATIONS_INDEX_HTML)
        self.assertIn("scene-sprite-rig-layer", APPLICATIONS_INDEX_HTML)
        self.assertIn("scene-particle-orbit", APPLICATIONS_INDEX_HTML)
        self.assertIn("spell-swirl", APPLICATIONS_INDEX_HTML)
        self.assertIn("spell-bolt", APPLICATIONS_INDEX_HTML)
        self.assertIn("impact-burst", APPLICATIONS_INDEX_HTML)
        self.assertIn("nova-ring", APPLICATIONS_INDEX_HTML)
        self.assertIn("starfall", APPLICATIONS_INDEX_HTML)
        self.assertIn("scene-choreography-overlay", APPLICATIONS_INDEX_HTML)
        self.assertIn("linkedParticleProjection", APPLICATIONS_INDEX_HTML)
        self.assertIn("scene-particle-bolt-run", APPLICATIONS_INDEX_HTML)
        self.assertIn("removeEventListener(\"pointerdown\", container.__mainComputerClickMovementHandler)", APPLICATIONS_INDEX_HTML)
        self.assertIn("startSceneMovement", APPLICATIONS_INDEX_HTML)
        self.assertIn("movementSpeed(scene)", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("scene-click-move", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("player-capsule", APPLICATIONS_INDEX_HTML)
        self.assertIn("parentId", APPLICATIONS_INDEX_HTML)
        self.assertIn("particle-emitter", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="game-editor-particle-density"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="webgl-particle-density"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="game-editor-preview"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="game-editor-webgl-canvas"', APPLICATIONS_INDEX_HTML)
        self.assertNotIn('id="game-editor-chat-toggle"', APPLICATIONS_INDEX_HTML)
        self.assertNotIn('game-editor-chat-popout', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="game-editor-chat-panel"', APPLICATIONS_INDEX_HTML)
        self.assertIn('class="game-editor-chat-panel"', APPLICATIONS_INDEX_HTML)
        self.assertIn('aria-label="Game Assistant embedded Chat Console"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-chat-console-embed="game-editor"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-chat-console-target-kind="game-project"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-chat-console-layout="full"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-chat-console-show-thread-rail="1"', APPLICATIONS_INDEX_HTML)
        self.assertIn("grid-template-columns: minmax(220px, 280px) minmax(420px, 1fr) minmax(360px, 460px);", APPLICATIONS_INDEX_HTML)
        self.assertIn("function gameEditorChatContextSnapshot", APPLICATIONS_INDEX_HTML)
        self.assertIn("window.MainComputerGameEditorContext", APPLICATIONS_INDEX_HTML)
        self.assertIn("function mountGameEditorChat", APPLICATIONS_INDEX_HTML)
        self.assertIn("function refreshGameEditorChatMount", APPLICATIONS_INDEX_HTML)
        self.assertIn("return mountGameEditorChat();", APPLICATIONS_INDEX_HTML)
        self.assertIn('layout: "full"', APPLICATIONS_INDEX_HTML)
        self.assertIn("showThreadRail: true", APPLICATIONS_INDEX_HTML)
        self.assertIn("const mount = api.mountEmbedded || window.chatConsoleMountEmbedded;", APPLICATIONS_INDEX_HTML)
        self.assertIn("getEmbeddedContext: gameEditorChatContextSnapshot", APPLICATIONS_INDEX_HTML)
        self.assertIn("game_builder_phase: \"ui-context-only\"", APPLICATIONS_INDEX_HTML)
        self.assertIn("mutation_allowed: false", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/game-editor/project/write", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/game-editor/asset/upload", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("MainComputerBabylonPreview", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("BABYLON", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("MeshBuilder", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("SceneLoader", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("GizmoManager", APPLICATIONS_INDEX_HTML)
        self.assertEqual(_application_route_target("/applications/webgl"), "webgl")
        self.assertEqual(_application_route_target("/applications/game-editor"), "game-editor")
        self.assertEqual(_application_route_target("/applications/game-editor/"), "game-editor")
        self.assertEqual(_application_route_target("/apps/game-editor"), "game-editor")
        self.assertEqual(_application_route_target("/app/game-editor"), "game-editor")
        self.assertEqual(_application_route_target("/applications/layout-builder"), "game-editor")
        self.assertEqual(_application_route_target("/apps/layout-builder"), "game-editor")
        self.assertEqual(_application_route_target("/app/layout-builder"), "game-editor")
        self.assertIsNone(_application_route_target("/applications/game-editor/unknown"))


if __name__ == "__main__":
    unittest.main()
