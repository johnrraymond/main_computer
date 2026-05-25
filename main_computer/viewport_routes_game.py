from __future__ import annotations

from main_computer.viewport_state import *  # noqa: F401,F403
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

    def _game_editor_scoped_chat_context(self, *, project_id: str, project_payload: dict[str, Any], files_payload: dict[str, Any], scripts_payload: dict[str, Any], visible_files: list[str]) -> str:
        file_lines = "\n".join(f"- `{path}`" for path in visible_files) or "- No files are present in this game project yet."
        active_scene = str(project_payload.get("project", {}).get("activeSceneId", "") or "")
        return (
            "You are answering inside the mounted Game Editor chat.\n"
            f"You are scoped ONLY to the active game project `{project_id}`.\n"
            f"Allowed root: `game_projects/{project_id}/`.\n"
            "Do not claim access to repo files such as `main_computer/`, tests, tools, or other projects.\n"
            "Do not propose or imply writes outside the allowed root.\n"
            "This phase is proposal-only: no files may be modified.\n\n"
            "Visible game-project files:\n"
            f"{file_lines}\n\n"
            f"Active scene: `{active_scene}`.\n"
            f"Project file count: {len(visible_files)} visible files; {scripts_payload.get('count', 0)} scripts; {files_payload.get('count', 0)} project data/assets/build files.\n"
        )

    def _game_editor_scope_response(self, *, cell: dict[str, Any], source: str, project_id: str, project_payload: dict[str, Any], files_payload: dict[str, Any], scripts_payload: dict[str, Any], visible_files: list[str], run_id: str, thread_id: str) -> ChatResponse:
        file_lines = "\n".join(f"- `{path}`" for path in visible_files) or "- No files are present in this game project yet."
        content = (
            f"I am scoped to the active Game Editor project `{project_id}` only.\n\n"
            "Visible game-project files:\n"
            f"{file_lines}\n\n"
            "Scope lock:\n"
            f"- Allowed root: `game_projects/{project_id}/`\n"
            "- Server-derived write policy: proposal-only; no files were modified.\n"
            "- Repo files such as `main_computer/`, tests, tools, and other projects are outside this mounted editor context.\n\n"
            f"Active scene: `{project_payload.get('project', {}).get('activeSceneId', '')}`\n"
            f"Project file count: {len(visible_files)} visible files; {scripts_payload.get('count', 0)} scripts; {files_payload.get('count', 0)} project data/assets/build files.\n\n"
            "For ordinary questions, this mounted route now runs the AI with this scoped context instead of returning this static scope card."
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
                "allowed_root": f"game_projects/{project_id}/",
                "visible_files": visible_files,
                "prompt": source,
                "auto_apply": False,
                "scope_card": True,
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
        run_id = str(body.get("run_id") or cell.get("run_id") or f"game_editor_edit_{int(time.time() * 1000)}").strip()
        thread_id = str(body.get("thread_id") or body.get("chat_thread_id") or "game-editor-chat").strip()
        scoped_context = self._game_editor_scoped_chat_context(
            project_id=project_id,
            project_payload=project_payload,
            files_payload=files_payload,
            scripts_payload=scripts_payload,
            visible_files=visible_files,
        )
        if _mounted_editor_scope_query(source):
            response = self._game_editor_scope_response(
                cell=cell,
                source=source,
                project_id=project_id,
                project_payload=project_payload,
                files_payload=files_payload,
                scripts_payload=scripts_payload,
                visible_files=visible_files,
                run_id=run_id,
                thread_id=thread_id,
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
        output_cell["metadata"] = {
            **(output_cell.get("metadata") if isinstance(output_cell.get("metadata"), dict) else {}),
            "run_id": run_id,
            "thread_id": thread_id,
            "activity_filter": "ai",
            "editor_edit_mode": "game-editor",
            "project_id": project_id,
            "allowed_root": f"game_projects/{project_id}/",
            "visible_files": visible_files,
            "auto_apply": False,
            "scope_card": _mounted_editor_scope_query(source),
        }
        self.server.chat_ai_processes.remember_route_result(run_id=run_id, payload={"ok": True, "status": "completed", "output_cell": output_cell, "run_id": run_id, "thread_id": thread_id})
        self.server.signal("api-game-editor-chat-edit", project_id=project_id, prompt_chars=len(source), visible_files=len(visible_files), scope_card=_mounted_editor_scope_query(source))
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
