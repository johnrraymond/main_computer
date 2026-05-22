from __future__ import annotations

from main_computer.viewport_state import *  # noqa: F401,F403

class ViewportEditorRoutesMixin:
    def _handle_editor_files(self) -> None:
        try:
            body = self._read_json()
            repo = self._editor_repo_dir(str(body.get("repo_dir", ".") or "."))
            path = str(body.get("path", "") or "").strip()
            query = str(body.get("query", "") or "").strip().lower()
            limit = max(1, min(1000, int(body.get("limit", 500) or 500)))
            if query:
                files = self._editor_file_entries(repo, query=query, limit=limit)
                self.server.signal("api-editor-files-search", repo=repo, query=query, count=len(files), limit=limit)
                self._send_json({"repo_dir": str(repo), "path": "", "files": files, "count": len(files), "limit": limit})
            else:
                directory = self._editor_directory_path(repo, path)
                entries = self._editor_directory_entries(repo, directory, limit=limit)
                relative_path = directory.relative_to(repo).as_posix() if directory != repo else ""
                self.server.signal("api-editor-files-dir", repo=repo, path=relative_path or ".", count=len(entries), limit=limit)
                self._send_json(
                    {
                        "repo_dir": str(repo),
                        "path": relative_path,
                        "entries": entries,
                        "count": len(entries),
                        "limit": limit,
                    }
                )
        except Exception as exc:
            self.server.signal("api-editor-files-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_editor_read(self) -> None:
        try:
            body = self._read_json()
            repo = self._editor_repo_dir(str(body.get("repo_dir", ".") or "."))
            files = self._editor_selected_files(repo, parse_file_list(body.get("files", "")))
            if not files:
                self._send_json({"ok": False, "error": "Mark at least one file to read."}, status=HTTPStatus.BAD_REQUEST)
                return
            output_parts: list[str] = []
            file_payloads: list[dict[str, Any]] = []
            for path in files[:20]:
                content = path.read_text(encoding="utf-8", errors="replace")
                if len(content) > 200_000:
                    content = content[:200_000] + "\n[truncated at 200000 characters]\n"
                relative = path.relative_to(repo).as_posix()
                file_payloads.append({"path": relative, "chars": len(content), "content": content})
                output_parts.append(f"--- {relative} ---\n{content}")
            stdout = "\n\n".join(output_parts)
            self.server.signal("api-editor-read", repo=repo, files=len(file_payloads), chars=len(stdout))
            self._write_aider_log(
                "editor_read",
                repo_dir=str(repo),
                files=[item["path"] for item in file_payloads],
                instruction=str(body.get("instruction", "") or ""),
                ok=True,
                stdout_chars=len(stdout),
                stdout_excerpt=self._log_excerpt(stdout),
            )
            self._append_aider_context_entry(
                kind="read",
                repo_dir=str(repo),
                files=[item["path"] for item in file_payloads],
                instruction=str(body.get("instruction", "") or ""),
                dry_run=True,
                ok=True,
                route="/api/applications/editor/read",
                returncode=0,
                duration_ms=0,
                result_excerpt=stdout,
            )
            self._send_json(
                {
                    "ok": True,
                    "kind": "read",
                    "repo_dir": str(repo),
                    "files": file_payloads,
                    "stdout": stdout,
                    "stderr": "",
                    "duration_ms": 0,
                    "dry_run": True,
                    "returncode": 0,
                    "timed_out": False,
                }
            )
        except Exception as exc:
            self.server.signal("api-editor-read-error", error=exc)
            self._write_aider_log("editor_read_error", error=str(exc))
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _editor_repo_dir(self, requested: str) -> Path:
        workspace = self.server.config.workspace.resolve()
        cleaned = requested.strip() or "."
        candidate = Path(cleaned)
        if not candidate.is_absolute():
            clean_parts = tuple(part for part in cleaned.replace("\\", "/").split("/") if part and part != ".")
            if clean_parts and clean_parts[0] == workspace.name:
                candidate = (workspace / Path(*clean_parts[1:])) if len(clean_parts) > 1 else workspace
            else:
                workspace_relative = workspace / Path(*clean_parts) if clean_parts else workspace
                candidate = workspace_relative
        resolved = candidate.resolve()
        try:
            resolved.relative_to(workspace)
        except ValueError as exc:
            raise ValueError("Editor repository must stay inside the local workspace.") from exc
        if not resolved.exists() or not resolved.is_dir():
            raise ValueError("Editor repository does not exist.")
        return resolved

    def _editor_directory_path(self, repo: Path, requested: str) -> Path:
        clean_parts = tuple(part for part in requested.replace("\\", "/").split("/") if part and part != ".")
        candidate = (repo / Path(*clean_parts)).resolve() if clean_parts else repo.resolve()
        try:
            candidate.relative_to(repo)
        except ValueError as exc:
            raise ValueError("Editor directory must stay inside the selected repository.") from exc
        if not candidate.exists() or not candidate.is_dir():
            raise ValueError("Editor directory does not exist.")
        return candidate

    def _editor_selected_files(self, repo: Path, files: list[str]) -> list[Path]:
        selected: list[Path] = []
        for raw in files:
            rel = raw.strip().replace("\\", "/")
            if not rel:
                continue
            candidate = (repo / rel).resolve()
            try:
                candidate.relative_to(repo)
            except ValueError as exc:
                raise ValueError(f"Selected file escapes repository root: {raw}") from exc
            if not candidate.exists() or not candidate.is_file():
                raise FileNotFoundError(f"Selected file does not exist: {rel}")
            if self._editor_skip_path(repo, candidate):
                raise ValueError(f"Selected file is not allowed: {rel}")
            selected.append(candidate)
        return selected

    def _editor_skip_path(self, repo: Path, path: Path) -> bool:
        skipped_dirs = {
            ".git",
            ".pytest_cache",
            "__pycache__",
            ".venv",
            "node_modules",
            "revision_control",
            "debug_asset_revisions",
        }
        skipped_prefixes = (
            "diagnostics_output",
            "harness_output",
        )
        skipped_suffixes = {
            ".pyc",
            ".pyo",
        }
        relative_parts = path.relative_to(repo).parts
        if any(part in skipped_dirs for part in relative_parts):
            return True
        if any(part.startswith(skipped_prefixes) for part in relative_parts):
            return True
        return path.is_file() and path.suffix.lower() in skipped_suffixes

    def _editor_directory_entries(self, repo: Path, directory: Path, *, limit: int) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        children = sorted(
            (child for child in directory.iterdir() if not self._editor_skip_path(repo, child)),
            key=lambda item: (not item.is_dir(), item.name.lower()),
        )
        for child in children[:limit]:
            relative = child.relative_to(repo).as_posix()
            stat = child.stat()
            entries.append(
                {
                    "path": relative,
                    "name": child.name,
                    "kind": "dir" if child.is_dir() else "file",
                    "has_children": child.is_dir() and any(
                        not self._editor_skip_path(repo, grandchild) for grandchild in child.iterdir()
                    ),
                    "bytes": 0 if child.is_dir() else stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
        return entries

    def _editor_file_entries(self, repo: Path, *, query: str, limit: int) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for path in sorted(repo.rglob("*"), key=lambda item: item.as_posix().lower()):
            if self._editor_skip_path(repo, path):
                continue
            if path.is_dir():
                continue
            try:
                relative = path.relative_to(repo).as_posix()
            except ValueError:
                continue
            if query and query not in relative.lower():
                continue
            entries.append(
                {
                    "path": relative,
                    "name": path.name,
                    "kind": "file",
                    "depth": max(0, len(path.relative_to(repo).parts) - 1),
                    "bytes": path.stat().st_size,
                }
            )
            if len(entries) >= limit:
                break
        return entries
