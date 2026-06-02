from __future__ import annotations

import argparse
import datetime as _dt
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator


SNAPSHOT_FORMAT = "antigit.snapshot.v1"
DEFAULT_SNAPSHOT_DIR = ".antigit/snapshots"
DEFAULT_EXCLUDED_DIRS = {
    ".antigit",
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "aider.log",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "venv",
    ".venv",
    "env",
}
DEFAULT_EXCLUDED_FILE_GLOBS = {
    "*.7z",
    "*.avi",
    "*.bak",
    "*.bin",
    "*.db",
    "*.dll",
    "*.egg-info",
    "*.exe",
    "*.gif",
    "*.ico",
    "*.jpeg",
    "*.jpg",
    "*.log",
    "*.mov",
    "*.mp3",
    "*.mp4",
    "*.ogg",
    "*.orig",
    "*.patch",
    "*.pid",
    "*.png",
    "*.pyc",
    "*.pyd",
    "*.rar",
    "*.rej",
    "*.sqlite",
    "*.sqlite3",
    "*.swp",
    "*.swo",
    "*.tar",
    "*.tar.gz",
    "*.temp",
    "*.tgz",
    "*.tmp",
    "*.wasm",
    "*.wav",
    "*.webp",
    "*.zip",
}
TEXT_SUFFIXES = {
    ".bat",
    ".c",
    ".cfg",
    ".cmd",
    ".cpp",
    ".cs",
    ".css",
    ".csv",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".ps1",
    ".py",
    ".rs",
    ".sh",
    ".sql",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
GRAMMAR_STOP_WORDS = {
    ".py": ["def", "class", "import", "from", "return", "if", "elif", "else", "for", "while", "try", "except", "with", "as"],
    ".js": ["function", "const", "let", "var", "return", "if", "else", "for", "while", "await", "async", "import", "export"],
    ".jsx": ["function", "const", "let", "return", "if", "else", "await", "async", "import", "export", "props", "state"],
    ".ts": ["function", "const", "let", "type", "interface", "return", "if", "else", "await", "async", "import", "export"],
    ".tsx": ["function", "const", "let", "type", "interface", "return", "if", "else", "await", "async", "import", "export", "props"],
    ".html": ["html", "head", "body", "div", "span", "script", "link", "class", "id", "data"],
    ".css": ["class", "id", "display", "position", "color", "margin", "padding", "grid", "flex"],
    ".json": ["object", "array", "string", "number", "true", "false", "null"],
    ".yaml": ["mapping", "sequence", "string", "number", "true", "false", "null"],
    ".yml": ["mapping", "sequence", "string", "number", "true", "false", "null"],
    ".toml": ["table", "array", "string", "number", "true", "false"],
    ".ps1": ["param", "function", "if", "else", "foreach", "try", "catch", "return"],
    ".sh": ["if", "then", "else", "fi", "for", "do", "done", "case", "esac", "function"],
    ".bat": ["if", "else", "for", "set", "call", "goto", "exit"],
    ".md": ["heading", "list", "code", "link", "table"],
}
GUESS_MAP = {
    "anti": ["inverse", "mirror", "counter", "reversal"],
    "git": ["snapshot", "tree", "commit", "checkout", "pull"],
    "pull": ["restore", "merge", "checkpoint", "state"],
    "snapshot": ["checkpoint", "archive", "manifest", "fossil"],
    "checkpoint": ["snapshot", "restore", "anchor", "state"],
    "restore": ["rollback", "recover", "original", "checkpoint"],
    "number": ["literal", "amount", "integer", "float"],
    "numbers": ["literal", "amount", "integer", "float"],
    "grammar": ["syntax", "token", "parser", "language"],
    "signal": ["event", "pulse", "trace", "plan"],
    "sopwith": ["stop", "word", "grammar", "signal"],
    "stop": ["token", "guard", "boundary", "grammar"],
    "word": ["token", "symbol", "identifier", "literal"],
    "python": ["def", "class", "import", "return"],
    "javascript": ["function", "const", "let", "return"],
    "json": ["object", "array", "string", "number"],
}
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,63}")
NUMBER_RE = re.compile(r"(?<![A-Za-z_])[-+]?(?:0x[0-9A-Fa-f]+|\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)(?![A-Za-z_])")


class AntiGitError(RuntimeError):
    pass


