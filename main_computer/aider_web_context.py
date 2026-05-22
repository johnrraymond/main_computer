from __future__ import annotations

import copy
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AiderWebContextStore:
    """Persist visible web-context history for the Aider dock."""

    def __init__(self, root: Path, *, max_archives: int = 80, max_entries: int = 400) -> None:
        self.root = root.resolve()
        self.archives_dir = self.root / "archives"
        self.histories_dir = self.root / "histories"
        self.active_path = self.root / "active.json"
        self.index_path = self.root / "index.json"
        self.max_archives = max(1, int(max_archives))
        self.max_entries = max(1, int(max_entries))
        self._lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        with self._lock:
            active = self._ensure_active(sync_archive=False, persist_repairs=False)
            archives = self._read_index()
            active_archive = self._current_archive_metadata(active, archives)
            if self._should_merge_active_archive(active, archives, active_archive):
                archives = self._merge_archive_index(archives, active_archive)
            return {
                "active": self._session_summary(active, include_entries=True),
                "current_archive": active_archive,
                "archives": archives,
                "archive_count": len(archives),
            }

    def append_entry(
        self,
        *,
        kind: str,
        repo_dir: str,
        files: list[str],
        instruction: str = "",
        dry_run: bool = True,
        ok: bool = True,
        returncode: int | None = None,
        duration_ms: int | None = None,
        result_excerpt: str = "",
        route: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._ensure_active(sync_archive=False)
            session = self._fork_active_session_if_needed(session)
            entry = self._entry_payload(
                session,
                kind=kind,
                repo_dir=repo_dir,
                files=files,
                instruction=instruction,
                dry_run=dry_run,
                ok=ok,
                returncode=returncode,
                duration_ms=duration_ms,
                result_excerpt=result_excerpt,
                route=route,
                metadata=metadata,
            )
            self._append_entry_to_session(session, entry)
            self._write_session(self.active_path, session)
            if not self._is_pending_thread(session):
                self._sync_active_archive(session)
            return self.status()

    def append_entry_to_archive(
        self,
        archive_id: str,
        *,
        kind: str,
        repo_dir: str,
        files: list[str],
        instruction: str = "",
        dry_run: bool = True,
        ok: bool = True,
        returncode: int | None = None,
        duration_ms: int | None = None,
        result_excerpt: str = "",
        route: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append an action result to the archive/thread that launched it.

        Background Aider jobs can finish after the browser has refreshed or after
        the user has loaded a different archived context. Writing by archive id
        keeps each thread's activity attached to its original UI history.
        """
        with self._lock:
            clean_archive_id = self._clean_archive_id(archive_id)
            session = self._read_archive(clean_archive_id)
            entry = self._entry_payload(
                session,
                kind=kind,
                repo_dir=repo_dir,
                files=files,
                instruction=instruction,
                dry_run=dry_run,
                ok=ok,
                returncode=returncode,
                duration_ms=duration_ms,
                result_excerpt=result_excerpt,
                route=route,
                metadata=metadata,
            )
            self._append_entry_to_session(session, entry)
            self._write_archive_payload(session, archive_id=clean_archive_id)
            if self.active_path.exists():
                active = self._read_session(self.active_path)
                if str(active.get("archive_id") or "").strip() == clean_archive_id and not self._is_pending_fork(active):
                    active = copy.deepcopy(session)
                    active.pop("archived_at", None)
                    self._write_session(self.active_path, active)
            return self.status()

    def prepare_aider_history_files(self, *, promote: bool = False) -> dict[str, str]:
        """Write Aider-compatible history files for the active archive/thread.

        The files are intentionally kept under the web-context store so every UI
        archive has an isolated Aider memory source instead of sharing the git
        root's default .aider.* files.
        """
        with self._lock:
            session = self._ensure_active(sync_archive=False)
            session = self._fork_active_session_if_needed(session)
            pending_thread = self._is_pending_thread(session)
            if promote and pending_thread:
                session["archive_id"] = self._archive_id()
                session["pending_thread"] = False
            self._write_session(self.active_path, session)
            archive_id = str(session.get("archive_id") or "").strip()
            history_id = archive_id or str(session.get("id") or "").strip() or self._session_id()
            history_dir = self.histories_dir / self._clean_archive_id(history_id)
            history_dir.mkdir(parents=True, exist_ok=True)
            chat_history_file = history_dir / ".aider.chat.history.md"
            input_history_file = history_dir / ".aider.input.history"
            entries = list(session.get("entries", []))[-self.max_entries :]
            chat_history_file.write_text(self._render_aider_chat_history(session, entries), encoding="utf-8")
            input_history_file.write_text(self._render_aider_input_history(entries), encoding="utf-8")
            if promote and pending_thread:
                self._sync_active_archive(session)
            elif archive_id and not self._is_pending_thread(session):
                self._sync_active_archive(session)
            return {
                "archive_id": archive_id or history_id,
                "history_id": history_id,
                "session_id": str(session.get("id") or ""),
                "chat_history_file": str(chat_history_file.resolve()),
                "input_history_file": str(input_history_file.resolve()),
            }


    def archive_active(self, label: str = "") -> dict[str, Any]:
        with self._lock:
            session = self._ensure_active(sync_archive=False)
            archived_at = self._now()
            if self._is_pending_fork(session):
                archived = self._current_archive_metadata(session, self._read_index())
            else:
                archived = self._sync_active_archive(session, archived_at=archived_at, label=label)
            active = self._new_session(
                repo_dir=str(session.get("repo_dir", ".") or "."),
                files=self._clean_files(session.get("files", [])),
                archive_id=self._archive_id(),
            )
            self._write_session(self.active_path, active)
            self._sync_active_archive(active)
            return {
                "ok": True,
                "archived": archived,
                "active": self._session_summary(active, include_entries=True),
                "archives": self._read_index(),
                "archive_count": len(self._read_index()),
            }

    def load_archive(self, archive_id: str) -> dict[str, Any]:
        with self._lock:
            archive = self._read_archive(archive_id)
            clean_archive_id = str(archive.get("archive_id") or archive_id)
            active = self._new_session(
                repo_dir=str(archive.get("repo_dir", ".") or "."),
                files=self._clean_files(archive.get("files", [])),
                archive_id=clean_archive_id,
                fork_on_write_archive_id=clean_archive_id,
            )
            active["entries"] = copy.deepcopy(list(archive.get("entries", [])))[: self.max_entries]
            active["updated_at"] = self._now()
            active["label"] = str(archive.get("label") or "").strip() or None
            self._write_session(self.active_path, active)
            status = self.status()
            status["ok"] = True
            status["loaded_archive"] = self._archive_metadata_from_payload(archive, archive_id=archive_id)
            return status

    def reset_active(self, *, repo_dir: str = ".", files: list[str] | None = None) -> dict[str, Any]:
        with self._lock:
            active = self._new_session(
                repo_dir=repo_dir,
                files=self._clean_files(files or []),
                pending_thread=True,
            )
            self._write_session(self.active_path, active)
            return {
                "ok": True,
                "active": self._session_summary(active, include_entries=True),
                "archives": self._read_index(),
                "archive_count": len(self._read_index()),
            }

    def _entry_payload(
        self,
        session: dict[str, Any],
        *,
        kind: str,
        repo_dir: str,
        files: list[str],
        instruction: str,
        dry_run: bool,
        ok: bool,
        returncode: int | None,
        duration_ms: int | None,
        result_excerpt: str,
        route: str,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        now = self._now()
        clean_files = self._clean_files(files)
        return {
            "id": self._entry_id(),
            "timestamp": now,
            "kind": str(kind or "event").strip().lower() or "event",
            "route": str(route or "").strip(),
            "repo_dir": str(repo_dir or session.get("repo_dir") or ".").strip() or ".",
            "files": clean_files,
            "file_count": len(clean_files),
            "instruction": str(instruction or "").strip(),
            "dry_run": bool(dry_run),
            "ok": bool(ok),
            "returncode": returncode,
            "duration_ms": duration_ms,
            "result_excerpt": self._clip_text(result_excerpt, 1200),
            "metadata": metadata or {},
        }

    def _append_entry_to_session(self, session: dict[str, Any], entry: dict[str, Any]) -> None:
        session["repo_dir"] = str(entry.get("repo_dir") or session.get("repo_dir") or ".")
        session["files"] = self._clean_files(entry.get("files", []))
        session["updated_at"] = str(entry.get("timestamp") or self._now())
        session.setdefault("entries", []).append(entry)
        session["entries"] = session["entries"][-self.max_entries :]

    def _write_archive_payload(self, session: dict[str, Any], *, archive_id: str) -> dict[str, Any]:
        archive_id = self._clean_archive_id(archive_id)
        session["archive_id"] = archive_id
        archived_at = str(session.get("archived_at") or self._now())
        metadata = self._archive_metadata(
            session,
            archive_id=archive_id,
            archived_at=archived_at,
            label=str(session.get("label") or ""),
        )
        self.archives_dir.mkdir(parents=True, exist_ok=True)
        archive_payload = copy.deepcopy(session)
        archive_payload["archive_id"] = archive_id
        archive_payload["archived_at"] = archived_at
        archive_payload["label"] = metadata["label"]
        archive_path = self.archives_dir / f"{archive_id}.json"
        archive_path.write_text(json.dumps(archive_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        archives = self._merge_archive_index([metadata, *self._read_index()], metadata)
        self._write_index(archives[: self.max_archives])
        return metadata

    def _ensure_active(
        self,
        *,
        sync_archive: bool = True,
        persist_repairs: bool = True,
    ) -> dict[str, Any]:
        if self.active_path.exists():
            active = self._read_session(self.active_path)
            repaired = False
            if not str(active.get("archive_id") or "").strip() and not self._is_pending_thread(active):
                active["archive_id"] = self._archive_id()
                repaired = True
            if repaired and persist_repairs:
                self._write_session(self.active_path, active)
            if sync_archive and not self._is_pending_fork(active) and not self._is_pending_thread(active):
                self._sync_active_archive(active)
            return active
        latest_archive_id = self._latest_archive_id()
        if latest_archive_id:
            try:
                archive = self._read_archive(latest_archive_id)
                active = self._new_session(
                    repo_dir=str(archive.get("repo_dir", ".") or "."),
                    files=self._clean_files(archive.get("files", [])),
                    archive_id=str(archive.get("archive_id") or latest_archive_id),
                    origin_archive_id=str(archive.get("origin_archive_id") or "") or None,
                )
                active["entries"] = copy.deepcopy(list(archive.get("entries", [])))[: self.max_entries]
                active["updated_at"] = self._now()
                active["label"] = str(archive.get("label") or "").strip() or None
                self._write_session(self.active_path, active)
                self._sync_active_archive(active)
                return active
            except Exception:
                pass
        active = self._new_session(archive_id=self._archive_id())
        self._write_session(self.active_path, active)
        self._sync_active_archive(active)
        return active

    def _new_session(
        self,
        *,
        repo_dir: str = ".",
        files: list[str] | None = None,
        archive_id: str | None = None,
        origin_archive_id: str | None = None,
        fork_on_write_archive_id: str | None = None,
        pending_thread: bool = False,
    ) -> dict[str, Any]:
        timestamp = self._now()
        return {
            "id": self._session_id(),
            "created_at": timestamp,
            "updated_at": timestamp,
            "archive_id": str(archive_id or "").strip() or None,
            "repo_dir": str(repo_dir or ".").strip() or ".",
            "files": self._clean_files(files or []),
            "origin_archive_id": str(origin_archive_id or "").strip() or None,
            "fork_on_write_archive_id": str(fork_on_write_archive_id or "").strip() or None,
            "pending_thread": bool(pending_thread),
            "entries": [],
        }

    def _read_session(self, path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["files"] = self._clean_files(payload.get("files", []))
        payload["entries"] = list(payload.get("entries", []))
        return payload

    def _write_session(self, path: Path, session: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_index(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        return [item for item in payload if isinstance(item, dict)]

    def _write_index(self, archives: list[dict[str, Any]]) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(archives, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_archive(self, archive_id: str) -> dict[str, Any]:
        clean_id = self._clean_archive_id(archive_id)
        path = self.archives_dir / f"{clean_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Archived Aider context not found: {clean_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["archive_id"] = clean_id
        payload["files"] = self._clean_files(payload.get("files", []))
        payload["entries"] = list(payload.get("entries", []))
        return payload

    def _latest_archive_id(self) -> str | None:
        archives = self._read_index()
        if not archives:
            return None
        latest = archives[0]
        archive_id = str(latest.get("id") or "").strip()
        return archive_id or None

    def _clean_archive_id(self, archive_id: str) -> str:
        value = str(archive_id or "").strip()
        if not value or any(ch not in "0123456789abcdefghijklmnopqrstuvwxyz-" for ch in value.lower()):
            raise ValueError("Archive id is required.")
        if "/" in value or "\\" in value or ".." in value:
            raise ValueError("Archive id is invalid.")
        return value

    def _clean_files(self, files: list[str] | tuple[str, ...] | Any) -> list[str]:
        result: list[str] = []
        for raw in files or []:
            item = str(raw or "").strip().replace("\\", "/")
            if item and item not in result:
                result.append(item)
        return result

    def _session_summary(self, session: dict[str, Any], *, include_entries: bool) -> dict[str, Any]:
        entries = list(session.get("entries", []))
        summary = {
            "id": str(session.get("id", "")),
            "archive_id": str(session.get("archive_id", "") or "") or None,
            "created_at": session.get("created_at"),
            "updated_at": session.get("updated_at"),
            "repo_dir": str(session.get("repo_dir", ".") or "."),
            "files": self._clean_files(session.get("files", [])),
            "entry_count": len(entries),
            "origin_archive_id": session.get("origin_archive_id"),
        }
        if include_entries:
            summary["entries"] = entries
        return summary

    def _sync_active_archive(
        self,
        session: dict[str, Any],
        *,
        archived_at: str | None = None,
        label: str = "",
    ) -> dict[str, Any]:
        archive_id = str(session.get("archive_id") or "").strip() or self._archive_id()
        session["archive_id"] = archive_id
        archived_at = archived_at or self._now()
        metadata = self._archive_metadata(session, archive_id=archive_id, archived_at=archived_at, label=label)
        self.archives_dir.mkdir(parents=True, exist_ok=True)
        archive_payload = copy.deepcopy(session)
        archive_payload["archive_id"] = archive_id
        archive_payload["archived_at"] = archived_at
        archive_payload["label"] = metadata["label"]
        archive_path = self.archives_dir / f"{archive_id}.json"
        archive_path.write_text(json.dumps(archive_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        archives = self._merge_archive_index([metadata, *self._read_index()], metadata)
        self._write_index(archives[: self.max_archives])
        return metadata

    def _merge_archive_index(
        self,
        archives: list[dict[str, Any]],
        latest: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        if latest:
            latest_id = str(latest.get("id") or "").strip()
            if latest_id:
                merged.append(latest)
                seen.add(latest_id)
        for item in archives:
            if not isinstance(item, dict):
                continue
            archive_id = str(item.get("id") or "").strip()
            if not archive_id or archive_id in seen:
                continue
            seen.add(archive_id)
            merged.append(item)
        return merged

    def _current_archive_metadata(
        self,
        session: dict[str, Any],
        archives: list[dict[str, Any]],
    ) -> dict[str, Any]:
        archive_id = str(session.get("archive_id") or "").strip()
        if self._is_pending_fork(session):
            for item in archives:
                if str(item.get("id") or "").strip() == archive_id:
                    return item
        return self._archive_metadata_from_session(session, archive_id=archive_id)

    def _should_merge_active_archive(
        self,
        session: dict[str, Any],
        archives: list[dict[str, Any]],
        active_archive: dict[str, Any],
    ) -> bool:
        archive_id = str(active_archive.get("id") or "").strip()
        if not archive_id:
            return False
        if not self._is_pending_fork(session):
            return True
        return not any(str(item.get("id") or "").strip() == archive_id for item in archives)

    def _fork_active_session_if_needed(self, session: dict[str, Any]) -> dict[str, Any]:
        if not self._is_pending_fork(session):
            return session
        source_archive_id = str(session.get("fork_on_write_archive_id") or "").strip()
        forked = copy.deepcopy(session)
        forked["archive_id"] = self._archive_id()
        forked["origin_archive_id"] = source_archive_id
        forked["fork_on_write_archive_id"] = None
        try:
            source_archive = self._read_archive(source_archive_id)
        except Exception:
            source_archive = {}
        derived_label = self._derived_clone_label(source_archive, source_archive_id)
        if derived_label:
            forked["label"] = derived_label
        return forked

    def _is_pending_fork(self, session: dict[str, Any]) -> bool:
        archive_id = str(session.get("archive_id") or "").strip()
        source_archive_id = str(session.get("fork_on_write_archive_id") or "").strip()
        return bool(archive_id and source_archive_id and archive_id == source_archive_id)

    def _is_pending_thread(self, session: dict[str, Any]) -> bool:
        return bool(session.get("pending_thread"))

    def _archive_metadata_from_session(self, session: dict[str, Any], *, archive_id: str) -> dict[str, Any]:
        archived_at = session.get("updated_at") or self._now()
        return self._archive_metadata(session, archive_id=archive_id, archived_at=str(archived_at), label=str(session.get("label") or ""))

    def _derived_clone_label(self, archive: dict[str, Any], archive_id: str) -> str:
        label = str(archive.get("label") or archive_id or "").strip()
        if not label:
            return ""
        return f"{label} copy"

    def _archive_metadata(
        self,
        session: dict[str, Any],
        *,
        archive_id: str,
        archived_at: str,
        label: str,
    ) -> dict[str, Any]:
        clean_label = str(label or "").strip() or self._default_archive_label(session, archived_at)
        return {
            "id": archive_id,
            "label": clean_label,
            "created_at": session.get("created_at"),
            "updated_at": session.get("updated_at"),
            "archived_at": archived_at,
            "repo_dir": str(session.get("repo_dir", ".") or "."),
            "files": self._clean_files(session.get("files", [])),
            "entry_count": len(list(session.get("entries", []))),
            "origin_archive_id": session.get("origin_archive_id"),
            "preview": self._default_archive_preview(session),
        }

    def _archive_metadata_from_payload(self, archive: dict[str, Any], *, archive_id: str) -> dict[str, Any]:
        return {
            "id": archive_id,
            "label": str(archive.get("label") or archive_id),
            "created_at": archive.get("created_at"),
            "updated_at": archive.get("updated_at"),
            "archived_at": archive.get("archived_at"),
            "repo_dir": str(archive.get("repo_dir", ".") or "."),
            "files": self._clean_files(archive.get("files", [])),
            "entry_count": len(list(archive.get("entries", []))),
            "origin_archive_id": archive.get("origin_archive_id"),
            "preview": self._default_archive_preview(archive),
        }

    def _default_archive_label(self, session: dict[str, Any], archived_at: str) -> str:
        preview = self._default_archive_preview(session)
        if preview:
            return preview
        return f"Archived {archived_at.replace('T', ' ')[:16]}"

    def _default_archive_preview(self, session: dict[str, Any]) -> str:
        for entry in list(session.get("entries", [])):
            instruction = str(entry.get("instruction", "")).strip()
            if instruction:
                return self._clip_text(instruction, 72)
        return ""

    def _clip_text(self, value: str, limit: int) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: max(1, limit - 1)].rstrip() + "…"

    def _render_aider_chat_history(self, session: dict[str, Any], entries: list[dict[str, Any]]) -> str:
        created_at = str(session.get("created_at") or self._now())
        lines: list[str] = [f"# aider chat started at {self._aider_history_timestamp(created_at)}", ""]
        for entry in entries:
            instruction = str(entry.get("instruction") or "").strip()
            if not instruction:
                continue
            lines.append(f"#### {instruction}")
            lines.append("")
            result = str(entry.get("result_excerpt") or "").strip()
            if result:
                lines.append(result)
                lines.append("")
            metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
            stdout = str(metadata.get("stdout_excerpt") or "").strip()
            if stdout:
                lines.append(stdout)
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _render_aider_input_history(self, entries: list[dict[str, Any]]) -> str:
        blocks: list[str] = []
        for entry in entries:
            instruction = str(entry.get("instruction") or "").strip()
            if not instruction:
                continue
            timestamp = self._aider_history_timestamp(str(entry.get("timestamp") or self._now()), include_fraction=True)
            blocks.append(f"# {timestamp}\n+{instruction}")
        return ("\n\n".join(blocks).rstrip() + "\n") if blocks else ""

    def _aider_history_timestamp(self, value: str, *, include_fraction: bool = False) -> str:
        text = str(value or "").strip()
        try:
            normalized = text.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            if include_fraction:
                return parsed.replace(tzinfo=None).isoformat(sep=" ")
            return parsed.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return text.replace("T", " ")[:26 if include_fraction else 19]


    def _now(self) -> str:
        return datetime.now(tz=timezone.utc).isoformat()

    def _session_id(self) -> str:
        return datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]

    def _archive_id(self) -> str:
        return datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]

    def _entry_id(self) -> str:
        return uuid.uuid4().hex[:10]
