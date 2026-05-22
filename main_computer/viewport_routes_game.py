from __future__ import annotations

from main_computer.viewport_state import *  # noqa: F401,F403

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
        player_sprite = {
            "id": "hero-sprite",
            "type": "sprite-actor",
            "x": 4,
            "y": 4,
            "width": 104,
            "height": 144,
            "props": {
                "label": "Main Character",
                "role": "player",
                "spawn": True,
                "color": "#7dd3fc",
                "z": 18,
                "bob": 10,
                "motion": "stride",
                "spriteSeries": ["idle-nw", "step-left", "idle-ne", "step-right"],
            },
        }
        sprite_support_aura = {
            "id": "hero-aura",
            "type": "particle-emitter",
            "x": 4,
            "y": 4,
            "width": 120,
            "height": 96,
            "props": {
                "label": "Arc Halo",
                "role": "support",
                "color": "#facc15",
                "particleCount": 30,
                "particleSize": 4,
                "spread": 0.9,
                "motion": "orbit",
                "z": 44,
            },
        }
        rival_sprite = {
            "id": "ruin-scout",
            "type": "sprite-actor",
            "x": 7,
            "y": 3,
            "width": 96,
            "height": 128,
            "props": {
                "label": "Ruin Scout",
                "color": "#c084fc",
                "z": 12,
                "bob": 6,
                "motion": "glide",
                "spriteSeries": ["watch", "lean", "ready", "dash"],
            },
        }
        starter_scene = {
            "id": "default-empty-scene",
            "name": "Isometric Battle Floor",
            "version": 2,
            "background": "radial-gradient(circle at 50% 24%, rgba(56, 189, 248, 0.16), rgba(15, 23, 42, 0.92) 55%, #020617 100%)",
            "objects": [player_sprite, sprite_support_aura, rival_sprite],
            "metadata": {
                "starter": True,
                "projection": "isometric",
                "tileWidth": 92,
                "tileHeight": 46,
                "originX": 480,
                "originY": 124,
                "particleOnly": False,
                "includesDefaultPlayer": True,
                "isometric": True,
            },
        }
        return {
            "version": 1,
            "id": project_id,
            "name": name,
            "description": "Isometric sprite-and-particle starter project for Main Computer.",
            "activeSceneId": "default-empty-scene",
            "settings": {"targetWidth": 960, "targetHeight": 540},
            "scenes": [starter_scene],
            "assets": [],
            "scripts": [],
            "metadata": {"createdBy": "Main Computer Scene Store", "revision": 5},
        }

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