@dataclass(frozen=True)
class SnapshotEntry:
    path: str
    size: int
    sha256: str
    mode: int
    stop_words: tuple[str, ...]
    numeric_literals: tuple[str, ...]


@dataclass(frozen=True)
class Snapshot:
    manifest: dict
    entries: dict[str, SnapshotEntry]
    archive_path: Path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%SZ")


def utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_relative(path: Path | PurePosixPath | str) -> str:
    raw = str(path).replace("\\", "/")
    normalized = PurePosixPath(raw)
    if normalized.is_absolute():
        raise AntiGitError(f"absolute paths are not allowed: {path}")
    parts = [part for part in normalized.parts if part not in ("", ".")]
    if not parts:
        raise AntiGitError("empty relative path is not allowed")
    if any(part == ".." for part in parts):
        raise AntiGitError(f"parent traversal is not allowed: {path}")
    if any(len(part) >= 2 and part[0].isalpha() and part[1] == ":" for part in parts):
        raise AntiGitError(f"windows drive designators are not allowed: {path}")
    return "/".join(parts)


def safe_join(root: Path, relative: str) -> Path:
    normalized = normalize_relative(relative)
    target = (root / normalized).resolve()
    root_resolved = root.resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise AntiGitError(f"path escapes root: {relative}") from exc
    return target


def posix_parts(relative: str) -> tuple[str, ...]:
    return tuple(PurePosixPath(normalize_relative(relative)).parts)


def path_tokens(relative: str) -> list[str]:
    words: list[str] = []
    for part in posix_parts(relative):
        stem = Path(part).stem
        pieces = re.split(r"[^A-Za-z0-9]+", stem)
        for piece in pieces:
            if not piece:
                continue
            # Split basic camelCase/PascalCase without losing snake_case.
            words.extend(re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", piece))
    return words


def stable_unique(values: Iterable[str], *, limit: int | None = None) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = value.strip().lower()
        if not item:
            continue
        if len(item) > 80:
            continue
        if item not in seen:
            seen.add(item)
            result.append(item)
            if limit is not None and len(result) >= limit:
                break
    return tuple(result)


def is_probably_text(path: Path, data: bytes) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    if not data:
        return True
    if b"\x00" in data:
        return False
    sample = data[:4096]
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def decode_text_for_signal(path: Path, data: bytes) -> str:
    if not is_probably_text(path, data):
        return ""
    try:
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def numeric_literals(text: str, *, limit: int = 24) -> tuple[str, ...]:
    return stable_unique(NUMBER_RE.findall(text), limit=limit)


def identifier_words(text: str, *, limit: int = 64) -> tuple[str, ...]:
    raw = IDENTIFIER_RE.findall(text)
    blocked = {
        "and",
        "are",
        "but",
        "for",
        "from",
        "has",
        "not",
        "the",
        "this",
        "that",
        "with",
        "you",
        "your",
    }
    return stable_unique((item for item in raw if item.lower() not in blocked), limit=limit)


def guess_stop_words(words: Iterable[str], *, limit: int = 64) -> tuple[str, ...]:
    generated: list[str] = []
    for word in stable_unique(words):
        generated.append(word)
        generated.extend(GUESS_MAP.get(word, ()))
        if "." in word:
            generated.extend(GRAMMAR_STOP_WORDS.get(Path(word).suffix.lower(), ()))
    return stable_unique(generated, limit=limit)


def sopwith_stop_words(relative: str, data: bytes = b"") -> tuple[str, ...]:
    suffix = PurePosixPath(relative).suffix.lower()
    text = decode_text_for_signal(Path(relative), data)
    words: list[str] = []
    words.extend(path_tokens(relative))
    words.extend(GRAMMAR_STOP_WORDS.get(suffix, ()))
    words.extend(identifier_words(text, limit=32))
    words.extend(numeric_literals(text, limit=12))
    words.extend(guess_stop_words(words, limit=32))
    return stable_unique(words, limit=96)


def load_ignore_file(root: Path) -> list[str]:
    ignore_path = root / ".antigitignore"
    if not ignore_path.exists():
        return []
    lines: list[str] = []
    for raw in ignore_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line.replace("\\", "/"))
    return lines


