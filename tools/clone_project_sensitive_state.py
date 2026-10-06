#!/usr/bin/env python3
"""
Clone Main Computer sensitive/runtime state while preserving repo-relative layout.

Primary invariant:
    --target-dir names the parent directory for the clone. Each run creates a
    unique slugged child directory, and copied files preserve repo-relative
    paths beneath that clone root.

Examples:

    # Inspect exactly what would be copied
    python tools/clone_project_sensitive_state.py --dry-run

    # Create a timestamped clone under runtime/state_clones/
    python tools/clone_project_sensitive_state.py

    # Explicit parent destination; a slugged clone directory is created inside it
    python tools/clone_project_sensitive_state.py \
        --target-dir C:\\Users\\subsi\\archive

    # Verify an existing clone against its manifest
    python tools/clone_project_sensitive_state.py \
        --verify C:\\Users\\subsi\\main_computer_state_backup
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


# ---------------------------------------------------------------------------
# State roots
# ---------------------------------------------------------------------------

# Mother is intentionally allowlisted rather than copied as one recursive root.
# runtime/state/mother also contains large transient execution history such as
# harness-runs and twiddles; those are not part of this archive contract.
MOTHER_EVIDENCE_ROOT = Path("runtime/state/mother/evidence")

# These are compact, irreplaceable Mother state trees and are copied in full.
MOTHER_STATE_ROOTS = (
    Path("runtime/state/mother/private-recovery"),
    Path("runtime/state/mother/reseal-inputs"),
    Path("runtime/state/mother/secrets"),
)

# Evidence is intentionally retained sparsely: every receipt explicitly
# referenced by current state plus the newest receipt in each immediate
# evidence category. Historical unreferenced receipts are not cloned.
CURRENT_STATE_REFERENCE_DIRS = (
    Path("runtime/state"),
    Path("runtime/state/mother"),
)
CURRENT_STATE_REFERENCE_SUFFIXES = {".json", ".yaml", ".yml", ".txt"}
CURRENT_STATE_REFERENCE_MAX_BYTES = 4 * 1024 * 1024

MOTHER_STATE_FILES = (
    Path("runtime/state/mother/identity.private.yaml"),
    Path("runtime/state/mother/identity.private.meta.json"),
)


# ---------------------------------------------------------------------------
# Secret discovery
# ---------------------------------------------------------------------------

# Exact filenames commonly carrying secrets or private configuration.
SECRET_EXACT_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    ".env.test",
    "applications.env",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "credentials.json",
    "credentials.yaml",
    "credentials.yml",
    "credentials.txt",
    "api-token.txt",
    "local.secrets",
    ".git-credentials",
    "git-credentials",
    "git-credentials.txt",
    "git-token",
    "git-token.txt",
    "github-token",
    "github-token.txt",
    "gitlab-token",
    "gitlab-token.txt",
    "gitea-token",
    "gitea-token.txt",
    "deploy-key",
    "deploy-key.txt",
    "deploy_key",
    "deploy_key.txt",
}

# Filename fragments used only inside the bounded secret-bearing roots below.
SECRET_NAME_MARKERS = (
    ".private.",
    ".secret.",
    ".secrets.",
    ".credential.",
    ".credentials.",
)

SECRET_SUFFIXES = {
    ".pem",
    ".p12",
    ".pfx",
    ".key",
    ".keystore",
    ".jks",
}

# Explicit private files that are part of Main Computer's local operational
# identity but are not all beneath Mother state.
EXPLICIT_SECRET_FILES = (
    Path(".env"),
    Path("local.secrets"),
    Path(".git-credentials"),
    Path("runtime/deployment/.env"),
    Path("runtime/state/main_computer.private.yaml"),
    Path("runtime/state/mother-bootstrap.private.yaml"),
    Path("runtime/applications_service/applications.env"),
    Path("runtime/coolify-local-docker/credentials.txt"),
    Path("runtime/coolify-local-docker/api-token.txt"),
    Path("runtime/git-tools/.env.local"),
)

# Only these roots are searched recursively for additional secret-looking
# files.  This is intentionally an allowlist.  Walking the entire repository
# is both slow and wrong: virtualenv/vendor certificate bundles, caches and
# generated artifacts are not project secrets merely because they contain a
# .pem or similar filename.
SECRET_SCAN_ROOTS = (
    Path("runtime/applications_service"),
    Path("runtime/coolify-local-docker"),
    Path("runtime/qemu-coolify-smoke"),
    Path("runtime/conductor/private"),
    Path("runtime/git-tools"),
)

# Mother state is selected by the explicit allowlist above and must not be scanned
# again by the filename-based secret pass. state_clones are outputs and must
# never become inputs to later clones.
EXCLUDED_SECRET_SCAN_ROOTS = (
    Path("runtime/state/mother"),
    Path("runtime/state_clones"),
)

EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "node_modules",
    "site-packages",
    "vendor",
    "dist",
    "build",
    "cache",
    "caches",
}


MANIFEST_NAME = "clone-sensitive-state-manifest.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp_slug() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def slugify_directory_name(name: str) -> str:
    chars: list[str] = []
    pending_dash = False
    for ch in name.strip().lower():
        if ch.isalnum():
            if pending_dash and chars:
                chars.append("-")
            chars.append(ch)
            pending_dash = False
        else:
            pending_dash = True
    slug = "".join(chars).strip("-")
    return slug or "project"


def clone_directory_slug(repo_root: Path) -> str:
    return f"{slugify_directory_name(repo_root.name)}-sensitive-state-{timestamp_slug()}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def repo_root_from_script() -> Path:
    """
    tools/clone_project_sensitive_state.py -> repository root.
    """
    return Path(__file__).resolve().parent.parent


def relative_to_repo(path: Path, repo_root: Path) -> Path:
    return path.resolve().relative_to(repo_root.resolve())


def is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def is_same_or_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def is_excluded_secret_scan_path(path: Path, repo_root: Path) -> bool:
    return any(
        is_same_or_under(path, repo_root / rel_root)
        for rel_root in EXCLUDED_SECRET_SCAN_ROOTS
    )

def human_size(size: int) -> str:
    value = float(size)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{size} B"


def is_excluded_path(
    path: Path,
    *,
    repo_root: Path,
    destination: Path,
) -> bool:
    try:
        rel = path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return True

    if any(
        part in EXCLUDED_DIR_NAMES or part.lower().startswith(".venv")
        for part in rel.parts
    ):
        return True

    # Never recursively clone the clone.
    if is_under(path, destination):
        return True

    return False


def looks_like_secret(path: Path) -> bool:
    """
    Filename-only classification.

    We intentionally do NOT grep file contents for tokens/passwords because:
      1. it creates false positives;
      2. it risks reading giant/generated files;
      3. the explicit Mother state root already handles operational evidence.
    """
    name = path.name.lower()

    if name in SECRET_EXACT_NAMES:
        return True

    if any(marker in name for marker in SECRET_NAME_MARKERS):
        return True

    if path.suffix.lower() in SECRET_SUFFIXES:
        return True

    # Catch common explicit secret/config names without matching broad words
    # like "key" inside normal source filenames.
    stem = path.stem.lower()

    if stem in {
        "secret",
        "secrets",
        "credential",
        "credentials",
        "private_key",
        "private-key",
        "identity.private",
    }:
        return True

    return False


def evidence_category(path: Path, evidence_root: Path) -> str:
    rel = path.relative_to(evidence_root)
    return rel.parts[0] if len(rel.parts) > 1 else "__root__"


def evidence_recency_key(path: Path) -> tuple[str, int, str]:
    # Evidence filenames conventionally begin with UTC timestamps.  Lexical
    # ordering is chronological for YYYYMMDDTHHMMSSZ.  Fall back to mtime for
    # categories whose receipts use another naming scheme.
    match = re.search(r"(\d{8}T\d{6}Z)", path.name, flags=re.IGNORECASE)
    timestamp = match.group(1).upper() if match else ""
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    return (timestamp, mtime_ns, path.name.lower())


def iter_current_state_reference_files(repo_root: Path) -> Iterable[Path]:
    """Yield bounded current-state text files that may point at evidence."""
    seen: set[Path] = set()

    # Explicit private/current files are always reference sources when present.
    for rel_path in (*MOTHER_STATE_FILES, *EXPLICIT_SECRET_FILES):
        path = repo_root / rel_path
        if path.is_file() and path not in seen:
            seen.add(path)
            yield path

    # Also inspect only files directly in runtime/state and runtime/state/mother.
    # Do not recurse into evidence, recovery history, harness runs, etc.
    for rel_dir in CURRENT_STATE_REFERENCE_DIRS:
        root = repo_root / rel_dir
        if not root.is_dir():
            continue
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for path in children:
            if path in seen or not path.is_file():
                continue
            if path.suffix.lower() not in CURRENT_STATE_REFERENCE_SUFFIXES:
                continue
            try:
                if path.stat().st_size > CURRENT_STATE_REFERENCE_MAX_BYTES:
                    continue
            except OSError:
                continue
            seen.add(path)
            yield path


def current_state_reference_text(repo_root: Path) -> tuple[str, int]:
    chunks: list[str] = []
    count = 0
    for path in iter_current_state_reference_files(repo_root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Normalize Windows paths so one comparison works for both separators.
        chunks.append(text.replace("\\", "/").lower())
        count += 1
    return "\n".join(chunks), count


def evidence_reference_tokens(path: Path, repo_root: Path, evidence_root: Path) -> tuple[str, ...]:
    rel_repo = relative_to_repo(path, repo_root).as_posix().lower()
    rel_evidence = path.relative_to(evidence_root).as_posix().lower()
    basename = path.name.lower()
    tokens = [rel_repo, rel_evidence, basename]

    # Evidence filenames frequently end with a SHA prefix.  Current state may
    # retain only the SHA rather than the complete path, so preserve on a
    # sufficiently long digest token as well.
    match = re.search(r"-([0-9a-fA-F]{12,64})(?:\.[^.]+)?$", path.name)
    if match:
        tokens.append(match.group(1).lower())
    return tuple(tokens)


def select_evidence_files(repo_root: Path) -> list[tuple[Path, str]]:
    evidence_root = repo_root / MOTHER_EVIDENCE_ROOT
    if not evidence_root.is_dir():
        return []

    candidates: list[Path] = []
    for current_root, dirs, files in os.walk(evidence_root):
        current = Path(current_root)
        dirs[:] = [d for d in dirs if d.lower() != "twiddles"]
        for filename in files:
            path = current / filename
            if path.is_file() or path.is_symlink():
                candidates.append(path)

    reference_text, reference_source_count = current_state_reference_text(repo_root)
    referenced: set[Path] = set()
    latest_by_category: dict[str, Path] = {}

    for path in candidates:
        category = evidence_category(path, evidence_root)
        previous = latest_by_category.get(category)
        if previous is None or evidence_recency_key(path) > evidence_recency_key(previous):
            latest_by_category[category] = path

        if reference_text and any(
            token and token in reference_text
            for token in evidence_reference_tokens(path, repo_root, evidence_root)
        ):
            referenced.add(path)

    selected: dict[Path, str] = {}
    for path in referenced:
        selected[path] = "mother-evidence-referenced"
    for category, path in latest_by_category.items():
        selected.setdefault(path, f"mother-evidence-latest:{category}")

    print(f"evidence_candidates={len(candidates)}", flush=True)
    print(f"evidence_categories={len(latest_by_category)}", flush=True)
    print(f"evidence_reference_sources={reference_source_count}", flush=True)
    print(f"evidence_referenced_selected={len(referenced)}", flush=True)
    print(f"evidence_selected_total={len(selected)}", flush=True)

    return sorted(selected.items(), key=lambda item: relative_to_repo(item[0], repo_root).as_posix())


def iter_state_root_files(
    repo_root: Path,
    destination: Path,
) -> Iterable[tuple[Path, str]]:
    # Explicit canonical Mother files.
    for rel_path in MOTHER_STATE_FILES:
        source = repo_root / rel_path
        if not source.exists() and not source.is_symlink():
            continue
        if is_excluded_path(source, repo_root=repo_root, destination=destination):
            continue
        if source.is_file() or source.is_symlink():
            yield source, "mother-state-file"

    # Evidence uses retention, not full-history copying.
    for source, reason in select_evidence_files(repo_root):
        if is_excluded_path(source, repo_root=repo_root, destination=destination):
            continue
        yield source, reason

    # Compact canonical Mother trees remain complete.
    for rel_root in MOTHER_STATE_ROOTS:
        root = repo_root / rel_root
        if not root.is_dir():
            continue

        for current_root, dirs, files in os.walk(root):
            current = Path(current_root)
            dirs[:] = [
                d
                for d in dirs
                if not is_excluded_path(
                    current / d,
                    repo_root=repo_root,
                    destination=destination,
                )
                and d.lower() != "twiddles"
            ]

            for filename in files:
                source = current / filename
                if "twiddles" in {part.lower() for part in relative_to_repo(source, repo_root).parts}:
                    continue
                if source.is_symlink():
                    yield source, f"mother-state-root:{rel_root.as_posix()}"
                    continue
                if source.is_file():
                    yield source, f"mother-state-root:{rel_root.as_posix()}"


def iter_discovered_secret_files(
    repo_root: Path,
    destination: Path,
) -> Iterable[tuple[Path, str]]:
    # First take the explicit private files.  No repository-wide scan occurs.
    for rel_path in EXPLICIT_SECRET_FILES:
        source = repo_root / rel_path
        if not source.exists() and not source.is_symlink():
            continue
        if is_excluded_path(source, repo_root=repo_root, destination=destination):
            continue
        if source.is_file() or source.is_symlink():
            yield source, "explicit-secret"

    # Then search only known secret-bearing runtime roots.
    for rel_root in SECRET_SCAN_ROOTS:
        root = repo_root / rel_root
        if not root.is_dir():
            continue

        for current_root, dirs, files in os.walk(root):
            current = Path(current_root)
            dirs[:] = [
                d
                for d in dirs
                if not is_excluded_path(
                    current / d,
                    repo_root=repo_root,
                    destination=destination,
                )
                and not is_excluded_secret_scan_path(current / d, repo_root)
            ]

            for filename in files:
                source = current / filename
                if is_excluded_path(
                    source,
                    repo_root=repo_root,
                    destination=destination,
                ):
                    continue
                if looks_like_secret(source):
                    yield source, f"secret-root:{rel_root.as_posix()}"


def collect_files(
    repo_root: Path,
    destination: Path,
) -> list[dict]:
    """Merge allowlisted canonical Mother state and bounded secret discovery."""
    selected: dict[str, dict] = {}

    print("discovery_phase=allowlisted_mother_state", flush=True)
    for source, reason in iter_state_root_files(repo_root, destination):
        rel = relative_to_repo(source, repo_root).as_posix()
        if rel in selected:
            continue
        selected[rel] = {
            "source": source,
            "relative_path": rel,
            "reason": reason,
        }
        print(f"DISCOVERED {rel} [{reason}]", flush=True)

    print("discovery_phase=bounded_project_secrets", flush=True)
    for source, reason in iter_discovered_secret_files(repo_root, destination):
        rel = relative_to_repo(source, repo_root).as_posix()
        if rel in selected:
            continue
        selected[rel] = {
            "source": source,
            "relative_path": rel,
            "reason": reason,
        }
        print(f"DISCOVERED {rel} [{reason}]", flush=True)

    print(f"discovery_complete selected_files={len(selected)}", flush=True)
    return [selected[key] for key in sorted(selected)]


def describe_source(path: Path) -> dict:
    if path.is_symlink():
        return {
            "kind": "symlink",
            "symlink_target": os.readlink(path),
        }

    st = path.stat()

    return {
        "kind": "file",
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "mode": stat.S_IMODE(st.st_mode),
        "sha256": sha256_file(path),
    }


def copy_one_with_source_hash(
    source: Path,
    target: Path,
) -> str | None:
    """Copy one entry while hashing the source stream exactly once."""
    target.parent.mkdir(parents=True, exist_ok=True)

    if source.is_symlink():
        link_target = os.readlink(source)
        if target.exists() or target.is_symlink():
            target.unlink()
        os.symlink(
            link_target,
            target,
            target_is_directory=source.resolve().is_dir(),
        )
        return None

    digest = hashlib.sha256()
    with source.open("rb") as src_handle, target.open("wb") as dst_handle:
        while True:
            chunk = src_handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            dst_handle.write(chunk)

    shutil.copystat(source, target)
    return digest.hexdigest()


def print_selection_size_report(files: list[dict]) -> None:
    by_bucket: dict[str, int] = {}
    largest: list[tuple[int, str]] = []
    total = 0

    for item in files:
        source: Path = item["source"]
        if source.is_symlink():
            size = 0
        else:
            try:
                size = source.stat().st_size
            except OSError:
                size = 0
        total += size
        rel = Path(item["relative_path"])
        parts = rel.parts
        if len(parts) >= 4 and parts[:3] == ("runtime", "state", "mother"):
            bucket = "/".join(parts[:4]) if len(parts) >= 4 else "/".join(parts[:3])
        elif len(parts) >= 2:
            bucket = "/".join(parts[:2])
        else:
            bucket = rel.as_posix()
        by_bucket[bucket] = by_bucket.get(bucket, 0) + size
        largest.append((size, rel.as_posix()))

    print("selection_size_by_root:", flush=True)
    for bucket, size in sorted(by_bucket.items(), key=lambda kv: kv[1], reverse=True):
        print(f"  {human_size(size):>10}  {bucket}", flush=True)

    print("largest_selected_files:", flush=True)
    for size, rel in sorted(largest, reverse=True)[:20]:
        print(f"  {human_size(size):>10}  {rel}", flush=True)

    print(f"selected_total_size={human_size(total)}", flush=True)


def clone_state(
    *,
    repo_root: Path,
    destination: Path,
    dry_run: bool,
) -> int:
    repo_root = repo_root.resolve()
    destination = destination.resolve()

    if destination == repo_root:
        raise RuntimeError("destination cannot be the repository root")
    if is_under(repo_root, destination):
        raise RuntimeError(
            "destination cannot be a parent of the repository; "
            "that would create an ambiguous recursive backup boundary"
        )

    print(f"repo_root={repo_root}", flush=True)
    print(f"clone_root={destination}", flush=True)
    print(f"dry_run={str(dry_run).lower()}", flush=True)
    print("status=DISCOVERING_FILES", flush=True)
    print(flush=True)

    files = collect_files(repo_root, destination)
    manifest_entries: list[dict] = []
    total_bytes = 0

    print(flush=True)
    print_selection_size_report(files)
    print(flush=True)
    print(f"selected_files={len(files)}", flush=True)
    print("status=STARTING_COPY" if not dry_run else "status=DRY_RUN_SELECTION", flush=True)
    print(flush=True)

    for index, item in enumerate(files, 1):
        source: Path = item["source"]
        rel = Path(item["relative_path"])
        target = destination / rel
        is_link = source.is_symlink()
        st = None if is_link else source.stat()
        size = 0 if st is None else st.st_size
        total_bytes += size
        prefix = f"[{index}/{len(files)}]"

        if dry_run:
            print(
                f"{prefix} WOULD COPY {human_size(size):>10} "
                f"{rel.as_posix()} [{item['reason']}]",
                flush=True,
            )
            continue

        print(
            f"{prefix} COPYING    {human_size(size):>10} "
            f"{rel.as_posix()} [{item['reason']}]",
            flush=True,
        )

        if is_link:
            metadata = {
                "kind": "symlink",
                "symlink_target": os.readlink(source),
            }
            copy_one_with_source_hash(source, target)
        else:
            assert st is not None
            source_sha = copy_one_with_source_hash(source, target)
            assert source_sha is not None
            copied_sha = sha256_file(target)
            if copied_sha != source_sha:
                raise RuntimeError(
                    f"post-copy SHA-256 mismatch for {rel}: "
                    f"source={source_sha} copied={copied_sha}"
                )
            metadata = {
                "kind": "file",
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
                "mode": stat.S_IMODE(st.st_mode),
                "sha256": source_sha,
            }

        manifest_entries.append({
            "path": rel.as_posix(),
            "reason": item["reason"],
            **metadata,
        })
        print(
            f"{prefix} COPIED     {human_size(size):>10} {rel.as_posix()}",
            flush=True,
        )

    print(flush=True)
    print(f"file_count={len(files)}", flush=True)
    print(f"total_file_bytes={total_bytes}", flush=True)
    print(f"total_file_size={human_size(total_bytes)}", flush=True)

    if dry_run:
        print("result=DRY_RUN_ONLY_NO_FILE_CONTENTS_READ", flush=True)
        return 0

    manifest = {
        "schema": "main-computer-sensitive-state-clone-v3",
        "created_at": utc_now().isoformat(),
        "repo_root": str(repo_root),
        "destination": str(destination),
        "file_count": len(manifest_entries),
        "total_file_bytes": total_bytes,
        "mother_evidence_root": MOTHER_EVIDENCE_ROOT.as_posix(),
        "mother_evidence_retention": "referenced-by-current-state-plus-latest-per-category",
        "mother_state_roots": [p.as_posix() for p in MOTHER_STATE_ROOTS],
        "mother_state_files": [p.as_posix() for p in MOTHER_STATE_FILES],
        "explicit_secret_files": [p.as_posix() for p in EXPLICIT_SECRET_FILES],
        "secret_scan_roots": [p.as_posix() for p in SECRET_SCAN_ROOTS],
        "excluded_secret_scan_roots": [p.as_posix() for p in EXCLUDED_SECRET_SCAN_ROOTS],
        "files": manifest_entries,
    }

    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"manifest={manifest_path}", flush=True)
    print("result=CLONE_COMPLETE_AND_HASH_VERIFIED", flush=True)
    return 0


def verify_clone(clone_root: Path) -> int:
    clone_root = clone_root.resolve()
    manifest_path = clone_root / MANIFEST_NAME

    if not manifest_path.is_file():
        raise RuntimeError(
            f"clone manifest not found: {manifest_path}"
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    failures: list[str] = []

    for entry in manifest.get("files", []):
        rel = Path(entry["path"])
        target = clone_root / rel
        kind = entry["kind"]

        if kind == "symlink":
            if not target.is_symlink():
                failures.append(
                    f"{rel.as_posix()}: expected symlink"
                )
                continue

            actual_target = os.readlink(target)
            expected_target = entry["symlink_target"]

            if actual_target != expected_target:
                failures.append(
                    f"{rel.as_posix()}: symlink target mismatch "
                    f"{actual_target!r} != {expected_target!r}"
                )

            continue

        if not target.is_file():
            failures.append(
                f"{rel.as_posix()}: missing file"
            )
            continue

        actual_sha = sha256_file(target)
        expected_sha = entry["sha256"]

        if actual_sha != expected_sha:
            failures.append(
                f"{rel.as_posix()}: SHA-256 mismatch "
                f"{actual_sha} != {expected_sha}"
            )

    if failures:
        print(
            json.dumps(
                {
                    "ok": False,
                    "clone_root": str(clone_root),
                    "failures": failures,
                },
                indent=2,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "clone_root": str(clone_root),
                "file_count": len(manifest.get("files", [])),
                "manifest": str(manifest_path),
            },
            indent=2,
        )
    )

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Clone project secrets and operational state while "
            "preserving repository-relative paths."
        )
    )

    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help=(
            "Repository root. Defaults to the parent of the tools directory "
            "containing this script."
        ),
    )

    parser.add_argument(
        "--target-dir",
        "--dest",
        dest="target_dir",
        type=Path,
        default=None,
        help=(
            "Parent directory in which a new slugged sensitive-state clone "
            "directory is created. Repository-relative paths are preserved "
            "beneath that clone directory. Defaults to runtime/state_clones "
            "under the repo. --dest is retained as a compatibility alias."
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print selected files without creating the clone.",
    )

    parser.add_argument(
        "--verify",
        type=Path,
        default=None,
        metavar="CLONE_ROOT",
        help="Verify an existing clone using its manifest.",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.verify is not None:
        return verify_clone(args.verify)

    repo_root = (
        args.repo_root.resolve()
        if args.repo_root is not None
        else repo_root_from_script()
    )

    if not repo_root.is_dir():
        raise RuntimeError(
            f"repository root does not exist: {repo_root}"
        )

    target_parent = (
        args.target_dir.resolve()
        if args.target_dir is not None
        else (repo_root / "runtime" / "state_clones").resolve()
    )
    destination = target_parent / clone_directory_slug(repo_root)

    return clone_state(
        repo_root=repo_root,
        destination=destination,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1)