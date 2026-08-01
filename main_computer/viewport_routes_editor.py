from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from main_computer.mcel_project_edit_transaction import (
    ProjectEditTransactionError,
    apply_project_edit_transaction,
    inspect_project_manifest,
    prepare_project_edit_transaction,
)
from main_computer.viewport_state import *  # noqa: F401,F403


_EDITOR_TRANSACTION_HANDLE = re.compile(r"^[a-f0-9]{32}$")
_EDITOR_TRANSACTION_METADATA = "editor_transaction_metadata.json"
_EDITOR_TRANSACTION_SCHEMA = "mcel-code-editor-project-transaction-v1"
_EDITOR_PROJECT_MANIFEST_SCHEMA = "mcel-code-editor-project-manifest-v1"
_EDITOR_FILE_SAVE_SCHEMA = "mcel-code-editor-file-save-v1"
_EDITOR_MAX_TRANSACTION_FILES = 100
_EDITOR_MAX_REPLACEMENT_CHARS = 20_000_000


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


    def _handle_editor_project_manifest(self) -> None:
        try:
            body = self._read_json()
            repo = self._editor_repo_dir(str(body.get("repo_dir", ".") or "."))
            report = inspect_project_manifest(
                repo_root=repo,
                project_root=body.get("project_root", "."),
            )
            self.server.signal(
                "api-editor-project-manifest",
                repo=repo,
                project_root=report["project_root"],
                file_count=report["file_count"],
            )
            self._send_json(
                {
                    **report,
                    "schema": _EDITOR_PROJECT_MANIFEST_SCHEMA,
                    "repo_dir": self._editor_workspace_relative(repo),
                }
            )
        except ProjectEditTransactionError as exc:
            self.server.signal("api-editor-project-manifest-error", error=exc, stage=exc.stage)
            self._send_json({"error": str(exc), **exc.as_dict()}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-editor-project-manifest-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_editor_project_transaction_prepare(self) -> None:
        output_dir: Path | None = None
        try:
            body = self._read_json()
            repo = self._editor_repo_dir(str(body.get("repo_dir", ".") or "."))
            raw_changes = body.get("changes")
            if raw_changes is None:
                raw_changes = body.get("replacement_files")
            if raw_changes is None and isinstance(body.get("reviewed_patch"), dict):
                raw_changes = body["reviewed_patch"].get("changes") or body["reviewed_patch"].get("replacement_files")
            if not isinstance(raw_changes, list) or not raw_changes:
                raise ValueError("A non-empty changes or replacement_files list is required.")
            client_changes = self._editor_client_changes(raw_changes)
            report, handle, output_dir = self._editor_prepare_project_transaction(
                repo=repo,
                project_root=body.get("project_root", "."),
                changes=client_changes,
                validation_profile=body.get("validation_profile", "none"),
                client_validations=body.get("validations"),
            )
            self.server.signal(
                "api-editor-project-transaction-prepared",
                repo=repo,
                project_root=report["project_root"],
                transaction_id=report["transaction_id"],
                handle=handle,
                files=len(report["changes"]),
            )
            self._send_json(
                {
                    "ok": True,
                    "schema": _EDITOR_TRANSACTION_SCHEMA,
                    "state": "prepared",
                    "handle": handle,
                    "repo_dir": self._editor_workspace_relative(repo),
                    "transaction": self._editor_public_transaction(report),
                }
            )
        except ProjectEditTransactionError as exc:
            if output_dir is not None:
                shutil.rmtree(output_dir, ignore_errors=True)
            self.server.signal("api-editor-project-transaction-prepare-error", error=exc, stage=exc.stage)
            self._send_json({"error": str(exc), **exc.as_dict()}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            if output_dir is not None:
                shutil.rmtree(output_dir, ignore_errors=True)
            self.server.signal("api-editor-project-transaction-prepare-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_editor_project_transaction_apply(self) -> None:
        try:
            body = self._read_json()
            repo = self._editor_repo_dir(str(body.get("repo_dir", ".") or "."))
            if body.get("reviewed") is not True or (
                body.get("approved") is not True and body.get("confirmed") is not True
            ):
                raise ValueError("Reviewed transaction apply requires reviewed=true and approved=true or confirmed=true.")
            handle = self._editor_transaction_handle(body.get("handle") or body.get("transaction_handle"))
            report_path = self._editor_transaction_report(repo, handle)
            receipt = apply_project_edit_transaction(
                repo_root=repo,
                transaction=report_path,
                reviewed=True,
                require_project_manifest=self._coerce_bool(body.get("require_project_manifest"), default=False),
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.server.signal(
                "api-editor-project-transaction-applied",
                repo=repo,
                project_root=receipt["project_root"],
                transaction_id=receipt["transaction_id"],
                handle=handle,
                files=len(receipt["files"]),
            )
            self._send_json(
                {
                    "ok": True,
                    "schema": _EDITOR_TRANSACTION_SCHEMA,
                    "state": "applied",
                    "handle": handle,
                    "repo_dir": self._editor_workspace_relative(repo),
                    "transaction": self._editor_public_transaction(report),
                    "receipt": self._editor_public_receipt(receipt),
                    "changedFiles": [item["path"] for item in receipt["files"]],
                }
            )
        except ProjectEditTransactionError as exc:
            self.server.signal("api-editor-project-transaction-apply-error", error=exc, stage=exc.stage)
            self._send_json({"error": str(exc), **exc.as_dict()}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-editor-project-transaction-apply-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_editor_project_file_save(self) -> None:
        output_dir: Path | None = None
        try:
            body = self._read_json()
            repo = self._editor_repo_dir(str(body.get("repo_dir", body.get("repoDir", ".")) or "."))
            if body.get("explicit_save") is not True and body.get("explicitSave") is not True and body.get("confirmed") is not True:
                raise ValueError("File save requires explicit_save=true or explicitSave=true.")
            if body.get("stale_source_checked") is not True and body.get("staleSourceChecked") is not True and body.get("sourceFreshnessChecked") is not True:
                raise ValueError("File save requires stale source verification.")
            write_policy = str(body.get("write_policy", body.get("writePolicy", "")) or "")
            if write_policy not in {"author-owned-source", "explicit-save"}:
                raise ValueError("File save requires the author-owned-source or explicit-save write policy.")
            path = body.get("path") or body.get("filePath") or body.get("selectedPath")
            expected_before = body.get("expected_before_sha256") or body.get("expectedBeforeSha256")
            replacement_text = body.get("replacement_text")
            if replacement_text is None:
                replacement_text = body.get("replacementText")
            if replacement_text is None:
                replacement_text = body.get("text", body.get("draftText", body.get("newText")))
            if not isinstance(path, str) or not path.strip():
                raise ValueError("File save requires a project-relative path.")
            if replacement_text is None:
                raise ValueError("File save requires replacement text.")
            client_changes = self._editor_client_changes(
                [
                    {
                        "operation": "modify",
                        "path": path,
                        "expected_before_sha256": expected_before,
                        "replacement_text": replacement_text,
                    }
                ]
            )
            report, handle, output_dir = self._editor_prepare_project_transaction(
                repo=repo,
                project_root=body.get("project_root", body.get("projectRoot", ".")),
                changes=client_changes,
                validation_profile=body.get("validation_profile", body.get("validationProfile", "none")),
                client_validations=body.get("validations"),
            )
            receipt = apply_project_edit_transaction(
                repo_root=repo,
                transaction=Path(report["report_path"]),
                reviewed=True,
                require_project_manifest=self._coerce_bool(
                    body.get("require_project_manifest", body.get("requireProjectManifest")),
                    default=False,
                ),
            )
            self.server.signal(
                "api-editor-project-file-saved",
                repo=repo,
                project_root=receipt["project_root"],
                transaction_id=receipt["transaction_id"],
                handle=handle,
                path=receipt["files"][0]["path"],
            )
            self._send_json(
                {
                    "ok": True,
                    "schema": _EDITOR_FILE_SAVE_SCHEMA,
                    "status": "pass",
                    "state": "applied",
                    "handle": handle,
                    "repo_dir": self._editor_workspace_relative(repo),
                    "savedPath": receipt["files"][0]["path"],
                    "transaction": self._editor_public_transaction(report),
                    "receipt": self._editor_public_receipt(receipt),
                    "changedFiles": [item["path"] for item in receipt["files"]],
                }
            )
        except ProjectEditTransactionError as exc:
            self.server.signal("api-editor-project-file-save-error", error=exc, stage=exc.stage)
            self._send_json({"error": str(exc), **exc.as_dict()}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-editor-project-file-save-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _editor_workspace_relative(self, path: Path) -> str:
        workspace = self.server.config.workspace.resolve()
        relative = path.resolve().relative_to(workspace)
        return relative.as_posix() if relative.parts else "."

    def _editor_transaction_store(self, repo: Path) -> Path:
        workspace = self.server.config.workspace.resolve()
        workspace_key = hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:16]
        repo_key = hashlib.sha256(str(repo.resolve()).encode("utf-8")).hexdigest()[:16]
        store = Path(tempfile.gettempdir()) / "main-computer-mcel-project-edit" / workspace_key / repo_key
        store.mkdir(parents=True, exist_ok=True)
        return store

    def _editor_transaction_handle(self, raw: object) -> str:
        handle = str(raw or "").strip().lower()
        if not _EDITOR_TRANSACTION_HANDLE.fullmatch(handle):
            raise ValueError("A valid server-issued transaction handle is required.")
        return handle

    def _editor_transaction_report(self, repo: Path, handle: str) -> Path:
        output_dir = self._editor_transaction_store(repo) / handle
        metadata_path = output_dir / _EDITOR_TRANSACTION_METADATA
        report_path = output_dir / "project_edit_transaction.json"
        if not metadata_path.is_file() or not report_path.is_file():
            raise FileNotFoundError("The requested project edit transaction does not exist.")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("handle") != handle or metadata.get("repo_root") != str(repo.resolve()):
            raise ValueError("The project edit transaction does not belong to the selected repository.")
        return report_path

    def _editor_client_changes(self, raw_changes: object) -> list[dict[str, Any]]:
        if not isinstance(raw_changes, list) or not raw_changes:
            raise ValueError("A non-empty changes or replacement_files list is required.")
        if len(raw_changes) > _EDITOR_MAX_TRANSACTION_FILES:
            raise ValueError(f"A project transaction may contain at most {_EDITOR_MAX_TRANSACTION_FILES} files.")

        normalized: list[dict[str, Any]] = []
        total_chars = 0
        for raw in raw_changes:
            if not isinstance(raw, dict):
                raise ValueError("Each project change must be a JSON object.")
            if raw.get("replacement_file") is not None or raw.get("replacementFile") is not None:
                raise ValueError("Browser project edits cannot reference server-side replacement files.")
            if raw.get("replacement_bytes") is not None or raw.get("replacementBytes") is not None:
                raise ValueError("Browser project edits support UTF-8 replacement text only.")
            replacement_text = raw.get("replacement_text")
            if replacement_text is None:
                replacement_text = raw.get("replacementText")
            if not isinstance(replacement_text, str):
                raise ValueError("Each browser project change requires replacement_text.")
            total_chars += len(replacement_text)
            if total_chars > _EDITOR_MAX_REPLACEMENT_CHARS:
                raise ValueError(
                    f"Combined replacement text exceeds {_EDITOR_MAX_REPLACEMENT_CHARS} characters."
                )
            normalized.append(
                {
                    "operation": str(raw.get("operation") or "").strip().lower(),
                    "path": raw.get("path"),
                    "expected_before_sha256": (
                        raw.get("expected_before_sha256")
                        if raw.get("expected_before_sha256") is not None
                        else raw.get("expectedBeforeSha256")
                    ),
                    "replacement_text": replacement_text,
                    "replacement_sha256": (
                        raw.get("replacement_sha256")
                        if raw.get("replacement_sha256") is not None
                        else raw.get("replacementSha256")
                    ),
                }
            )
        return normalized

    def _editor_validation_commands(self, profile: object, client_validations: object) -> list[dict[str, Any]]:
        if client_validations is not None and client_validations != "" and client_validations != []:
            raise ValueError("Browser-supplied validation commands are not allowed.")
        normalized = str(profile or "none").strip().lower()
        if normalized in {"", "none"}:
            return []
        if normalized == "python-compileall":
            return [
                {
                    "argv": [sys.executable, "-m", "compileall", "-q", "."],
                    "cwd": ".",
                    "timeout_seconds": 120,
                }
            ]
        raise ValueError("Unsupported validation_profile; allowed values are none and python-compileall.")

    def _editor_prepare_project_transaction(
        self,
        *,
        repo: Path,
        project_root: object,
        changes: list[dict[str, Any]],
        validation_profile: object,
        client_validations: object,
    ) -> tuple[dict[str, Any], str, Path]:
        handle = secrets.token_hex(16)
        output_dir = self._editor_transaction_store(repo) / handle
        output_dir.mkdir(parents=True, exist_ok=False)
        try:
            report = prepare_project_edit_transaction(
                repo_root=repo,
                project_root=project_root,
                changes=changes,
                output_dir=output_dir,
                validations=self._editor_validation_commands(validation_profile, client_validations),
                artifact_name="mcel-code-editor-project-edit.zip",
            )
            metadata = {
                "schema": _EDITOR_TRANSACTION_SCHEMA,
                "handle": handle,
                "repo_root": str(repo.resolve()),
                "project_root": report["project_root"],
                "transaction_id": report["transaction_id"],
            }
            (output_dir / _EDITOR_TRANSACTION_METADATA).write_text(
                json.dumps(metadata, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return report, handle, output_dir
        except Exception:
            shutil.rmtree(output_dir, ignore_errors=True)
            raise

    def _editor_public_transaction(self, report: dict[str, Any]) -> dict[str, Any]:
        public = json.loads(json.dumps(report))
        public.pop("report_path", None)
        artifact = public.get("artifact")
        if isinstance(artifact, dict):
            artifact.pop("path", None)
        return public

    def _editor_public_receipt(self, receipt: dict[str, Any]) -> dict[str, Any]:
        public = json.loads(json.dumps(receipt))
        public.pop("receipt_path", None)
        return public

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
