from __future__ import annotations

from main_computer.viewport_state import *  # noqa: F401,F403
import difflib
import hashlib
import os

from main_computer.chat_ai_subprocess import append_text_log, config_to_payload
from main_computer.models import ChatResponse


def _mounted_editor_should_inline_test_provider(provider: Any) -> bool:
    module = str(getattr(getattr(provider, "__class__", None), "__module__", "") or "")
    if not provider or module.startswith("main_computer.providers"):
        return False
    return os.environ.get("MAIN_COMPUTER_DISABLE_INLINE_TEST_PROVIDER", "").strip().lower() not in {"1", "true", "yes", "on"}


def _mounted_editor_scope_query(source: str) -> bool:
    text = re.sub(r"\s+", " ", str(source or "").strip().lower())
    return (
        "what files can you see" in text
        or "which files can you see" in text
        or "what can you see" in text
        or text in {"scope", "show scope", "show me the scope"}
        or ("visible" in text and "files" in text)
    )


def _mounted_editor_edit_request(source: str) -> bool:
    text = re.sub(r"\s+", " ", str(source or "").strip().lower())
    if not text or _mounted_editor_scope_query(text):
        return False
    edit_verbs = {
        "add",
        "adjust",
        "change",
        "create",
        "decrease",
        "delete",
        "edit",
        "fix",
        "generate",
        "increase",
        "insert",
        "make",
        "modify",
        "move",
        "remove",
        "rename",
        "replace",
        "set",
        "tune",
        "update",
    }
    if re.search(r"\b(" + "|".join(sorted(edit_verbs)) + r")\b", text):
        return True
    return bool(re.search(r"\b(higher|lower|faster|slower|brighter|darker|bigger|smaller|stronger|weaker)\b", text))


