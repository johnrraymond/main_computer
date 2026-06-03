#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


NUMERIC_LITERAL_RE = re.compile(r"(?<![\w.])-?(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?)")
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


SOPWITH_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "anti": ("git", "checkpoint", "inverse", "restore"),
    "antigit": ("anti", "git", "checkpoint", "machine", "state"),
    "checkpoint": ("snapshot", "clone", "state", "restore"),
    "clone": ("copy", "duplicate", "mirror", "state"),
    "diff": ("change", "modified", "added", "deleted"),
    "git": ("pull", "track", "state", "history"),
    "grammar": ("syntax", "token", "language", "deterministic"),
    "number": ("numeric", "literal", "integer", "float"),
    "numbers": ("numeric", "literal", "integer", "float"),
    "pull": ("restore", "copy", "checkpoint", "state"),
    "python": ("def", "class", "import", "return"),
    "snapshot": ("checkpoint", "clone", "copy", "state"),
    "sopwith": ("stop", "word", "signal", "grammar"),
    "state": ("machine", "raw", "runtime", "checkpoint"),
    "stop": ("word", "signal", "grammar", "query"),
    "word": ("token", "grammar", "signal", "query"),
}

LANGUAGE_WORDS_BY_SUFFIX: dict[str, tuple[str, ...]] = {
    ".py": ("def", "class", "import", "from", "return", "if", "else", "for", "while", "try", "except", "with", "as"),
    ".js": ("function", "const", "let", "var", "return", "import", "export", "class", "if", "else"),
    ".ts": ("function", "const", "let", "type", "interface", "return", "import", "export", "class"),
    ".tsx": ("function", "const", "let", "type", "interface", "return", "import", "export", "class"),
    ".jsx": ("function", "const", "let", "return", "import", "export", "class"),
    ".json": ("object", "array", "string", "number", "true", "false", "null"),
    ".yml": ("key", "value", "list", "map"),
    ".yaml": ("key", "value", "list", "map"),
    ".toml": ("table", "key", "value", "array"),
    ".md": ("heading", "link", "list", "code"),
    ".html": ("html", "head", "body", "div", "script", "style"),
    ".css": ("selector", "class", "id", "property", "value"),
    ".sh": ("if", "then", "fi", "for", "do", "done", "export"),
    ".ps1": ("param", "function", "if", "foreach", "return"),
}


@dataclass(frozen=True)
class EntryInfo:
    path: str
    kind: str
    sha256: str
    size: int
    numeric_literals: tuple[str, ...]
    sopwith_stop_words: tuple[str, ...]


@dataclass(frozen=True)
class CopyStats:
    files: int
    directories: int
    symlinks: int
    bytes: int


