from __future__ import annotations

"""Apply edited OpenClaw Markdown persistence exports back to a workspace.

The companion extractor intentionally exports exact Markdown file payloads. This
script is the inverse operation for those JSON/JSONL exports: it writes edited
``file.text`` payloads back into the OpenClaw persistence workspace using
path-safety checks, expected-current SHA checks, backups, and readback
verification.

Important convention:
  The exported ``file.sha256`` is treated as the expected current SHA-256 of the
  target file at the time of extraction. You may edit ``file.text`` without
  updating ``file.sha256``. The unchanged SHA lets this script detect whether
  OpenClaw memory changed since the export was taken.
"""

import argparse
import datetime as _dt
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


SUPPORTED_SCHEMA_VERSION = "main-computer.openclaw-persistence-export.v1"
APPLY_SCHEMA_VERSION = "main-computer.openclaw-persistence-apply.v1"
MEMORY_FILE_NAMES = {"MEMORY.md", "DREAMS.md"}


class ApplyError(RuntimeError):
    """The export cannot be safely applied."""


def utc_now_compact() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def default_memory_root() -> Path:
    return Path(os.environ.get("OPENCLAW_WORKSPACE", "~/.openclaw/workspace")).expanduser()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_memory_relative(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ApplyError(f"invalid relative_path: {raw!r}")
    value = raw.replace("\\", "/").strip()
    candidate = Path(value)
    if candidate.is_absolute():
        raise ApplyError(f"absolute paths are not allowed in memory exports: {raw!r}")
    parts = tuple(part for part in value.split("/") if part)
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ApplyError(f"unsafe relative_path in memory export: {raw!r}")

    normalized = "/".join(parts)
    if normalized in MEMORY_FILE_NAMES:
        return normalized
    if len(parts) >= 2 and parts[0] == "memory" and normalized.lower().endswith(".md"):
        return normalized
    raise ApplyError(
        "unsupported OpenClaw memory path "
        f"{raw!r}; expected MEMORY.md, DREAMS.md, or memory/**/*.md"
    )


def target_for(memory_root: Path, relative_path: str) -> Path:
    root = memory_root.resolve()
    target = (root / Path(relative_path)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ApplyError(f"target path escapes memory root: {relative_path}") from exc
    return target


def load_json_export(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ApplyError("JSON export must be an object")
    return payload


def load_jsonl_export(path: Path) -> dict[str, Any]:
    manifest: dict[str, Any] | None = None
    files: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ApplyError(f"JSONL line {line_no} is not an object")
            record_type = record.get("record_type")
            if record_type == "manifest":
                manifest = {key: value for key, value in record.items() if key != "record_type"}
            elif record_type == "file":
                file_record = {key: value for key, value in record.items() if key != "record_type"}
                files.append(file_record)
    if manifest is None:
        raise ApplyError("JSONL export is missing a manifest record")
    manifest["files"] = files
    return manifest


def load_export(path: Path, fmt: str = "auto") -> dict[str, Any]:
    selected = fmt
    if selected == "auto":
        suffix = path.suffix.lower()
        if suffix == ".jsonl":
            selected = "jsonl"
        elif suffix == ".json":
            selected = "json"
        elif suffix in {".md", ".markdown"}:
            raise ApplyError(
                "Markdown exports are review-only for pushback. "
                "Edit the JSON or JSONL export so exact file.text payloads and SHA guards are preserved."
            )
        else:
            selected = "json"

    if selected == "json":
        return load_json_export(path)
    if selected == "jsonl":
        return load_jsonl_export(path)
    raise ApplyError(f"unsupported export format: {fmt}")


def validate_export(payload: dict[str, Any]) -> list[dict[str, Any]]:
    schema_version = payload.get("schema_version")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ApplyError(
            f"unsupported export schema_version {schema_version!r}; "
            f"expected {SUPPORTED_SCHEMA_VERSION!r}"
        )
    files = payload.get("files")
    if not isinstance(files, list):
        raise ApplyError("export payload is missing a files array")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, file_record in enumerate(files):
        if not isinstance(file_record, dict):
            raise ApplyError(f"files[{index}] is not an object")
        relative_path = normalize_memory_relative(file_record.get("relative_path"))
        if relative_path in seen:
            raise ApplyError(f"duplicate file record for {relative_path}")
        seen.add(relative_path)

        text = file_record.get("text")
        if not isinstance(text, str):
            raise ApplyError(f"{relative_path} is missing exact text; re-export without --no-full-text")

        expected_sha = file_record.get("sha256")
        if expected_sha is not None and not isinstance(expected_sha, str):
            raise ApplyError(f"{relative_path} sha256 must be a string when present")
        if expected_sha and len(expected_sha) != 64:
            raise ApplyError(f"{relative_path} sha256 is not a 64-character hex digest")

        normalized.append(
            {
                "relative_path": relative_path,
                "text": text,
                "expected_current_sha256": expected_sha or "",
                "desired_sha256": sha256_text(text),
                "source_size_bytes": file_record.get("size_bytes"),
                "newline_style": file_record.get("newline_style"),
            }
        )
    return normalized


def copy_backup(source: Path, memory_root: Path, backup_root: Path) -> str | None:
    if not source.exists():
        return None
    relative = source.resolve().relative_to(memory_root.resolve())
    target = backup_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return str(target)


def atomic_write_text(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temp_path, target)
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def plan_and_apply(
    *,
    export_path: Path,
    memory_root: Path,
    fmt: str = "auto",
    dry_run: bool = False,
    allow_create: bool = False,
    skip_current_sha_check: bool = False,
    no_backup: bool = False,
    backup_dir: Path | None = None,
    verify_after: bool = False,
) -> dict[str, Any]:
    payload = load_export(export_path, fmt)
    files = validate_export(payload)

    memory_root = memory_root.expanduser().resolve()
    backup_root = (
        backup_dir.expanduser().resolve()
        if backup_dir is not None
        else (memory_root.parent / "backups" / f"apply-openclaw-persistence-{utc_now_compact()}").resolve()
    )

    results: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    changed = 0
    created = 0
    unchanged = 0
    already_applied = 0

    for file_record in files:
        relative_path = file_record["relative_path"]
        target = target_for(memory_root, relative_path)
        desired_text = file_record["text"]
        desired_sha = file_record["desired_sha256"]
        expected_sha = file_record["expected_current_sha256"]

        exists = target.exists()
        current_sha = sha256_file(target) if exists else None

        status = "pending"
        reason = ""
        backup_path = None

        if exists and current_sha == desired_sha:
            status = "already_applied" if expected_sha and current_sha != expected_sha else "unchanged"
            if status == "already_applied":
                already_applied += 1
            else:
                unchanged += 1
        elif not exists and not allow_create:
            status = "conflict"
            reason = "target_missing; pass --allow-create to create exported memory files"
            conflicts.append({"relative_path": relative_path, "reason": reason})
        elif exists and expected_sha and current_sha != expected_sha and not skip_current_sha_check:
            status = "conflict"
            reason = (
                "current_sha_mismatch; memory changed since export "
                f"(current={current_sha}, expected={expected_sha})"
            )
            conflicts.append({"relative_path": relative_path, "reason": reason})
        else:
            status = "would_create" if not exists else "would_update"
            if not dry_run:
                if exists and not no_backup:
                    backup_path = copy_backup(target, memory_root, backup_root)
                atomic_write_text(target, desired_text)
                readback_sha = sha256_file(target)
                if verify_after and readback_sha != desired_sha:
                    raise ApplyError(
                        f"readback verification failed for {relative_path}: "
                        f"got {readback_sha}, expected {desired_sha}"
                    )
                status = "created" if not exists else "updated"
                if exists:
                    changed += 1
                else:
                    created += 1

        results.append(
            {
                "relative_path": relative_path,
                "target_path": str(target),
                "status": status,
                "reason": reason,
                "current_sha256": current_sha,
                "expected_current_sha256": expected_sha,
                "desired_sha256": desired_sha,
                "backup_path": backup_path,
                "text_size_bytes": len(desired_text.encode("utf-8")),
            }
        )

    ok = not conflicts
    return {
        "ok": ok,
        "apply": "openclaw-persistence-pushback",
        "schema_version": APPLY_SCHEMA_VERSION,
        "export_schema_version": payload.get("schema_version"),
        "generated_at_utc": utc_now_iso(),
        "dry_run": dry_run,
        "memory_root": str(memory_root),
        "export_path": str(export_path),
        "backup_root": None if no_backup or dry_run else str(backup_root),
        "stats": {
            "file_count": len(files),
            "changed": changed,
            "created": created,
            "unchanged": unchanged,
            "already_applied": already_applied,
            "conflict_count": len(conflicts),
        },
        "conflicts": conflicts,
        "files": results,
    }


def run_self_test() -> dict[str, Any]:
    root = Path(tempfile.gettempdir()) / f"openclaw-persistence-apply-selftest-{os.getpid()}"
    if root.exists():
        shutil.rmtree(root)
    try:
        memory_root = root / "workspace"
        memory_file = memory_root / "memory" / "2099-01-01.md"
        memory_file.parent.mkdir(parents=True)
        original = "# Daily\n\nRemember alpha.\n"
        updated = "# Daily\n\nRemember alpha.\nRemember beta.\n"
        memory_file.write_text(original, encoding="utf-8", newline="\n")

        export = {
            "schema_version": SUPPORTED_SCHEMA_VERSION,
            "generated_at_utc": utc_now_iso(),
            "memory_root": str(memory_root),
            "stats": {"file_count": 1},
            "files": [
                {
                    "relative_path": "memory/2099-01-01.md",
                    "sha256": sha256_text(original),
                    "text": updated,
                    "size_bytes": len(original.encode("utf-8")),
                    "newline_style": "lf",
                }
            ],
        }
        export_path = root / "edited-export.json"
        export_path.write_text(json.dumps(export, indent=2), encoding="utf-8")

        dry = plan_and_apply(
            export_path=export_path,
            memory_root=memory_root,
            dry_run=True,
            verify_after=True,
        )
        if dry["stats"]["changed"] != 0:
            raise ApplyError("dry-run must not count committed changes")

        result = plan_and_apply(
            export_path=export_path,
            memory_root=memory_root,
            verify_after=True,
        )
        if not result["ok"]:
            raise ApplyError(f"self-test apply produced conflicts: {result['conflicts']}")
        if memory_file.read_text(encoding="utf-8") != updated:
            raise ApplyError("self-test failed to write edited memory text")

        repeated = plan_and_apply(
            export_path=export_path,
            memory_root=memory_root,
            verify_after=True,
        )
        if repeated["stats"]["already_applied"] != 1:
            raise ApplyError("self-test failed already-applied detection")

        result["self_test_repeated"] = repeated["stats"]
        return result
    finally:
        if root.exists():
            shutil.rmtree(root)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply edited OpenClaw persistence JSON/JSONL exports back to a Markdown memory workspace."
    )
    parser.add_argument("--memory-root", type=Path, default=default_memory_root())
    parser.add_argument("--export", type=Path, required=False, help="Edited JSON or JSONL export from extract_openclaw_persistence.py")
    parser.add_argument("--format", choices=("auto", "json", "jsonl"), default="auto")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-create", action="store_true")
    parser.add_argument("--skip-current-sha-check", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--verify-after", action="store_true")
    parser.add_argument("--summary-json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    try:
        if args.self_test:
            result = run_self_test()
            print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
            return 0

        if args.export is None:
            raise ApplyError("--export is required unless --self-test is used")

        result = plan_and_apply(
            export_path=args.export,
            memory_root=args.memory_root,
            fmt=args.format,
            dry_run=args.dry_run,
            allow_create=args.allow_create,
            skip_current_sha_check=args.skip_current_sha_check,
            no_backup=args.no_backup,
            backup_dir=args.backup_dir,
            verify_after=args.verify_after,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
        return 0 if result["ok"] else 2
    except (ApplyError, OSError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "apply": "openclaw-persistence-pushback",
                    "error": str(exc),
                },
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
