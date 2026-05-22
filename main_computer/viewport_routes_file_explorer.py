from __future__ import annotations

from main_computer.viewport_state import *  # noqa: F401,F403

class ViewportFileExplorerRoutesMixin:
    def _handle_file_explorer_roots(self) -> None:
        try:
            self._read_json()
            roots = self._file_explorer_roots()
            self.server.signal("api-file-explorer-roots", count=len(roots))
            self._send_json({"ok": True, "roots": roots, "count": len(roots), "read_only": True})
        except Exception as exc:
            self.server.signal("api-file-explorer-error", route="roots", error=exc)
            self._send_json({"ok": False, "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_file_explorer_list(self) -> None:
        try:
            body = self._read_json()
            root_id = str(body.get("root_id", "") or "")
            directory = self._file_explorer_resolve(root_id, str(body.get("relative_path", "") or ""), must_exist=True)
            if not directory.is_dir():
                raise ValueError("Selected path is not a directory.")
            entries: list[dict[str, Any]] = []
            for child in directory.iterdir():
                try:
                    entries.append(self._file_explorer_entry_payload(child, root_id))
                except OSError:
                    continue
            entries.sort(key=lambda item: (0 if item["kind"] == "directory" else 1, str(item["name"]).lower()))
            root = self._file_explorer_root_path(root_id)
            relative_path = directory.relative_to(root).as_posix() if directory != root else ""
            self.server.signal("api-file-explorer-list", root_id=root_id, path=relative_path, count=len(entries))
            self._send_json({"ok": True, "root_id": root_id, "relative_path": relative_path, "entries": entries, "count": len(entries), "read_only": True})
        except Exception as exc:
            self.server.signal("api-file-explorer-error", route="list", error=exc)
            self._send_json({"ok": False, "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_file_explorer_read(self) -> None:
        try:
            body = self._read_json()
            root_id = str(body.get("root_id", "") or "")
            path = self._file_explorer_resolve(root_id, str(body.get("relative_path", "") or ""), must_exist=True)
            payload = self._file_explorer_entry_payload(path, root_id)
            if path.is_dir():
                raise ValueError("Directories cannot be read.")
            stat = path.stat()
            if stat.st_size > 512 * 1024:
                self._send_json({"ok": True, "readable": False, "reason": "file too large for preview", "entry": payload, "read_only": True})
                return
            data = path.read_bytes()
            if b"\x00" in data[:4096]:
                self._send_json({"ok": True, "readable": False, "reason": "binary file preview is disabled", "entry": payload, "read_only": True})
                return
            content = data.decode("utf-8", errors="replace")
            self.server.signal("api-file-explorer-read", root_id=root_id, path=payload["relative_path"], bytes=stat.st_size)
            self._send_json({"ok": True, "readable": True, "entry": payload, "content": content, "encoding": "utf-8", "read_only": True})
        except Exception as exc:
            self.server.signal("api-file-explorer-error", route="read", error=exc)
            self._send_json({"ok": False, "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_file_explorer_search(self) -> None:
        try:
            body = self._read_json()
            root_id = str(body.get("root_id", "") or "")
            query = str(body.get("query", "") or "").strip().lower()
            limit = max(1, min(int(body.get("limit", 50) or 50), 200))
            start = self._file_explorer_resolve(root_id, str(body.get("relative_path", "") or ""), must_exist=True)
            if not start.is_dir():
                raise ValueError("Search path must be a directory.")
            if not query:
                raise ValueError("Search query is required.")
            results: list[dict[str, Any]] = []
            scanned = 0
            for directory, dirnames, filenames in os.walk(start):
                scanned += 1
                if scanned > 200:
                    break
                current = Path(directory)
                kept_dirnames = []
                for dirname in dirnames:
                    try:
                        child = current / dirname
                        child.stat()
                        kept_dirnames.append(dirname)
                        if query in dirname.lower() and len(results) < limit:
                            results.append(self._file_explorer_entry_payload(child, root_id))
                    except OSError:
                        continue
                dirnames[:] = kept_dirnames
                for filename in filenames:
                    if len(results) >= limit:
                        break
                    if query not in filename.lower():
                        continue
                    try:
                        results.append(self._file_explorer_entry_payload(current / filename, root_id))
                    except OSError:
                        continue
                if len(results) >= limit:
                    break
            results.sort(key=lambda item: (0 if item["kind"] == "directory" else 1, str(item["name"]).lower()))
            self.server.signal("api-file-explorer-search", root_id=root_id, results=len(results))
            self._send_json({"ok": True, "root_id": root_id, "query": query, "results": results, "count": len(results), "read_only": True})
        except Exception as exc:
            self.server.signal("api-file-explorer-error", route="search", error=exc)
            self._send_json({"ok": False, "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_path_mounts(self) -> None:
        resolver = self.server.mounted_windows_path_resolver
        self.server.signal("api-path-mounts", enabled=resolver.enabled, count=len(resolver.mounts))
        self._send_json(resolver.status())

    def _file_explorer_root_candidates(self) -> dict[str, Path]:
        candidates: dict[str, Path] = {
            "workspace": self.server.config.workspace,
            "debug-root": self.server.debug_root,
            "cwd": Path.cwd(),
            "home": Path.home(),
        }
        workspace_parent = self.server.config.workspace.parent
        candidates["workspace-parent"] = workspace_parent
        if self.server.debug_root.parent != self.server.debug_root:
            candidates["debug-root-parent"] = self.server.debug_root.parent

        resolver = self.server.mounted_windows_path_resolver
        if resolver.enabled:
            candidates.update(resolver.root_candidates(available_only=True))
        elif os.name == "nt":
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                drive = Path(f"{letter}:/")
                if drive.exists():
                    candidates[f"drive-{letter.lower()}"] = drive
        else:
            candidates["filesystem-root"] = Path("/")
        return candidates

    def _file_explorer_roots(self) -> list[dict[str, Any]]:
        roots: list[dict[str, Any]] = []
        seen: set[Path] = set()
        for root_id, path in self._file_explorer_root_candidates().items():
            try:
                resolved = path.resolve()
                if resolved in seen or not resolved.exists() or not resolved.is_dir():
                    continue
                seen.add(resolved)
                mounted_metadata = self._file_explorer_mounted_root_metadata(root_id)
                roots.append(
                    {
                        "id": root_id,
                        "label": mounted_metadata.get("label", root_id.replace("-", " ").title()),
                        "path_display": mounted_metadata.get("path_display", str(resolved)),
                        "main_computer_purview": self._file_explorer_is_main_computer_purview(resolved),
                        "mounted_windows_drive": bool(mounted_metadata),
                    }
                )
            except OSError:
                continue
        return roots

    def _file_explorer_root_path(self, root_id: str) -> Path:
        resolver = self.server.mounted_windows_path_resolver
        if resolver.enabled and resolver.is_mounted_root(root_id):
            return resolver.resolve(root_id, "", must_exist=True)
        candidates = self._file_explorer_root_candidates()
        if root_id not in candidates:
            raise ValueError("Unknown file explorer root.")
        root = candidates[root_id].resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError("File explorer root is unavailable.")
        return root

    def _file_explorer_resolve(self, root_id: str, relative_path: str, must_exist: bool = True) -> Path:
        resolver = self.server.mounted_windows_path_resolver
        if resolver.enabled and resolver.is_mounted_root(root_id):
            return resolver.resolve(root_id, relative_path, must_exist=must_exist)

        root = self._file_explorer_root_path(root_id)
        raw_path = str(relative_path or "").replace("\\", "/").strip("/")
        parts = [part for part in raw_path.split("/") if part and part != "."]
        if any(part == ".." for part in parts):
            raise ValueError("Path traversal is not allowed.")
        candidate = (root / Path(*parts)).resolve() if parts else root
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("Path escapes selected root.") from exc
        if must_exist and not candidate.exists():
            raise ValueError("Path not found.")
        return candidate

    def _file_explorer_entry_payload(self, path: Path, root_id: str) -> dict[str, Any]:
        relative_path = self._file_explorer_relative_path(path, root_id)
        try:
            stat = path.stat()
        except OSError:
            stat = None
        kind = "directory" if path.is_dir() else "symlink" if path.is_symlink() else "file" if path.is_file() else "other"
        category, suggested_app = self._file_explorer_category(path, relative_path, kind)
        main_purview = self._file_explorer_is_main_computer_purview(path) or category in {"code", "text", "spreadsheet", "game", "asset"}
        mounted_metadata = self._file_explorer_mounted_root_metadata(root_id)
        if mounted_metadata:
            path_display = self.server.mounted_windows_path_resolver.display_path(root_id, relative_path)
        else:
            path_display = f"{root_id}:/{relative_path}" if relative_path else f"{root_id}:/"
        return {
            "kind": kind,
            "name": path.name or str(path),
            "path_display": path_display,
            "relative_path": relative_path,
            "extension": path.suffix.lower(),
            "bytes": stat.st_size if stat and path.is_file() else 0,
            "mtime": stat.st_mtime if stat else 0.0,
            "category": category,
            "main_computer_purview": main_purview,
            "suggested_app": suggested_app,
            "mounted_windows_drive": bool(mounted_metadata),
        }

    def _file_explorer_relative_path(self, path: Path, root_id: str) -> str:
        resolver = self.server.mounted_windows_path_resolver
        if resolver.enabled and resolver.is_mounted_root(root_id):
            return resolver.relative_path(root_id, path)
        root = self._file_explorer_root_path(root_id)
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            try:
                return path.resolve().relative_to(root).as_posix()
            except ValueError:
                return path.name

    def _file_explorer_mounted_root_metadata(self, root_id: str) -> dict[str, Any]:
        resolver = self.server.mounted_windows_path_resolver
        if not (resolver.enabled and resolver.is_mounted_root(root_id)):
            return {}
        for root in resolver.roots(available_only=False):
            if root["id"] == root_id:
                return root
        return {}

    def _file_explorer_category(self, path: Path, relative_path: str, kind: str) -> tuple[str, str | None]:
        normalized = relative_path.replace("\\", "/").lower()
        suffix = path.suffix.lower()
        if kind == "directory" and any(part in normalized.split("/") for part in {"game_projects", "assets", "scripts", "data", "builds"}):
            return "game", "game-editor"
        if path.name == "project.json" or any(marker in f"/{normalized}/" for marker in {"/game_projects/", "/assets/", "/scripts/", "/data/", "/builds/"}):
            return "game", "game-editor"
        if suffix in {".csv", ".tsv", ".xlsx", ".xls"}:
            return "spreadsheet", "spreadsheet"
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".mp3", ".wav", ".ogg", ".glb", ".gltf", ".obj"}:
            return "asset", "game-editor"
        if suffix in {".txt", ".md", ".rst", ".log"}:
            return "text", "document"
        if suffix in {".py", ".js", ".ts", ".html", ".css", ".json", ".toml", ".yaml", ".yml", ".ps1", ".sh"}:
            return "code", "code-editor"
        return "other", None

    def _file_explorer_is_main_computer_purview(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
        except OSError:
            return False
        for root in {self.server.config.workspace.resolve(), self.server.debug_root.resolve()}:
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                pass
        parts = {part.lower() for part in resolved.parts}
        return "main_computer" in parts or "game_projects" in parts