class ViewportGameRoutesMixin:
    def _handle_game_editor_post(self) -> None:
        try:
            body = self._read_json()
            route = self.path
            if route == "/api/applications/game-editor/projects":
                self._send_json(self._game_projects_payload())
                return
            if route == "/api/applications/game-editor/project/read":
                self._send_json(self._game_project_read_payload(str(body.get("project_id", "") or "")))
                return
            if route == "/api/applications/game-editor/chat/edit":
                self._handle_game_editor_chat_edit(body)
                return
            if route == "/api/applications/game-editor/chat/apply-rag-proposal":
                self._handle_game_editor_rag_apply(body)
                return
            if route == "/api/applications/game-editor/project/write":
                self._handle_game_project_write(body)
                return
            if route == "/api/applications/game-editor/project/create":
                self._handle_game_project_create(body)
                return
            if route == "/api/applications/game-editor/project/duplicate":
                self._handle_game_project_duplicate(body)
                return
            if route == "/api/applications/game-editor/project/export":
                self._handle_game_project_export(body)
                return
            if route == "/api/applications/game-editor/project/import":
                self._handle_game_project_import(body)
                return
            if route == "/api/applications/game-editor/files/list":
                self._send_json(self._game_files_payload(str(body.get("project_id", "") or "")))
                return
            if route == "/api/applications/game-editor/file/read":
                self._handle_game_file_read(body)
                return
            if route == "/api/applications/game-editor/file/write":
                self._handle_game_file_write(body)
                return
            if route == "/api/applications/game-editor/file/delete":
                self._handle_game_file_delete(body)
                return
            if route == "/api/applications/game-editor/file/move":
                self._handle_game_file_move(body)
                return
            if route == "/api/applications/game-editor/assets":
                self._send_json(self._game_assets_payload(str(body.get("project_id", "") or "")))
                return
            if route == "/api/applications/game-editor/asset/upload":
                self._handle_game_asset_upload(body)
                return
            if route == "/api/applications/game-editor/asset/delete":
                self._handle_game_asset_delete(body)
                return
            if route == "/api/applications/game-editor/asset/move":
                self._handle_game_asset_move(body)
                return
            if route == "/api/applications/game-editor/scripts":
                self._send_json(self._game_scripts_payload(str(body.get("project_id", "") or "")))
                return
            if route == "/api/applications/game-editor/script/read":
                body["path"] = f"scripts/{str(body.get('path', '') or '')}"
                self._handle_game_file_read(body)
                return
            if route == "/api/applications/game-editor/script/write":
                body["path"] = f"scripts/{str(body.get('path', '') or '')}"
                self._handle_game_file_write(body)
                return
            if route == "/api/applications/game-editor/script/create":
                body["path"] = f"scripts/{str(body.get('path', '') or '')}"
                body.setdefault("expected_content_hash", None)
                self._handle_game_file_write(body)
                return
            if route == "/api/applications/game-editor/script/delete":
                body["path"] = f"scripts/{str(body.get('path', '') or '')}"
                self._handle_game_file_delete(body)
                return
            raise ValueError("Unknown Game Editor route.")
        except GameEditorConflict as exc:
            self.server.signal("api-game-editor-conflict", route=self.path, error=exc)
            self._send_json({"ok": False, "conflict": True, "error": str(exc)}, status=HTTPStatus.CONFLICT)
        except Exception as exc:
            self.server.signal("api-game-editor-error", route=self.path, error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _game_chat_enabled_plugin(self, body: dict[str, Any], expected_id: str = "game-editor-edit") -> dict[str, Any]:
        plugins = body.get("mount_plugins")
        if not isinstance(plugins, list):
            plugins = []
        for plugin in plugins:
            if isinstance(plugin, dict) and plugin.get("id") == expected_id and plugin.get("enabled") is not False:
                return plugin
        state = body.get("mount_plugin_state")
        if isinstance(state, dict):
            plugin_state = state.get(expected_id)
            if isinstance(plugin_state, dict) and plugin_state.get("enabled") is not False:
                return {"id": expected_id, "enabled": True}
        raise ValueError("Game Editor edit chat requires the checked game-editor-edit mount plugin.")

    def _game_chat_require_game_editor_mount(self, body: dict[str, Any]) -> None:
        embedded = body.get("embedded_context") if isinstance(body.get("embedded_context"), dict) else {}
        source = body.get("embedded_context_source") if isinstance(body.get("embedded_context_source"), dict) else {}
        active_app = str(embedded.get("active_app") or source.get("active_app") or "").strip()
        if active_app != "game-editor":
            raise ValueError("Game Editor edit chat must come from a Game Editor embedded chat mount.")

    def _game_chat_project_id(self, body: dict[str, Any], plugin: dict[str, Any]) -> str:
        embedded = body.get("embedded_context") if isinstance(body.get("embedded_context"), dict) else {}
        source = body.get("embedded_context_source") if isinstance(body.get("embedded_context_source"), dict) else {}
        for value in (
            embedded.get("project_id"),
            embedded.get("target_id"),
            source.get("target_id"),
            body.get("project_id"),
            body.get("target_id"),
            plugin.get("project_id"),
            plugin.get("target_id"),
        ):
            text = str(value or "").strip()
            if text:
                return self._game_project_id(text)
        return self._default_game_project_id()

    def _game_editor_visible_project_files(self, project_id: str, *, limit: int = 80) -> list[str]:
        root = self._game_project_root(project_id)
        visible: list[str] = []
        project_file = root / "project.json"
        if project_file.is_file():
            visible.append(f"game_projects/{root.name}/project.json")
        for folder in ("scripts", "data", "assets", "builds"):
            folder_root = root / folder
            if not folder_root.is_dir():
                continue
            for path in sorted(folder_root.rglob("*")):
                if path.is_file():
                    visible.append(f"game_projects/{root.name}/{path.relative_to(root).as_posix()}")
                    if len(visible) >= limit:
                        return visible
        return visible

    def _game_editor_selected_object_id(self, body: dict[str, Any], plugin: dict[str, Any]) -> str:
        embedded = body.get("embedded_context") if isinstance(body.get("embedded_context"), dict) else {}
        source = body.get("embedded_context_source") if isinstance(body.get("embedded_context_source"), dict) else {}
        for value in (
            embedded.get("selected_object_id"),
            embedded.get("selectedObjectId"),
            source.get("selected_object_id"),
            source.get("selectedObjectId"),
            body.get("selected_object_id"),
            body.get("selectedObjectId"),
            plugin.get("selected_object_id"),
            plugin.get("selectedObjectId"),
        ):
            text = str(value or "").strip()
            if text:
                return text
        selected = embedded.get("selected_object") or source.get("selected_object") or body.get("selected_object")
        if isinstance(selected, dict):
            return str(selected.get("id") or "").strip()
        return ""

    def _game_editor_object_summary(self, obj: dict[str, Any]) -> dict[str, Any]:
        props = obj.get("props") if isinstance(obj.get("props"), dict) else {}
        return {
            "id": str(obj.get("id") or ""),
            "type": str(obj.get("type") or ""),
            "name": str(props.get("label") or obj.get("name") or ""),
            "parentId": str(obj.get("parentId") or ""),
            "role": str(props.get("role") or ""),
            "spawn": bool(props.get("spawn", False)),
            "x": obj.get("x"),
            "y": obj.get("y"),
            "width": obj.get("width"),
            "height": obj.get("height"),
            "editable_props": {
                key: props.get(key)
                for key in (
                    "label",
                    "role",
                    "spawn",
                    "color",
                    "motion",
                    "spellState",
                    "particleCount",
                    "particleSize",
                    "spread",
                    "orbitRadius",
                    "verticalLift",
                    "z",
                    "zOffset",
                    "bob",
                    "spriteSeries",
                    "spriteRig",
                )
                if key in props
            },
        }

    def _game_editor_scene_summary(self, scene: dict[str, Any]) -> dict[str, Any]:
        objects = scene.get("objects") if isinstance(scene.get("objects"), list) else []
        type_counts: dict[str, int] = {}
        for obj in objects:
            if isinstance(obj, dict):
                obj_type = str(obj.get("type") or "unknown")
                type_counts[obj_type] = type_counts.get(obj_type, 0) + 1
        return {
            "id": str(scene.get("id") or ""),
            "name": str(scene.get("name") or ""),
            "version": scene.get("version"),
            "object_count": len(objects),
            "object_types": dict(sorted(type_counts.items())),
            "metadata": scene.get("metadata") if isinstance(scene.get("metadata"), dict) else {},
        }

    def _game_editor_context_payload(
        self,
        *,
        body: dict[str, Any],
        plugin: dict[str, Any],
        project_id: str,
        project_payload: dict[str, Any],
        files_payload: dict[str, Any],
        scripts_payload: dict[str, Any],
        visible_files: list[str],
    ) -> dict[str, Any]:
        project = project_payload.get("project") if isinstance(project_payload.get("project"), dict) else {}
        scenes = [scene for scene in project.get("scenes", []) if isinstance(scene, dict)] if isinstance(project.get("scenes"), list) else []
        scene_summaries = [self._game_editor_scene_summary(scene) for scene in scenes]
        active_scene_id = str(project.get("activeSceneId") or "")
        active_scene = next((scene for scene in scenes if str(scene.get("id") or "") == active_scene_id), scenes[0] if scenes else {})
        active_scene_summary = self._game_editor_scene_summary(active_scene) if isinstance(active_scene, dict) and active_scene else {}
        selected_object_id = self._game_editor_selected_object_id(body, plugin)
        active_objects = active_scene.get("objects") if isinstance(active_scene.get("objects"), list) else []
        selected_object = None
        if selected_object_id:
            selected_object = next((obj for obj in active_objects if isinstance(obj, dict) and str(obj.get("id") or "") == selected_object_id), None)
        object_summaries = [self._game_editor_object_summary(obj) for obj in active_objects if isinstance(obj, dict)]

        files = files_payload.get("files") if isinstance(files_payload.get("files"), list) else []
        script_records = scripts_payload.get("scripts") if isinstance(scripts_payload.get("scripts"), list) else []

        assets = [item for item in files if isinstance(item, dict) and item.get("kind") == "asset"]
        data_files = [item for item in files if isinstance(item, dict) and item.get("kind") == "data"]
        build_files = [item for item in files if isinstance(item, dict) and item.get("kind") == "build"]

        return {
            "active_project_id": project_id,
            "allowed_root": f"game_projects/{project_id}/",
            "write_policy": {
                "mode": "proposal-only",
                "auto_apply": False,
                "writes_enabled": False,
                "server_derived_allowed_root": True,
            },
            "project_manifest": {
                "path": f"game_projects/{project_id}/project.json",
                "id": str(project.get("id") or project_id),
                "name": str(project.get("name") or project_id),
                "description": str(project.get("description") or ""),
                "version": project.get("version"),
                "settings": project.get("settings") if isinstance(project.get("settings"), dict) else {},
                "metadata": project.get("metadata") if isinstance(project.get("metadata"), dict) else {},
                "content_hash": project_payload.get("content_hash"),
                "bytes": project_payload.get("bytes"),
            },
            "scene_list": scene_summaries,
            "active_scene": active_scene_summary,
            "active_scene_objects": object_summaries[:40],
            "selected_object": self._game_editor_object_summary(selected_object) if isinstance(selected_object, dict) else None,
            "selected_object_id": selected_object_id,
            "scripts": [
                {
                    "path": f"game_projects/{project_id}/scripts/{str(item.get('path') or '')}",
                    "content_hash": item.get("content_hash"),
                    "bytes": item.get("bytes"),
                }
                for item in script_records
                if isinstance(item, dict)
            ],
            "assets": [
                {
                    "path": f"game_projects/{project_id}/{str(item.get('path') or '')}",
                    "kind": item.get("kind"),
                    "content_hash": item.get("content_hash"),
                    "bytes": item.get("bytes"),
                }
                for item in assets
            ],
            "data_files": [
                {
                    "path": f"game_projects/{project_id}/{str(item.get('path') or '')}",
                    "kind": item.get("kind"),
                    "content_hash": item.get("content_hash"),
                    "bytes": item.get("bytes"),
                }
                for item in data_files
            ],
            "build_files": [
                {
                    "path": f"game_projects/{project_id}/{str(item.get('path') or '')}",
                    "kind": item.get("kind"),
                    "content_hash": item.get("content_hash"),
                    "bytes": item.get("bytes"),
                }
                for item in build_files
            ],
            "visible_files": visible_files,
            "counts": {
                "visible_files": len(visible_files),
                "scenes": len(scene_summaries),
                "active_scene_objects": len(object_summaries),
                "scripts": len(script_records),
                "assets": len(assets),
                "data_files": len(data_files),
                "build_files": len(build_files),
            },
        }

    def _game_editor_context_summary(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "active_project_id": context.get("active_project_id"),
            "allowed_root": context.get("allowed_root"),
            "project_manifest": context.get("project_manifest"),
            "scene_list": context.get("scene_list"),
            "active_scene": context.get("active_scene"),
            "active_scene_objects": context.get("active_scene_objects"),
            "selected_object": context.get("selected_object"),
            "counts": context.get("counts"),
            "write_policy": context.get("write_policy"),
        }

    def _game_editor_compact_json(self, value: Any, *, limit: int = 1600) -> str:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    def _game_editor_scoped_chat_context(self, *, context: dict[str, Any]) -> str:
        project_id = str(context.get("active_project_id") or "")
        visible_files = context.get("visible_files") if isinstance(context.get("visible_files"), list) else []
        file_lines = "\n".join(f"- `{path}`" for path in visible_files) or "- No files are present in this game project yet."
        project_manifest = context.get("project_manifest") if isinstance(context.get("project_manifest"), dict) else {}
        scenes = context.get("scene_list") if isinstance(context.get("scene_list"), list) else []
        active_scene = context.get("active_scene") if isinstance(context.get("active_scene"), dict) else {}
        selected_object = context.get("selected_object") if isinstance(context.get("selected_object"), dict) else None
        scripts = context.get("scripts") if isinstance(context.get("scripts"), list) else []
        assets = context.get("assets") if isinstance(context.get("assets"), list) else []
        data_files = context.get("data_files") if isinstance(context.get("data_files"), list) else []
        object_summaries = context.get("active_scene_objects") if isinstance(context.get("active_scene_objects"), list) else []

        scene_lines = "\n".join(
            f"- `{scene.get('id')}` ({scene.get('name') or 'unnamed'}): {scene.get('object_count', 0)} objects; types {self._game_editor_compact_json(scene.get('object_types', {}), limit=280)}"
            for scene in scenes[:20]
            if isinstance(scene, dict)
        ) or "- No scenes were found in project.json."
        object_lines = "\n".join(
            (
                f"- `{obj.get('id')}` {obj.get('type') or 'object'} "
                f"label={obj.get('name') or ''} role={obj.get('role') or ''} "
                f"parent={obj.get('parentId') or ''} "
                f"pos=({obj.get('x')},{obj.get('y')}) size=({obj.get('width')}x{obj.get('height')}) "
                f"editable_props={self._game_editor_compact_json(obj.get('editable_props', {}), limit=900)}"
            ).rstrip()
            for obj in object_summaries[:25]
            if isinstance(obj, dict)
        ) or "- No objects were found in the active scene."
        script_lines = "\n".join(f"- `{item.get('path')}`" for item in scripts[:40] if isinstance(item, dict)) or "- No scripts are present."
        asset_lines = "\n".join(f"- `{item.get('path')}`" for item in assets[:40] if isinstance(item, dict)) or "- No assets are present."
        data_lines = "\n".join(f"- `{item.get('path')}`" for item in data_files[:40] if isinstance(item, dict)) or "- No data/config files are present."

        selected_line = (
            (
                f"`{selected_object.get('id')}` ({selected_object.get('type')}) "
                f"label={selected_object.get('name') or ''} "
                f"editable_props={self._game_editor_compact_json(selected_object.get('editable_props', {}), limit=900)}"
            )
            if isinstance(selected_object, dict)
            else "None supplied by the mounted editor."
        )

        return (
            "You are answering inside the mounted Game Editor chat.\n"
            f"You are scoped to the active game project `{project_id}` plus the Game Editor implementation files explicitly listed in proposal-mode evidence.\n"
            f"Allowed root: `{context.get('allowed_root')}`.\n"
            "Do not propose or imply writes outside the allowed roots or allowed editor source files supplied by the server.\n"
            "This phase is proposal-only: no files may be modified.\n\n"
            "Game Editor project context:\n"
            f"- Active game project id: `{project_id}`\n"
            f"- Project manifest: `{project_manifest.get('path')}` name={project_manifest.get('name')!r} version={project_manifest.get('version')!r}\n"
            f"- Project settings: {self._game_editor_compact_json(project_manifest.get('settings', {}), limit=900)}\n"
            f"- Game builder metadata: {self._game_editor_compact_json(project_manifest.get('metadata', {}), limit=900)}\n"
            f"- Active scene: `{active_scene.get('id', '')}` ({active_scene.get('name', 'unnamed')})\n"
            f"- Active scene metadata: {self._game_editor_compact_json(active_scene.get('metadata', {}), limit=1200)}\n"
            f"- Selected object: {selected_line}\n"
            "- Editable object data is exposed as raw evidence records. Infer likely targets from ids, labels, roles, types, parent ids, scene membership, selected-object state, editable props, and literal file content.\n"
            "- Prefer existing editable fields over inventing new metadata. Do not rely on project-specific synonym rules.\n\n"
            "Scene list:\n"
            f"{scene_lines}\n\n"
            "Active scene object summary:\n"
            f"{object_lines}\n\n"
            "Scripts:\n"
            f"{script_lines}\n\n"
            "Assets:\n"
            f"{asset_lines}\n\n"
            "Data/config files:\n"
            f"{data_lines}\n\n"
            "Visible game-project files:\n"
            f"{file_lines}\n\n"
            f"Project file count: {context.get('counts', {}).get('visible_files', len(visible_files))} visible files; "
            f"{context.get('counts', {}).get('scripts', 0)} scripts; "
            f"{context.get('counts', {}).get('assets', 0)} assets; "
            f"{context.get('counts', {}).get('data_files', 0)} data/config files.\n"
        )


    def _game_editor_scope_response(self, *, cell: dict[str, Any], source: str, context: dict[str, Any], run_id: str, thread_id: str) -> ChatResponse:
        project_id = str(context.get("active_project_id") or "")
        visible_files = context.get("visible_files") if isinstance(context.get("visible_files"), list) else []
        file_lines = "\n".join(f"- `{path}`" for path in visible_files) or "- No files are present in this game project yet."
        project_manifest = context.get("project_manifest") if isinstance(context.get("project_manifest"), dict) else {}
        active_scene = context.get("active_scene") if isinstance(context.get("active_scene"), dict) else {}
        scene_lines = "\n".join(
            f"- `{scene.get('id')}` ({scene.get('name') or 'unnamed'}): {scene.get('object_count', 0)} objects"
            for scene in (context.get("scene_list") if isinstance(context.get("scene_list"), list) else [])[:20]
            if isinstance(scene, dict)
        ) or "- No scenes were found in project.json."
        content = (
            f"I am scoped to the active Game Editor project `{project_id}` only.\n\n"
            "Project context:\n"
            f"- Project manifest: `{project_manifest.get('path')}`\n"
            f"- Active scene: `{active_scene.get('id', '')}` ({active_scene.get('name', 'unnamed')})\n"
            f"- Selected object: {self._game_editor_compact_json(context.get('selected_object'), limit=600) if context.get('selected_object') else 'None supplied by the mounted editor.'}\n"
            f"- Counts: {self._game_editor_compact_json(context.get('counts', {}), limit=600)}\n\n"
            "Scenes:\n"
            f"{scene_lines}\n\n"
            "Visible game-project files:\n"
            f"{file_lines}\n\n"
            "Scope lock:\n"
            f"- Allowed root: `{context.get('allowed_root')}`\n"
            "- Server-derived write policy: proposal-only; no files were modified.\n"
            "- Repo files such as `main_computer/`, tests, tools, and other projects are outside this mounted editor context.\n\n"
            "For ordinary questions, this mounted route runs the AI with this scoped context instead of returning this static scope card. "
            "For edit requests, it returns a structured proposal without writing files."
        )
        return ChatResponse(
            content=content,
            provider="main-computer-mounted-editor",
            model="game-editor-scoped-chat",
            metadata={
                "run_id": run_id,
                "thread_id": thread_id,
                "editor_edit_mode": "game-editor",
                "project_id": project_id,
                "allowed_root": context.get("allowed_root"),
                "visible_files": visible_files,
                "prompt": source,
                "auto_apply": False,
                "scope_card": True,
                "editor_intent": "scope",
                "game_context": self._game_editor_context_summary(context),
            },
        )

    def _game_editor_prompt_tokens(self, source: str) -> set[str]:
        stopwords = {
            "about",
            "after",
            "also",
            "and",
            "can",
            "could",
            "edit",
            "for",
            "from",
            "game",
            "have",
            "make",
            "please",
            "project",
            "that",
            "the",
            "this",
            "with",
            "would",
        }
        return {token for token in re.findall(r"[a-z0-9_-]{3,}", str(source or "").lower()) if token not in stopwords}

    def _game_editor_proposal_candidates(self, *, project_id: str, source: str, context: dict[str, Any]) -> list[dict[str, Any]]:
        allowed_root = f"game_projects/{project_id}/"
        visible_files = [str(path) for path in context.get("visible_files", []) if isinstance(path, str)]
        tokens = self._game_editor_prompt_tokens(source)
        source_text = str(source or "").lower()
        scored: list[tuple[int, str, str]] = []

        def add_score(path: str, score: int, reason: str) -> None:
            if not path.startswith(allowed_root):
                return
            if path.endswith("/.gitkeep"):
                return
            scored.append((score, path, reason))

        for path in visible_files:
            rel = path.removeprefix(allowed_root)
            lower = path.lower()
            score = sum(3 for token in tokens if token in lower)
            reason = "Existing game-project file that matches the request."
            if rel == "project.json":
                if re.search(r"\b(color|colour|tint|red|blue|green|yellow|purple|orange|white|black|sprite|visual|main|character|char|hero|player|scene|object|movement|jump|control|vfx|particle|metadata|project|settings|spawn)\b", source_text):
                    score += 10
                    reason = "project.json contains active scene objects and editable object props such as Main Character/player props.color."
                else:
                    score += 1
                    reason = "project.json is the project manifest and safest first proposal target for project-level edits."
            elif rel.startswith("scripts/"):
                if re.search(r"\b(script|code|controller|player|jump|move|movement|input|behavior)\b", source_text):
                    score += 7
                    reason = "A script/controller file is likely responsible for requested gameplay behavior."
            elif rel.startswith("data/"):
                if re.search(r"\b(data|config|json|balance|settings|stats)\b", source_text):
                    score += 6
                    reason = "A data/config file may hold tunable game values for this request."
            elif rel.startswith("assets/"):
                if re.search(r"\b(asset|image|sprite|sound|audio|texture|art)\b", source_text):
                    score += 6
                    reason = "An asset file may be involved in the requested visual/audio change."
            elif rel.startswith("builds/"):
                if re.search(r"\b(build|export|bundle|release)\b", source_text):
                    score += 6
                    reason = "A build artifact path is relevant to the requested build/export change."
            if score > 0:
                add_score(path, score, reason)

        if re.search(r"\b(add|create|generate|new)\b", source_text) and re.search(r"\b(script|controller|jump|player|movement|input|behavior)\b", source_text):
            proposed = f"{allowed_root}scripts/player-controller.js"
            exists = (self._game_project_root(project_id) / "scripts" / "player-controller.js").is_file()
            scored.append((9, proposed, "Create or update a scoped player-controller script for the requested gameplay behavior." if exists else "Create a scoped player-controller script for the requested gameplay behavior."))

        if re.search(r"\b(data|config|balance|stats)\b", source_text) and re.search(r"\b(add|create|new)\b", source_text):
            proposed = f"{allowed_root}data/gameplay.json"
            exists = (self._game_project_root(project_id) / "data" / "gameplay.json").is_file()
            scored.append((7, proposed, "Create or update scoped gameplay data/config for the requested change." if exists else "Create scoped gameplay data/config for the requested change."))

        project_json = f"{allowed_root}project.json"
        if not any(path == project_json for _, path, _ in scored):
            add_score(project_json, 2, "project.json is the project manifest and safest first proposal target for project-level edits.")

        deduped: dict[str, tuple[int, str]] = {}
        for score, path, reason in scored:
            current = deduped.get(path)
            if current is None or score > current[0]:
                deduped[path] = (score, reason)

        root = self._game_project_root(project_id)
        proposals: list[dict[str, Any]] = []
        for path, (score, reason) in sorted(deduped.items(), key=lambda item: (-item[1][0], item[0]))[:6]:
            rel = path.removeprefix(allowed_root)
            target = root / rel
            proposals.append({
                "path": path,
                "operation": "modify" if target.is_file() else "create",
                "reason": reason,
                "exists": target.is_file(),
                "score": score,
            })
        return proposals


    def _game_editor_rag_json_pointer_escape(self, value: str) -> str:
        return str(value).replace("~", "~0").replace("/", "~1")

    def _game_editor_rag_json_pointer_get(self, document: Any, pointer: str) -> Any:
        if pointer == "":
            return document
        if not pointer.startswith("/"):
            raise ValueError(f"Invalid JSON pointer {pointer!r}")
        current = document
        for raw_part in pointer.split("/")[1:]:
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, list):
                current = current[int(part)]
            elif isinstance(current, dict):
                if part not in current:
                    raise ValueError(f"Object pointer segment {part!r} is missing in {pointer!r}")
                current = current[part]
            else:
                raise ValueError(f"Pointer {pointer!r} descends into a scalar")
        return current

    def _game_editor_rag_json_pointer_set(self, document: Any, pointer: str, value: Any) -> None:
        if pointer == "":
            raise ValueError("Replacing a full document is not supported in mounted proposal mode.")
        parent_pointer, leaf = pointer.rsplit("/", 1)
        parent = self._game_editor_rag_json_pointer_get(document, parent_pointer)
        key = leaf.replace("~1", "/").replace("~0", "~")
        if isinstance(parent, list):
            parent[int(key)] = value
        elif isinstance(parent, dict):
            if key not in parent:
                raise ValueError(f"Object pointer segment {key!r} is missing in {pointer!r}")
            parent[key] = value
        else:
            raise ValueError(f"Pointer {pointer!r} parent is scalar")

    def _game_editor_rag_sha256_text(self, text: str) -> str:
        return hashlib.sha256(str(text).encode("utf-8")).hexdigest()

    def _game_editor_rag_safe_relpath(self, raw: str) -> str | None:
        text = str(raw or "").replace("\\", "/").strip()
        if not text or text.startswith("/") or Path(text).is_absolute():
            return None
        parts = [part for part in text.split("/") if part and part != "."]
        if not parts or any(part == ".." for part in parts):
            return None
        return "/".join(parts)

    def _game_editor_allowed_editor_source_paths(self) -> list[str]:
        return [
            "main_computer/viewport_routes_game.py",
            "main_computer/web/applications/scripts/game-editor.js",
            "main_computer/web/applications/scripts/chat-console.js",
            "main_computer/web/applications/scripts/dom-bindings/game-editor.js",
            "main_computer/web/applications/scripts/dom-bindings/game-editor-state.js",
            "main_computer/web/applications/styles/game-editor.css",
            "tests/test_viewport_game_editor.py",
        ]

    def _game_editor_rag_path_allowed(self, path: str, evidence: dict[str, Any]) -> bool:
        safe = self._game_editor_rag_safe_relpath(path)
        if not safe:
            return False
        allowed_roots = [str(item) for item in evidence.get("allowed_roots", []) if isinstance(item, str)]
        allowed_paths = {str(item) for item in evidence.get("allowed_editor_source_paths", []) if isinstance(item, str)}
        return any(safe.startswith(root) for root in allowed_roots) or safe in allowed_paths

    def _game_editor_rag_source_files(self, evidence: dict[str, Any]) -> dict[str, str]:
        files: dict[str, str] = {}
        for item in evidence.get("text_files", []):
            if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("content"), str):
                files[item["path"]] = item["content"]
        return files

    def _game_editor_rag_object_record(self, *, project_id: str, scene_index: int, scene: dict[str, Any], object_index: int, obj: dict[str, Any], active_scene_id: str) -> dict[str, Any]:
        allowed_root = f"game_projects/{project_id}/"
        props = obj.get("props") if isinstance(obj.get("props"), dict) else {}
        editable_props = {str(k): v for k, v in props.items() if isinstance(v, (str, int, float, bool)) or v is None}
        editable_json_pointers = {
            f"props.{key}": f"/scenes/{scene_index}/objects/{object_index}/props/{self._game_editor_rag_json_pointer_escape(str(key))}"
            for key in editable_props
        }
        for key in ("x", "y", "width", "height", "rotation", "scale"):
            if key in obj and (isinstance(obj.get(key), (str, int, float, bool)) or obj.get(key) is None):
                editable_json_pointers[key] = f"/scenes/{scene_index}/objects/{object_index}/{self._game_editor_rag_json_pointer_escape(key)}"
        return {
            "record_type": "scene_object",
            "path": allowed_root + "project.json",
            "json_pointer": f"/scenes/{scene_index}/objects/{object_index}",
            "scene": {"id": str(scene.get("id") or ""), "name": str(scene.get("name") or ""), "index": scene_index, "active": str(scene.get("id") or "") == active_scene_id},
            "object": {
                "id": str(obj.get("id") or ""),
                "type": str(obj.get("type") or ""),
                "parentId": str(obj.get("parentId") or ""),
                "x": obj.get("x"),
                "y": obj.get("y"),
                "width": obj.get("width"),
                "height": obj.get("height"),
                "props": props,
            },
            "editable_json_pointers": editable_json_pointers,
        }

    def _game_editor_rag_text_file_record(self, *, path: Path, repo_rel: str, kind: str) -> dict[str, Any] | None:
        try:
            if path.stat().st_size > 80_000:
                return None
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        return {"path": repo_rel, "kind": kind, "suffix": path.suffix.lower(), "bytes": path.stat().st_size, "sha256": self._game_editor_rag_sha256_text(text), "content": text}

    def _game_editor_golden_path_evidence(self, *, project_id: str, context: dict[str, Any]) -> dict[str, Any]:
        repo_root = self.server.debug_root.resolve()
        project_root = self._game_project_root(project_id).resolve()
        allowed_root = f"game_projects/{project_id}/"
        allowed_editor_paths = [path for path in self._game_editor_allowed_editor_source_paths() if (repo_root / path).is_file()]

        project_file = project_root / "project.json"
        project_text = project_file.read_text(encoding="utf-8")
        project = json.loads(project_text)
        scenes = [scene for scene in project.get("scenes", []) if isinstance(scene, dict)] if isinstance(project.get("scenes"), list) else []
        active_scene_id = str(project.get("activeSceneId") or "")
        editable_records: list[dict[str, Any]] = []
        for scene_index, scene in enumerate(scenes):
            objects = scene.get("objects") if isinstance(scene.get("objects"), list) else []
            for object_index, obj in enumerate(objects):
                if isinstance(obj, dict):
                    editable_records.append(self._game_editor_rag_object_record(project_id=project_id, scene_index=scene_index, scene=scene, object_index=object_index, obj=obj, active_scene_id=active_scene_id))
                if len(editable_records) >= 120:
                    break
            if len(editable_records) >= 120:
                break

        text_files: list[dict[str, Any]] = []
        file_inventory: list[dict[str, Any]] = []
        for path in sorted(project_root.rglob("*")):
            if not path.is_file():
                continue
            rel_to_project = path.relative_to(project_root).as_posix()
            repo_rel = allowed_root + rel_to_project
            kind = rel_to_project.split("/", 1)[0] if "/" in rel_to_project else "manifest"
            file_inventory.append({"path": repo_rel, "relative_to_project": rel_to_project, "kind": kind, "suffix": path.suffix.lower(), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            if path.suffix.lower() in {".css", ".html", ".js", ".json", ".md", ".mjs", ".py", ".ts", ".tsx", ".txt", ".yaml", ".yml"}:
                record = self._game_editor_rag_text_file_record(path=path, repo_rel=repo_rel, kind=kind)
                if record:
                    text_files.append(record)

        editor_source_files: list[dict[str, Any]] = []
        for repo_rel in allowed_editor_paths:
            path = repo_root / repo_rel
            kind = "game-editor-server" if repo_rel.endswith("viewport_routes_game.py") else "game-editor-ui"
            record = self._game_editor_rag_text_file_record(path=path, repo_rel=repo_rel, kind=kind)
            if record:
                editor_source_files.append({k: v for k, v in record.items() if k != "content"})
                text_files.append(record)

        return {
            "mode": "game_editor_mount_rag_evidence",
            "app": "game-editor",
            "target_kind": "game-project-plus-editor",
            "project_id": project_id,
            "project_path": allowed_root.rstrip("/"),
            "allowed_root": allowed_root,
            "allowed_roots": [allowed_root],
            "allowed_editor_source_paths": allowed_editor_paths,
            "write_policy": {"mode": "proposal-only", "writes_enabled": False, "auto_apply": False, "server_derived_allowed_roots": True},
            "project_manifest": {"path": allowed_root + "project.json", "sha256": self._game_editor_rag_sha256_text(project_text), "id": str(project.get("id") or project_id), "name": str(project.get("name") or project_id), "description": str(project.get("description") or ""), "version": project.get("version"), "activeSceneId": active_scene_id, "settings": project.get("settings") if isinstance(project.get("settings"), dict) else {}},
            "scenes": context.get("scene_list", []),
            "editable_object_records": editable_records,
            "file_inventory": file_inventory,
            "editor_source_files": editor_source_files,
            "text_files": text_files,
        }

    def _game_editor_golden_path_prompt(self, *, source: str, evidence: dict[str, Any]) -> str:
        contract = {
            "runtime_user_prompt": source,
            "selected_game_editor_evidence": evidence,
            "validation_contract": [
                "Every path is deterministically checked against allowed_roots or allowed_editor_source_paths.",
                "Every json_edit old_value is checked at json_pointer before materialization.",
                "Every text_replacement old_text must occur exactly once in the supplied file content.",
                "The server materializes full replacement files for review and does not modify source files.",
            ],
        }
        return (
            "Game Editor edit proposal mode:\n"
            "Game Editor golden-path RAG proposal mode:\n"
            "Use only the supplied evidence. The evidence includes the active game project plus explicitly allowed Game Editor implementation files.\n"
            "Server-scoped candidate file targets: validated RAG evidence records plus allowed editor source files.\n"
            "You may infer likely targets from object ids, labels, roles, types, parent ids, scene membership, editable props, file names, and literal file content.\n"
            "Do not use hard-coded project-specific synonym rules. Prefer existing editable fields over inventing metadata.\n"
            "Editable object data lives in `project.json` as raw evidence records under scenes[].objects[].props; infer targets from evidence instead of synonym tables.\n"
            "Return JSON only with this shape:\n"
            "{\n"
            '  "ok": true,\n'
            '  "mode": "game_editor_rag_edit_proposal",\n'
            '  "target_kind": "game-project-plus-editor",\n'
            '  "target_id": "<exact project_id>",\n'
            '  "allowed_roots": ["game_projects/<project_id>/"],\n'
            '  "summary": "brief summary",\n'
            '  "grounding": [{"evidence_type": "scene_object|text_file|project_manifest|file_inventory|editor_source", "path": "repo-relative path", "json_pointer": "pointer or empty", "exact_value": "exact old value when relevant", "reason": "why"}],\n'
            '  "json_edits": [{"path": "repo-relative .json path", "json_pointer": "/pointer", "old_value": "exact current value", "new_value": "replacement", "reason": "why"}],\n'
            '  "text_replacements": [{"path": "repo-relative text path", "old_text": "exact substring", "new_text": "replacement substring", "reason": "why"}],\n'
            '  "create_files": [{"path": "repo-relative path inside the game project scripts/ or data/", "content": "complete file content", "reason": "why"}],\n'
            '  "warnings": []\n'
            "}\n"
            "Rules:\n"
            "- Use json_edits for project.json object/scene data when possible.\n"
            "- Use text_replacements for Game Editor implementation changes.\n"
            "- Create files only inside the active game project, preferably scripts/ or data/.\n"
            "- Never include Git operations, apply instructions, or mutation claims.\n\n"
            + json.dumps(contract, indent=2, sort_keys=True)
        )

    def _game_editor_parse_jsonish(self, text: str) -> dict[str, Any]:
        raw = str(text or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("Model response did not contain a JSON object.")
            payload = json.loads(raw[start : end + 1])
        if not isinstance(payload, dict):
            raise ValueError("Model JSON response must be an object.")
        return payload

    def _game_editor_materialize_golden_path_proposal(self, *, evidence: dict[str, Any], proposal: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        issues: list[str] = []
        warnings: list[str] = []
        if proposal.get("ok") is not True:
            issues.append("proposal ok must be true")
        if proposal.get("mode") != "game_editor_rag_edit_proposal":
            issues.append("mode must be game_editor_rag_edit_proposal")
        if proposal.get("target_id") != evidence.get("project_id"):
            issues.append("target_id must equal evidence project_id")
        if proposal.get("target_kind") not in {"game-project-plus-editor", "game-project"}:
            issues.append("target_kind must be game-project-plus-editor")
        for key in ("grounding", "json_edits", "text_replacements", "create_files", "warnings"):
            if key not in proposal:
                issues.append(f"missing key: {key}")
            elif not isinstance(proposal.get(key), list):
                issues.append(f"{key} must be a list")
        if isinstance(proposal.get("warnings"), list):
            warnings.extend(str(item) for item in proposal.get("warnings", []) if str(item).strip())

        source_texts = self._game_editor_rag_source_files(evidence)
        replacements: dict[str, dict[str, Any]] = {}
        staged_json_docs: dict[str, Any] = {}

        for index, edit in enumerate(proposal.get("json_edits", []) if isinstance(proposal.get("json_edits"), list) else []):
            if not isinstance(edit, dict):
                issues.append(f"json_edits[{index}] must be an object")
                continue
            path = self._game_editor_rag_safe_relpath(str(edit.get("path") or ""))
            pointer = str(edit.get("json_pointer") or "")
            if not path or not self._game_editor_rag_path_allowed(path, evidence):
                issues.append(f"json_edits[{index}] path is not allowed: {edit.get('path')!r}")
                continue
            if not path.endswith(".json"):
                issues.append(f"json_edits[{index}] path must be .json: {path}")
                continue
            if path not in source_texts:
                issues.append(f"json_edits[{index}] path is not in text evidence: {path}")
                continue
            if path not in staged_json_docs:
                try:
                    staged_json_docs[path] = json.loads(source_texts[path])
                except json.JSONDecodeError as exc:
                    issues.append(f"json_edits[{index}] target is not valid JSON: {path}: {exc}")
                    continue
            try:
                current = self._game_editor_rag_json_pointer_get(staged_json_docs[path], pointer)
            except Exception as exc:
                issues.append(f"json_edits[{index}] invalid pointer: {exc}")
                continue
            if current != edit.get("old_value"):
                issues.append(f"json_edits[{index}] old_value mismatch at {path}{pointer}: expected {edit.get('old_value')!r}, found {current!r}")
                continue
            try:
                self._game_editor_rag_json_pointer_set(staged_json_docs[path], pointer, edit.get("new_value"))
            except Exception as exc:
                issues.append(f"json_edits[{index}] could not set value: {exc}")

        for path, doc in staged_json_docs.items():
            original = source_texts[path]
            replacement = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
            if replacement != original:
                replacements[path] = {"path": path, "operation": "modify", "original_sha256": self._game_editor_rag_sha256_text(original), "replacement_sha256": self._game_editor_rag_sha256_text(replacement), "replacement_text": replacement}

        for index, item in enumerate(proposal.get("text_replacements", []) if isinstance(proposal.get("text_replacements"), list) else []):
            if not isinstance(item, dict):
                issues.append(f"text_replacements[{index}] must be an object")
                continue
            path = self._game_editor_rag_safe_relpath(str(item.get("path") or ""))
            if not path or not self._game_editor_rag_path_allowed(path, evidence):
                issues.append(f"text_replacements[{index}] path is not allowed: {item.get('path')!r}")
                continue
            old_text = item.get("old_text")
            new_text = item.get("new_text")
            if not isinstance(old_text, str) or not old_text:
                issues.append(f"text_replacements[{index}] old_text must be non-empty")
                continue
            if not isinstance(new_text, str):
                issues.append(f"text_replacements[{index}] new_text must be a string")
                continue
            if path not in source_texts:
                issues.append(f"text_replacements[{index}] path is not in text evidence: {path}")
                continue
            original_source = source_texts[path]
            current_text = replacements[path]["replacement_text"] if path in replacements else original_source
            count = current_text.count(old_text)
            if count != 1:
                issues.append(f"text_replacements[{index}] old_text occurrence count must be 1 in {path}; found {count}")
                continue
            replacement = current_text.replace(old_text, new_text, 1)
            replacements[path] = {"path": path, "operation": "modify", "original_sha256": self._game_editor_rag_sha256_text(original_source), "replacement_sha256": self._game_editor_rag_sha256_text(replacement), "replacement_text": replacement}

        for index, item in enumerate(proposal.get("create_files", []) if isinstance(proposal.get("create_files"), list) else []):
            if not isinstance(item, dict):
                issues.append(f"create_files[{index}] must be an object")
                continue
            path = self._game_editor_rag_safe_relpath(str(item.get("path") or ""))
            if not path or not self._game_editor_rag_path_allowed(path, evidence):
                issues.append(f"create_files[{index}] path is not allowed: {item.get('path')!r}")
                continue
            game_root = str(evidence.get("allowed_root") or "")
            rel_to_game = path.removeprefix(game_root).lstrip("/")
            if not path.startswith(game_root) or not (rel_to_game.startswith("scripts/") or rel_to_game.startswith("data/")):
                issues.append(f"create_files[{index}] creates are limited to active game scripts/ or data/: {path}")
                continue
            if path in source_texts:
                issues.append(f"create_files[{index}] target already exists in evidence: {path}")
                continue
            content = item.get("content")
            if not isinstance(content, str):
                issues.append(f"create_files[{index}] content must be a string")
                continue
            replacements[path] = {"path": path, "operation": "create", "original_sha256": None, "replacement_sha256": self._game_editor_rag_sha256_text(content), "replacement_text": content}

        return sorted(replacements.values(), key=lambda item: item["path"]), {"ok": not issues, "issues": issues, "warnings": warnings}

    def _game_editor_write_golden_path_outputs(self, *, project_id: str, evidence: dict[str, Any], proposal: dict[str, Any], materialized: list[dict[str, Any]], validation: dict[str, Any]) -> dict[str, str]:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        output_dir = self.server.debug_root / "diagnostics_output" / "game_editor_mount_rag_proposals" / f"{project_id}_{stamp}"
        output_dir.mkdir(parents=True, exist_ok=True)
        def write(path: Path, text: str) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        def write_json(path: Path, payload: Any) -> None:
            write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        write_json(output_dir / "evidence.json", evidence)
        write_json(output_dir / "proposal.json", proposal)
        write_json(output_dir / "validation.json", validation)
        source_texts = self._game_editor_rag_source_files(evidence)
        diff_parts: list[str] = []
        manifest_files: list[dict[str, Any]] = []
        for item in materialized:
            rel = str(item["path"])
            write(output_dir / "files" / rel, str(item["replacement_text"]))
            before = source_texts.get(rel, "")
            diff_parts.append("".join(difflib.unified_diff(before.splitlines(keepends=True), str(item["replacement_text"]).splitlines(keepends=True), fromfile=f"a/{rel}", tofile=f"b/{rel}")))
            manifest_files.append({"path": rel, "operation": item["operation"], "original_sha256": item["original_sha256"], "replacement_sha256": item["replacement_sha256"], "payload": f"files/{rel}"})
        write_json(output_dir / "manifest.json", {"mode": "game_editor_mount_rag_proposal", "artifact_type": "proposal_only_full_replacement_payloads", "project_id": project_id, "allowed_root": evidence.get("allowed_root"), "allowed_roots": evidence.get("allowed_roots"), "allowed_editor_source_paths": evidence.get("allowed_editor_source_paths"), "auto_apply": False, "files": manifest_files})
        write(output_dir / "reference.patch", "\n".join(part for part in diff_parts if part))
        return {"output_dir": str(output_dir), "manifest": str(output_dir / "manifest.json"), "reference_patch": str(output_dir / "reference.patch")}

    def _game_editor_rag_auto_apply_requested(self, *, body: dict[str, Any], plugin: dict[str, Any]) -> bool:
        """Return true only when the mounted Game Editor plugin explicitly requests live apply."""
        candidates = [
            body.get("auto_apply"),
            body.get("live_apply"),
            body.get("apply"),
            plugin.get("auto_apply") if isinstance(plugin, dict) else None,
            plugin.get("live_apply") if isinstance(plugin, dict) else None,
        ]
        return any(value is True or str(value).strip().lower() in {"1", "true", "yes", "apply", "live"} for value in candidates)

    def _game_editor_proposal_response(
        self,
        *,
        body: dict[str, Any],
        cell: dict[str, Any],
        source: str,
        project_id: str,
        context: dict[str, Any],
        visible_files: list[str],
        run_id: str,
        thread_id: str,
        scoped_context: str,
    ) -> ChatResponse:
        evidence = self._game_editor_golden_path_evidence(project_id=project_id, context=context)
        golden_context = self._game_editor_golden_path_prompt(source=source, evidence=evidence)
        ai_response = self._game_editor_scoped_ai_response(
            body=body,
            cell=cell,
            source=source,
            project_id=project_id,
            visible_files=visible_files,
            run_id=run_id,
            thread_id=thread_id,
            scoped_context=golden_context,
        )

        requested_auto_apply = self._game_editor_rag_auto_apply_requested(body=body, plugin=self._game_chat_enabled_plugin(body))
        warnings = [
            "The mounted Game Editor route used the golden-path RAG pipeline: evidence, AI proposal, deterministic validation, materialized replacement payloads, and guarded apply when requested.",
        ]
        if requested_auto_apply:
            warnings.append("Auto-apply requested: validated replacements will be written only after deterministic validation passes.")
        else:
            warnings.append("Proposal-only mode: no files were modified.")
        proposal_payload: dict[str, Any] | None = None
        materialized: list[dict[str, Any]] = []
        validation: dict[str, Any] = {"ok": False, "issues": [], "warnings": []}
        outputs: dict[str, str] = {}

        try:
            proposal_payload = self._game_editor_parse_jsonish(ai_response.content)
            materialized, validation = self._game_editor_materialize_golden_path_proposal(evidence=evidence, proposal=proposal_payload)
            outputs = self._game_editor_write_golden_path_outputs(
                project_id=project_id,
                evidence=evidence,
                proposal=proposal_payload,
                materialized=materialized,
                validation=validation,
            )
        except Exception as exc:  # noqa: BLE001 - proposal validation feedback is returned to the UI.
            validation = {"ok": False, "issues": [str(exc)], "warnings": []}
            proposal_payload = None

        if validation.get("warnings"):
            warnings.extend(str(item) for item in validation.get("warnings", []) if str(item).strip())

        proposed_files = [
            {
                "path": str(item.get("path") or ""),
                "operation": str(item.get("operation") or ""),
                "reason": "Validated and materialized from the AI proposal.",
                "exists": item.get("operation") == "modify",
                "original_sha256": item.get("original_sha256"),
                "replacement_sha256": item.get("replacement_sha256"),
            }
            for item in materialized
            if isinstance(item, dict)
        ]

        if proposed_files:
            file_lines = "\n".join(
                f"- `{item['path']}` ({item['operation']}): replacement `{item.get('replacement_sha256')}`"
                for item in proposed_files
            )
        else:
            fallback_candidates = self._game_editor_proposal_candidates(project_id=project_id, source=source, context=context)
            proposed_files = [
                {
                    "path": str(item.get("path") or ""),
                    "operation": str(item.get("operation") or ""),
                    "reason": str(item.get("reason") or "Candidate target from server-side scope heuristics."),
                    "exists": bool(item.get("exists")),
                    "score": item.get("score"),
                }
                for item in fallback_candidates
                if isinstance(item, dict)
            ]
            file_lines = "\n".join(
                f"- `{item['path']}` ({item['operation']}): {item['reason']}"
                for item in fallback_candidates
            ) or "- No scoped file targets were identified."

        proposal = {
            "version": 3,
            "type": "game-editor-file-proposal",
            "mode": "pending-apply" if requested_auto_apply else "proposal-only",
            "rag_backed": True,
            "ai_backed": True,
            "auto_apply": requested_auto_apply,
            "project_id": project_id,
            "allowed_root": evidence.get("allowed_root"),
            "allowed_roots": evidence.get("allowed_roots"),
            "allowed_editor_source_paths": evidence.get("allowed_editor_source_paths"),
            "prompt": source,
            "within_allowed_root": all(self._game_editor_rag_path_allowed(str(item.get("path") or ""), evidence) for item in proposed_files),
            "validation": validation,
            "proposed_files": proposed_files,
            "materialized_files": [
                {
                    "path": item.get("path"),
                    "operation": item.get("operation"),
                    "original_sha256": item.get("original_sha256"),
                    "replacement_sha256": item.get("replacement_sha256"),
                }
                for item in materialized
            ],
            "apply_payloads": [
                {
                    "path": item.get("path"),
                    "operation": item.get("operation"),
                    "original_sha256": item.get("original_sha256"),
                    "replacement_sha256": item.get("replacement_sha256"),
                    "replacement_text": item.get("replacement_text"),
                }
                for item in materialized
            ],
            "outputs": outputs,
            "model_proposal": proposal_payload,
            "warnings": warnings,
            "evidence_summary": {
                "editable_object_records": len(evidence.get("editable_object_records", [])),
                "game_file_inventory": len(evidence.get("file_inventory", [])),
                "editor_source_files": len(evidence.get("editor_source_files", [])),
                "text_files": len(evidence.get("text_files", [])),
            },
            "game_context": self._game_editor_context_summary(context),
        }

        apply_result: dict[str, Any] | None = None
        if requested_auto_apply and validation.get("ok") and proposal.get("apply_payloads"):
            apply_result = self._game_editor_rag_apply_payloads(project_id=project_id, payloads=proposal.get("apply_payloads") if isinstance(proposal.get("apply_payloads"), list) else [])
            proposal["apply_result"] = apply_result
            proposal["mode"] = "applied" if apply_result.get("ok") else "apply-failed"
            warnings.append(
                "Applied validated RAG replacement payloads to the workspace."
                if apply_result.get("ok")
                else "Auto-apply was requested, but the guarded apply step failed; source files were not fully updated."
            )
        elif requested_auto_apply and not validation.get("ok"):
            warnings.append("Auto-apply was requested, but deterministic validation failed; no files were modified.")
        elif requested_auto_apply and not proposal.get("apply_payloads"):
            warnings.append("Auto-apply was requested, but no replacement payloads were materialized; no files were modified.")

        ai_content = str(ai_response.content or "").strip()
        validation_text = "passed" if validation.get("ok") else "failed"
        issue_lines = "\n".join(f"- {issue}" for issue in validation.get("issues", []) if str(issue).strip())
        output_lines = ""
        if outputs:
            output_lines = (
                "\nReview artifacts:\n"
                f"- Manifest: `{outputs.get('manifest')}`\n"
                f"- Reference patch: `{outputs.get('reference_patch')}`\n"
            )

        if apply_result:
            apply_text = "applied" if apply_result.get("ok") else "apply failed"
            apply_lines = "\n".join(
                f"- `{item.get('path')}` ({item.get('operation')}): wrote `{item.get('written_sha256')}`"
                for item in apply_result.get("files", [])
                if isinstance(item, dict)
            ) or "- No files were written."
            heading = (
                "Applied — golden-path RAG wrote validated replacement files.\n\n"
                if apply_result.get("ok")
                else "Apply failed — golden-path RAG did not complete the write.\n\n"
            )
            apply_section = f"Apply result: **{apply_text}**.\n{apply_lines}\n\n"
        else:
            heading = "Proposal only — golden-path RAG; no files were modified.\n\n"
            apply_section = ""
        content = (
            heading
            + f"Deterministic validation: **{validation_text}**.\n\n"
            + apply_section
            + "Validated/materialized file targets for review:\n"
            + f"{file_lines}\n"
            + f"{output_lines}\n"
            + "Review notes:\n"
            + "\n".join(f"- {warning}" for warning in warnings)
        )
        if issue_lines:
            content += f"\n\nValidation issues:\n{issue_lines}"
        if ai_content:
            content += f"\n\nAI structured proposal/raw response:\n```json\n{ai_content}\n```"

        response_metadata = ai_response.metadata if isinstance(ai_response.metadata, dict) else {}
        return ChatResponse(
            content=content,
            provider=ai_response.provider,
            model=ai_response.model,
            metadata={
                **response_metadata,
                "run_id": run_id,
                "thread_id": thread_id,
                "editor_edit_mode": "game-editor",
                "editor_intent": "apply_edit" if apply_result and apply_result.get("ok") else "propose_edit",
                "project_id": project_id,
                "allowed_root": evidence.get("allowed_root"),
                "allowed_roots": evidence.get("allowed_roots"),
                "allowed_editor_source_paths": evidence.get("allowed_editor_source_paths"),
                "visible_files": context.get("visible_files", []),
                "prompt": source,
                "auto_apply": requested_auto_apply,
                "apply_result": apply_result,
                "scope_card": False,
                "proposal": proposal,
                "game_context": self._game_editor_context_summary(context),
            },
        )

    def _game_editor_scoped_ai_response(self, *, body: dict[str, Any], cell: dict[str, Any], source: str, project_id: str, visible_files: list[str], run_id: str, thread_id: str, scoped_context: str) -> ChatResponse:
        log_path = self._chat_console_session_log_path(run_id)
        attachments = self._chat_console_evaluation_attachments(cell.get("attachments") if isinstance(cell.get("attachments"), list) else [])
        append_text_log(
            log_path,
            "route accepted mounted Game Editor AI request",
            run_id=run_id,
            thread_id=thread_id,
            project_id=project_id,
            source_chars=len(source),
            source=source,
            visible_files=visible_files,
            scoped_context_chars=len(scoped_context),
        )
        self.server.activity.record(
            source="game-editor",
            kind="ai",
            time_model="parallel",
            severity="info",
            title="Game Editor scoped AI request queued",
            message=source[:500],
            status="running",
            tags=["ai", "local-ai", "chat-console", "game-editor", "mounted-editor", "subprocess"],
            data={
                "run_id": run_id,
                "thread_id": thread_id,
                "activity_filter": "ai",
                "editor_edit_mode": "game-editor",
                "project_id": project_id,
                "allowed_root": f"game_projects/{project_id}/",
                "visible_files": visible_files,
                "raw_thinking_exposed": False,
                "running_text": "Game Editor scoped AI subprocess queued",
                "rag_type": "game_editor_scoped_chat",
            },
        )
        if _mounted_editor_should_inline_test_provider(getattr(getattr(self.server, "computer", None), "provider", None)):
            inline_source = f"{scoped_context}\n\nUser request:\n{source}"
            if hasattr(self.server.computer, "chat_console_ai"):
                inline_response = self.server.computer.chat_console_ai(inline_source, attachments=attachments)
            else:
                inline_response = self.server.computer.chat(inline_source)
            payload = {
                "response": {
                    "content": inline_response.content,
                    "provider": inline_response.provider,
                    "model": inline_response.model,
                    "metadata": inline_response.metadata,
                }
            }
        else:
            payload = self.server.chat_ai_processes.run(
                command={
                    "mode": "chat_console_ai",
                    "run_id": run_id,
                    "source": source,
                    "attachments": attachments,
                    "config": config_to_payload(self.server.config),
                    "scoped_context": {
                        "label": "game-editor",
                        "text": scoped_context,
                    },
                },
                thread_id=thread_id,
                log_file=log_path,
                activity_bus=self.server.activity,
                cwd=self.server.debug_root,
                max_local_concurrency=1,
            )
        response_payload = payload.get("response") if isinstance(payload.get("response"), dict) else {}
        response = ChatResponse(
            content=str(response_payload.get("content") or ""),
            provider=str(response_payload.get("provider") or getattr(getattr(self.server.computer, "provider", None), "name", "")),
            model=str(response_payload.get("model") or getattr(getattr(self.server.computer, "provider", None), "model", "")),
            metadata=response_payload.get("metadata") if isinstance(response_payload.get("metadata"), dict) else {},
        )
        append_text_log(
            log_path,
            "route completed mounted Game Editor AI request",
            run_id=run_id,
            thread_id=thread_id,
            project_id=project_id,
            response_chars=len(response.content),
            provider=response.provider,
            model=response.model,
        )
        return response


    def _game_editor_rag_repo_path(self, rel_path: str, evidence: dict[str, Any]) -> Path:
        safe = self._game_editor_rag_safe_relpath(rel_path)
        if not safe or not self._game_editor_rag_path_allowed(safe, evidence):
            raise ValueError(f"RAG apply path is not allowed: {rel_path!r}")
        target = (self.server.debug_root / safe).resolve()
        root = self.server.debug_root.resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"RAG apply path escapes workspace: {rel_path!r}") from exc
        return target

    def _game_editor_rag_apply_payloads(self, *, project_id: str, payloads: list[Any]) -> dict[str, Any]:
        context = self._game_editor_context_payload(
            body={},
            plugin={},
            project_id=project_id,
            project_payload=self._game_project_read_payload(project_id),
            files_payload=self._game_files_payload(project_id),
            scripts_payload=self._game_scripts_payload(project_id),
            visible_files=self._game_editor_visible_project_files(project_id),
        )
        evidence = self._game_editor_golden_path_evidence(project_id=project_id, context=context)
        issues: list[str] = []
        warnings: list[str] = []
        written: list[dict[str, Any]] = []

        if not isinstance(payloads, list) or not payloads:
            raise ValueError("payloads must be a non-empty list.")

        for index, item in enumerate(payloads):
            if not isinstance(item, dict):
                issues.append(f"payloads[{index}] must be an object")
                continue
            rel_path = self._game_editor_rag_safe_relpath(str(item.get("path") or ""))
            operation = str(item.get("operation") or "").strip().lower()
            replacement_text = item.get("replacement_text")
            if operation not in {"modify", "create"}:
                issues.append(f"payloads[{index}] operation must be modify or create")
                continue
            if not isinstance(replacement_text, str):
                issues.append(f"payloads[{index}] replacement_text must be a string")
                continue
            try:
                target = self._game_editor_rag_repo_path(rel_path, evidence)
            except Exception as exc:  # noqa: BLE001 - surfaced as validation feedback.
                issues.append(f"payloads[{index}] {exc}")
                continue

            replacement_sha256 = self._game_editor_rag_sha256_text(replacement_text)
            expected_replacement_sha256 = item.get("replacement_sha256")
            if expected_replacement_sha256 and expected_replacement_sha256 != replacement_sha256:
                issues.append(f"payloads[{index}] replacement_sha256 mismatch for {rel_path}")
                continue

            current_exists = target.exists()
            if operation == "modify" and not current_exists:
                issues.append(f"payloads[{index}] cannot modify missing file: {rel_path}")
                continue
            if operation == "create" and current_exists:
                issues.append(f"payloads[{index}] cannot create existing file: {rel_path}")
                continue

            current_sha256 = None
            if current_exists:
                current_bytes = target.read_bytes()
                current_sha256 = hashlib.sha256(current_bytes).hexdigest()
            expected_original_sha256 = item.get("original_sha256")
            if operation == "modify" and expected_original_sha256 != current_sha256:
                issues.append(
                    f"payloads[{index}] original_sha256 mismatch for {rel_path}: "
                    f"expected {expected_original_sha256!r}, found {current_sha256!r}"
                )
                continue
            if operation == "create" and expected_original_sha256 not in {None, ""}:
                issues.append(f"payloads[{index}] create original_sha256 must be null for {rel_path}")
                continue

            if rel_path.endswith(".json"):
                try:
                    json.loads(replacement_text)
                except json.JSONDecodeError as exc:
                    issues.append(f"payloads[{index}] replacement JSON is invalid for {rel_path}: {exc}")
                    continue

            if not issues:
                payload = replacement_text.encode("utf-8")
                self._game_atomic_write(target, payload)
                written.append(
                    {
                        "path": rel_path,
                        "operation": operation,
                        "original_sha256": current_sha256,
                        "replacement_sha256": replacement_sha256,
                        "written_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    }
                )

        ok = not issues
        if ok:
            self.server.signal(
                "api-game-editor-rag-apply",
                project_id=project_id,
                files=[item["path"] for item in written],
                count=len(written),
            )
        return {
            "ok": ok,
            "project_id": project_id,
            "mode": "rag-validated-live-apply",
            "allowed_root": evidence.get("allowed_root"),
            "allowed_roots": evidence.get("allowed_roots"),
            "allowed_editor_source_paths": evidence.get("allowed_editor_source_paths"),
            "files": written,
            "issues": issues,
            "warnings": warnings,
        }

    def _handle_game_editor_rag_apply(self, body: dict[str, Any]) -> None:
        plugin = self._game_chat_enabled_plugin(body)
        self._game_chat_require_game_editor_mount(body)
        project_id = self._game_chat_project_id(body, plugin)
        payloads = body.get("payloads")
        if payloads is None:
            proposal = body.get("proposal") if isinstance(body.get("proposal"), dict) else {}
            payloads = proposal.get("apply_payloads")
        result = self._game_editor_rag_apply_payloads(project_id=project_id, payloads=payloads if isinstance(payloads, list) else [])
        status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
        self._send_json(result, status=status)

    def _handle_game_editor_chat_edit(self, body: dict[str, Any]) -> None:
        cell = body.get("cell") if isinstance(body.get("cell"), dict) else {}
        cell_type, source = validate_evaluation_cell(cell)
        if cell_type != "ai":
            raise ValueError("Game Editor edit chat only accepts AI cells.")
        plugin = self._game_chat_enabled_plugin(body)
        self._game_chat_require_game_editor_mount(body)
        project_id = self._game_chat_project_id(body, plugin)
        project_payload = self._game_project_read_payload(project_id)
        files_payload = self._game_files_payload(project_id)
        scripts_payload = self._game_scripts_payload(project_id)
        visible_files = self._game_editor_visible_project_files(project_id)
        context = self._game_editor_context_payload(
            body=body,
            plugin=plugin,
            project_id=project_id,
            project_payload=project_payload,
            files_payload=files_payload,
            scripts_payload=scripts_payload,
            visible_files=visible_files,
        )
        run_id = str(body.get("run_id") or cell.get("run_id") or f"game_editor_edit_{int(time.time() * 1000)}").strip()
        thread_id = str(body.get("thread_id") or body.get("chat_thread_id") or "game-editor-chat").strip()
        scoped_context = self._game_editor_scoped_chat_context(context=context)
        scope_card = _mounted_editor_scope_query(source)
        proposal_request = _mounted_editor_edit_request(source)
        if scope_card:
            response = self._game_editor_scope_response(
                cell=cell,
                source=source,
                context=context,
                run_id=run_id,
                thread_id=thread_id,
            )
        elif proposal_request:
            response = self._game_editor_proposal_response(
                body=body,
                cell=cell,
                source=source,
                project_id=project_id,
                context=context,
                visible_files=visible_files,
                run_id=run_id,
                thread_id=thread_id,
                scoped_context=scoped_context,
            )
        else:
            response = self._game_editor_scoped_ai_response(
                body=body,
                cell=cell,
                source=source,
                project_id=project_id,
                visible_files=visible_files,
                run_id=run_id,
                thread_id=thread_id,
                scoped_context=scoped_context,
            )
        output_cell = build_output_cell(cell, ai_response_to_parts(response), status="ok", provider=response.provider, model=response.model)
        output_cell.setdefault("metadata", {})
        response_metadata = response.metadata if isinstance(response.metadata, dict) else {}
        editor_intent = str(response_metadata.get("editor_intent") or ("scope" if scope_card else "propose_edit" if proposal_request else "answer"))
        output_cell["metadata"] = {
            **(output_cell.get("metadata") if isinstance(output_cell.get("metadata"), dict) else {}),
            **response_metadata,
            "run_id": run_id,
            "thread_id": thread_id,
            "activity_filter": "ai",
            "editor_edit_mode": "game-editor",
            "editor_intent": editor_intent,
            "project_id": project_id,
            "allowed_root": f"game_projects/{project_id}/",
            "visible_files": visible_files,
            "auto_apply": bool(response_metadata.get("auto_apply", False)),
            "apply_result": response_metadata.get("apply_result") if isinstance(response_metadata.get("apply_result"), dict) else None,
            "scope_card": scope_card,
            "game_context": self._game_editor_context_summary(context),
        }
        self.server.chat_ai_processes.remember_route_result(run_id=run_id, payload={"ok": True, "status": "completed", "output_cell": output_cell, "run_id": run_id, "thread_id": thread_id})
        self.server.signal("api-game-editor-chat-edit", project_id=project_id, prompt_chars=len(source), visible_files=len(visible_files), scope_card=scope_card, proposal_request=proposal_request)
        self._send_json({"ok": True, "status": "completed", "output_cell": output_cell, "run_id": run_id, "thread_id": thread_id})

    def _default_game_project_id(self) -> str:
        return "webgl-demo"

    def _handle_game_project_write(self, body: dict[str, Any]) -> None:
        project_id = str(body.get("project_id", "") or "")
        project_file = self._game_project_root(project_id) / "project.json"
        self._game_require_hash(project_file, str(body.get("expected_content_hash", "") or ""))
        project = body.get("project")
        if not isinstance(project, dict):
            raise ValueError("project must be an object.")
        project.setdefault("id", project_id)
        payload = json.dumps(project, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        self._game_atomic_write(project_file, payload)
        self.server.signal("api-game-project-write", project_id=project_id, bytes=len(payload))
        self._send_json({"ok": True, **self._game_file_shared(project_file), "project_id": project_id})

    def _handle_game_project_create(self, body: dict[str, Any]) -> None:
        project_id = self._game_project_id(str(body.get("project_id") or body.get("id") or body.get("name") or "new-game"))
        root = self._game_project_root(project_id, must_exist=False)
        if root.exists():
            raise ValueError("Game project already exists.")
        self._game_create_project_dirs(root)
        project = self._game_starter_project(project_id, str(body.get("name") or project_id.replace("-", " ").title()))
        project_file = root / "project.json"
        self._game_atomic_write(project_file, json.dumps(project, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
        self.server.signal("api-game-project-create", project_id=project_id)
        self._send_json({"ok": True, "project": project, **self._game_file_shared(project_file)})

    def _handle_game_project_duplicate(self, body: dict[str, Any]) -> None:
        source = self._game_project_root(str(body.get("project_id", "") or ""))
        target_id = self._game_project_id(str(body.get("new_project_id") or body.get("target_project_id") or "copy"))
        target = self._game_project_root(target_id, must_exist=False)
        if target.exists():
            raise ValueError("Target game project already exists.")
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.tmp", "*.bak"))
        project_file = target / "project.json"
        try:
            project = json.loads(project_file.read_text(encoding="utf-8"))
            if isinstance(project, dict):
                project["id"] = target_id
                project["name"] = str(body.get("name") or f"{project.get('name', source.name)} Copy")
                self._game_atomic_write(project_file, json.dumps(project, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
        except Exception:
            pass
        self.server.signal("api-game-project-duplicate", source=source.name, target=target_id)
        self._send_json({"ok": True, "project_id": target_id, **self._game_file_shared(project_file)})

    def _handle_game_project_export(self, body: dict[str, Any]) -> None:
        project_id = str(body.get("project_id", "") or "")
        root = self._game_project_root(project_id)
        with tempfile.TemporaryDirectory() as temp_dir:
            zip_path = Path(temp_dir) / f"{project_id}.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(root).as_posix())
            data = zip_path.read_bytes()
        self.server.signal("api-game-project-export", project_id=project_id, bytes=len(data))
        self._send_json({"ok": True, "project_id": project_id, "filename": f"{project_id}.zip", "content_base64": base64.b64encode(data).decode("ascii"), "content_hash": hashlib.sha256(data).hexdigest(), "bytes": len(data)})

    def _handle_game_project_import(self, body: dict[str, Any]) -> None:
        project_id = self._game_project_id(str(body.get("project_id") or "imported-game"))
        target = self._game_project_root(project_id, must_exist=False)
        if target.exists():
            raise ValueError("Target game project already exists.")
        raw = self._game_decode_bytes(body)
        with tempfile.TemporaryDirectory() as temp_dir:
            zip_path = Path(temp_dir) / "project.zip"
            zip_path.write_bytes(raw)
            with zipfile.ZipFile(zip_path, "r") as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    self._game_safe_relative(info.filename)
                target.mkdir(parents=True)
                archive.extractall(target)
        self._game_create_project_dirs(target)
        if not (target / "project.json").is_file():
            project = self._game_starter_project(project_id, project_id.replace("-", " ").title())
            self._game_atomic_write(target / "project.json", json.dumps(project, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
        self.server.signal("api-game-project-import", project_id=project_id, bytes=len(raw))
        self._send_json({"ok": True, "project_id": project_id, **self._game_file_shared(target / "project.json")})

    def _handle_game_file_read(self, body: dict[str, Any]) -> None:
        path = self._game_file_path(str(body.get("project_id", "") or ""), str(body.get("path", "") or ""))
        mode = str(body.get("mode", "text") or "text")
        data = path.read_bytes()
        payload: dict[str, Any] = {"ok": True, "path": path.relative_to(self._game_project_root(str(body.get("project_id", "") or ""))).as_posix(), **self._game_file_shared(path)}
        if mode == "base64":
            payload["content_base64"] = base64.b64encode(data).decode("ascii")
            payload["encoding"] = "base64"
        else:
            payload["content"] = data.decode("utf-8", errors="replace")
            payload["encoding"] = "text"
        self._send_json(payload)

    def _handle_game_file_write(self, body: dict[str, Any]) -> None:
        path = self._game_file_path(str(body.get("project_id", "") or ""), str(body.get("path", "") or ""), must_exist=False)
        if path.exists():
            self._game_require_hash(path, str(body.get("expected_content_hash", "") or ""))
        raw = self._game_decode_bytes(body)
        self._game_atomic_write(path, raw)
        self.server.signal("api-game-file-write", path=path.name, bytes=len(raw))
        self._send_json({"ok": True, "path": self._game_relative_path(path), **self._game_file_shared(path)})

    def _handle_game_file_delete(self, body: dict[str, Any]) -> None:
        path = self._game_file_path(str(body.get("project_id", "") or ""), str(body.get("path", "") or ""))
        self._game_require_hash(path, str(body.get("expected_content_hash", "") or ""))
        path.unlink()
        self._send_json({"ok": True, "deleted": True, "path": str(body.get("path", "") or "")})

    def _handle_game_file_move(self, body: dict[str, Any]) -> None:
        src = self._game_file_path(str(body.get("project_id", "") or ""), str(body.get("path", "") or ""))
        self._game_require_hash(src, str(body.get("expected_content_hash", "") or ""))
        dst = self._game_file_path(str(body.get("project_id", "") or ""), str(body.get("new_path", "") or ""), must_exist=False)
        if dst.exists():
            raise ValueError("Destination already exists.")
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dst)
        self._send_json({"ok": True, "path": self._game_relative_path(dst), **self._game_file_shared(dst)})

    def _handle_game_asset_upload(self, body: dict[str, Any]) -> None:
        project_id = str(body.get("project_id", "") or "")
        asset_path = str(body.get("path") or body.get("name") or "")
        self._game_safe_relative(asset_path)
        target = self._game_file_path(project_id, f"assets/{asset_path}", must_exist=False)
        raw = self._game_decode_bytes(body)
        if len(raw) > int(body.get("max_bytes") or 50 * 1024 * 1024):
            raise ValueError("Asset exceeds maximum upload size.")
        if target.exists() and not self._coerce_bool(body.get("replace"), default=False):
            raise ValueError("Asset already exists; set replace=true.")
        if target.exists():
            self._game_require_hash(target, str(body.get("expected_content_hash", "") or ""))
        self._game_atomic_write(target, raw)
        self.server.signal("api-game-asset-upload", project_id=project_id, path=self._game_relative_path(target), bytes=len(raw))
        self._send_json({"ok": True, "asset": self._game_asset_payload(target)})

    def _handle_game_asset_delete(self, body: dict[str, Any]) -> None:
        asset_path = str(body.get("path", "") or "")
        self._game_safe_relative(asset_path)
        body["path"] = f"assets/{asset_path}"
        self._handle_game_file_delete(body)

    def _handle_game_asset_move(self, body: dict[str, Any]) -> None:
        asset_path = str(body.get("path", "") or "")
        new_asset_path = str(body.get("new_path", "") or "")
        self._game_safe_relative(asset_path)
        self._game_safe_relative(new_asset_path)
        body["path"] = f"assets/{asset_path}"
        body["new_path"] = f"assets/{new_asset_path}"
        self._handle_game_file_move(body)

    def _handle_game_asset_read(self) -> None:
        try:
            from urllib.parse import parse_qs
            query = parse_qs(urlsplit(self.path).query)
            project_id = str(query.get("project_id", [""])[0] or "")
            asset_path = str(query.get("path", [""])[0] or "")
            path = self._game_file_path(project_id, f"assets/{asset_path}")
            data = path.read_bytes()
            kind = self._game_asset_kind(path)
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if kind not in {"image", "audio", "video", "text"}:
                content_type = "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:
            self.server.signal("api-game-asset-read-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _game_projects_root(self) -> Path:
        root = (self.server.debug_root / "game_projects").resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _game_project_id(self, raw: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(raw or "").strip().lower()).strip("-")
        if not slug or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", slug):
            raise ValueError("project_id must be a safe slug.")
        return slug

    def _game_project_root(self, project_id: str, *, must_exist: bool = True) -> Path:
        root = self._game_projects_root()
        candidate = (root / self._game_project_id(project_id or self._default_game_project_id())).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("Game project must stay inside game_projects.") from exc
        if must_exist and not candidate.is_dir():
            raise ValueError("Game project does not exist.")
        return candidate

    def _game_safe_relative(self, requested: str) -> list[str]:
        raw = str(requested or "").replace("\\", "/").strip()
        if Path(raw).is_absolute() or raw.startswith("/"):
            raise ValueError("Game project paths must be relative.")
        parts = [part for part in raw.split("/") if part and part != "."]
        if not parts:
            raise ValueError("Game project path is required.")
        if any(part == ".." for part in parts):
            raise ValueError("Game project paths may not contain traversal.")
        return parts

    def _game_file_path(self, project_id: str, requested: str, *, must_exist: bool = True) -> Path:
        root = self._game_project_root(project_id)
        parts = self._game_safe_relative(requested)
        if parts[0] not in {"assets", "scripts", "data", "builds", "project.json"}:
            raise ValueError("Game file path must be project.json or inside assets, scripts, data, or builds.")
        if parts[0] == "project.json" and len(parts) != 1:
            raise ValueError("project.json may not have child paths.")
        candidate = (root.joinpath(*parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("Game project paths must stay inside the selected project.") from exc
        if must_exist and not candidate.is_file():
            raise ValueError("Game project file does not exist.")
        return candidate

    def _game_relative_path(self, path: Path) -> str:
        for project in self._game_projects_root().iterdir():
            if project.is_dir():
                try:
                    return path.resolve().relative_to(project.resolve()).as_posix()
                except ValueError:
                    continue
        return path.name

    def _game_create_project_dirs(self, root: Path) -> None:
        for name in ("assets", "scripts", "data", "builds"):
            (root / name).mkdir(parents=True, exist_ok=True)

    def _game_starter_project(self, project_id: str = "webgl-demo", name: str = "Game Surface") -> dict[str, Any]:
        return {'version': 1,
 'id': project_id,
 'name': name,
 'description': 'Phase 4 finale sprite/particle rig starter project for Main Computer.',
 'activeSceneId': 'default-empty-scene',
 'settings': {'targetWidth': 960, 'targetHeight': 540},
 'scenes': [{'id': 'default-empty-scene',
             'name': 'Arcstorm Finale Showcase',
             'version': 5,
             'background': 'radial-gradient(circle at 50% 18%, rgba(56, 189, 248, 0.22), rgba(15, 23, 42, 0.95) 58%, '
                           '#020617 100%)',
             'objects': [{'id': 'hero-sprite',
                          'type': 'sprite-actor',
                          'x': 4,
                          'y': 4,
                          'width': 124,
                          'height': 166,
                          'props': {'label': 'Main Character',
                                    'role': 'player',
                                    'spawn': True,
                                    'color': '#7dd3fc',
                                    'z': 24,
                                    'bob': 12,
                                    'motion': 'stride',
                                    'spellState': 'finale-casting',
                                    'spriteSeries': ['idle', 'charge', 'cast', 'release', 'echo', 'recover'],
                                    'spriteRig': {'style': 'energy-silhouette',
                                                  'layers': ['shadow',
                                                             'aura',
                                                             'afterimage',
                                                             'core',
                                                             'mantle',
                                                             'cast-flare',
                                                             'weapon-trail',
                                                             'spell-wings',
                                                             'sparkles'],
                                                  'castFrames': ['idle',
                                                                 'charge',
                                                                 'cast',
                                                                 'release',
                                                                 'echo',
                                                                 'recover'],
                                                  'finisher': True}}},
                         {'id': 'hero-spell-aura',
                          'type': 'particle-emitter',
                          'parentId': 'hero-sprite',
                          'x': 0,
                          'y': 0,
                          'width': 210,
                          'height': 168,
                          'props': {'label': 'Hero Spell Swirl',
                                    'role': 'spell',
                                    'color': '#facc15',
                                    'particleCount': 72,
                                    'particleSize': 4,
                                    'spread': 1.18,
                                    'motion': 'spell-swirl',
                                    'orbitRadius': 70,
                                    'verticalLift': 64,
                                    'zOffset': 78}},
                         {'id': 'hero-rune-ring',
                          'type': 'particle-emitter',
                          'parentId': 'hero-sprite',
                          'x': 0,
                          'y': 0,
                          'width': 180,
                          'height': 92,
                          'props': {'label': 'Casting Rune Ring',
                                    'role': 'spell',
                                    'color': '#67e8f9',
                                    'particleCount': 44,
                                    'particleSize': 3,
                                    'spread': 1.04,
                                    'motion': 'rune-ring',
                                    'orbitRadius': 80,
                                    'verticalLift': 20,
                                    'zOffset': 12}},
                         {'id': 'arcstorm-nova',
                          'type': 'particle-emitter',
                          'parentId': 'hero-sprite',
                          'x': 0,
                          'y': 0,
                          'width': 260,
                          'height': 170,
                          'props': {'label': 'Arcstorm Nova',
                                    'role': 'finisher',
                                    'color': '#fef3c7',
                                    'particleCount': 84,
                                    'particleSize': 4,
                                    'spread': 1.32,
                                    'motion': 'nova-ring',
                                    'orbitRadius': 96,
                                    'verticalLift': 28,
                                    'zOffset': 36,
                                    'pulseDelay': 1080}},
                         {'id': 'finish-shockwave',
                          'type': 'particle-emitter',
                          'parentId': 'hero-sprite',
                          'x': 0,
                          'y': 0,
                          'width': 320,
                          'height': 110,
                          'props': {'label': 'Ground Shockwave',
                                    'role': 'finisher',
                                    'color': '#93c5fd',
                                    'particleCount': 64,
                                    'particleSize': 3,
                                    'spread': 1.4,
                                    'motion': 'shockwave-ring',
                                    'orbitRadius': 112,
                                    'verticalLift': 8,
                                    'zOffset': 4,
                                    'pulseDelay': 1260}},
                         {'id': 'ruin-scout',
                          'type': 'sprite-actor',
                          'x': 7,
                          'y': 3,
                          'width': 108,
                          'height': 148,
                          'props': {'label': 'Ruin Scout',
                                    'color': '#c084fc',
                                    'z': 14,
                                    'bob': 7,
                                    'motion': 'glide',
                                    'spellState': 'staggered',
                                    'spriteSeries': ['watch', 'brace', 'hit', 'fracture', 'recover'],
                                    'spriteRig': {'style': 'void-silhouette',
                                                  'layers': ['shadow',
                                                             'aura',
                                                             'afterimage',
                                                             'core',
                                                             'mantle',
                                                             'hit-flash',
                                                             'sparkles'],
                                                  'castFrames': ['watch', 'brace', 'hit', 'fracture', 'recover']}}},
                         {'id': 'ruin-curse',
                          'type': 'particle-emitter',
                          'parentId': 'ruin-scout',
                          'x': 0,
                          'y': 0,
                          'width': 145,
                          'height': 126,
                          'props': {'label': 'Void Curse Swirl',
                                    'role': 'spell',
                                    'color': '#c084fc',
                                    'particleCount': 44,
                                    'particleSize': 4,
                                    'spread': 1.05,
                                    'motion': 'spell-swirl',
                                    'orbitRadius': 52,
                                    'verticalLift': 44,
                                    'zOffset': 62}},
                         {'id': 'echo-wraith',
                          'type': 'sprite-actor',
                          'x': 2,
                          'y': 7,
                          'width': 104,
                          'height': 142,
                          'props': {'label': 'Echo Wraith',
                                    'color': '#38bdf8',
                                    'z': 12,
                                    'bob': 8,
                                    'motion': 'phase',
                                    'spellState': 'linked',
                                    'spriteSeries': ['materialize', 'aim', 'bind', 'shatter'],
                                    'spriteRig': {'style': 'echo-silhouette',
                                                  'layers': ['shadow',
                                                             'aura',
                                                             'afterimage',
                                                             'core',
                                                             'mantle',
                                                             'hit-flash',
                                                             'sparkles'],
                                                  'castFrames': ['materialize', 'aim', 'bind', 'shatter']}}},
                         {'id': 'wraith-curse',
                          'type': 'particle-emitter',
                          'parentId': 'echo-wraith',
                          'x': 0,
                          'y': 0,
                          'width': 140,
                          'height': 118,
                          'props': {'label': 'Echo Curse Swirl',
                                    'role': 'spell',
                                    'color': '#38bdf8',
                                    'particleCount': 38,
                                    'particleSize': 3,
                                    'spread': 1.05,
                                    'motion': 'spell-swirl',
                                    'orbitRadius': 50,
                                    'verticalLift': 38,
                                    'zOffset': 58}},
                         {'id': 'hero-arc-bolt',
                          'type': 'particle-emitter',
                          'parentId': 'hero-sprite',
                          'x': 0,
                          'y': 0,
                          'width': 260,
                          'height': 72,
                          'props': {'label': 'Hero Arc Bolt',
                                    'role': 'projectile',
                                    'color': '#fde68a',
                                    'particleCount': 46,
                                    'particleSize': 4,
                                    'spread': 1,
                                    'motion': 'spell-bolt',
                                    'sourceId': 'hero-sprite',
                                    'targetId': 'ruin-scout',
                                    'sourceZOffset': 94,
                                    'targetZOffset': 68,
                                    'zOffset': 84,
                                    'pulseDelay': 420}},
                         {'id': 'hero-chain-bolt',
                          'type': 'particle-emitter',
                          'parentId': 'hero-sprite',
                          'x': 0,
                          'y': 0,
                          'width': 300,
                          'height': 74,
                          'props': {'label': 'Chain Bolt',
                                    'role': 'projectile',
                                    'color': '#bfdbfe',
                                    'particleCount': 42,
                                    'particleSize': 4,
                                    'spread': 0.95,
                                    'motion': 'spell-bolt',
                                    'sourceId': 'hero-sprite',
                                    'targetId': 'echo-wraith',
                                    'sourceZOffset': 86,
                                    'targetZOffset': 64,
                                    'zOffset': 78,
                                    'pulseDelay': 860}},
                         {'id': 'ruin-impact-burst',
                          'type': 'particle-emitter',
                          'parentId': 'ruin-scout',
                          'x': 0,
                          'y': 0,
                          'width': 182,
                          'height': 142,
                          'props': {'label': 'Impact Burst',
                                    'role': 'impact',
                                    'color': '#fb7185',
                                    'particleCount': 62,
                                    'particleSize': 4,
                                    'spread': 1.22,
                                    'motion': 'impact-burst',
                                    'orbitRadius': 60,
                                    'verticalLift': 44,
                                    'zOffset': 72,
                                    'pulseDelay': 760}},
                         {'id': 'wraith-impact-burst',
                          'type': 'particle-emitter',
                          'parentId': 'echo-wraith',
                          'x': 0,
                          'y': 0,
                          'width': 164,
                          'height': 132,
                          'props': {'label': 'Echo Impact Burst',
                                    'role': 'impact',
                                    'color': '#60a5fa',
                                    'particleCount': 50,
                                    'particleSize': 3,
                                    'spread': 1.18,
                                    'motion': 'impact-burst',
                                    'orbitRadius': 54,
                                    'verticalLift': 38,
                                    'zOffset': 68,
                                    'pulseDelay': 1040}},
                         {'id': 'sky-rune-fall',
                          'type': 'particle-emitter',
                          'x': 5,
                          'y': 2,
                          'width': 640,
                          'height': 280,
                          'props': {'label': 'Sky Rune Fall',
                                    'role': 'arena',
                                    'color': '#e0f2fe',
                                    'particleCount': 72,
                                    'particleSize': 3,
                                    'spread': 1.15,
                                    'motion': 'starfall',
                                    'z': 132,
                                    'verticalLift': 130,
                                    'pulseDelay': 640}},
                         {'id': 'leyline-current',
                          'type': 'particle-emitter',
                          'x': 4,
                          'y': 7,
                          'width': 500,
                          'height': 96,
                          'props': {'label': 'Leyline Current',
                                    'color': '#34d399',
                                    'particleCount': 56,
                                    'particleSize': 3,
                                    'spread': 1.35,
                                    'motion': 'stream',
                                    'z': 4}}],
             'metadata': {'starter': True,
                          'projection': 'isometric',
                          'tileWidth': 92,
                          'tileHeight': 46,
                          'originX': 480,
                          'originY': 124,
                          'particleOnly': False,
                          'includesDefaultPlayer': True,
                          'isometric': True,
                          'rolloutPhase': 'phase-4-finale-showcase',
                          'characterModel': 'sprite-particle-rig',
                          'meshActorsEnabled': False,
                          'controls': {'movement': 'left-click', 'keyboardMovement': False, 'clickToMove': True, 'movementActorId': 'hero-sprite'},
                          'movementBounds': {'minX': 0, 'maxX': 10, 'minY': 0, 'maxY': 10},
                'vfx': {'particleMultiplier': 2, 'effectMultiplier': 2, 'maxParticlesPerEmitter': 440},
                'quadrupleParticles': False,
                'uiParticleControls': True,
                          'parentedParticles': True,
                          'linkedSpellProjectiles': True,
                          'targetedParticles': True,
                          'finaleShowcase': True,
                          'choreography': {'title': 'Arcstorm Finale',
                                           'durationMs': 6400,
                                           'cameraPulse': True,
                                           'beats': [{'label': 'Charge', 'timeMs': 0, 'cue': 'hero-spell-aura'},
                                                     {'label': 'Bind', 'timeMs': 1200, 'cue': 'hero-chain-bolt'},
                                                     {'label': 'Release', 'timeMs': 2400, 'cue': 'hero-arc-bolt'},
                                                     {'label': 'Nova', 'timeMs': 3600, 'cue': 'arcstorm-nova'},
                                                     {'label': 'Aftershock',
                                                      'timeMs': 5000,
                                                      'cue': 'finish-shockwave'}]}}}],
 'assets': [],
 'scripts': [],
 'metadata': {'createdBy': 'Main Computer Scene Store', 'revision': 8, 'phase': 'phase-4-finale-showcase'}}

    def _game_hash(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _game_file_shared(self, path: Path) -> dict[str, Any]:
        stat = path.stat()
        return {"content_hash": self._game_hash(path), "mtime": stat.st_mtime, "bytes": stat.st_size}

    def _game_require_hash(self, path: Path, expected: str) -> None:
        if not expected:
            raise GameEditorConflict("expected_content_hash is required.")
        if expected != self._game_hash(path):
            raise GameEditorConflict("expected_content_hash is stale.")

    def _game_atomic_write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)

    def _game_decode_bytes(self, body: dict[str, Any]) -> bytes:
        if "content_base64" in body:
            return base64.b64decode(str(body.get("content_base64") or ""), validate=True)
        return str(body.get("content", "") or "").encode("utf-8")

    def _game_project_payload(self, root: Path) -> dict[str, Any]:
        project_file = root / "project.json"
        name = root.name
        if project_file.is_file():
            try:
                data = json.loads(project_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    name = str(data.get("name") or name)
            except Exception:
                pass
        payload: dict[str, Any] = {"id": root.name, "name": name, "path": f"game_projects/{root.name}"}
        if project_file.is_file():
            payload.update(self._game_file_shared(project_file))
        return payload

    def _game_projects_payload(self) -> dict[str, Any]:
        root = self._game_projects_root()
        projects = [self._game_project_payload(path) for path in sorted(root.iterdir(), key=lambda item: (item.name != self._default_game_project_id(), item.name.lower())) if path.is_dir() and (path / "project.json").is_file()]
        for project in projects:
            project["default"] = project.get("id") == self._default_game_project_id()
        return {"ok": True, "root": "game_projects", "projects": projects, "count": len(projects)}

    def _game_project_read_payload(self, project_id: str) -> dict[str, Any]:
        root = self._game_project_root(project_id)
        project_file = root / "project.json"
        project = json.loads(project_file.read_text(encoding="utf-8"))
        return {"ok": True, "project_id": root.name, "project": project, **self._game_file_shared(project_file)}

    def _game_asset_kind(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}:
            return "image"
        if suffix in {".mp3", ".wav", ".ogg", ".flac"}:
            return "audio"
        if suffix in {".mp4", ".webm", ".mov"}:
            return "video"
        if suffix in {".txt", ".md", ".json", ".csv", ".xml", ".html", ".css"}:
            return "text"
        if suffix in {".js", ".ts", ".py", ".lua"}:
            return "script"
        if suffix in {".glsl", ".vert", ".frag", ".wgsl"}:
            return "shader"
        if suffix in {".zip", ".tar", ".gz", ".7z"}:
            return "archive"
        if suffix in {".obj", ".gltf", ".glb", ".fbx"}:
            return "model"
        if suffix in {".ttf", ".otf", ".woff", ".woff2"}:
            return "font"
        return "binary" if suffix in {".exe", ".bin", ""} else "unknown"

    def _game_asset_payload(self, path: Path) -> dict[str, Any]:
        rel = self._game_relative_path(path)
        project_id = path.resolve().relative_to(self._game_projects_root()).parts[0]
        kind = self._game_asset_kind(path)
        asset_rel = rel.removeprefix("assets/")
        return {"name": path.name, "path": asset_rel, "extension": path.suffix, "kind": kind, "preview_supported": kind in {"image", "audio", "video", "text", "script", "shader"}, "url": f"/api/applications/game-editor/asset/read?project_id={project_id}&path={asset_rel}", **self._game_file_shared(path)}

    def _game_files_payload(self, project_id: str) -> dict[str, Any]:
        root = self._game_project_root(project_id)
        files = []
        for folder in ("assets", "scripts", "data", "builds"):
            for path in sorted((root / folder).rglob("*")):
                if path.is_file():
                    files.append({"path": path.relative_to(root).as_posix(), "kind": folder.rstrip("s"), **self._game_file_shared(path)})
        return {"ok": True, "project_id": root.name, "files": files, "count": len(files)}

    def _game_assets_payload(self, project_id: str) -> dict[str, Any]:
        root = self._game_project_root(project_id)
        assets = [self._game_asset_payload(path) for path in sorted((root / "assets").rglob("*")) if path.is_file()]
        return {"ok": True, "project_id": root.name, "assets": assets, "count": len(assets)}

    def _game_scripts_payload(self, project_id: str) -> dict[str, Any]:
        root = self._game_project_root(project_id)
        scripts = [{"path": path.relative_to(root / "scripts").as_posix(), **self._game_file_shared(path)} for path in sorted((root / "scripts").rglob("*")) if path.is_file()]
        return {"ok": True, "project_id": root.name, "scripts": scripts, "count": len(scripts)}
