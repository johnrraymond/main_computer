#!/usr/bin/env python3
"""
new_diff.py

Show what a zip would change compared with local code, before patching.

Typical use from inside the repo root:

    python new_diff.py patch_or_snapshot.zip

Typical use from the parent of the repo folder:

    python new_diff.py patch_or_snapshot.zip --root .

By default this compares only files present in the zip. It does not infer
deletions from files omitted from the zip.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
from pathlib import Path
import re
import sys
import zipfile
from dataclasses import dataclass
from difflib import unified_diff
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_CONTEXT = 3


@dataclass(frozen=True)
class ZipEntry:
    archive_path: str
    compare_path: str
    data: bytes


@dataclass
class DiffStats:
    compared: int = 0
    unchanged: int = 0
    created: int = 0
    modified: int = 0
    binary_changed: int = 0
    type_changed: int = 0
    errors: int = 0

    @property
    def changed(self) -> int:
        return self.created + self.modified + self.binary_changed + self.type_changed

    @property
    def clean(self) -> bool:
        return self.changed == 0 and self.errors == 0


class DiffError(Exception):
    pass


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def normalize_zip_path(raw_name: str) -> Optional[str]:
    """Return a safe normalized zip path, or None for directory-like entries."""
    name = raw_name.replace("\\", "/")

    # Strip a Windows drive prefix if a zip creator accidentally stored one.
    name = re.sub(r"^[A-Za-z]:/+", "", name)

    # Zip paths are logically relative POSIX paths.
    name = name.lstrip("/")
    if not name or name.endswith("/"):
        return None

    parts: List[str] = []
    for part in name.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise DiffError(f"unsafe zip path contains '..': {raw_name!r}")
        if "\x00" in part:
            raise DiffError(f"unsafe zip path contains NUL byte: {raw_name!r}")
        parts.append(part)

    if not parts:
        return None
    return "/".join(parts)


def is_generated_noise(path: str) -> bool:
    parts = path.split("/")
    return (
        "__pycache__" in parts
        or path.endswith(".pyc")
        or path.endswith(".pyo")
        or path == ".DS_Store"
        or "/.DS_Store" in path
        or path.startswith("__MACOSX/")
        or path.startswith(".git/")
        or "/.git/" in path
    )


def ignored_by_patterns(path: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def choose_payload_mode(paths: Sequence[str], requested: str) -> str:
    """Choose raw zip mode or verified bundle mode."""
    if requested != "auto":
        return requested
    normalized = set(paths)
    has_bundle_markers = "manifest.json" in normalized and "reference.patch" in normalized
    has_files_payload = any(path.startswith("files/") and path != "files/" for path in normalized)
    return "bundle" if has_bundle_markers and has_files_payload else "raw"


def load_zip_entries(
    zip_path: Path,
    *,
    bundle_mode: str,
    include_generated: bool,
    ignore_patterns: Sequence[str],
) -> List[ZipEntry]:
    if not zip_path.is_file():
        raise DiffError(f"zip not found: {zip_path}")

    with zipfile.ZipFile(zip_path) as zf:
        raw_infos = [info for info in zf.infolist() if not info.is_dir()]
        normalized_pairs: List[Tuple[zipfile.ZipInfo, str]] = []
        for info in raw_infos:
            normalized = normalize_zip_path(info.filename)
            if normalized is not None:
                normalized_pairs.append((info, normalized))

        mode = choose_payload_mode([path for _, path in normalized_pairs], bundle_mode)

        entries: List[ZipEntry] = []
        seen: Dict[str, str] = {}
        for info, archive_path in normalized_pairs:
            if mode == "bundle":
                if not archive_path.startswith("files/"):
                    continue
                compare_path = archive_path[len("files/") :]
                if not compare_path:
                    continue
            else:
                compare_path = archive_path

            if not include_generated and is_generated_noise(compare_path):
                continue
            if ignored_by_patterns(compare_path, ignore_patterns):
                continue

            prior = seen.get(compare_path)
            if prior is not None:
                raise DiffError(
                    f"duplicate payload path after normalization: {compare_path!r} "
                    f"from {prior!r} and {info.filename!r}"
                )
            seen[compare_path] = info.filename
            entries.append(
                ZipEntry(
                    archive_path=archive_path,
                    compare_path=compare_path,
                    data=zf.read(info),
                )
            )

    entries.sort(key=lambda entry: entry.compare_path)
    return entries


def common_archive_root(paths: Sequence[str]) -> Optional[str]:
    """Return a single top-level root if every path is under it."""
    tops = {path.split("/", 1)[0] for path in paths if path}
    if len(tops) != 1:
        return None
    root = next(iter(tops))
    # Treat it as a root only if every file is below root/, not if the zip is one file.
    if all(path.startswith(root + "/") for path in paths):
        return root
    return None


def count_existing(root: Path, paths: Iterable[str]) -> int:
    total = 0
    for path in paths:
        if (root / Path(*path.split("/"))).exists():
            total += 1
    return total


def decide_strip_root(entries: Sequence[ZipEntry], local_root: Path, strip_root: str) -> Tuple[Optional[str], str]:
    root = common_archive_root([entry.compare_path for entry in entries])
    if root is None:
        return None, "zip has no single archive root"

    if strip_root == "always":
        return root, f"stripping archive root {root!r} because --strip-root=always"
    if strip_root == "never":
        return None, f"keeping archive root {root!r} because --strip-root=never"

    original_paths = [entry.compare_path for entry in entries]
    stripped_paths = [
        entry.compare_path[len(root) + 1 :] if entry.compare_path.startswith(root + "/") else entry.compare_path
        for entry in entries
    ]

    keep_score = count_existing(local_root, original_paths)
    strip_score = count_existing(local_root, stripped_paths)

    if local_root.name == root:
        return root, f"stripping archive root {root!r}; local root folder has the same name"
    if (local_root / root).exists() and keep_score >= strip_score:
        return None, f"keeping archive root {root!r}; {root!r} exists under local root"
    if strip_score > keep_score:
        return root, f"stripping archive root {root!r}; more local matches are found without it"
    return None, f"keeping archive root {root!r}; more local matches are found with it"


def mapped_relative_path(entry: ZipEntry, stripped_root: Optional[str]) -> str:
    path = entry.compare_path
    if stripped_root is not None and path.startswith(stripped_root + "/"):
        return path[len(stripped_root) + 1 :]
    return path


def local_path_for(root: Path, relative_posix_path: str) -> Path:
    return root / Path(*relative_posix_path.split("/"))


def looks_binary(data: bytes) -> bool:
    sample = data[:8192]
    if b"\x00" in sample:
        return True
    # Source files are usually UTF-8. If the sample cannot decode, treat it as binary
    # to avoid dumping unreadable terminal output.
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def decode_text(data: bytes) -> List[str]:
    # Preserve unusual bytes without crashing.
    return data.decode("utf-8", errors="surrogateescape").splitlines(keepends=True)


def print_created_text(relative: str, entry: ZipEntry, context: int) -> None:
    lines = decode_text(entry.data)
    diff = unified_diff(
        [],
        lines,
        fromfile="/dev/null",
        tofile=f"zip/{entry.archive_path}",
        n=context,
        lineterm="",
    )
    for line in diff:
        print(line, end="\n" if not line.endswith("\n") else "")


def print_modified_text(relative: str, entry: ZipEntry, local_data: bytes, context: int) -> None:
    before = decode_text(local_data)
    after = decode_text(entry.data)
    diff = unified_diff(
        before,
        after,
        fromfile=f"local/{relative}",
        tofile=f"zip/{entry.archive_path}",
        n=context,
        lineterm="",
    )
    for line in diff:
        print(line, end="\n" if not line.endswith("\n") else "")


def compare_entries(
    entries: Sequence[ZipEntry],
    *,
    local_root: Path,
    stripped_root: Optional[str],
    context: int,
    summary_only: bool,
    names_only: bool,
) -> DiffStats:
    stats = DiffStats()

    for entry in entries:
        relative = mapped_relative_path(entry, stripped_root)
        target = local_path_for(local_root, relative)
        stats.compared += 1

        if target.exists() and target.is_dir():
            stats.type_changed += 1
            if not summary_only:
                print(f"TYPE CHANGE  {relative}")
                if not names_only:
                    print(f"  local path is a directory; zip entry is a file")
            continue

        if not target.exists():
            stats.created += 1
            if names_only:
                print(f"CREATE       {relative}")
            elif summary_only:
                pass
            elif looks_binary(entry.data):
                print(f"CREATE       {relative}  (binary, {len(entry.data)} bytes)")
            else:
                print_created_text(relative, entry, context)
            continue

        try:
            local_data = target.read_bytes()
        except OSError as exc:
            stats.errors += 1
            eprint(f"ERROR        {relative}: could not read local file: {exc}")
            continue

        if local_data == entry.data:
            stats.unchanged += 1
            continue

        local_binary = looks_binary(local_data)
        zip_binary = looks_binary(entry.data)
        if local_binary or zip_binary:
            stats.binary_changed += 1
            if names_only:
                print(f"BINARY       {relative}")
            elif not summary_only:
                print(
                    f"BINARY       {relative}  "
                    f"(local {len(local_data)} bytes -> zip {len(entry.data)} bytes)"
                )
            continue

        stats.modified += 1
        if names_only:
            print(f"MODIFY       {relative}")
        elif not summary_only:
            print_modified_text(relative, entry, local_data, context)

    return stats


def print_summary(stats: DiffStats) -> None:
    print("")
    print("Summary:")
    print(f"  compared:       {stats.compared}")
    print(f"  unchanged:      {stats.unchanged}")
    print(f"  created:        {stats.created}")
    print(f"  modified text:  {stats.modified}")
    print(f"  binary changed: {stats.binary_changed}")
    print(f"  type changed:   {stats.type_changed}")
    print(f"  errors:         {stats.errors}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Show the diff between files in a zip and local code before patching.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python new_diff.py patch.zip
  python new_diff.py patch.zip --root C:\\path\\to\\main_computer_test
  python new_diff.py patch.zip --summary-only
  python new_diff.py patch.zip --check

Notes:
  - Only files present in the zip are compared.
  - Local-only files are ignored by default; omitted zip files are not treated as deletes.
  - Zips rooted as repo_name/... are auto-mapped whether you run from the repo root
    or from the parent directory.
  - Verified bundles with manifest.json, reference.patch, and files/... are detected
    automatically; only files/... payloads are compared.
""",
    )
    parser.add_argument("zipfile", type=Path, help="zip file to compare against local code")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="local comparison root; default is the current directory",
    )
    parser.add_argument(
        "--strip-root",
        choices=("auto", "always", "never"),
        default="auto",
        help="strip one common archive root before comparing; default: auto",
    )
    parser.add_argument(
        "--bundle-mode",
        choices=("auto", "raw", "bundle"),
        default="auto",
        help="treat zip as raw snapshot/patch or verified bundle; default: auto",
    )
    parser.add_argument(
        "-U",
        "--context",
        type=int,
        default=DEFAULT_CONTEXT,
        help=f"number of unified diff context lines; default: {DEFAULT_CONTEXT}",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="do not print individual diffs; only print the summary",
    )
    parser.add_argument(
        "--names-only",
        action="store_true",
        help="print only changed file names and change types",
    )
    parser.add_argument(
        "--check",
        "--exit-code",
        action="store_true",
        help="exit with code 1 when differences are found",
    )
    parser.add_argument(
        "--include-generated",
        action="store_true",
        help="include __pycache__, .pyc, .pyo, .git, .DS_Store, and __MACOSX entries",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="GLOB",
        help="ignore zip payload paths matching GLOB; may be repeated",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress mapping note; summary/diff output is unchanged",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        local_root = args.root.resolve()
        entries = load_zip_entries(
            args.zipfile,
            bundle_mode=args.bundle_mode,
            include_generated=args.include_generated,
            ignore_patterns=args.ignore,
        )
        if not entries:
            raise DiffError("no comparable file entries found in zip")

        stripped_root, mapping_note = decide_strip_root(entries, local_root, args.strip_root)

        if not args.quiet:
            eprint(f"zip:        {args.zipfile}")
            eprint(f"local root: {local_root}")
            eprint(f"mapping:    {mapping_note}")
            eprint("")

        stats = compare_entries(
            entries,
            local_root=local_root,
            stripped_root=stripped_root,
            context=max(0, args.context),
            summary_only=args.summary_only,
            names_only=args.names_only,
        )
        print_summary(stats)

        if stats.errors:
            return 2
        if args.check and stats.changed:
            return 1
        return 0

    except zipfile.BadZipFile as exc:
        eprint(f"ERROR: invalid zip file: {exc}")
        return 2
    except DiffError as exc:
        eprint(f"ERROR: {exc}")
        return 2
    except KeyboardInterrupt:
        eprint("Interrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
