#!/usr/bin/env python3
"""Guarded multi-file project edit transactions for MCEL application work.

Version one is intentionally narrow:

* complete replacement files only;
* ``modify`` and ``create`` operations only;
* UTF-8 text payloads only;
* exact before hashes for every modified file;
* isolated project staging and no-shell validation commands;
* changed-files overlay packaging followed by ``new_patch.py --dry-run``;
* explicit reviewed apply with per-file atomic replacement and best-effort rollback.

The module does not choose edits.  It receives an already reviewed replacement
set and proves that the set can be staged, validated, packaged, and applied
without silently accepting source drift.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


TRANSACTION_FORMAT = "mcel-project-edit-transaction-v1"
RECEIPT_FORMAT = "mcel-project-edit-apply-receipt-v1"
SUPPORTED_OPERATIONS = {"modify", "create"}
DEFAULT_VALIDATION_TIMEOUT_SECONDS = 120
MAX_VALIDATION_TIMEOUT_SECONDS = 600
MAX_CAPTURE_CHARS = 40_000
SKIP_STAGE_NAMES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "tools/patching/reports",
}


class ProjectEditTransactionError(RuntimeError):
    """A bounded transaction failed before it could be trusted."""

    def __init__(
        self,
        stage: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "format": TRANSACTION_FORMAT,
            "failed_stage": self.stage,
            "reason": str(self),
            "details": self.details,
        }


@dataclass(frozen=True)
class NormalizedChange:
    operation: str
    project_relative_path: str
    repo_relative_path: str
    expected_before_sha256: str | None
    replacement_bytes: bytes
    replacement_sha256: str


@dataclass(frozen=True)
class ValidationCommand:
    argv: tuple[str, ...]
    cwd: str
    timeout_seconds: int


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256_bytes(encoded)


def _has_windows_drive_designator(part: str) -> bool:
    return len(part) >= 2 and part[0].isalpha() and part[1] == ":"


def normalize_relative_path(raw: object, *, allow_dot: bool = False) -> str:
    value = str(raw if raw is not None else "").replace("\\", "/").strip()
    if "\x00" in value:
        raise ProjectEditTransactionError("request_validation", "Path contains a NUL byte")
    if allow_dot and value in {"", "."}:
        return "."
    normalized = PurePosixPath(value)
    parts = [part for part in normalized.parts if part not in {"", "."}]
    if not parts:
        raise ProjectEditTransactionError("request_validation", "Empty relative path is not allowed")
    if normalized.is_absolute():
        raise ProjectEditTransactionError("request_validation", f"Absolute path is not allowed: {value}")
    if any(part == ".." for part in parts):
        raise ProjectEditTransactionError("request_validation", f"Parent traversal is not allowed: {value}")
    if any(_has_windows_drive_designator(part) for part in parts):
        raise ProjectEditTransactionError(
            "request_validation",
            f"Windows drive designator is not allowed: {value}",
        )
    return "/".join(parts)


def _safe_path(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise ProjectEditTransactionError(
            "path_safety",
            f"Resolved path escapes the allowed root: {relative}",
        ) from exc
    return target


def _ensure_no_symlink_path(root: Path, relative: str, *, allow_missing_leaf: bool) -> Path:
    root = root.resolve()
    current = root
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        current = current / part
        leaf = index == len(parts) - 1
        if current.is_symlink():
            raise ProjectEditTransactionError(
                "path_safety",
                f"Symlink-backed transaction paths are not supported: {relative}",
            )
        if not current.exists() and not (allow_missing_leaf and leaf):
            # Missing intermediate directories are allowed for create operations,
            # but their nearest existing parent must still be inside the root.
            break
    resolved = current.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ProjectEditTransactionError(
            "path_safety",
            f"Transaction path escapes the project root: {relative}",
        ) from exc
    return root / relative


def _read_replacement_bytes(change: Mapping[str, Any]) -> bytes:
    present = [
        key
        for key in ("replacement_text", "replacement_bytes", "replacement_file")
        if change.get(key) is not None
    ]
    if len(present) != 1:
        raise ProjectEditTransactionError(
            "request_validation",
            "Each change must provide exactly one of replacement_text, replacement_bytes, or replacement_file",
        )

    key = present[0]
    if key == "replacement_text":
        value = change[key]
        if not isinstance(value, str):
            raise ProjectEditTransactionError("request_validation", "replacement_text must be a string")
        payload = value.encode("utf-8")
    elif key == "replacement_bytes":
        value = change[key]
        if not isinstance(value, (bytes, bytearray)):
            raise ProjectEditTransactionError("request_validation", "replacement_bytes must be bytes")
        payload = bytes(value)
    else:
        value = change[key]
        if not isinstance(value, (str, os.PathLike)):
            raise ProjectEditTransactionError("request_validation", "replacement_file must be a path")
        replacement_path = Path(value).resolve()
        if not replacement_path.is_file():
            raise ProjectEditTransactionError(
                "request_validation",
                f"replacement_file does not exist: {replacement_path}",
            )
        payload = replacement_path.read_bytes()

    if b"\x00" in payload:
        raise ProjectEditTransactionError(
            "request_validation",
            "Binary replacement payloads are not supported in transaction version one",
        )
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProjectEditTransactionError(
            "request_validation",
            "Replacement payload is not valid UTF-8 text",
        ) from exc
    return payload


def _normalize_project_root(repo_root: Path, raw: object) -> tuple[str, Path]:
    relative = normalize_relative_path(raw, allow_dot=True)
    project_root = repo_root if relative == "." else _safe_path(repo_root, relative)
    if not project_root.is_dir():
        raise ProjectEditTransactionError(
            "project_detection",
            f"Project root does not exist or is not a directory: {relative}",
        )
    if project_root.is_symlink():
        raise ProjectEditTransactionError(
            "path_safety",
            f"Symlink-backed project roots are not supported: {relative}",
        )
    return relative, project_root


def _normalize_changes(
    *,
    repo_root: Path,
    project_root_relative: str,
    project_root: Path,
    changes: Sequence[Mapping[str, Any]],
) -> list[NormalizedChange]:
    if not isinstance(changes, Sequence) or isinstance(changes, (str, bytes)) or not changes:
        raise ProjectEditTransactionError(
            "request_validation",
            "At least one project file change is required",
        )

    normalized: list[NormalizedChange] = []
    seen: set[str] = set()

    for raw_change in changes:
        if not isinstance(raw_change, Mapping):
            raise ProjectEditTransactionError("request_validation", "Each change must be an object")
        operation = str(raw_change.get("operation") or "").strip().lower()
        if operation not in SUPPORTED_OPERATIONS:
            raise ProjectEditTransactionError(
                "request_validation",
                f"Unsupported operation {operation!r}; version one supports modify and create only",
            )
        project_relative = normalize_relative_path(raw_change.get("path"))
        duplicate_key = project_relative.casefold()
        if duplicate_key in seen:
            raise ProjectEditTransactionError(
                "request_validation",
                f"Duplicate transaction path: {project_relative}",
            )
        seen.add(duplicate_key)

        target = _ensure_no_symlink_path(
            project_root,
            project_relative,
            allow_missing_leaf=operation == "create",
        )
        replacement_bytes = _read_replacement_bytes(raw_change)
        replacement_sha256 = sha256_bytes(replacement_bytes)
        declared_replacement_sha = raw_change.get("replacement_sha256")
        if declared_replacement_sha is not None and declared_replacement_sha != replacement_sha256:
            raise ProjectEditTransactionError(
                "request_validation",
                f"Replacement hash mismatch for {project_relative}",
                details={
                    "expected": declared_replacement_sha,
                    "actual": replacement_sha256,
                },
            )

        expected_before = raw_change.get("expected_before_sha256")
        if operation == "modify":
            if not isinstance(expected_before, str) or len(expected_before) != 64:
                raise ProjectEditTransactionError(
                    "request_validation",
                    f"Modify requires a 64-character expected_before_sha256: {project_relative}",
                )
            if not target.is_file():
                raise ProjectEditTransactionError(
                    "source_verification",
                    f"Modify target does not exist as a regular file: {project_relative}",
                )
            actual_before = sha256_file(target)
            if actual_before != expected_before:
                raise ProjectEditTransactionError(
                    "source_verification",
                    f"Source hash mismatch for {project_relative}",
                    details={"expected": expected_before, "actual": actual_before},
                )
            if actual_before == replacement_sha256:
                raise ProjectEditTransactionError(
                    "request_validation",
                    f"Replacement is a no-op for {project_relative}",
                )
        else:
            if expected_before not in {None, ""}:
                raise ProjectEditTransactionError(
                    "request_validation",
                    f"Create must not declare expected_before_sha256: {project_relative}",
                )
            expected_before = None
            if target.exists() or target.is_symlink():
                raise ProjectEditTransactionError(
                    "source_verification",
                    f"Create target already exists: {project_relative}",
                )

        repo_relative = (
            project_relative
            if project_root_relative == "."
            else f"{project_root_relative}/{project_relative}"
        )
        normalized.append(
            NormalizedChange(
                operation=operation,
                project_relative_path=project_relative,
                repo_relative_path=repo_relative,
                expected_before_sha256=expected_before,
                replacement_bytes=replacement_bytes,
                replacement_sha256=replacement_sha256,
            )
        )

    return sorted(normalized, key=lambda item: item.repo_relative_path)


def _normalize_validations(
    validations: Sequence[Mapping[str, Any]] | None,
) -> list[ValidationCommand]:
    result: list[ValidationCommand] = []
    for raw in validations or ():
        if not isinstance(raw, Mapping):
            raise ProjectEditTransactionError("request_validation", "Each validation must be an object")
        argv = raw.get("argv")
        if (
            not isinstance(argv, Sequence)
            or isinstance(argv, (str, bytes))
            or not argv
            or any(not isinstance(item, str) or not item for item in argv)
        ):
            raise ProjectEditTransactionError(
                "request_validation",
                "Validation argv must be a non-empty list of non-empty strings",
            )
        cwd = normalize_relative_path(raw.get("cwd", "."), allow_dot=True)
        timeout_raw = raw.get("timeout_seconds", DEFAULT_VALIDATION_TIMEOUT_SECONDS)
        try:
            timeout = int(timeout_raw)
        except (TypeError, ValueError) as exc:
            raise ProjectEditTransactionError(
                "request_validation",
                "Validation timeout_seconds must be an integer",
            ) from exc
        if timeout < 1 or timeout > MAX_VALIDATION_TIMEOUT_SECONDS:
            raise ProjectEditTransactionError(
                "request_validation",
                f"Validation timeout_seconds must be between 1 and {MAX_VALIDATION_TIMEOUT_SECONDS}",
            )
        result.append(ValidationCommand(tuple(argv), cwd, timeout))
    return result


def _skip_stage_path(relative: str) -> bool:
    parts = [part.lower() for part in relative.replace("\\", "/").split("/") if part]
    lowered = "/".join(parts)
    for item in SKIP_STAGE_NAMES:
        item_parts = item.lower().split("/")
        if len(item_parts) == 1 and item_parts[0] in parts:
            return True
        if lowered == item.lower() or lowered.startswith(f"{item.lower()}/"):
            return True
    return False


def _project_manifest(project_root: Path) -> tuple[list[dict[str, Any]], str]:
    files: list[dict[str, Any]] = []
    for path in sorted(project_root.rglob("*"), key=lambda item: item.relative_to(project_root).as_posix()):
        relative = path.relative_to(project_root).as_posix()
        if _skip_stage_path(relative):
            continue
        if path.is_symlink():
            raise ProjectEditTransactionError(
                "project_staging",
                f"Symlinks are not supported in transaction project trees: {relative}",
            )
        if not path.is_file():
            continue
        files.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return files, _canonical_json_sha256(files)


def inspect_project_manifest(
    *,
    repo_root: Path,
    project_root: object = ".",
) -> dict[str, Any]:
    """Return the exact project manifest used by edit transaction freshness checks."""

    repo_root = Path(repo_root).resolve()
    if not repo_root.is_dir():
        raise ProjectEditTransactionError("repo_detection", f"Repository root does not exist: {repo_root}")
    project_root_relative, project_root_path = _normalize_project_root(repo_root, project_root)
    files, manifest_sha256 = _project_manifest(project_root_path)
    return {
        "ok": True,
        "format": "mcel-project-manifest-v1",
        "repo_root_name": repo_root.name,
        "project_root": project_root_relative,
        "project_manifest_sha256": manifest_sha256,
        "file_count": len(files),
        "files": files,
    }


def _copy_project(project_root: Path, staged_project: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        base = Path(directory)
        ignored: set[str] = set()
        for name in names:
            candidate = base / name
            try:
                relative = candidate.relative_to(project_root).as_posix()
            except ValueError:
                continue
            if _skip_stage_path(relative):
                ignored.add(name)
        return ignored

    shutil.copytree(project_root, staged_project, symlinks=True, ignore=ignore)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.mcel-txn")
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _apply_changes_to_stage(staged_project: Path, changes: Sequence[NormalizedChange]) -> None:
    for change in changes:
        target = _safe_path(staged_project, change.project_relative_path)
        if change.operation == "modify" and not target.is_file():
            raise ProjectEditTransactionError(
                "project_staging",
                f"Staged modify target disappeared: {change.project_relative_path}",
            )
        if change.operation == "create" and target.exists():
            raise ProjectEditTransactionError(
                "project_staging",
                f"Staged create target unexpectedly exists: {change.project_relative_path}",
            )
        _atomic_write_bytes(target, change.replacement_bytes)
        if sha256_file(target) != change.replacement_sha256:
            raise ProjectEditTransactionError(
                "project_staging",
                f"Staged replacement hash mismatch: {change.project_relative_path}",
            )


def _capture(text: str) -> str:
    if len(text) <= MAX_CAPTURE_CHARS:
        return text
    return text[-MAX_CAPTURE_CHARS:]


def _run_validations(
    *,
    staged_project: Path,
    validations: Sequence[ValidationCommand],
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for index, validation in enumerate(validations):
        cwd = staged_project if validation.cwd == "." else _safe_path(staged_project, validation.cwd)
        if not cwd.is_dir():
            raise ProjectEditTransactionError(
                "validation",
                f"Validation cwd is not a staged directory: {validation.cwd}",
            )
        env = os.environ.copy()
        env["MCEL_PROJECT_EDIT_STAGED_ROOT"] = str(staged_project)
        try:
            process = subprocess.run(
                list(validation.argv),
                cwd=cwd,
                env=env,
                shell=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                errors="replace",
                timeout=validation.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            report = {
                "index": index,
                "ok": False,
                "argv": list(validation.argv),
                "cwd": validation.cwd,
                "timeout_seconds": validation.timeout_seconds,
                "returncode": None,
                "stdout": _capture(exc.stdout or ""),
                "stderr": _capture(exc.stderr or ""),
                "timed_out": True,
            }
            reports.append(report)
            raise ProjectEditTransactionError(
                "validation",
                f"Validation timed out: {validation.argv[0]}",
                details={"validations": reports},
            ) from exc

        report = {
            "index": index,
            "ok": process.returncode == 0,
            "argv": list(validation.argv),
            "cwd": validation.cwd,
            "timeout_seconds": validation.timeout_seconds,
            "returncode": process.returncode,
            "stdout": _capture(process.stdout),
            "stderr": _capture(process.stderr),
            "timed_out": False,
        }
        reports.append(report)
        if process.returncode != 0:
            raise ProjectEditTransactionError(
                "validation",
                f"Validation failed with exit code {process.returncode}: {validation.argv[0]}",
                details={"validations": reports},
            )
    return reports


def _validate_artifact_name(raw: str) -> str:
    name = str(raw or "").strip()
    if not name or Path(name).name != name or not name.lower().endswith(".zip"):
        raise ProjectEditTransactionError(
            "request_validation",
            "artifact_name must be a plain .zip filename",
        )
    return name


def _package_overlay(
    *,
    repo_root: Path,
    changes: Sequence[NormalizedChange],
    artifact_path: Path,
) -> list[dict[str, Any]]:
    archive_root = repo_root.name
    if not archive_root:
        raise ProjectEditTransactionError("artifact_packaging", "Repository root has no archive name")
    replacement_files: list[dict[str, Any]] = []
    temporary = artifact_path.with_name(f".{artifact_path.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for change in changes:
                member = f"{archive_root}/{change.repo_relative_path}"
                archive.writestr(member, change.replacement_bytes)
                replacement_files.append(
                    {
                        "operation": change.operation,
                        "path": change.repo_relative_path,
                        "archive_member": member,
                        "expected_before_sha256": change.expected_before_sha256,
                        "replacement_sha256": change.replacement_sha256,
                        "size": len(change.replacement_bytes),
                    }
                )
        temporary.replace(artifact_path)
    finally:
        temporary.unlink(missing_ok=True)
    return replacement_files


def _inspect_artifact(
    *,
    artifact_path: Path,
    archive_root: str,
    expected_members: Iterable[str],
) -> list[str]:
    expected = sorted(expected_members)
    members: list[str] = []
    try:
        with zipfile.ZipFile(artifact_path) as archive:
            for info in archive.infolist():
                raw = info.filename.replace("\\", "/")
                if info.is_dir() or raw.endswith("/"):
                    continue
                normalized = normalize_relative_path(raw)
                if not normalized.startswith(f"{archive_root}/"):
                    raise ProjectEditTransactionError(
                        "artifact_packaging",
                        f"Artifact member is outside {archive_root}/: {normalized}",
                    )
                members.append(normalized)
    except zipfile.BadZipFile as exc:
        raise ProjectEditTransactionError("artifact_packaging", "Artifact is not a valid zip") from exc
    if sorted(members) != expected:
        raise ProjectEditTransactionError(
            "artifact_packaging",
            "Artifact members do not exactly match the replacement set",
            details={"expected": expected, "actual": sorted(members)},
        )
    return sorted(members)


def _run_new_patch_dry_run(
    *,
    repo_root: Path,
    changes: Sequence[NormalizedChange],
    artifact_path: Path,
) -> dict[str, Any]:
    patch_tool = repo_root / "new_patch.py"
    if not patch_tool.is_file():
        raise ProjectEditTransactionError(
            "dry_run",
            "new_patch.py is not available at the repository root",
        )

    with tempfile.TemporaryDirectory(prefix="mcel-project-edit-dryrun-") as temp:
        dry_root = Path(temp)
        shutil.copy2(patch_tool, dry_root / "new_patch.py")
        for change in changes:
            if change.operation != "modify":
                continue
            source = _safe_path(repo_root, change.repo_relative_path)
            destination = _safe_path(dry_root, change.repo_relative_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        command = [sys.executable, "new_patch.py", str(artifact_path), "--dry-run"]
        process = subprocess.run(
            command,
            cwd=dry_root,
            shell=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        report = {
            "ok": process.returncode == 0,
            "returncode": process.returncode,
            "command": f"{sys.executable} new_patch.py {artifact_path.name} --dry-run",
            "stdout": _capture(process.stdout),
            "stderr": _capture(process.stderr),
        }
        if process.returncode != 0:
            raise ProjectEditTransactionError(
                "dry_run",
                "new_patch.py dry-run rejected the project edit artifact",
                details={"dry_run": report},
            )
        return report


def _write_json_atomic(path: Path, payload: Any) -> None:
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write_bytes(path, encoded)


def prepare_project_edit_transaction(
    *,
    repo_root: Path,
    project_root: object,
    changes: Sequence[Mapping[str, Any]],
    output_dir: Path,
    validations: Sequence[Mapping[str, Any]] | None = None,
    artifact_name: str = "mcel-project-edit-overlay.zip",
) -> dict[str, Any]:
    """Stage, validate, package, and dry-run a reviewed replacement-file set.

    This function does not modify the live repository.  On success it returns a
    serializable transaction report and writes the report beside the overlay zip.
    """

    repo_root = Path(repo_root).resolve()
    output_dir = Path(output_dir).resolve()
    if not repo_root.is_dir():
        raise ProjectEditTransactionError("repo_detection", f"Repository root does not exist: {repo_root}")
    project_root_relative, project_root_path = _normalize_project_root(repo_root, project_root)
    try:
        output_dir.relative_to(project_root_path)
    except ValueError:
        pass
    else:
        raise ProjectEditTransactionError(
            "request_validation",
            "output_dir must be outside the edited project root",
        )
    normalized_changes = _normalize_changes(
        repo_root=repo_root,
        project_root_relative=project_root_relative,
        project_root=project_root_path,
        changes=changes,
    )
    normalized_validations = _normalize_validations(validations)
    artifact_name = _validate_artifact_name(artifact_name)

    source_files, source_manifest_sha256 = _project_manifest(project_root_path)
    plan_core = {
        "project_root": project_root_relative,
        "source_project_manifest_sha256": source_manifest_sha256,
        "changes": [
            {
                "operation": item.operation,
                "path": item.project_relative_path,
                "repo_relative_path": item.repo_relative_path,
                "expected_before_sha256": item.expected_before_sha256,
                "replacement_sha256": item.replacement_sha256,
            }
            for item in normalized_changes
        ],
        "validations": [
            {
                "argv": list(item.argv),
                "cwd": item.cwd,
                "timeout_seconds": item.timeout_seconds,
            }
            for item in normalized_validations
        ],
    }
    transaction_id = _canonical_json_sha256(plan_core)[:24]

    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / artifact_name
    report_path = output_dir / "project_edit_transaction.json"

    with tempfile.TemporaryDirectory(prefix="mcel-project-edit-stage-") as temp:
        staged_project = Path(temp) / "project"
        _copy_project(project_root_path, staged_project)
        _apply_changes_to_stage(staged_project, normalized_changes)
        validation_reports = _run_validations(
            staged_project=staged_project,
            validations=normalized_validations,
        )

    try:
        replacement_files = _package_overlay(
            repo_root=repo_root,
            changes=normalized_changes,
            artifact_path=artifact_path,
        )
        expected_members = [item["archive_member"] for item in replacement_files]
        members = _inspect_artifact(
            artifact_path=artifact_path,
            archive_root=repo_root.name,
            expected_members=expected_members,
        )
        dry_run = _run_new_patch_dry_run(
            repo_root=repo_root,
            changes=normalized_changes,
            artifact_path=artifact_path,
        )
    except Exception:
        artifact_path.unlink(missing_ok=True)
        raise

    report = {
        "ok": True,
        "format": TRANSACTION_FORMAT,
        "state": "prepared",
        "transaction_id": transaction_id,
        "repo_root_name": repo_root.name,
        "project_root": project_root_relative,
        "source_project_manifest_sha256": source_manifest_sha256,
        "source_project_files": source_files,
        "changes": plan_core["changes"],
        "validations": validation_reports,
        "artifact": {
            "path": str(artifact_path),
            "file": artifact_path.name,
            "sha256": sha256_file(artifact_path),
            "members": members,
            "replacement_files": replacement_files,
        },
        "dry_run": dry_run,
        "live_write": False,
        "limitations": {
            "supported_operations": sorted(SUPPORTED_OPERATIONS),
            "delete_supported": False,
            "rename_supported": False,
            "binary_supported": False,
            "crash_atomicity": False,
        },
    }
    _write_json_atomic(report_path, report)
    report["report_path"] = str(report_path)
    return report


def _load_transaction(transaction: Mapping[str, Any] | Path | str) -> tuple[dict[str, Any], Path | None]:
    if isinstance(transaction, Mapping):
        return dict(transaction), None
    report_path = Path(transaction).resolve()
    if not report_path.is_file():
        raise ProjectEditTransactionError(
            "apply_preflight",
            f"Transaction report does not exist: {report_path}",
        )
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectEditTransactionError(
            "apply_preflight",
            f"Transaction report is not valid UTF-8 JSON: {report_path}",
        ) from exc
    if not isinstance(payload, dict):
        raise ProjectEditTransactionError("apply_preflight", "Transaction report must contain an object")
    return payload, report_path


def _artifact_path_for_transaction(report: Mapping[str, Any], report_path: Path | None) -> Path:
    artifact = report.get("artifact")
    if not isinstance(artifact, Mapping):
        raise ProjectEditTransactionError("apply_preflight", "Transaction artifact metadata is missing")
    raw_path = artifact.get("path")
    raw_file = artifact.get("file")
    candidates: list[Path] = []
    if report_path is not None and isinstance(raw_file, str):
        candidates.append(report_path.parent / raw_file)
    if isinstance(raw_path, str):
        candidates.append(Path(raw_path))
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
    raise ProjectEditTransactionError("apply_preflight", "Transaction artifact zip is missing")


def _current_project_manifest(project_root: Path) -> tuple[list[dict[str, Any]], str]:
    return _project_manifest(project_root)


def _compare_manifest(
    source_files: Sequence[Mapping[str, Any]],
    current_files: Sequence[Mapping[str, Any]],
    touched_paths: set[str],
) -> list[dict[str, Any]]:
    before = {
        str(item.get("path")): str(item.get("sha256"))
        for item in source_files
        if isinstance(item, Mapping) and item.get("path")
    }
    current = {
        str(item.get("path")): str(item.get("sha256"))
        for item in current_files
        if isinstance(item, Mapping) and item.get("path")
    }
    drift: list[dict[str, Any]] = []
    for path in sorted(set(before) | set(current)):
        if path in touched_paths:
            continue
        if before.get(path) != current.get(path):
            drift.append(
                {
                    "path": path,
                    "source_sha256": before.get(path),
                    "current_sha256": current.get(path),
                }
            )
    return drift


def apply_project_edit_transaction(
    *,
    repo_root: Path,
    transaction: Mapping[str, Any] | Path | str,
    reviewed: bool,
    require_project_manifest: bool = False,
    receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Apply a prepared transaction after rechecking every authority boundary.

    ``reviewed=True`` is mandatory.  Each file is replaced atomically; if an
    ordinary write fails, already written files are restored best-effort.  A
    process or machine crash can still interrupt a multi-file set, so version one
    does not claim filesystem-level crash atomicity.
    """

    if reviewed is not True:
        raise ProjectEditTransactionError(
            "apply_authorization",
            "Reviewed apply requires reviewed=True",
        )

    repo_root = Path(repo_root).resolve()
    report, report_path = _load_transaction(transaction)
    if report.get("format") != TRANSACTION_FORMAT or report.get("state") != "prepared" or report.get("ok") is not True:
        raise ProjectEditTransactionError(
            "apply_preflight",
            "Transaction is not a prepared MCEL project edit transaction",
        )
    if report.get("repo_root_name") != repo_root.name:
        raise ProjectEditTransactionError(
            "apply_preflight",
            f"Repository root name mismatch: expected {report.get('repo_root_name')}, found {repo_root.name}",
        )
    dry_run = report.get("dry_run")
    if not isinstance(dry_run, Mapping) or dry_run.get("ok") is not True:
        raise ProjectEditTransactionError(
            "apply_preflight",
            "Transaction does not contain a successful new_patch.py dry-run",
        )

    project_root_relative, project_root = _normalize_project_root(repo_root, report.get("project_root"))
    raw_changes = report.get("changes")
    if not isinstance(raw_changes, list) or not raw_changes:
        raise ProjectEditTransactionError("apply_preflight", "Transaction contains no changes")

    artifact_path = _artifact_path_for_transaction(report, report_path)
    artifact = report["artifact"]
    expected_artifact_sha = artifact.get("sha256")
    actual_artifact_sha = sha256_file(artifact_path)
    if expected_artifact_sha != actual_artifact_sha:
        raise ProjectEditTransactionError(
            "apply_preflight",
            "Transaction artifact hash mismatch",
            details={"expected": expected_artifact_sha, "actual": actual_artifact_sha},
        )

    parsed: list[dict[str, Any]] = []
    expected_members: list[str] = []
    seen: set[str] = set()
    for item in raw_changes:
        if not isinstance(item, Mapping):
            raise ProjectEditTransactionError("apply_preflight", "Invalid transaction change entry")
        operation = str(item.get("operation") or "")
        if operation not in SUPPORTED_OPERATIONS:
            raise ProjectEditTransactionError("apply_preflight", f"Unsupported transaction operation: {operation}")
        project_relative = normalize_relative_path(item.get("path"))
        repo_relative = (
            project_relative if project_root_relative == "." else f"{project_root_relative}/{project_relative}"
        )
        if item.get("repo_relative_path") != repo_relative:
            raise ProjectEditTransactionError(
                "apply_preflight",
                f"Transaction path mapping mismatch: {project_relative}",
            )
        key = project_relative.casefold()
        if key in seen:
            raise ProjectEditTransactionError("apply_preflight", f"Duplicate transaction path: {project_relative}")
        seen.add(key)
        member = f"{repo_root.name}/{repo_relative}"
        expected_members.append(member)
        parsed.append(
            {
                "operation": operation,
                "project_relative_path": project_relative,
                "repo_relative_path": repo_relative,
                "member": member,
                "expected_before_sha256": item.get("expected_before_sha256"),
                "replacement_sha256": item.get("replacement_sha256"),
            }
        )

    _inspect_artifact(
        artifact_path=artifact_path,
        archive_root=repo_root.name,
        expected_members=expected_members,
    )

    replacement_payloads: dict[str, bytes] = {}
    with zipfile.ZipFile(artifact_path) as archive:
        for item in parsed:
            payload = archive.read(item["member"])
            if b"\x00" in payload:
                raise ProjectEditTransactionError("apply_preflight", f"Binary artifact member is unsupported: {item['member']}")
            try:
                payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ProjectEditTransactionError(
                    "apply_preflight",
                    f"Artifact member is not UTF-8 text: {item['member']}",
                ) from exc
            actual_sha = sha256_bytes(payload)
            if actual_sha != item["replacement_sha256"]:
                raise ProjectEditTransactionError(
                    "apply_preflight",
                    f"Replacement hash mismatch in artifact: {item['project_relative_path']}",
                    details={"expected": item["replacement_sha256"], "actual": actual_sha},
                )
            replacement_payloads[item["project_relative_path"]] = payload

    touched_paths = {item["project_relative_path"] for item in parsed}
    source_files = report.get("source_project_files")
    current_files, current_manifest_sha = _current_project_manifest(project_root)
    drift = _compare_manifest(
        source_files if isinstance(source_files, list) else [],
        current_files,
        touched_paths,
    )
    if require_project_manifest and drift:
        raise ProjectEditTransactionError(
            "apply_preflight",
            "Project files outside the transaction changed since preparation",
            details={"unrelated_drift": drift},
        )

    originals: dict[str, bytes | None] = {}
    targets: dict[str, Path] = {}
    for item in parsed:
        relative = item["project_relative_path"]
        target = _ensure_no_symlink_path(
            project_root,
            relative,
            allow_missing_leaf=item["operation"] == "create",
        )
        targets[relative] = target
        if item["operation"] == "modify":
            if not target.is_file():
                raise ProjectEditTransactionError(
                    "apply_preflight",
                    f"Modify target no longer exists: {relative}",
                )
            actual_before = sha256_file(target)
            if actual_before != item["expected_before_sha256"]:
                raise ProjectEditTransactionError(
                    "apply_preflight",
                    f"Live source hash drifted for {relative}",
                    details={"expected": item["expected_before_sha256"], "actual": actual_before},
                )
            originals[relative] = target.read_bytes()
        else:
            if target.exists() or target.is_symlink():
                raise ProjectEditTransactionError(
                    "apply_preflight",
                    f"Create target now exists: {relative}",
                )
            originals[relative] = None

    written: list[str] = []
    rollback_errors: list[str] = []
    try:
        for item in parsed:
            relative = item["project_relative_path"]
            _atomic_write_bytes(targets[relative], replacement_payloads[relative])
            if sha256_file(targets[relative]) != item["replacement_sha256"]:
                raise OSError(f"post-write hash mismatch for {relative}")
            written.append(relative)
    except Exception as exc:
        for relative in reversed(written):
            try:
                original = originals[relative]
                target = targets[relative]
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    _atomic_write_bytes(target, original)
            except Exception as rollback_exc:  # pragma: no cover - exceptional filesystem failure
                rollback_errors.append(f"{relative}: {rollback_exc}")
        raise ProjectEditTransactionError(
            "apply_write",
            f"Project edit apply failed and rollback was attempted: {exc}",
            details={
                "written_before_failure": written,
                "rollback_ok": not rollback_errors,
                "rollback_errors": rollback_errors,
            },
        ) from exc

    files = [
        {
            "operation": item["operation"],
            "path": item["repo_relative_path"],
            "project_relative_path": item["project_relative_path"],
            "before_sha256": item["expected_before_sha256"],
            "after_sha256": item["replacement_sha256"],
            "written_sha256": sha256_file(targets[item["project_relative_path"]]),
        }
        for item in parsed
    ]
    receipt = {
        "ok": True,
        "format": RECEIPT_FORMAT,
        "transaction_id": report.get("transaction_id"),
        "project_root": project_root_relative,
        "artifact_sha256": actual_artifact_sha,
        "source_project_manifest_sha256": report.get("source_project_manifest_sha256"),
        "pre_apply_project_manifest_sha256": current_manifest_sha,
        "unrelated_project_drift": drift,
        "strict_project_manifest_required": require_project_manifest,
        "files": files,
        "rollback_required": False,
        "crash_atomicity": False,
    }

    if receipt_path is None:
        base = report_path.parent if report_path is not None else artifact_path.parent
        receipt_path = base / "project_edit_apply_receipt.json"
    receipt_path = Path(receipt_path).resolve()
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(receipt_path, receipt)
    receipt["receipt_path"] = str(receipt_path)
    return receipt