def is_excluded(relative: str, user_patterns: Iterable[str] = ()) -> bool:
    normalized = normalize_relative(relative)
    parts = posix_parts(normalized)
    if any(part in DEFAULT_EXCLUDED_DIRS for part in parts[:-1]):
        return True
    name = parts[-1]
    if name in DEFAULT_EXCLUDED_DIRS:
        return True
    for pattern in DEFAULT_EXCLUDED_FILE_GLOBS:
        if fnmatch.fnmatch(name.lower(), pattern.lower()) or fnmatch.fnmatch(normalized.lower(), pattern.lower()):
            return True
    for pattern in user_patterns:
        pattern = pattern.strip().replace("\\", "/")
        if not pattern:
            continue
        anchored = pattern.startswith("/")
        pattern = pattern.lstrip("/")
        if pattern.endswith("/"):
            prefix = pattern.rstrip("/") + "/"
            if normalized == pattern.rstrip("/") or normalized.startswith(prefix):
                return True
            continue
        if anchored:
            if fnmatch.fnmatch(normalized, pattern):
                return True
        elif fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(normalized, pattern):
            return True
    return False


def iter_snapshot_files(root: Path, *, include: list[str] | None = None, include_excluded: bool = False) -> Iterator[Path]:
    root = root.resolve()
    user_patterns = [] if include_excluded else load_ignore_file(root)
    include_set = {normalize_relative(item) for item in include or []}
    if include_set:
        for relative in sorted(include_set):
            path = safe_join(root, relative)
            if path.is_file():
                yield path
        return

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if not include_excluded and is_excluded(relative, user_patterns):
            continue
        yield path


def make_entry(root: Path, path: Path) -> SnapshotEntry:
    data = path.read_bytes()
    relative = normalize_relative(path.relative_to(root).as_posix())
    return SnapshotEntry(
        path=relative,
        size=len(data),
        sha256=sha256_bytes(data),
        mode=path.stat().st_mode & 0o777,
        stop_words=sopwith_stop_words(relative, data),
        numeric_literals=numeric_literals(decode_text_for_signal(path, data)),
    )


def entry_to_json(entry: SnapshotEntry) -> dict:
    return {
        "path": entry.path,
        "size": entry.size,
        "sha256": entry.sha256,
        "mode": oct(entry.mode),
        "stop_words": list(entry.stop_words),
        "numeric_literals": list(entry.numeric_literals),
    }


def entry_from_json(payload: dict) -> SnapshotEntry:
    mode_raw = payload.get("mode", "0o644")
    if isinstance(mode_raw, int):
        mode = mode_raw
    else:
        mode = int(str(mode_raw), 8)
    return SnapshotEntry(
        path=normalize_relative(payload["path"]),
        size=int(payload["size"]),
        sha256=str(payload["sha256"]),
        mode=mode,
        stop_words=tuple(str(item) for item in payload.get("stop_words", [])),
        numeric_literals=tuple(str(item) for item in payload.get("numeric_literals", [])),
    )


def default_snapshot_path(root: Path) -> Path:
    destination = root / DEFAULT_SNAPSHOT_DIR
    return destination / f"antigit-{root.name}-{utc_stamp()}.zip"


