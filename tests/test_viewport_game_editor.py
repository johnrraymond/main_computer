from __future__ import annotations

import base64
import copy
import io
import json
import shutil
import hashlib
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
        for project_id, project_payload in (("starter-game", STARTER_PROJECT), ("webgl-demo", WEBGL_PROJECT)):
            source_root = ROOT / "game_projects" / project_id
            project_root = self.root / "game_projects" / project_id
            for folder in ("assets", "scripts", "data", "builds"):
                source_folder = source_root / folder
                target_folder = project_root / folder
                if project_id == "webgl-demo" and folder == "assets" and source_folder.exists():
                    shutil.copytree(source_folder, target_folder, dirs_exist_ok=True)
                else:
                    target_folder.mkdir(parents=True, exist_ok=True)
            (project_root / "project.json").write_text(json.dumps(project_payload, indent=2), encoding="utf-8")
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
        self.assertEqual(project["project"]["scenes"][0]["metadata"]["projection"], "shuttle-3d")
        scene = project["project"]["scenes"][0]
        objects = {obj["id"]: obj for obj in scene["objects"]}
        self.assertEqual(scene["metadata"]["rolloutPhase"], "phase-2-shuttle-3d-lookaround")
        self.assertIn("mother ship", scene["metadata"]["setting"])
        self.assertEqual(scene["metadata"]["characterModel"], "first-person-cadet-presence")
        self.assertFalse(scene["metadata"]["meshActorsEnabled"])
        self.assertEqual(scene["metadata"]["controls"]["mode"], "lookaround")
        self.assertTrue(scene["metadata"]["controls"]["pointerDrag"])
        self.assertEqual(scene["metadata"]["controls"]["keyboard"], "arrow-keys")
        self.assertEqual(scene["metadata"]["controls"]["movement"], "stationary-inside-shuttle")
        self.assertTrue(scene["metadata"]["lookAroundEnabled"])
        self.assertTrue(scene["metadata"]["parentedParticles"])
        self.assertFalse(scene["metadata"]["linkedSpellProjectiles"])
        self.assertTrue(scene["metadata"]["linkedSensorPulses"])
        self.assertTrue(scene["metadata"]["targetedParticles"])
        self.assertEqual(objects["hero-spell-aura"]["parentId"], "hero-sprite")
        self.assertEqual(objects["hero-spell-aura"]["props"]["motion"], "rune-ring")
        self.assertEqual(objects["console-status-glow"]["parentId"], "nav-console")
        self.assertEqual(objects["warp-core-hum"]["props"]["motion"], "nova-ring")
        self.assertEqual(objects["hero-arc-bolt"]["props"]["motion"], "spell-bolt")
        self.assertEqual(objects["hero-arc-bolt"]["props"]["targetId"], "forward-viewer")
        self.assertEqual(objects["viewer-starfield"]["props"]["motion"], "starfall")
        self.assertEqual(objects["viewport-starfield"]["type"], "shuttle3d-starfield")
        self.assertTrue(objects["viewport-starfield"]["props"]["visibleThroughViewport"])
        self.assertEqual(objects["mother-ship"]["type"], "shuttle3d-mother-ship")
        self.assertTrue(objects["mother-ship"]["props"]["visibleThroughViewport"])
        self.assertEqual(objects["lookaround-camera"]["type"], "shuttle3d-camera")
        self.assertEqual(objects["shuttle-floor"]["type"], "shuttle-deck")
        self.assertEqual(objects["forward-viewer"]["type"], "shuttle-window")
        self.assertEqual(scene["metadata"]["choreography"]["title"], "Shuttle Look-Around Boot")
        self.assertEqual(scene["metadata"]["vfx"]["particleMultiplier"], 2)
        self.assertEqual(scene["metadata"]["vfx"]["effectMultiplier"], 1.5)
        self.assertTrue(scene["metadata"]["uiParticleControls"])
        self.assertNotIn("mesh-actor", json.dumps(project["project"]))
        self.assertNotIn("player-capsule", json.dumps(project["project"]))
        self.assertNotIn("hero-mesh", json.dumps(project["project"]))
        self.assertRegex(project["content_hash"], r"^[0-9a-f]{64}$")

    def test_project_read_prelinks_prebuilt_gpu_forge_atlas_for_game_surface(self) -> None:
        project = self.read_project()
        scene = project["project"]["scenes"][0]
        effect = next(obj for obj in scene["objects"] if obj["id"] == "hero-arc-bolt")
        atlas = effect["props"]["gpuForgeAtlas"]

        self.assertEqual(atlas["path"], "gpu-forge/default-empty-scene-hero-arc-bolt-storm-lash-prebuilt.svg")
        self.assertEqual(atlas["metadataPath"], "gpu-forge/default-empty-scene-hero-arc-bolt-storm-lash-prebuilt.json")
        self.assertEqual(atlas["backend"], "prebuilt-game-gpu-forge-atlas")
        self.assertEqual(atlas["playback"], "storm-lash")
        self.assertTrue(atlas["prebuilt"])
        self.assertEqual(effect["props"]["gpuForgePlayback"], "storm-lash")
        self.assertEqual(project["project"]["metadata"]["gpuForgePrebuilt"]["effect_id"], "hero-arc-bolt")

        assets = self.post("/api/applications/game-editor/assets", {"project_id": "webgl-demo"})
        paths = {asset["path"]: asset for asset in assets["assets"]}
        self.assertIn(atlas["path"], paths)
        self.assertEqual(paths[atlas["path"]]["kind"], "image")
        self.assertIn(atlas["metadataPath"], paths)
        self.assertEqual(paths[atlas["metadataPath"]]["kind"], "text")

        with urlopen(f"{self.base_url}/api/applications/game-editor/asset/read?project_id=webgl-demo&path={atlas['path']}", timeout=5) as response:
            self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
            self.assertIn(b"<svg", response.read())

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

    def test_gpu_forge_status_and_effect_atlas_bake_write_project_assets(self) -> None:
        status = self.post("/api/applications/game-editor/gpu-forge/status", {"project_id": "webgl-demo"})
        self.assertTrue(status["ok"])
        self.assertEqual(status["mode"], "game-gpu-forge")
        self.assertFalse(status["capabilities"]["screen_repaint"])
        self.assertTrue(status["capabilities"]["effect_atlas_bake"])
        self.assertTrue(status["capabilities"]["browser_atlas_playback"])
        self.assertFalse(status["renderer"]["live_stream_required"])
        self.assertEqual(status["endpoints"]["bake_effect_atlas"], "/api/applications/game-editor/gpu-forge/bake-effect-atlas")

        data = self.post(
            "/api/applications/game-editor/gpu-forge/bake-effect-atlas",
            {
                "project_id": "webgl-demo",
                "scene_id": "default-empty-scene",
                "effect_id": "hero-arc-bolt",
            },
        )

        self.assertTrue(data["ok"])
        self.assertEqual(data["mode"], "game-gpu-forge-effect-atlas")
        self.assertIn(data["metadata"]["backend"], {"local-procedural-svg-fallback", "container-procedural-svg"})
        self.assertEqual(data["metadata"]["future_backend"], "game-renderer-container-gpu")
        self.assertEqual(data["metadata"]["effect_id"], "hero-arc-bolt")
        self.assertEqual(data["metadata"]["effect_motion"], "spell-bolt")
        self.assertFalse(data["metadata"]["container_contract"]["live_stream_required"])
        self.assertEqual(data["metadata"]["frame_count"], 12)
        self.assertEqual(data["metadata"]["browser_binding"]["target"], "particle-emitter.props.gpuForgeAtlas")
        self.assertEqual(data["object_patch"]["gpuForgeAtlas"]["path"], data["metadata"]["atlas_path"])
        self.assertEqual(data["object_patch"]["gpuForgeAtlas"]["playback"], "storm-lash")

        atlas_asset = data["atlas_asset"]
        metadata_asset = data["metadata_asset"]
        self.assertEqual(atlas_asset["kind"], "image")
        self.assertEqual(metadata_asset["kind"], "text")
        self.assertTrue(atlas_asset["path"].startswith("gpu-forge/"))
        self.assertTrue(metadata_asset["path"].startswith("gpu-forge/"))
        self.assertTrue((self.root / "game_projects" / "webgl-demo" / "assets" / atlas_asset["path"]).is_file())
        self.assertTrue((self.root / "game_projects" / "webgl-demo" / "assets" / metadata_asset["path"]).is_file())

        with urlopen(f"{self.base_url}/api/applications/game-editor/asset/read?project_id=webgl-demo&path={atlas_asset['path']}", timeout=5) as response:
            self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
            self.assertIn(b"<svg", response.read())


    def test_gpu_forge_frontend_links_baked_atlas_for_scene_viewer_playback(self) -> None:
        editor_script = (ROOT / "main_computer" / "web" / "applications" / "scripts" / "game-editor.js").read_text(encoding="utf-8")
        scene_viewer_script = (ROOT / "main_computer" / "web" / "applications" / "scripts" / "scene-viewer.js").read_text(encoding="utf-8")
        editor_style = (ROOT / "main_computer" / "web" / "applications" / "styles" / "game-editor.css").read_text(encoding="utf-8")

        self.assertIn("applyGameEditorGpuForgeBakeToScene", editor_script)
        self.assertIn("activeGameEditorGpuForgeBinding", editor_script)
        self.assertIn("prebuilt GPU Forge atlas active", editor_script)
        self.assertIn("gpuForgeAtlas", editor_script)
        self.assertIn("save project to persist link", editor_script)
        self.assertIn("sceneObjectGpuForgeAtlas", scene_viewer_script)
        self.assertIn("scene-gpu-forge-atlas", scene_viewer_script)
        self.assertIn("renderGpuForgeStormLash", scene_viewer_script)
        self.assertIn("storm-lash", scene_viewer_script)
        self.assertIn("data-gpu-forge-atlas", editor_style)
        self.assertIn("scene-gpu-forge-storm-lash", editor_style)
        self.assertIn("scene-gpu-forge-atlas-play", editor_style)

    def test_game_editor_chat_edit_route_is_locked_to_project_scope(self) -> None:
        data = self.post(
            "/api/applications/game-editor/chat/edit",
            {
                "thread_id": "test-game-chat",
                "cell": {"id": "chat-game-scope", "type": "ai", "source": "What files can you see?"},
                "embedded_context": {"active_app": "game-editor", "project_id": "webgl-demo", "target_kind": "game-project", "target_id": "webgl-demo"},
                "embedded_context_source": {"active_app": "game-editor", "target_kind": "game-project", "target_id": "webgl-demo"},
                "mount_plugins": [{"id": "game-editor-edit", "enabled": True, "target_id": "webgl-demo", "project_id": "webgl-demo", "allowed_write_paths": ["main_computer/router.py"]}],
            },
        )
        self.assertTrue(data["ok"])
        output = data["output_cell"]
        self.assertEqual(output["metadata"]["editor_edit_mode"], "game-editor")
        self.assertEqual(output["metadata"]["project_id"], "webgl-demo")
        self.assertEqual(output["metadata"]["allowed_root"], "game_projects/webgl-demo/")
        text = "\n".join(str(part.get("content", "")) for part in output["parts"])
        self.assertIn("game_projects/webgl-demo/project.json", text)
        self.assertIn("proposal-only", text)
        self.assertNotIn("main_computer/router.py", text)
        self.assertNotIn("main_computer_test", text)
        self.assertFalse(output["metadata"]["auto_apply"])
        self.assertEqual(output["metadata"]["editor_intent"], "scope")
        context = output["metadata"]["game_context"]
        self.assertEqual(context["active_project_id"], "webgl-demo")
        self.assertEqual(context["project_manifest"]["path"], "game_projects/webgl-demo/project.json")
        self.assertEqual(context["active_scene"]["id"], "default-empty-scene")
        self.assertEqual(context["counts"]["scenes"], 1)
        self.assertGreater(context["counts"]["active_scene_objects"], 0)

        from main_computer.models import ChatResponse

        class FakeMountedProvider:
            name = "fake-mounted-provider"
            model = "fake-mounted-model"

        class FakeMountedComputer:
            provider = FakeMountedProvider()

            def chat_console_ai(self, source: str, attachments: list | None = None) -> ChatResponse:
                return ChatResponse(
                    content=f"inline scoped AI ran: {'Allowed root: `game_projects/webgl-demo/`' in source}",
                    provider="fake-mounted-provider",
                    model="fake-mounted-model",
                )

        self.server.computer = FakeMountedComputer()
        ai_data = self.post(
            "/api/applications/game-editor/chat/edit",
            {
                "thread_id": "test-game-chat",
                "cell": {"id": "chat-game-ai", "type": "ai", "source": "Say hello from this game."},
                "embedded_context": {"active_app": "game-editor", "project_id": "webgl-demo", "target_kind": "game-project", "target_id": "webgl-demo"},
                "embedded_context_source": {"active_app": "game-editor", "target_kind": "game-project", "target_id": "webgl-demo"},
                "mount_plugins": [{"id": "game-editor-edit", "enabled": True, "target_id": "webgl-demo", "project_id": "webgl-demo"}],
            },
        )
        ai_text = "\n".join(str(part.get("content", "")) for part in ai_data["output_cell"]["parts"])
        self.assertIn("inline scoped AI ran: True", ai_text)
        self.assertFalse(ai_data["output_cell"]["metadata"]["scope_card"])

        self.assertEqual(
            self.post_error(
                "/api/applications/game-editor/chat/edit",
                {
                    "cell": {"id": "chat-game-scope", "type": "ai", "source": "What files can you see?"},
                    "embedded_context": {"active_app": "game-editor", "project_id": "webgl-demo"},
                },
            ).code,
            400,
        )


    def test_game_editor_edit_requests_return_ai_backed_proposal_only_targets(self) -> None:
        project_file = self.root / "game_projects" / "webgl-demo" / "project.json"
        before = project_file.read_bytes()
        ai_calls: list[str] = []

        from main_computer.models import ChatResponse

        class FakeMountedProvider:
            name = "fake-mounted-provider"
            model = "fake-mounted-model"

        class FakeMountedComputer:
            provider = FakeMountedProvider()

            def chat_console_ai(self, source: str, attachments: list | None = None) -> ChatResponse:
                ai_calls.append(source)
                return ChatResponse(
                    content="AI drafted a scoped proposal for the player jump without applying it.",
                    provider="fake-mounted-provider",
                    model="fake-mounted-model",
                )

        self.server.computer = FakeMountedComputer()

        data = self.post(
            "/api/applications/game-editor/chat/edit",
            {
                "thread_id": "test-game-proposal",
                "cell": {"id": "chat-game-proposal", "type": "ai", "source": "Make the player jump higher and add a player controller script."},
                "embedded_context": {
                    "active_app": "game-editor",
                    "project_id": "webgl-demo",
                    "target_kind": "game-project",
                    "target_id": "webgl-demo",
                    "selected_object_id": "hero-sprite",
                },
                "embedded_context_source": {"active_app": "game-editor", "target_kind": "game-project", "target_id": "webgl-demo"},
                "mount_plugins": [{"id": "game-editor-edit", "enabled": True, "target_id": "webgl-demo", "project_id": "webgl-demo"}],
            },
        )

        self.assertEqual(len(ai_calls), 1)
        self.assertIn("Game Editor edit proposal mode:", ai_calls[0])
        self.assertIn("Server-scoped candidate file targets:", ai_calls[0])
        self.assertIn("Make the player jump higher", ai_calls[0])
        self.assertTrue(data["ok"])
        output = data["output_cell"]
        self.assertEqual(output["model"], "fake-mounted-model")
        self.assertEqual(output["metadata"]["editor_intent"], "propose_edit")
        self.assertEqual(output["metadata"]["allowed_root"], "game_projects/webgl-demo/")
        self.assertFalse(output["metadata"]["auto_apply"])
        self.assertFalse(output["metadata"]["scope_card"])

        proposal = output["metadata"]["proposal"]
        self.assertEqual(proposal["type"], "game-editor-file-proposal")
        self.assertEqual(proposal["mode"], "proposal-only")
        self.assertFalse(proposal["auto_apply"])
        self.assertTrue(proposal["within_allowed_root"])
        proposed_paths = [item["path"] for item in proposal["proposed_files"]]
        self.assertIn("game_projects/webgl-demo/project.json", proposed_paths)
        self.assertIn("game_projects/webgl-demo/scripts/player-controller.js", proposed_paths)
        self.assertTrue(all(path.startswith("game_projects/webgl-demo/") for path in proposed_paths))
        self.assertNotIn("main_computer/router.py", proposed_paths)

        context = output["metadata"]["game_context"]
        self.assertEqual(context["selected_object"]["id"], "hero-sprite")
        self.assertEqual(context["active_scene"]["id"], "default-empty-scene")
        text = "\n".join(str(part.get("content", "")) for part in output["parts"])
        self.assertIn("Proposal only", text)
        self.assertIn("no files were modified", text)
        self.assertIn("game_projects/webgl-demo/project.json", text)
        self.assertIn("game_projects/webgl-demo/scripts/player-controller.js", text)

        self.assertEqual(project_file.read_bytes(), before)


    def test_game_editor_color_edit_context_exposes_main_character_props(self) -> None:
        project_file = self.root / "game_projects" / "webgl-demo" / "project.json"
        before = project_file.read_bytes()
        ai_calls: list[str] = []

        from main_computer.models import ChatResponse

        class FakeMountedProvider:
            name = "fake-mounted-provider"
            model = "fake-mounted-model"

        class FakeMountedComputer:
            provider = FakeMountedProvider()

            def chat_console_ai(self, source: str, attachments: list | None = None) -> ChatResponse:
                ai_calls.append(source)
                return ChatResponse(
                    content="AI proposed changing hero-sprite props.color from #7dd3fc to #ef4444 in project.json without applying it.",
                    provider="fake-mounted-provider",
                    model="fake-mounted-model",
                )

        self.server.computer = FakeMountedComputer()

        data = self.post(
            "/api/applications/game-editor/chat/edit",
            {
                "thread_id": "test-game-color-proposal",
                "cell": {"id": "chat-game-color-proposal", "type": "ai", "source": "Change the main char's color red."},
                "embedded_context": {
                    "active_app": "game-editor",
                    "project_id": "webgl-demo",
                    "target_kind": "game-project",
                    "target_id": "webgl-demo",
                },
                "embedded_context_source": {"active_app": "game-editor", "target_kind": "game-project", "target_id": "webgl-demo"},
                "mount_plugins": [{"id": "game-editor-edit", "enabled": True, "target_id": "webgl-demo", "project_id": "webgl-demo"}],
            },
        )

        self.assertEqual(len(ai_calls), 1)
        self.assertIn("hero-sprite", ai_calls[0])
        self.assertIn("Player Cadet", ai_calls[0])
        self.assertIn("#93c5fd", ai_calls[0])
        self.assertIn("props.color", ai_calls[0])
        self.assertIn("Editable object data lives in `project.json`", ai_calls[0])
        self.assertTrue(data["ok"])

        output = data["output_cell"]
        self.assertEqual(output["metadata"]["editor_intent"], "propose_edit")
        proposal = output["metadata"]["proposal"]
        proposed_paths = [item["path"] for item in proposal["proposed_files"]]
        self.assertIn("game_projects/webgl-demo/project.json", proposed_paths)
        self.assertTrue(all(path.startswith("game_projects/webgl-demo/") for path in proposed_paths))
        context = output["metadata"]["game_context"]
        player = next(obj for obj in context["active_scene_objects"] if obj["id"] == "hero-sprite")
        self.assertEqual(player["editable_props"]["label"], "Player Cadet")
        self.assertEqual(player["editable_props"]["role"], "player")
        self.assertEqual(player["editable_props"]["color"], "#93c5fd")

        text = "\n".join(str(part.get("content", "")) for part in output["parts"])
        self.assertIn("hero-sprite props.color", text)
        self.assertIn("no files were modified", text)
        self.assertEqual(project_file.read_bytes(), before)


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
        self.assertIn("3D shuttle look-around surface", APPLICATIONS_INDEX_HTML)
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
        self.assertIn("renderShuttle3dScene", APPLICATIONS_INDEX_HTML)
        self.assertIn("bindShuttle3dLookaround", APPLICATIONS_INDEX_HTML)
        self.assertIn("scene-shuttle3d-mother-ship", APPLICATIONS_INDEX_HTML)
        self.assertIn("scene-shuttle3d-starfield", APPLICATIONS_INDEX_HTML)
        self.assertIn("shuttle3d-lookaround", APPLICATIONS_INDEX_HTML)
        self.assertIn("removeEventListener(\"pointerdown\", container.__mainComputerClickMovementHandler)", APPLICATIONS_INDEX_HTML)
        self.assertIn("startSceneMovement", APPLICATIONS_INDEX_HTML)
        self.assertIn("movementSpeed(scene)", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("scene-click-move", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("player-capsule", APPLICATIONS_INDEX_HTML)
        self.assertIn("parentId", APPLICATIONS_INDEX_HTML)
        self.assertIn("particle-emitter", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="game-editor-particle-density"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="webgl-particle-density"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="game-editor-gpu-forge-bake"', APPLICATIONS_INDEX_HTML)
        self.assertIn("function bakeGameEditorGpuForgeAtlas", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/game-editor/gpu-forge/bake-effect-atlas", APPLICATIONS_INDEX_HTML)
        self.assertIn("live_stream_required: false", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="game-editor-preview"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="game-editor-webgl-canvas"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="game-editor-chat-toggle"', APPLICATIONS_INDEX_HTML)
        self.assertIn('aria-controls="game-editor-chat-popout"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="game-editor-chat-popout"', APPLICATIONS_INDEX_HTML)
        self.assertIn('class="game-editor-chat-popout"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="game-editor-chat-panel"', APPLICATIONS_INDEX_HTML)
        self.assertIn('class="game-editor-chat-panel"', APPLICATIONS_INDEX_HTML)
        self.assertIn('aria-label="Game Assistant popout Chat Console"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-chat-console-embed="game-editor"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-chat-console-target-kind="game-project"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-chat-console-layout="full"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-chat-console-show-thread-rail="1"', APPLICATIONS_INDEX_HTML)
        self.assertIn("const embeddedLinkedThreadId", APPLICATIONS_INDEX_HTML)
        self.assertIn("return embeddedLinkedThreadId() || chatConsoleState?.id", APPLICATIONS_INDEX_HTML)
        self.assertIn("grid-template-columns: minmax(220px, 280px) minmax(420px, 1fr);", APPLICATIONS_INDEX_HTML)
        self.assertIn("width: min(980px, calc(100% - 28px));", APPLICATIONS_INDEX_HTML)
        self.assertIn("function gameEditorChatContextSnapshot", APPLICATIONS_INDEX_HTML)
        self.assertIn("window.MainComputerGameEditorContext", APPLICATIONS_INDEX_HTML)
        self.assertIn("function mountGameEditorChat", APPLICATIONS_INDEX_HTML)
        self.assertIn("function refreshGameEditorChatMount", APPLICATIONS_INDEX_HTML)
        self.assertIn("function setGameEditorChatOpen", APPLICATIONS_INDEX_HTML)
        self.assertIn("setGameEditorChatOpen(!gameEditorState.chatOpen)", APPLICATIONS_INDEX_HTML)
        self.assertIn('nodes.chatPopout?.addEventListener("click", (event) => {', APPLICATIONS_INDEX_HTML)
        self.assertIn("event.stopPropagation();", APPLICATIONS_INDEX_HTML)
        self.assertIn("nodes.chatPopout.hidden = !shouldOpen", APPLICATIONS_INDEX_HTML)
        self.assertIn("return mountGameEditorChat();", APPLICATIONS_INDEX_HTML)
        self.assertIn('layout: "full"', APPLICATIONS_INDEX_HTML)
        self.assertIn("showThreadRail: true", APPLICATIONS_INDEX_HTML)
        self.assertIn("const mount = api.mountEmbedded || window.chatConsoleMountEmbedded;", APPLICATIONS_INDEX_HTML)
        self.assertIn("getEmbeddedContext: gameEditorChatContextSnapshot", APPLICATIONS_INDEX_HTML)
        self.assertIn("plugins: [", APPLICATIONS_INDEX_HTML)
        self.assertIn('id: "game-editor-edit"', APPLICATIONS_INDEX_HTML)
        self.assertIn('label: "Edit this game"', APPLICATIONS_INDEX_HTML)
        self.assertIn("defaultEnabled: true", APPLICATIONS_INDEX_HTML)
        self.assertIn('endpoint: "/api/applications/game-editor/chat/edit"', APPLICATIONS_INDEX_HTML)
        self.assertIn('pathway: "game-editor-rag-edit-smoke"', APPLICATIONS_INDEX_HTML)
        self.assertIn("function chatConsoleActiveMountPlugins", APPLICATIONS_INDEX_HTML)
        self.assertIn("function renderChatConsoleMountPluginControls", APPLICATIONS_INDEX_HTML)
        self.assertIn("payload.mount_plugins", APPLICATIONS_INDEX_HTML)
        self.assertIn("game_builder_phase: \"scoped-editor-context\"", APPLICATIONS_INDEX_HTML)
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

    def test_game_editor_chat_edit_auto_applies_validated_rag_payloads(self) -> None:
        project_file = self.root / "game_projects" / "webgl-demo" / "project.json"
        before_project = json.loads(project_file.read_text(encoding="utf-8"))
        old_color = before_project["scenes"][0]["objects"][5]["props"]["color"]

        from main_computer.models import ChatResponse

        class FakeMountedProvider:
            name = "fake-mounted-provider"
            model = "fake-mounted-model"

        class FakeMountedComputer:
            provider = FakeMountedProvider()

            def chat_console_ai(self, source: str, attachments: list | None = None) -> ChatResponse:
                return ChatResponse(
                    content=json.dumps(
                        {
                            "ok": True,
                            "mode": "game_editor_rag_edit_proposal",
                            "target_kind": "game-project-plus-editor",
                            "target_id": "webgl-demo",
                            "allowed_roots": ["game_projects/webgl-demo/"],
                            "summary": "Change the main enemy color to red.",
                            "grounding": [
                                {
                                    "evidence_type": "scene_object",
                                    "path": "game_projects/webgl-demo/project.json",
                                    "json_pointer": "/scenes/0/objects/5/props/color",
                                    "exact_value": old_color,
                                    "reason": "The model selected the enemy object from the supplied RAG evidence.",
                                }
                            ],
                            "json_edits": [
                                {
                                    "path": "game_projects/webgl-demo/project.json",
                                    "json_pointer": "/scenes/0/objects/5/props/color",
                                    "old_value": old_color,
                                    "new_value": "#ff0000",
                                    "reason": "Set the selected enemy color to red.",
                                }
                            ],
                            "text_replacements": [],
                            "create_files": [],
                            "warnings": [],
                        }
                    ),
                    provider="fake-mounted-provider",
                    model="fake-mounted-model",
                )

        self.server.computer = FakeMountedComputer()

        data = self.post(
            "/api/applications/game-editor/chat/edit",
            {
                "thread_id": "test-game-auto-apply",
                "auto_apply": True,
                "live_apply": True,
                "cell": {"id": "chat-game-auto-apply", "type": "ai", "source": "Change the main enemy's color red."},
                "embedded_context": {
                    "active_app": "game-editor",
                    "project_id": "webgl-demo",
                    "target_kind": "game-project",
                    "target_id": "webgl-demo",
                },
                "embedded_context_source": {"active_app": "game-editor", "target_kind": "game-project", "target_id": "webgl-demo"},
                "mount_plugins": [{"id": "game-editor-edit", "enabled": True, "target_id": "webgl-demo", "project_id": "webgl-demo", "auto_apply": True, "live_apply": True}],
            },
        )

        self.assertTrue(data["ok"])
        output = data["output_cell"]
        self.assertEqual(output["metadata"]["editor_intent"], "apply_edit")
        self.assertTrue(output["metadata"]["auto_apply"])
        self.assertTrue(output["metadata"]["apply_result"]["ok"])
        self.assertEqual(output["metadata"]["proposal"]["mode"], "applied")
        after_project = json.loads(project_file.read_text(encoding="utf-8"))
        self.assertEqual(after_project["scenes"][0]["objects"][5]["props"]["color"], "#ff0000")
        self.assertNotEqual(after_project["scenes"][0]["objects"][5]["props"]["color"], old_color)
        text = "\n".join(str(part.get("content", "")) for part in output["parts"])
        self.assertIn("Applied", text)
        self.assertIn("golden-path RAG wrote validated replacement files", text)


    def test_game_editor_rag_apply_validated_payload_writes_game_project(self) -> None:
        project_file = self.root / "game_projects" / "webgl-demo" / "project.json"
        project = json.loads(project_file.read_text(encoding="utf-8"))
        old_color = project["scenes"][0]["objects"][5]["props"]["color"]
        project["scenes"][0]["objects"][5]["props"]["color"] = "#ff0000"
        replacement_text = json.dumps(project, ensure_ascii=False, indent=2) + "\n"

        payload = {
            "thread_id": "test-game-rag-apply",
            "embedded_context": {"active_app": "game-editor", "project_id": "webgl-demo", "target_kind": "game-project", "target_id": "webgl-demo"},
            "embedded_context_source": {"active_app": "game-editor", "target_kind": "game-project", "target_id": "webgl-demo"},
            "mount_plugins": [{"id": "game-editor-edit", "enabled": True, "target_id": "webgl-demo", "project_id": "webgl-demo"}],
            "payloads": [
                {
                    "path": "game_projects/webgl-demo/project.json",
                    "operation": "modify",
                    "original_sha256": hashlib.sha256(project_file.read_bytes()).hexdigest(),
                    "replacement_sha256": hashlib.sha256(replacement_text.encode("utf-8")).hexdigest(),
                    "replacement_text": replacement_text,
                }
            ],
        }

        data = self.post("/api/applications/game-editor/chat/apply-rag-proposal", payload)
        self.assertTrue(data["ok"])
        self.assertEqual(data["mode"], "rag-validated-live-apply")
        self.assertEqual(data["files"][0]["path"], "game_projects/webgl-demo/project.json")
        after = json.loads(project_file.read_text(encoding="utf-8"))
        self.assertEqual(after["scenes"][0]["objects"][5]["props"]["color"], "#ff0000")
        self.assertNotEqual(after["scenes"][0]["objects"][5]["props"]["color"], old_color)

    def test_game_editor_rag_apply_rejects_stale_hash_and_out_of_scope_path(self) -> None:
        project_file = self.root / "game_projects" / "webgl-demo" / "project.json"
        before = project_file.read_text(encoding="utf-8")

        stale = self.post_error(
            "/api/applications/game-editor/chat/apply-rag-proposal",
            {
                "embedded_context": {"active_app": "game-editor", "project_id": "webgl-demo", "target_kind": "game-project", "target_id": "webgl-demo"},
                "embedded_context_source": {"active_app": "game-editor", "target_kind": "game-project", "target_id": "webgl-demo"},
                "mount_plugins": [{"id": "game-editor-edit", "enabled": True, "target_id": "webgl-demo", "project_id": "webgl-demo"}],
                "payloads": [
                    {
                        "path": "game_projects/webgl-demo/project.json",
                        "operation": "modify",
                        "original_sha256": "not-the-current-hash",
                        "replacement_sha256": hashlib.sha256(before.encode("utf-8")).hexdigest(),
                        "replacement_text": before,
                    }
                ],
            },
        )
        self.assertEqual(stale.code, 400)
        self.assertEqual(project_file.read_text(encoding="utf-8"), before)

        escaped = self.post_error(
            "/api/applications/game-editor/chat/apply-rag-proposal",
            {
                "embedded_context": {"active_app": "game-editor", "project_id": "webgl-demo", "target_kind": "game-project", "target_id": "webgl-demo"},
                "embedded_context_source": {"active_app": "game-editor", "target_kind": "game-project", "target_id": "webgl-demo"},
                "mount_plugins": [{"id": "game-editor-edit", "enabled": True, "target_id": "webgl-demo", "project_id": "webgl-demo"}],
                "payloads": [
                    {
                        "path": "main_computer/router.py",
                        "operation": "modify",
                        "original_sha256": None,
                        "replacement_sha256": hashlib.sha256(b"").hexdigest(),
                        "replacement_text": "",
                    }
                ],
            },
        )
        self.assertEqual(escaped.code, 400)



if __name__ == "__main__":
    unittest.main()
