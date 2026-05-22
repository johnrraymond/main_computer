from __future__ import annotations

import difflib
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RevisionControl:
    """Small local checkpoint store for main_computer project files."""

    def __init__(self, root: Path, store: Path) -> None:
        self.root = root.resolve()
        self.store = store.resolve()
        self.snapshots_dir = self.store / "snapshots"
        self.index_path = self.store / "index.json"
        self.excluded_dirs = {
            ".git",
            "__pycache__",
            ".pytest_cache",
            "diagnostics_output",
            "diagnostics_output_viewport",
            "harness_output",
            "revision_control",
            "debug_assets",
            "debug_asset_revisions",
            "energy_credits",
        }

    def status(self) -> dict[str, Any]:
        snapshots = self._read_index()
        return {
            "root": str(self.root),
            "store": str(self.store),
            "count": len(snapshots),
            "latest": snapshots[0] if snapshots else None,
            "snapshots": snapshots,
        }

    def create_snapshot(self, label: str = "", reason: str = "manual", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        snapshot_id = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]
        snapshot_dir = self.snapshots_dir / snapshot_id
        files_dir = snapshot_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)

        files: list[dict[str, Any]] = []
        for path in self._iter_files():
            relative = self._relative(path)
            target = files_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            stat = path.stat()
            files.append({"path": relative, "bytes": stat.st_size, "mtime": stat.st_mtime})

        created_at = datetime.now(tz=timezone.utc).isoformat()
        entry = {
            "id": snapshot_id,
            "label": label.strip() or reason,
            "reason": reason,
            "created_at": created_at,
            "file_count": len(files),
            "metadata": metadata or {},
        }
        (snapshot_dir / "manifest.json").write_text(
            json.dumps({"entry": entry, "files": files}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        snapshots = [entry, *self._read_index()]
        self._write_index(snapshots[:80])
        return {**self.status(), "created": entry}

    def diff_snapshot(self, snapshot_id: str, path: str) -> dict[str, Any]:
        relative = self._clean_relative(path)
        current_path = self._root_path(relative, must_exist=False)
        snapshot_path = self._snapshot_file(snapshot_id, relative)
        before = snapshot_path.read_text(encoding="utf-8") if snapshot_path.exists() else ""
        after = current_path.read_text(encoding="utf-8") if current_path.exists() else ""
        diff = "\n".join(
            difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile=f"{snapshot_id}/{relative}",
                tofile=relative,
                lineterm="",
            )
        )
        return {"id": snapshot_id, "path": relative, "diff": diff}

    def restore_file(self, snapshot_id: str, path: str) -> dict[str, Any]:
        relative = self._clean_relative(path)
        snapshot_path = self._snapshot_file(snapshot_id, relative)
        if not snapshot_path.exists():
            raise FileNotFoundError(f"Snapshot file not found: {relative}")
        target = self._root_path(relative, must_exist=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snapshot_path, target)
        return {"id": snapshot_id, "path": relative, "restored": True, "bytes": target.stat().st_size}

    def restore_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        manifest = self._read_snapshot_manifest(snapshot_id)
        files = manifest.get("files", [])
        restored = 0
        for item in files:
            if not isinstance(item, dict):
                continue
            relative = str(item.get("path", ""))
            if not relative:
                continue
            snapshot_path = self._snapshot_file(snapshot_id, relative)
            if not snapshot_path.exists():
                continue
            target = self._root_path(relative, must_exist=False)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snapshot_path, target)
            restored += 1
        return {
            "id": snapshot_id,
            "restored": True,
            "file_count": restored,
            "metadata": manifest.get("entry", {}).get("metadata", {}),
        }

    def snapshot_before_write(self, path: Path, reason: str) -> dict[str, Any] | None:
        if not path.exists():
            return None
        return self.create_snapshot(label=f"before {self._relative(path)}", reason=reason)

    def _iter_files(self) -> list[Path]:
        files: list[Path] = []
        if not self.root.exists():
            return files
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in self.excluded_dirs for part in path.relative_to(self.root).parts):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > 2_000_000:
                continue
            try:
                path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            files.append(path)
        return sorted(files)

    def _read_index(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            snapshots = data.get("snapshots", [])
            return snapshots if isinstance(snapshots, list) else []
        except Exception:
            return []

    def _write_index(self, snapshots: list[dict[str, Any]]) -> None:
        self.store.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps({"snapshots": snapshots}, ensure_ascii=False, indent=2), encoding="utf-8")

    def _snapshot_file(self, snapshot_id: str, relative: str) -> Path:
        clean_id = "".join(ch for ch in snapshot_id if ch.isalnum() or ch in {"-", "_"})
        if not clean_id:
            raise ValueError("Snapshot id is required.")
        candidate = (self.snapshots_dir / clean_id / "files" / relative).resolve()
        snapshots_root = self.snapshots_dir.resolve()
        if snapshots_root not in candidate.parents:
            raise ValueError("Snapshot path must stay inside revision_control.")
        return candidate

    def _read_snapshot_manifest(self, snapshot_id: str) -> dict[str, Any]:
        clean_id = "".join(ch for ch in snapshot_id if ch.isalnum() or ch in {"-", "_"})
        if not clean_id:
            raise ValueError("Snapshot id is required.")
        manifest_path = (self.snapshots_dir / clean_id / "manifest.json").resolve()
        snapshots_root = self.snapshots_dir.resolve()
        if snapshots_root not in manifest_path.parents:
            raise ValueError("Snapshot manifest path must stay inside revision_control.")
        if not manifest_path.exists():
            raise FileNotFoundError(f"Snapshot not found: {snapshot_id}")
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Snapshot manifest is invalid.")
        return data

    def _root_path(self, relative: str, *, must_exist: bool) -> Path:
        candidate = (self.root / relative).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("Revision path must stay inside the project.")
        if must_exist and not candidate.exists():
            raise FileNotFoundError(f"Project file not found: {relative}")
        return candidate

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def _clean_relative(self, raw_path: str) -> str:
        cleaned = raw_path.strip().replace("\\", "/")
        if not cleaned:
            raise ValueError("Revision file path is required.")
        if cleaned.startswith("/") or ".." in Path(cleaned).parts:
            raise ValueError("Revision file path must be relative.")
        return cleaned