def create_snapshot(root: Path, output: Path | None, *, include: list[str] | None = None, include_excluded: bool = False) -> tuple[Path, dict]:
    root = root.resolve()
    if not root.exists():
        raise AntiGitError(f"root does not exist: {root}")
    output = (output or default_snapshot_path(root)).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    entries = [make_entry(root, path) for path in iter_snapshot_files(root, include=include, include_excluded=include_excluded)]
    manifest = {
        "format": SNAPSHOT_FORMAT,
        "created_at_utc": utc_iso(),
        "root_name": root.name,
        "file_count": len(entries),
        "files": [entry_to_json(entry) for entry in entries],
    }

    with tempfile.NamedTemporaryFile("wb", delete=False, dir=str(output.parent), suffix=".tmp") as tmp:
        temp_name = Path(tmp.name)
    try:
        with zipfile.ZipFile(temp_name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            for entry in entries:
                path = safe_join(root, entry.path)
                archive.write(path, f"files/{entry.path}")
        temp_name.replace(output)
    finally:
        if temp_name.exists():
            temp_name.unlink()

    return output, manifest


def load_snapshot(archive_path: Path) -> Snapshot:
    archive_path = archive_path.resolve()
    if not archive_path.exists():
        raise AntiGitError(f"snapshot archive does not exist: {archive_path}")
    with zipfile.ZipFile(archive_path) as archive:
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except KeyError as exc:
            raise AntiGitError("snapshot archive is missing manifest.json") from exc
    if manifest.get("format") != SNAPSHOT_FORMAT:
        raise AntiGitError(f"unsupported snapshot format: {manifest.get('format')!r}")
    entries = {entry.path: entry for entry in (entry_from_json(item) for item in manifest.get("files", []))}
    return Snapshot(manifest=manifest, entries=entries, archive_path=archive_path)


def archive_member_for(entry: SnapshotEntry) -> str:
    return f"files/{entry.path}"


def read_snapshot_file(snapshot: Snapshot, entry: SnapshotEntry) -> bytes:
    with zipfile.ZipFile(snapshot.archive_path) as archive:
        data = archive.read(archive_member_for(entry))
    actual = sha256_bytes(data)
    if actual != entry.sha256:
        raise AntiGitError(f"snapshot hash mismatch for {entry.path}: {actual} != {entry.sha256}")
    return data


def target_file_state(root: Path, entry: SnapshotEntry) -> tuple[str, str | None]:
    target = safe_join(root, entry.path)
    if not target.exists():
        return "missing", None
    if not target.is_file():
        return "blocked", None
    data = target.read_bytes()
    digest = sha256_bytes(data)
    if digest == entry.sha256:
        return "same", digest
    return "drifted", digest


def diff_action_for_state(state: str) -> str:
    if state == "missing":
        return "create"
    if state == "drifted":
        return "restore"
    if state == "same":
        return "unchanged"
    return "blocked"


def snapshot_signal(root: Path, snapshot: Snapshot, *, include_unchanged: bool = False) -> list[dict]:
    root = root.resolve()
    events: list[dict] = []
    for path, entry in sorted(snapshot.entries.items()):
        state, target_sha = target_file_state(root, entry)
        action = diff_action_for_state(state)
        if action == "unchanged" and not include_unchanged:
            continue
        events.append(
            {
                "event": "antigit.pull.signal",
                "path": path,
                "action": action,
                "state": state,
                "snapshot_sha256": entry.sha256,
                "target_sha256": target_sha,
                "size": entry.size,
                "numeric_literals": list(entry.numeric_literals),
                "sopwith_stop_words": list(entry.stop_words),
            }
        )
    return events


def extra_target_files(root: Path, snapshot: Snapshot) -> list[str]:
    existing = {
        path.relative_to(root).as_posix()
        for path in iter_snapshot_files(root)
        if path.is_file()
    }
    return sorted(existing.difference(snapshot.entries))


def write_json_lines(items: Iterable[dict]) -> None:
    for item in items:
        print(json.dumps(item, sort_keys=True))


def print_signal_text(events: list[dict]) -> None:
    if not events:
        print("anti-git signal: checkpoint already matches target")
        return
    for event in events:
        stop = ", ".join(event["sopwith_stop_words"][:12])
        numbers = ", ".join(event["numeric_literals"][:8]) or "-"
        print(f"{event['action']:9} {event['path']}  numbers=[{numbers}]  sopwith=[{stop}]")


def pull_snapshot(root: Path, snapshot: Snapshot, *, dry_run: bool = False, emit_signal: bool = False, json_output: bool = False) -> int:
    root = root.resolve()
    events = snapshot_signal(root, snapshot, include_unchanged=False)

    if emit_signal:
        if json_output:
            write_json_lines(events)
        else:
            print_signal_text(events)

    changed = 0
    blocked = 0
    for event in events:
        if event["action"] == "blocked":
            blocked += 1
            continue
        if event["action"] not in {"create", "restore"}:
            continue
        changed += 1
        if dry_run:
            continue
        entry = snapshot.entries[event["path"]]
        data = read_snapshot_file(snapshot, entry)
        target = safe_join(root, entry.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        try:
            os.chmod(target, entry.mode)
        except OSError:
            # Windows may not preserve POSIX-style modes. The bytes are the authoritative restore.
            pass

    summary = {
        "event": "antigit.pull.summary",
        "archive": str(snapshot.archive_path),
        "root": str(root),
        "dry_run": dry_run,
        "changed_files": changed,
        "blocked_files": blocked,
    }
    if json_output:
        print(json.dumps(summary, sort_keys=True))
    elif not emit_signal:
        print(f"anti-git pull: changed_files={changed} blocked_files={blocked} dry_run={dry_run}")
    return 1 if blocked else 0


def command_snapshot(args: argparse.Namespace) -> int:
    output, manifest = create_snapshot(
        Path(args.root),
        Path(args.output) if args.output else None,
        include=list(args.include or []),
        include_excluded=bool(args.include_excluded),
    )
    summary = {
        "event": "antigit.snapshot.created",
        "archive": str(output),
        "format": SNAPSHOT_FORMAT,
        "file_count": manifest["file_count"],
        "root_name": manifest["root_name"],
        "created_at_utc": manifest["created_at_utc"],
    }
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(f"anti-git snapshot: {output}")
        print(f"files: {manifest['file_count']}")
    return 0


def command_signal(args: argparse.Namespace) -> int:
    snapshot = load_snapshot(Path(args.snapshot))
    events = snapshot_signal(Path(args.root), snapshot, include_unchanged=bool(args.include_unchanged))
    if args.json:
        write_json_lines(events)
    else:
        print_signal_text(events)
    return 0


def command_pull(args: argparse.Namespace) -> int:
    snapshot = load_snapshot(Path(args.snapshot))
    return pull_snapshot(
        Path(args.root),
        snapshot,
        dry_run=bool(args.dry_run),
        emit_signal=bool(args.emit_signal),
        json_output=bool(args.json),
    )


def command_guess_stop_words(args: argparse.Namespace) -> int:
    words = list(args.words or [])
    guessed = guess_stop_words(words, limit=args.limit)
    payload = {
        "event": "antigit.sopwith_stop_words.guess",
        "input": words,
        "sopwith_stop_words": list(guessed),
    }
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(" ".join(payload["sopwith_stop_words"]))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Anti-git snapshots a live tree, compares it to a checkpoint, and pulls "
            "checkpoint bytes back while optionally emitting sopwith stop-word signals."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="Create an anti-git checkpoint archive from a repository tree.")
    snap.add_argument("root", nargs="?", default=".", help="Repository or code-base root to snapshot.")
    snap.add_argument("--output", "-o", default=None, help="Snapshot zip path. Defaults to .antigit/snapshots/.")
    snap.add_argument("--include", action="append", help="Limit snapshot to a repository-relative file path. May be repeated.")
    snap.add_argument("--include-excluded", action="store_true", help="Do not apply built-in or .antigitignore exclusions.")
    snap.add_argument("--json", action="store_true", help="Print a JSON summary.")
    snap.set_defaults(func=command_snapshot)

    signal = sub.add_parser("signal", help="Compare a checkpoint to a target tree and emit pull-plan signals.")
    signal.add_argument("snapshot", help="Anti-git snapshot zip.")
    signal.add_argument("--root", default=".", help="Target repository root. Defaults to the current directory.")
    signal.add_argument("--include-unchanged", action="store_true", help="Include files that already match the snapshot.")
    signal.add_argument("--json", action="store_true", help="Print one JSON object per signal.")
    signal.set_defaults(func=command_signal)

    pull = sub.add_parser("pull", help="Restore checkpoint bytes into the target tree.")
    pull.add_argument("snapshot", help="Anti-git snapshot zip.")
    pull.add_argument("--root", default=".", help="Target repository root. Defaults to the current directory.")
    pull.add_argument("--dry-run", action="store_true", help="Report planned restoration without writing files.")
    pull.add_argument("--emit-signal", action="store_true", help="Print one signal per changed checkpoint file.")
    pull.add_argument("--json", action="store_true", help="Print JSON lines for signals and summary.")
    pull.set_defaults(func=command_pull)

    guess = sub.add_parser("guess-stop-words", help="Guess deterministic sopwith stop words from seed words.")
    guess.add_argument("words", nargs="+", help="Seed words, paths, languages, or signal labels.")
    guess.add_argument("--limit", type=int, default=64, help="Maximum number of output stop words.")
    guess.add_argument("--json", action="store_true", help="Print a JSON payload.")
    guess.set_defaults(func=command_guess_stop_words)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except AntiGitError as exc:
        print(f"antigit error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("antigit interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
