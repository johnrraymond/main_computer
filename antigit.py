#!/usr/bin/env python3
"""External raw machine-state checkpoints for Main Computer projects.

`antigit` intentionally does not behave like git:

* the source project is never edited;
* .gitignore rules are not obeyed;
* ignored files, caches, logs, zip files, and .git data are treated as raw
  local machine state.

The snapshot operation is best-effort at the individual filesystem-entry level.
A locked or permission-denied file should not prevent the rest of the checkpoint
from being captured.  Skipped entries are reported and recorded next to the
checkpoint so callers can decide whether they matter.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import random
import re
import shutil
import stat
import string
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable, Iterator


PROGRESS_EVERY = 100
READ_CHUNK_SIZE = 1024 * 1024
ISSUES_SCHEMA_VERSION = 1


@dataclass
class CopyIssue:
    path: str
    operation: str
    error_type: str
    error: str
    errno: int | None = None
    winerror: int | None = None
    fatal: bool = False


@dataclass
class CopyStats:
    planned_entries: int = 0
    copied_entries: int = 0
    copied_files: int = 0
    copied_dirs: int = 0
    copied_symlinks: int = 0
    skipped_entries: int = 0
    metadata_warnings: int = 0


class AntigitError(Exception):
    """Expected CLI error."""


def _print(message: str, *, json_mode: bool = False) -> None:
    if not json_mode:
        print(message, flush=True)


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def _is_relative_to(child: Path, parent: Path) -> bool:
    child_resolved = _safe_resolve(child)
    parent_resolved = _safe_resolve(parent)
    try:
        child_resolved.relative_to(parent_resolved)
        return True
    except ValueError:
        return False


def _checkpoint_name(source: Path) -> str:
    return f"antigit_{source.name}_checkpoint"


def _default_checkpoint_root(source: Path) -> Path:
    return source.parent / "checkpoint"


def _random_suffix(length: int = 8) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def _issue(path: Path, source_root: Path, operation: str, exc: BaseException, *, fatal: bool = False) -> CopyIssue:
    try:
        rel = path.relative_to(source_root).as_posix()
    except ValueError:
        rel = str(path)
    return CopyIssue(
        path=rel,
        operation=operation,
        error_type=exc.__class__.__name__,
        error=str(exc),
        errno=getattr(exc, "errno", None),
        winerror=getattr(exc, "winerror", None),
        fatal=fatal,
    )


def _on_rm_error(func: Any, path: str, exc_info: Any) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        func(path)
    except Exception:
        raise


def _rmtree_best_effort(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    shutil.rmtree(path, onerror=_on_rm_error)


def _iter_entries(root: Path, issues: list[CopyIssue]) -> Iterator[Path]:
    """Yield all raw filesystem entries under root without following symlinks.

    Directory enumeration errors are recorded and skipped rather than aborting
    the entire checkpoint.
    """

    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as entries:
                children = []
                for entry in entries:
                    path = Path(entry.path)
                    children.append(path)
                children.sort(key=lambda p: p.name.lower())
        except OSError as exc:
            issues.append(_issue(directory, root, "scandir", exc))
            continue

        for path in children:
            yield path
            try:
                is_dir = path.is_dir() and not path.is_symlink()
            except OSError as exc:
                issues.append(_issue(path, root, "stat", exc))
                continue
            if is_dir:
                stack.append(path)


def _copystat_best_effort(src: Path, dst: Path, source_root: Path, issues: list[CopyIssue]) -> None:
    try:
        shutil.copystat(src, dst, follow_symlinks=False)
    except OSError as exc:
        # Metadata failures are common on Windows for locked, readonly, or
        # special .git object files.  The file content is the important part.
        issues.append(_issue(src, source_root, "copystat", exc))


def _copy_file_contents(src: Path, dst: Path) -> None:
    with src.open("rb") as source, dst.open("wb") as target:
        while True:
            chunk = source.read(READ_CHUNK_SIZE)
            if not chunk:
                break
            target.write(chunk)


def _copy_file_best_effort(src: Path, dst: Path, source_root: Path, issues: list[CopyIssue]) -> bool:
    # Test hook used only by the local test suite to make a subprocess simulate
    # the exact kind of per-entry failure that is hard to trigger portably.
    fail_basename = os.environ.get("ANTIGIT_TEST_FAIL_COPY_BASENAME")
    if fail_basename and src.name == fail_basename:
        exc = PermissionError(errno.EACCES, "simulated access denied", str(src))
        issues.append(_issue(src, source_root, "copy-file", exc))
        return False

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        _copy_file_contents(src, dst)
    except OSError as exc:
        try:
            if dst.exists() or dst.is_symlink():
                dst.unlink()
        except OSError:
            pass
        issues.append(_issue(src, source_root, "copy-file", exc))
        return False

    _copystat_best_effort(src, dst, source_root, issues)
    return True


def _copy_symlink_best_effort(src: Path, dst: Path, source_root: Path, issues: list[CopyIssue]) -> bool:
    try:
        target = os.readlink(src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, dst)
        return True
    except OSError as exc:
        issues.append(_issue(src, source_root, "copy-symlink", exc))
        return False


def _copy_raw_tree(source: Path, target: Path, *, json_mode: bool = False) -> tuple[CopyStats, list[CopyIssue]]:
    issues: list[CopyIssue] = []
    entries = list(_iter_entries(source, issues))
    stats = CopyStats(planned_entries=len(entries))

    _print(f"antigit: copy plan contains {stats.planned_entries} raw filesystem entries.", json_mode=json_mode)
    _print("antigit: cloning the complete source directory into a temporary checkpoint.", json_mode=json_mode)

    target.mkdir(parents=True, exist_ok=True)

    for index, src in enumerate(entries, start=1):
        rel = src.relative_to(source)
        dst = target / rel

        try:
            if src.is_symlink():
                copied = _copy_symlink_best_effort(src, dst, source, issues)
                if copied:
                    stats.copied_entries += 1
                    stats.copied_symlinks += 1
                else:
                    stats.skipped_entries += 1
            elif src.is_dir():
                try:
                    dst.mkdir(parents=True, exist_ok=True)
                    _copystat_best_effort(src, dst, source, issues)
                    stats.copied_entries += 1
                    stats.copied_dirs += 1
                except OSError as exc:
                    issues.append(_issue(src, source, "copy-dir", exc))
                    stats.skipped_entries += 1
            elif src.is_file():
                copied = _copy_file_best_effort(src, dst, source, issues)
                if copied:
                    stats.copied_entries += 1
                    stats.copied_files += 1
                else:
                    stats.skipped_entries += 1
            else:
                # Devices, sockets, and other non-regular entries are machine
                # state but are not portable checkpoint payloads.
                exc = OSError(errno.EINVAL, "unsupported filesystem entry type", str(src))
                issues.append(_issue(src, source, "copy-special", exc))
                stats.skipped_entries += 1
        except OSError as exc:
            issues.append(_issue(src, source, "copy-entry", exc))
            stats.skipped_entries += 1

        if not json_mode and (index == 1 or index % PROGRESS_EVERY == 0 or index == stats.planned_entries):
            print(
                "antigit: copying raw machine state... "
                f"{index}/{stats.planned_entries} entries, "
                f"{stats.copied_files} files, {stats.copied_dirs} dirs, "
                f"{stats.copied_symlinks} symlinks.",
                flush=True,
            )

    stats.metadata_warnings = sum(1 for issue in issues if issue.operation == "copystat")
    return stats, issues


def _write_issues_file(checkpoint_root: Path, checkpoint_name: str, issues: list[CopyIssue]) -> Path | None:
    if not issues:
        return None
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    path = checkpoint_root / f"{checkpoint_name}.copy_issues.json"
    payload = {
        "schema_version": ISSUES_SCHEMA_VERSION,
        "checkpoint_name": checkpoint_name,
        "created_at_epoch": time.time(),
        "issues": [asdict(issue) for issue in issues],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _relative_file_map(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    if not root.exists():
        return result
    issues: list[CopyIssue] = []
    for path in _iter_entries(root, issues):
        try:
            if path.is_file() or path.is_symlink():
                result[path.relative_to(root).as_posix()] = path
        except OSError:
            continue
    return result


def _file_digest(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(READ_CHUNK_SIZE)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


_NUMERIC_RE = re.compile(r"(?<![A-Za-z_])-?\d+(?:\.\d+)?")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _read_text_sample(path: Path, limit: int = 512_000) -> str:
    try:
        with path.open("rb") as handle:
            data = handle.read(limit)
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


def _numeric_literals(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for match in _NUMERIC_RE.finditer(text):
        seen.setdefault(match.group(0), None)
    return list(seen)


def _guess_stop_words_from_text(*texts: str) -> list[str]:
    seen: dict[str, None] = {}
    for text in texts:
        for match in _WORD_RE.finditer(text):
            seen.setdefault(match.group(0), None)
    return list(seen)


def _emit_signal(source: Path, checkpoint: Path, *, json_mode: bool = False) -> int:
    source_files = _relative_file_map(source)
    checkpoint_files = _relative_file_map(checkpoint)

    changed = 0
    for rel in sorted(set(source_files) | set(checkpoint_files)):
        source_path = source_files.get(rel)
        checkpoint_path = checkpoint_files.get(rel)

        if source_path is not None and checkpoint_path is None:
            action = "added"
        elif source_path is None and checkpoint_path is not None:
            action = "deleted"
        else:
            source_digest = _file_digest(source_path) if source_path else None
            checkpoint_digest = _file_digest(checkpoint_path) if checkpoint_path else None
            if source_digest == checkpoint_digest:
                continue
            action = "modified"

        changed += 1
        event: dict[str, Any] = {
            "event": "antigit.signal",
            "path": rel,
            "action": action,
        }

        before_text = _read_text_sample(checkpoint_path) if checkpoint_path else ""
        after_text = _read_text_sample(source_path) if source_path else ""

        if action == "modified":
            event["before_numeric_literals"] = _numeric_literals(before_text)
            event["after_numeric_literals"] = _numeric_literals(after_text)
            event["sopwith_stop_words"] = _guess_stop_words_from_text(before_text, after_text)
        elif action == "added":
            event["numeric_literals"] = _numeric_literals(after_text)
            event["sopwith_stop_words"] = _guess_stop_words_from_text(after_text)
        elif action == "deleted":
            event["numeric_literals"] = _numeric_literals(before_text)
            event["sopwith_stop_words"] = _guess_stop_words_from_text(before_text)

        if json_mode:
            print(json.dumps(event, sort_keys=True))
        else:
            print(f"{action}: {rel}")

    summary = {
        "event": "antigit.signal.summary",
        "changed_files": changed,
        "source": str(source),
        "checkpoint": str(checkpoint),
    }
    if json_mode:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(f"antigit signal: {changed} changed files.")
    return 0


def _snapshot(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve()
    if not source.exists() or not source.is_dir():
        raise AntigitError(f"source project does not exist or is not a directory: {source}")

    checkpoint_root = Path(args.checkpoint_root).resolve() if args.checkpoint_root else _default_checkpoint_root(source).resolve()
    if _is_relative_to(checkpoint_root, source):
        raise AntigitError("checkpoint root must not be inside the source project")

    name = _checkpoint_name(source)
    checkpoint = checkpoint_root / name
    temp = checkpoint_root / f".{name}.tmp-{_random_suffix()}"
    issues_path: Path | None = None

    _print("antigit snapshot: starting external raw machine-state checkpoint.", json_mode=args.json)
    _print(f"antigit: source project: {source}", json_mode=args.json)
    _print(f"antigit: checkpoint directory: {checkpoint}", json_mode=args.json)
    _print("antigit: source project will not be edited.", json_mode=args.json)
    _print("antigit: .gitignore is copied as data but its ignore rules are not obeyed.", json_mode=args.json)
    _print("antigit: zip files, ignored files, .git, caches, logs, and local runtime files are included.", json_mode=args.json)

    had_previous = checkpoint.exists()
    if had_previous:
        _print("antigit: previous checkpoint exists; signal can be emitted before replacement.", json_mode=args.json)
        if args.emit_signal:
            _emit_signal(source, checkpoint, json_mode=args.json)
    else:
        _print("antigit: no previous checkpoint exists; this is the first raw clone.", json_mode=args.json)

    checkpoint_root.mkdir(parents=True, exist_ok=True)
    if temp.exists():
        _rmtree_best_effort(temp)

    try:
        stats, issues = _copy_raw_tree(source, temp, json_mode=args.json)

        if checkpoint.exists():
            old = checkpoint_root / f".{name}.old-{_random_suffix()}"
            checkpoint.rename(old)
            try:
                temp.rename(checkpoint)
            except Exception:
                old.rename(checkpoint)
                raise
            _rmtree_best_effort(old)
        else:
            temp.rename(checkpoint)

        issues_path = _write_issues_file(checkpoint_root, name, issues)
    finally:
        if temp.exists():
            try:
                _rmtree_best_effort(temp)
            except Exception:
                pass

    if issues and not args.json:
        print(
            f"antigit: warning: skipped {stats.skipped_entries} entries "
            f"and saw {stats.metadata_warnings} metadata warnings; checkpoint still completed.",
            file=sys.stderr,
            flush=True,
        )
        if issues_path:
            print(f"antigit: copy issue details: {issues_path}", file=sys.stderr, flush=True)

    payload = {
        "event": "antigit.snapshot.created",
        "source": str(source),
        "checkpoint": str(checkpoint),
        "checkpoint_name": name,
        "checkpoint_root": str(checkpoint_root),
        "writes_to_source": False,
        "uses_gitignore": False,
        "had_previous_checkpoint": had_previous,
        "planned_entries": stats.planned_entries,
        "copied_entries": stats.copied_entries,
        "copied_files": stats.copied_files,
        "copied_dirs": stats.copied_dirs,
        "copied_symlinks": stats.copied_symlinks,
        "skipped_entries": stats.skipped_entries,
        "metadata_warnings": stats.metadata_warnings,
        "copy_issues_path": str(issues_path) if issues_path else None,
    }

    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print("antigit snapshot: checkpoint complete.")
    return 0


def _signal(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve()
    if not source.exists() or not source.is_dir():
        raise AntigitError(f"source project does not exist or is not a directory: {source}")
    checkpoint_root = Path(args.checkpoint_root).resolve() if args.checkpoint_root else _default_checkpoint_root(source).resolve()
    checkpoint = checkpoint_root / _checkpoint_name(source)
    return _emit_signal(source, checkpoint, json_mode=args.json)


def _guess_stop_words(args: argparse.Namespace) -> int:
    words: list[str] = []
    for value in args.words:
        for match in _WORD_RE.finditer(value):
            token = match.group(0).lower()
            if token not in words:
                words.append(token)

    if words:
        result = [words[0], "stop", "word"]
        for token in words[1:]:
            if token not in result:
                result.append(token)
    else:
        result = ["stop", "word"]

    for token in [
        "restore",
        "snapshot",
        "checkpoint",
        "integer",
        "float",
        "number",
        "literal",
        "file",
        "path",
    ]:
        if token not in result:
            result.append(token)

    payload = {"event": "antigit.guess_stop_words", "sopwith_stop_words": result}
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print("\n".join(result))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="External raw machine-state checkpoint helper.")
    sub = parser.add_subparsers(dest="command", required=True)

    snapshot = sub.add_parser("snapshot", help="Create or replace an external raw checkpoint clone.")
    snapshot.add_argument("source")
    snapshot.add_argument("--checkpoint-root")
    snapshot.add_argument("--emit-signal", action="store_true")
    snapshot.add_argument("--json", action="store_true")
    snapshot.set_defaults(func=_snapshot)

    signal = sub.add_parser("signal", help="Compare source project against its current checkpoint.")
    signal.add_argument("source")
    signal.add_argument("--checkpoint-root")
    signal.add_argument("--json", action="store_true")
    signal.set_defaults(func=_signal)

    stop_words = sub.add_parser("guess-stop-words", help="Expand seed words into deterministic signal stop words.")
    stop_words.add_argument("words", nargs="*")
    stop_words.add_argument("--json", action="store_true")
    stop_words.set_defaults(func=_guess_stop_words)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return int(args.func(args))
    except AntigitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