def eprint(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def emit_human(enabled: bool, message: str) -> None:
    if enabled:
        print(message, flush=True)


def unique_preserve_order(words: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for word in words:
        normalized = word.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return tuple(ordered)


def guess_stop_words_from_seeds(seeds: Iterable[str]) -> tuple[str, ...]:
    words: list[str] = []

    def visit(seed: str) -> None:
        normalized = seed.strip().lower()
        if not normalized or normalized in words:
            return
        words.append(normalized)
        for expanded in SOPWITH_EXPANSIONS.get(normalized, ()):
            visit(expanded)

    for seed in seeds:
        visit(seed)

    return unique_preserve_order(words)


def split_path_words(path: str) -> tuple[str, ...]:
    pieces = re.split(r"[^A-Za-z0-9_]+", path.replace("/", " "))
    words: list[str] = []
    for piece in pieces:
        if not piece:
            continue
        words.append(piece)
        words.extend(part for part in piece.split("_") if part)
    return unique_preserve_order(words)


def extract_text(data: bytes) -> str:
    if b"\x00" in data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("latin-1")
        except UnicodeDecodeError:
            return ""


def numeric_literals_from_text(text: str) -> tuple[str, ...]:
    return unique_preserve_order(match.group(0) for match in NUMERIC_LITERAL_RE.finditer(text))


def identifiers_from_text(text: str, limit: int = 40) -> tuple[str, ...]:
    return unique_preserve_order(match.group(0) for match in WORD_RE.finditer(text))[:limit]


def sopwith_words_for_file(relative_path: str, data: bytes | None = None) -> tuple[str, ...]:
    path_obj = Path(relative_path)
    seeds: list[str] = []
    seeds.extend(split_path_words(relative_path))
    suffix = path_obj.suffix.lower()
    seeds.extend(LANGUAGE_WORDS_BY_SUFFIX.get(suffix, ()))
    if data is not None:
        text = extract_text(data)
        seeds.extend(identifiers_from_text(text))
        seeds.extend(numeric_literals_from_text(text))
    return guess_stop_words_from_seeds(seeds)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def safe_resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def default_checkpoint_root(source_root: Path) -> Path:
    return source_root.resolve().parent / "checkpoint"


def checkpoint_name_for_source(source_root: Path) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", source_root.name).strip("._")
    if not cleaned:
        cleaned = "project"
    return f"antigit_{cleaned}_checkpoint"


def checkpoint_path(source_root: Path, checkpoint_root: Path) -> Path:
    return checkpoint_root / checkpoint_name_for_source(source_root)


def is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_source_and_checkpoint(source_root: Path, checkpoint_root: Path) -> tuple[Path, Path, Path]:
    source = safe_resolve(source_root)
    checkpoint_base = safe_resolve(checkpoint_root)
    checkpoint = checkpoint_base / checkpoint_name_for_source(source)

    if not source.exists():
        raise ValueError(f"source project does not exist: {source}")
    if not source.is_dir():
        raise ValueError(f"source project is not a directory: {source}")

    if is_relative_to(checkpoint_base, source) or is_relative_to(checkpoint, source):
        raise ValueError("checkpoint root must not be inside the source project")

    return source, checkpoint_base, checkpoint


def relative_file_key(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def collect_entry_infos(root: Path, *, progress: bool = False, label: str = "scan") -> dict[str, EntryInfo]:
    entries: dict[str, EntryInfo] = {}
    visited = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_dir() and not path.is_symlink():
            continue

        relative = relative_file_key(path, root)
        visited += 1

        if path.is_symlink():
            target = os.readlink(path)
            data = target.encode("utf-8", errors="surrogateescape")
            info = EntryInfo(
                path=relative,
                kind="symlink",
                sha256=sha256_bytes(b"symlink:" + data),
                size=len(data),
                numeric_literals=numeric_literals_from_text(target),
                sopwith_stop_words=sopwith_words_for_file(relative, data),
            )
        else:
            data = path.read_bytes()
            text = extract_text(data)
            info = EntryInfo(
                path=relative,
                kind="file",
                sha256=sha256_bytes(data),
                size=len(data),
                numeric_literals=numeric_literals_from_text(text),
                sopwith_stop_words=sopwith_words_for_file(relative, data),
            )
        entries[relative] = info

        if progress and visited % 250 == 0:
            emit_human(True, f"antigit: {label}: indexed {visited} raw entries so far...")

    if progress:
        emit_human(True, f"antigit: {label}: indexed {visited} raw file/symlink entries.")
    return entries


def signal_events(source_root: Path, checkpoint: Path) -> tuple[list[dict], dict]:
    source_entries = collect_entry_infos(source_root)
    checkpoint_entries = collect_entry_infos(checkpoint) if checkpoint.exists() else {}

    events: list[dict] = []
    for path in sorted(set(source_entries) | set(checkpoint_entries)):
        source_info = source_entries.get(path)
        checkpoint_info = checkpoint_entries.get(path)

        if checkpoint_info is None and source_info is not None:
            events.append(
                {
                    "event": "antigit.signal",
                    "path": path,
                    "action": "added",
                    "numeric_literals": list(source_info.numeric_literals),
                    "sopwith_stop_words": list(source_info.sopwith_stop_words),
                    "size": source_info.size,
                    "kind": source_info.kind,
                }
            )
        elif source_info is None and checkpoint_info is not None:
            events.append(
                {
                    "event": "antigit.signal",
                    "path": path,
                    "action": "deleted",
                    "before_numeric_literals": list(checkpoint_info.numeric_literals),
                    "sopwith_stop_words": list(checkpoint_info.sopwith_stop_words),
                    "size": checkpoint_info.size,
                    "kind": checkpoint_info.kind,
                }
            )
        elif source_info is not None and checkpoint_info is not None and source_info.sha256 != checkpoint_info.sha256:
            events.append(
                {
                    "event": "antigit.signal",
                    "path": path,
                    "action": "modified",
                    "before_numeric_literals": list(checkpoint_info.numeric_literals),
                    "after_numeric_literals": list(source_info.numeric_literals),
                    "sopwith_stop_words": list(
                        unique_preserve_order(checkpoint_info.sopwith_stop_words + source_info.sopwith_stop_words)
                    ),
                    "before_size": checkpoint_info.size,
                    "after_size": source_info.size,
                    "kind": source_info.kind,
                }
            )

    summary = {
        "event": "antigit.signal.summary",
        "source": str(source_root),
        "checkpoint": str(checkpoint),
        "changed_files": len(events),
        "added": sum(1 for event in events if event["action"] == "added"),
        "modified": sum(1 for event in events if event["action"] == "modified"),
        "deleted": sum(1 for event in events if event["action"] == "deleted"),
    }
    return events, summary


def emit_signal(events: list[dict], summary: dict, *, json_output: bool) -> None:
    if json_output:
        for event in events:
            print(json.dumps(event, sort_keys=True), flush=True)
        print(json.dumps(summary, sort_keys=True), flush=True)
        return

    if not events:
        print("antigit signal: no raw machine-state changes relative to the checkpoint.", flush=True)
    else:
        print(f"antigit signal: {len(events)} changed raw entries:", flush=True)
        for event in events:
            words = ", ".join(event.get("sopwith_stop_words", [])[:12])
            print(f"  {event['action']:<8} {event['path']}  sopwith=[{words}]", flush=True)
    print(
        "antigit signal summary: "
        f"added={summary['added']} modified={summary['modified']} deleted={summary['deleted']}",
        flush=True,
    )


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def copy_symlink(source: Path, destination: Path) -> None:
    target = os.readlink(source)
    try:
        os.symlink(target, destination)
    except (AttributeError, NotImplementedError, OSError):
        # Some Windows environments disallow creating symlinks.  Fall back to
        # copying the referent when possible so the checkpoint still contains
        # usable machine state instead of failing late and silently.
        if source.exists() and source.is_file():
            shutil.copy2(source, destination)
        elif source.exists() and source.is_dir():
            shutil.copytree(source, destination, symlinks=True)
        else:
            destination.write_text(target, encoding="utf-8")


def clone_directory(source_root: Path, destination: Path, *, verbose: bool) -> CopyStats:
    directories = 0
    files = 0
    symlinks = 0
    bytes_copied = 0

    destination.mkdir(parents=True, exist_ok=False)

    all_paths = sorted(source_root.rglob("*"), key=lambda item: item.relative_to(source_root).as_posix())
    emit_human(verbose, f"antigit: copy plan contains {len(all_paths)} raw filesystem entries.")

    for path in all_paths:
        relative = path.relative_to(source_root)
        target = destination / relative

        if path.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            copy_symlink(path, target)
            symlinks += 1
        elif path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            shutil.copystat(path, target, follow_symlinks=False)
            directories += 1
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            files += 1
            try:
                bytes_copied += path.stat().st_size
            except OSError:
                pass

        processed = directories + files + symlinks
        if verbose and (processed == 1 or processed % 100 == 0):
            print(
                "antigit: copying raw machine state... "
                f"{processed}/{len(all_paths)} entries, {files} files, {directories} dirs, {symlinks} symlinks.",
                flush=True,
            )

    emit_human(
        verbose,
        "antigit: finished copying raw machine state "
        f"({files} files, {directories} directories, {symlinks} symlinks, {bytes_copied} bytes).",
    )
    return CopyStats(files=files, directories=directories, symlinks=symlinks, bytes=bytes_copied)


def replace_checkpoint(temp_checkpoint: Path, checkpoint: Path, *, verbose: bool) -> None:
    backup: Path | None = None
    if checkpoint.exists() or checkpoint.is_symlink():
        if not checkpoint.is_dir() or checkpoint.is_symlink():
            raise ValueError(f"checkpoint path exists but is not a directory: {checkpoint}")
        backup = checkpoint.with_name(checkpoint.name + ".previous-delete")
        if backup.exists():
            remove_path(backup)
        emit_human(verbose, "antigit: moving previous checkpoint aside before replacement.")
        checkpoint.rename(backup)

    try:
        emit_human(verbose, "antigit: installing the new checkpoint directory.")
        temp_checkpoint.rename(checkpoint)
    except Exception:
        if backup is not None and not checkpoint.exists():
            backup.rename(checkpoint)
        raise
    else:
        if backup is not None:
            emit_human(verbose, "antigit: deleting the previous checkpoint copy.")
            remove_path(backup)


def create_snapshot(
    source_root: Path,
    checkpoint_root: Path,
    *,
    emit_signal_before_replace: bool,
    json_output: bool,
) -> int:
    source, checkpoint_base, checkpoint = validate_source_and_checkpoint(source_root, checkpoint_root)
    verbose = not json_output

    emit_human(verbose, "antigit snapshot: starting external raw machine-state checkpoint.")
    emit_human(verbose, f"antigit: source project: {source}")
    emit_human(verbose, f"antigit: checkpoint directory: {checkpoint}")
    emit_human(verbose, "antigit: source project will not be edited.")
    emit_human(verbose, "antigit: .gitignore is copied as data but its ignore rules are not obeyed.")
    emit_human(verbose, "antigit: zip files, ignored files, .git, caches, logs, and local runtime files are included.")

    checkpoint_base.mkdir(parents=True, exist_ok=True)

    events: list[dict] = []
    summary: dict | None = None
    if checkpoint.exists():
        emit_human(verbose, "antigit: existing checkpoint found; comparing it to the live source before replacement.")
        if verbose:
            collect_entry_infos(source, progress=True, label="source scan")
            collect_entry_infos(checkpoint, progress=True, label="checkpoint scan")
        events, summary = signal_events(source, checkpoint)
        if emit_signal_before_replace:
            emit_signal(events, summary, json_output=json_output)
        elif verbose:
            emit_human(
                True,
                "antigit: comparison complete: "
                f"{summary['changed_files']} changed entries "
                f"(added={summary['added']}, modified={summary['modified']}, deleted={summary['deleted']}).",
            )
    else:
        emit_human(verbose, "antigit: no previous checkpoint exists; this is the first raw clone.")
        if emit_signal_before_replace:
            summary = {
                "event": "antigit.signal.summary",
                "source": str(source),
                "checkpoint": str(checkpoint),
                "changed_files": 0,
                "added": 0,
                "modified": 0,
                "deleted": 0,
            }
            emit_signal([], summary, json_output=json_output)

    temp_parent = checkpoint_base
    temp_checkpoint = Path(tempfile.mkdtemp(prefix=f".{checkpoint.name}.tmp-", dir=temp_parent))
    remove_path(temp_checkpoint)
    try:
        emit_human(verbose, "antigit: cloning the complete source directory into a temporary checkpoint.")
        stats = clone_directory(source, temp_checkpoint, verbose=verbose)
        replace_checkpoint(temp_checkpoint, checkpoint, verbose=verbose)
    except Exception:
        if temp_checkpoint.exists() or temp_checkpoint.is_symlink():
            remove_path(temp_checkpoint)
        raise

    payload = {
        "event": "antigit.snapshot.created",
        "source": str(source),
        "checkpoint_root": str(checkpoint_base),
        "checkpoint": str(checkpoint),
        "checkpoint_name": checkpoint.name,
        "writes_to_source": False,
        "uses_gitignore": False,
        "files_copied": stats.files,
        "directories_copied": stats.directories,
        "symlinks_copied": stats.symlinks,
        "bytes_copied": stats.bytes,
    }
    if summary is not None:
        payload["changed_files_before_replace"] = summary["changed_files"]

    if json_output:
        print(json.dumps(payload, sort_keys=True), flush=True)
    else:
        print("antigit snapshot: complete.", flush=True)
        print(f"antigit: checkpoint is ready at {checkpoint}", flush=True)
        print(
            "antigit: source remained read-only; all writes went to the sibling checkpoint directory.",
            flush=True,
        )
    return 0


def run_signal(source_root: Path, checkpoint_root: Path, *, json_output: bool) -> int:
    source, checkpoint_base, checkpoint = validate_source_and_checkpoint(source_root, checkpoint_root)
    if not checkpoint.exists():
        summary = {
            "event": "antigit.signal.summary",
            "source": str(source),
            "checkpoint": str(checkpoint),
            "changed_files": 0,
            "added": 0,
            "modified": 0,
            "deleted": 0,
        }
        if json_output:
            print(json.dumps(summary, sort_keys=True), flush=True)
        else:
            print(f"antigit signal: no checkpoint exists yet at {checkpoint}", flush=True)
            print("antigit signal summary: added=0 modified=0 deleted=0", flush=True)
        return 0

    if not json_output:
        print(f"antigit signal: comparing source {source}", flush=True)
        print(f"antigit signal: against checkpoint {checkpoint}", flush=True)
        collect_entry_infos(source, progress=True, label="source scan")
        collect_entry_infos(checkpoint, progress=True, label="checkpoint scan")

    events, summary = signal_events(source, checkpoint)
    emit_signal(events, summary, json_output=json_output)
    return 0


def run_guess_stop_words(seeds: list[str], *, json_output: bool) -> int:
    words = guess_stop_words_from_seeds(seeds)
    if json_output:
        print(json.dumps({"event": "antigit.sopwith_stop_words", "sopwith_stop_words": list(words)}, sort_keys=True))
    else:
        print("sopwith stop words:")
        for word in words:
            print(f"  {word}")
    return 0


def add_common_snapshot_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("source", nargs="?", default=".", help="Source project directory. Defaults to the current directory.")
    parser.add_argument(
        "--checkpoint-root",
        default=None,
        help="Directory that contains the named checkpoint. Defaults to ../checkpoint from the source project.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON and suppress human progress narration.")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Anti-git snapshots raw machine state by cloning the current project to "
            "../checkpoint/antigit_<project>_checkpoint without obeying .gitignore."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot_parser = subparsers.add_parser("snapshot", help="Create or replace the external checkpoint directory clone.")
    add_common_snapshot_args(snapshot_parser)
    snapshot_parser.add_argument(
        "--emit-signal",
        action="store_true",
        help="Emit the changes relative to the previous checkpoint before replacing it.",
    )

    signal_parser = subparsers.add_parser("signal", help="Compare the live source project to the existing checkpoint.")
    add_common_snapshot_args(signal_parser)

    guess_parser = subparsers.add_parser("guess-stop-words", help="Expand seed terms into deterministic sopwith stop words.")
    guess_parser.add_argument("seeds", nargs="+")
    guess_parser.add_argument("--json", action="store_true")

    return parser.parse_args(argv)


def checkpoint_root_from_args(source: Path, raw_checkpoint_root: str | None) -> Path:
    if raw_checkpoint_root is None:
        return default_checkpoint_root(source)
    return Path(raw_checkpoint_root)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        if args.command == "snapshot":
            source = Path(args.source)
            checkpoint_root = checkpoint_root_from_args(source, args.checkpoint_root)
            return create_snapshot(
                source,
                checkpoint_root,
                emit_signal_before_replace=args.emit_signal,
                json_output=args.json,
            )
        if args.command == "signal":
            source = Path(args.source)
            checkpoint_root = checkpoint_root_from_args(source, args.checkpoint_root)
            return run_signal(source, checkpoint_root, json_output=args.json)
        if args.command == "guess-stop-words":
            return run_guess_stop_words(args.seeds, json_output=args.json)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        return 2
    except OSError as exc:
        print(f"error: filesystem operation failed: {exc}", file=sys.stderr, flush=True)
        return 1

    print(f"error: unsupported command: {args.command}", file=sys.stderr, flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
