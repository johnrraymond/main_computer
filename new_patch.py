from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable


REPORT_ROOT = Path("tools") / "patching" / "reports" / "new_patch_runs"


class PatchApplicationError(RuntimeError):
    pass


@dataclass(frozen=True)
class FileChange:
    path: str
    operation: str
    sha256: str | None = None


@dataclass(frozen=True)
class BundleData:
    root: Path
    manifest_path: Path
    reference_patch_path: Path | None
    files_root: Path
    changes: tuple[FileChange, ...]


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def read_text_for_diff(path: Path) -> list[str]:
    data = path.read_bytes()
    if b"\x00" in data:
        raise PatchApplicationError(f"Binary file changes are not supported: {path}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PatchApplicationError(f"Non-UTF-8 file changes are not supported: {path}") from exc
    return text.splitlines(keepends=True)


def normalize_repo_relative(path: PurePosixPath | Path | str) -> str:
    raw = str(path).replace("\\", "/")
    normalized = PurePosixPath(raw)
    if normalized.is_absolute():
        raise PatchApplicationError(f"Absolute paths are not allowed: {path}")
    parts = [part for part in normalized.parts if part not in ("", ".")]
    if not parts:
        raise PatchApplicationError("Empty repository-relative path is not allowed")
    if any(part == ".." for part in parts):
        raise PatchApplicationError(f"Parent path traversal is not allowed: {path}")
    return "/".join(parts)


def has_windows_drive_designator(part: str) -> bool:
    return len(part) >= 2 and part[0].isalpha() and part[1] == ":"


def normalize_zip_member_path(raw_name: str) -> PurePosixPath | None:
    """Return a safe extraction path for one zip member.

    Zip archives can contain POSIX absolute paths, Windows absolute paths, and
    parent traversal segments.  Validate before extraction so malicious raw
    snapshot zips cannot escape the temporary extraction root or be normalized
    into misleading repository-relative paths.
    """

    raw = raw_name.replace("\\", "/")
    if "\x00" in raw:
        raise PatchApplicationError(f"Unsafe zip member path contains a NUL byte: {raw_name!r}")

    normalized = PurePosixPath(raw)
    parts = [part for part in normalized.parts if part not in ("", ".")]

    if not parts:
        return None
    if normalized.is_absolute():
        raise PatchApplicationError(f"Unsafe zip member path is absolute: {raw_name}")
    if any(part == ".." for part in parts):
        raise PatchApplicationError(f"Unsafe zip member path contains parent traversal: {raw_name}")
    if any(has_windows_drive_designator(part) for part in parts):
        raise PatchApplicationError(f"Unsafe zip member path contains a Windows drive designator: {raw_name}")

    return PurePosixPath(*parts)


def safe_target_path(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    root_resolved = root.resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise PatchApplicationError(f"Resolved path escapes target root: {relative}") from exc
    return target


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def normalized_patch_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def collect_files(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = normalize_repo_relative(path.relative_to(root).as_posix())
        result[relative] = path
    return result


def build_unified_diff(old_root: Path, files_root: Path, changes: Iterable[FileChange]) -> str:
    chunks: list[str] = []
    for change in sorted(changes, key=lambda item: item.path):
        old_path = safe_target_path(old_root, change.path)
        new_path = safe_target_path(files_root, change.path)
        if change.operation == "delete":
            old_lines = read_text_for_diff(old_path)
            new_lines: list[str] = []
            fromfile = f"a/{change.path}"
            tofile = "/dev/null"
        elif change.operation == "create":
            old_lines = []
            new_lines = read_text_for_diff(new_path)
            fromfile = "/dev/null"
            tofile = f"b/{change.path}"
        elif change.operation == "modify":
            old_lines = read_text_for_diff(old_path)
            new_lines = read_text_for_diff(new_path)
            fromfile = f"a/{change.path}"
            tofile = f"b/{change.path}"
        else:
            raise PatchApplicationError(f"Unsupported operation: {change.operation}")
        diff_text = "".join(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=fromfile,
                tofile=tofile,
                lineterm="\n",
            )
        )
        if diff_text:
            chunks.append(diff_text)
    return "".join(chunks)


def detect_single_top_level_dir(paths: list[PurePosixPath]) -> str | None:
    top_parts = {path.parts[0] for path in paths if path.parts}
    if len(top_parts) != 1:
        return None
    return next(iter(top_parts))


def extract_zip_normalized(zip_path: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            normalized = normalize_zip_member_path(info.filename)
            if normalized is None:
                continue

            raw_name = info.filename
            is_dir = info.is_dir() or raw_name.endswith("/") or raw_name.endswith("\\")
            dest = (destination / normalized.as_posix()).resolve()
            try:
                dest.relative_to(destination_resolved)
            except ValueError as exc:
                raise PatchApplicationError(f"Unsafe zip member path escapes extraction root: {raw_name}") from exc

            if is_dir:
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, dest.open("wb") as handle:
                shutil.copyfileobj(source, handle)


def find_bundle_root(extracted_root: Path) -> Path | None:
    direct = extracted_root
    if (direct / "manifest.json").exists() and (direct / "files").is_dir():
        return direct
    children = [child for child in extracted_root.iterdir() if child.is_dir()]
    if len(children) == 1:
        child = children[0]
        if (child / "manifest.json").exists() and (child / "files").is_dir():
            return child
    return None


def find_snapshot_root(extracted_root: Path) -> Path:
    files = [PurePosixPath(str(path.relative_to(extracted_root)).replace("\\", "/")) for path in extracted_root.rglob("*") if path.is_file()]
    top_dir = detect_single_top_level_dir(files)
    if top_dir and (extracted_root / top_dir).is_dir():
        return extracted_root / top_dir
    return extracted_root


def snapshot_changes(snapshot_root: Path, target_root: Path) -> tuple[FileChange, ...]:
    changes: list[FileChange] = []
    for relative, source_path in collect_files(snapshot_root).items():
        target_path = safe_target_path(target_root, relative)
        if not target_path.exists():
            op = "create"
        elif target_path.read_bytes() != source_path.read_bytes():
            op = "modify"
        else:
            continue
        changes.append(FileChange(path=relative, operation=op, sha256=sha256_file(source_path)))
    return tuple(sorted(changes, key=lambda item: item.path))


def write_bundle(
    bundle_root: Path,
    source_root: Path,
    changes: tuple[FileChange, ...],
    reference_patch: str | None,
) -> BundleData:
    files_root = bundle_root / "files"
    files_root.mkdir(parents=True, exist_ok=True)
    for change in changes:
        if change.operation == "delete":
            continue
        source_path = safe_target_path(source_root, change.path)
        destination = safe_target_path(files_root, change.path)
        ensure_parent(destination)
        shutil.copy2(source_path, destination)
    manifest = {
        "format": 1,
        "changes": [
            {"path": change.path, "operation": change.operation, "sha256": change.sha256}
            for change in changes
        ],
    }
    manifest_path = bundle_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    reference_patch_path: Path | None = None
    if reference_patch is not None:
        reference_patch_path = bundle_root / "reference.patch"
        reference_patch_path.write_text(reference_patch, encoding="utf-8")
    return BundleData(
        root=bundle_root,
        manifest_path=manifest_path,
        reference_patch_path=reference_patch_path,
        files_root=files_root,
        changes=changes,
    )


def load_bundle(bundle_root: Path) -> BundleData:
    manifest_path = bundle_root / "manifest.json"
    reference_patch_path = bundle_root / "reference.patch"
    files_root = bundle_root / "files"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    changes = tuple(
        FileChange(
            path=normalize_repo_relative(item["path"]),
            operation=item["operation"],
            sha256=item.get("sha256"),
        )
        for item in manifest.get("changes", [])
    )
    return BundleData(
        root=bundle_root,
        manifest_path=manifest_path,
        reference_patch_path=reference_patch_path if reference_patch_path.exists() else None,
        files_root=files_root,
        changes=changes,
    )


def compute_verification(bundle: BundleData, target_root: Path) -> tuple[str, str, str | None]:
    actual = normalized_patch_text(build_unified_diff(target_root, bundle.files_root, bundle.changes))
    if bundle.reference_patch_path is None:
        return "no_reference", actual, None
    expected = normalized_patch_text(bundle.reference_patch_path.read_text(encoding="utf-8"))
    return ("exact" if actual == expected else "fuzzy"), actual, expected


def validate_bundle_targets(bundle: BundleData, target_root: Path) -> None:
    for change in bundle.changes:
        target_path = safe_target_path(target_root, change.path)
        staged_path = safe_target_path(bundle.files_root, change.path)
        if change.operation in {"create", "modify"}:
            if not staged_path.exists():
                raise PatchApplicationError(f"Bundle is missing replacement file: {change.path}")
            if change.sha256 and sha256_file(staged_path) != change.sha256:
                raise PatchApplicationError(f"Bundle file hash mismatch: {change.path}")
        if change.operation == "modify" and not target_path.exists():
            raise PatchApplicationError(f"Expected existing target file is missing: {change.path}")
        if change.operation == "delete" and not target_path.exists():
            raise PatchApplicationError(f"Expected delete target file is missing: {change.path}")


def apply_bundle(bundle: BundleData, target_root: Path) -> None:
    for change in bundle.changes:
        target_path = safe_target_path(target_root, change.path)
        staged_path = safe_target_path(bundle.files_root, change.path)
        if change.operation == "delete":
            if target_path.exists():
                target_path.unlink()
            continue
        ensure_parent(target_path)
        shutil.copy2(staged_path, target_path)


def create_run_dir(repo_root: Path) -> Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = next(tempfile._get_candidate_names())[:6]
    run_dir = repo_root / REPORT_ROOT / f"{stamp}-{suffix}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def write_run_report(run_dir: Path, payload: dict) -> None:
    (run_dir / "run.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_bundle_from_snapshot(zip_path: Path, target_root: Path, run_dir: Path) -> BundleData:
    extracted = run_dir / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    extract_zip_normalized(zip_path, extracted)
    snapshot_root = find_snapshot_root(extracted)
    changes = snapshot_changes(snapshot_root, target_root)
    bundle_root = run_dir / "bundle"
    return write_bundle(bundle_root, snapshot_root, changes, reference_patch=None)


def bundle_from_zip(zip_path: Path, run_dir: Path) -> BundleData:
    extracted = run_dir / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    extract_zip_normalized(zip_path, extracted)
    bundle_root = find_bundle_root(extracted)
    if bundle_root is None:
        raise PatchApplicationError("Zip file is not a verified patch-set bundle")
    loaded = load_bundle(bundle_root)
    destination = run_dir / "bundle"
    shutil.copytree(bundle_root, destination)
    return load_bundle(destination)


def inverse_change(change: FileChange, target_root: Path) -> FileChange:
    if change.operation == "create":
        return FileChange(path=change.path, operation="delete", sha256=None)
    target_path = safe_target_path(target_root, change.path)
    if change.operation == "modify":
        return FileChange(path=change.path, operation="modify", sha256=sha256_file(target_path))
    if change.operation == "delete":
        return FileChange(path=change.path, operation="create", sha256=sha256_file(target_path))
    raise PatchApplicationError(f"Unsupported operation: {change.operation}")


def build_undo_bundle(bundle: BundleData, target_root: Path, run_dir: Path) -> tuple[BundleData, str, Path]:
    undo_source_root = run_dir / "undo_files"
    undo_source_root.mkdir(parents=True, exist_ok=True)

    undo_changes: list[FileChange] = []
    for change in bundle.changes:
        inverse = inverse_change(change, target_root)
        undo_changes.append(inverse)
        if inverse.operation == "delete":
            continue
        original_path = safe_target_path(target_root, change.path)
        undo_path = safe_target_path(undo_source_root, change.path)
        ensure_parent(undo_path)
        shutil.copy2(original_path, undo_path)

    undo_changes_tuple = tuple(sorted(undo_changes, key=lambda item: item.path))
    undo_patch = build_unified_diff(bundle.files_root, undo_source_root, undo_changes_tuple)
    undo_bundle = write_bundle(run_dir / "undo_bundle", undo_source_root, undo_changes_tuple, undo_patch)
    undo_zip_path = run_dir / "undo_bundle.zip"
    zip_directory(undo_bundle.root, undo_zip_path)
    return undo_bundle, undo_patch, undo_zip_path


def zip_directory(source_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_dir.rglob("*")):
            if path.is_dir():
                continue
            archive.write(path, path.relative_to(source_dir).as_posix())


def quote_command_arg(value: Path | str) -> str:
    text = str(value)
    escaped = text.replace('"', r'\"')
    return f'"{escaped}"'


def undo_command(undo_zip_path: Path, target_root: Path) -> str:
    return f"python new_patch.py {quote_command_arg(undo_zip_path)} --target-root {quote_command_arg(target_root)}"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply a raw snapshot zip or verified patch-set zip.")
    parser.add_argument("zip_path", help="Zip file containing either an exported snapshot or a verified patch-set bundle.")
    parser.add_argument("--target-root", default=None, help="Repository root to modify. Defaults to the directory containing this script.")
    parser.add_argument("--dry-run", action="store_true", help="Build and verify without copying files into the target repository.")
    parser.add_argument("--allowfuzz", action="store_true", help="Allow overwrite when reference.patch exists and does not exactly match the live diff.")
    return parser.parse_args(argv)


def determine_target_root(args: argparse.Namespace) -> Path:
    if args.target_root:
        return Path(args.target_root).resolve()
    return Path(__file__).resolve().parent


def print_run_summary(
    *,
    run_dir: Path,
    bundle: BundleData,
    verification: str,
    changes_count: int,
    actual_patch_path: Path,
    undo_patch_path: Path,
    undo_zip_path: Path,
    target_root: Path,
) -> None:
    print(f"run_dir: {run_dir}")
    print(f"bundle: {bundle.root}")
    print(f"verification: {verification}")
    print(f"changed_files: {changes_count}")
    print(f"actual.patch: {actual_patch_path}")
    print(f"undo.patch: {undo_patch_path}")
    print(f"undo_bundle_zip: {undo_zip_path}")
    print(f"undo command: {undo_command(undo_zip_path, target_root)}")


def print_actual_diff(actual_patch: str) -> None:
    print("actual diff:")
    if actual_patch:
        print(actual_patch, end="" if actual_patch.endswith("\n") else "\n")
    else:
        print("(no changes)")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    zip_path = Path(args.zip_path).resolve()
    if not zip_path.exists():
        print(f"error: zip file not found: {zip_path}", file=sys.stderr)
        return 2
    target_root = determine_target_root(args)
    if not target_root.exists():
        print(f"error: target root not found: {target_root}", file=sys.stderr)
        return 2

    run_dir = create_run_dir(target_root)
    mode = "snapshot"
    try:
        try:
            bundle = bundle_from_zip(zip_path, run_dir)
            mode = "bundle"
        except PatchApplicationError:
            bundle = build_bundle_from_snapshot(zip_path, target_root, run_dir)
            mode = "snapshot"

        validate_bundle_targets(bundle, target_root)
        verification, actual_patch, expected_patch = compute_verification(bundle, target_root)

        actual_patch_path = run_dir / "actual.patch"
        actual_patch_path.write_text(actual_patch, encoding="utf-8")
        expected_patch_path: Path | None = None
        if expected_patch is not None:
            expected_patch_path = run_dir / "expected.patch"
            expected_patch_path.write_text(expected_patch, encoding="utf-8")

        undo_bundle, undo_patch, undo_zip_path = build_undo_bundle(bundle, target_root, run_dir)
        undo_patch_path = run_dir / "undo.patch"
        undo_patch_path.write_text(undo_patch, encoding="utf-8")

        report = {
            "mode": mode,
            "zip_path": str(zip_path),
            "target_root": str(target_root),
            "run_dir": str(run_dir),
            "verification": verification,
            "allowfuzz": bool(args.allowfuzz),
            "dry_run": bool(args.dry_run),
            "changed_files": [change.path for change in bundle.changes],
            "actual_patch_path": str(actual_patch_path),
            "undo_patch_path": str(undo_patch_path),
            "undo_bundle_zip": str(undo_zip_path),
            "undo_command": undo_command(undo_zip_path, target_root),
            "manifest_path": str(bundle.manifest_path),
            "reference_patch_path": str(bundle.reference_patch_path) if bundle.reference_patch_path else None,
            "expected_patch_path": str(expected_patch_path) if expected_patch_path else None,
            "undo_manifest_path": str(undo_bundle.manifest_path),
        }

        if args.dry_run:
            report["status"] = "dry_run_ok" if verification != "fuzzy" else "dry_run_fuzzy"
            write_run_report(run_dir, report)
            print_run_summary(
                run_dir=run_dir,
                bundle=bundle,
                verification=verification,
                changes_count=len(bundle.changes),
                actual_patch_path=actual_patch_path,
                undo_patch_path=undo_patch_path,
                undo_zip_path=undo_zip_path,
                target_root=target_root,
            )
            print_actual_diff(actual_patch)
            print("dry-run only; no files were copied.")
            if verification == "fuzzy" and not args.allowfuzz:
                print("blocked: reference.patch does not exactly match the live diff; rerun with --allowfuzz to overwrite anyway.")
                return 1
            return 0

        if verification == "fuzzy" and not args.allowfuzz:
            report["status"] = "blocked_on_fuzz"
            write_run_report(run_dir, report)
            print_run_summary(
                run_dir=run_dir,
                bundle=bundle,
                verification=verification,
                changes_count=len(bundle.changes),
                actual_patch_path=actual_patch_path,
                undo_patch_path=undo_patch_path,
                undo_zip_path=undo_zip_path,
                target_root=target_root,
            )
            print("blocked: reference.patch does not exactly match the live diff; rerun with --allowfuzz to overwrite anyway.")
            return 1

        apply_bundle(bundle, target_root)
        report["status"] = (
            "applied"
            if verification in {"exact", "no_reference"}
            else "applied_with_fuzz"
        )
        write_run_report(run_dir, report)
        print_run_summary(
            run_dir=run_dir,
            bundle=bundle,
            verification=verification,
            changes_count=len(bundle.changes),
            actual_patch_path=actual_patch_path,
            undo_patch_path=undo_patch_path,
            undo_zip_path=undo_zip_path,
            target_root=target_root,
        )
        if verification == "fuzzy":
            print("applied: overwrote target files because --allowfuzz was set.")
        else:
            print("applied: copied replacement files into the target root.")
        return 0
    except PatchApplicationError as exc:
        write_run_report(run_dir, {
            "mode": mode,
            "zip_path": str(zip_path),
            "target_root": str(target_root),
            "run_dir": str(run_dir),
            "status": "error",
            "error": str(exc),
        })
        print(f"run_dir: {run_dir}")
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