class DebugAssetRevisionControl:
    """Checkpoint store that only snapshots the debug_assets directory."""

    def __init__(self, assets_root: Path, store: Path) -> None:
        self.assets_root = assets_root.resolve()
        self.store = store.resolve()
        self.snapshots_dir = self.store / "snapshots"
        self.index_path = self.store / "index.json"

    def status(self) -> dict[str, Any]:
        snapshots = self._read_index()
        return {
            "assets_root": str(self.assets_root),
            "store": str(self.store),
            "count": len(snapshots),
            "latest": snapshots[0] if snapshots else None,
            "snapshots": snapshots,
        }

    def create_snapshot(self, label: str = "", reason: str = "manual") -> dict[str, Any]:
        snapshot_id = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]
        snapshot_dir = self.snapshots_dir / snapshot_id
        files_dir = snapshot_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)

        files: list[dict[str, Any]] = []
        for path in self._iter_asset_files():
            relative = path.resolve().relative_to(self.assets_root).as_posix()
            target = files_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            stat = path.stat()
            files.append({"path": relative, "bytes": stat.st_size, "mtime": stat.st_mtime})

        entry = {
            "id": snapshot_id,
            "label": label.strip() or reason,
            "reason": reason,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "asset_count": len([file for file in files if file["path"] != "manifest.json"]),
            "file_count": len(files),
        }
        (snapshot_dir / "manifest.json").write_text(
            json.dumps({"entry": entry, "files": files}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        snapshots = [entry, *self._read_index()]
        self._write_index(snapshots[:120])
        return {**self.status(), "created": entry}

    def restore(self, snapshot_id: str) -> dict[str, Any]:
        snapshot_files = self._snapshot_files_dir(snapshot_id)
        if not snapshot_files.exists():
            raise FileNotFoundError(f"Debug asset snapshot not found: {snapshot_id}")
        self._clear_assets()
        self.assets_root.mkdir(parents=True, exist_ok=True)
        restored = 0
        for path in sorted(snapshot_files.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(snapshot_files)
            target = self.assets_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            restored += 1
        return {"id": snapshot_id, "restored": True, "file_count": restored, **self.status()}

    def reset(self, label: str = "before asset reset") -> dict[str, Any]:
        created = self.create_snapshot(label=label, reason="pre-reset")
        self._clear_assets()
        self.assets_root.mkdir(parents=True, exist_ok=True)
        return {**self.status(), "created": created.get("created"), "reset": True}

    def snapshot_before_change(self, reason: str) -> dict[str, Any]:
        return self.create_snapshot(label=f"before {reason}", reason=reason)

    def _iter_asset_files(self) -> list[Path]:
        if not self.assets_root.exists():
            return []
        files: list[Path] = []
        for path in self.assets_root.rglob("*"):
            if not path.is_file():
                continue
            try:
                if path.stat().st_size > 2_000_000:
                    continue
            except OSError:
                continue
            files.append(path)
        return sorted(files)

    def _clear_assets(self) -> None:
        if not self.assets_root.exists():
            return
        for path in sorted(self.assets_root.iterdir(), key=lambda item: len(item.parts), reverse=True):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    def _snapshot_files_dir(self, snapshot_id: str) -> Path:
        clean_id = "".join(ch for ch in snapshot_id if ch.isalnum() or ch in {"-", "_"})
        if not clean_id:
            raise ValueError("Debug asset snapshot id is required.")
        candidate = (self.snapshots_dir / clean_id / "files").resolve()
        snapshots_root = self.snapshots_dir.resolve()
        if candidate != snapshots_root and snapshots_root not in candidate.parents:
            raise ValueError("Debug asset snapshot path must stay inside debug_asset_revisions.")
        return candidate

    def _read_index(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            snapshots = data.get("snapshots", [])
            return snapshots if isinstance(snapshots, list) else []
        except Exception:
            return []

    def _write_index(self, snapshots: list[dict[str, Any]]) -> None:
        self.store.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps({"snapshots": snapshots}, ensure_ascii=False, indent=2), encoding="utf-8")
