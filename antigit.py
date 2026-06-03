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


TEXT_SCAN_LIMIT = 2 * 1024 * 1024

LANGUAGE_GRAMMAR_WORDS: dict[str, tuple[str, ...]] = {
    ".py": ("def", "class", "import", "from", "return", "with", "for", "while", "if", "elif", "else", "try", "except", "raise", "yield", "async", "await", "lambda"),
    ".js": ("function", "class", "import", "export", "return", "const", "let", "var", "if", "else", "for", "while", "async", "await"),
    ".jsx": ("function", "class", "import", "export", "return", "const", "let", "props", "state", "component"),
    ".ts": ("function", "class", "interface", "type", "import", "export", "return", "const", "let", "async", "await"),
    ".tsx": ("function", "class", "interface", "type", "import", "export", "return", "const", "let", "props", "state", "component"),
    ".json": ("object", "array", "string", "number", "boolean", "null", "key", "value"),
    ".yml": ("mapping", "sequence", "key", "value", "anchor", "alias"),
    ".yaml": ("mapping", "sequence", "key", "value", "anchor", "alias"),
    ".toml": ("table", "array", "string", "integer", "float", "boolean", "key", "value"),
    ".ini": ("section", "key", "value", "setting"),
    ".cfg": ("section", "key", "value", "setting"),
    ".md": ("heading", "link", "code", "list", "quote"),
    ".rst": ("heading", "directive", "role", "literal", "section"),
    ".html": ("html", "head", "body", "div", "span", "script", "style", "class", "id"),
    ".css": ("selector", "property", "value", "class", "id", "media"),
    ".sh": ("if", "then", "else", "fi", "for", "while", "do", "done", "case", "export"),
    ".ps1": ("param", "function", "if", "else", "foreach", "where", "object", "return"),
    ".sql": ("select", "insert", "update", "delete", "from", "where", "join", "group", "order"),
}

GUESS_MAP: dict[str, tuple[str, ...]] = {
    "anti": ("git", "inverse", "checkpoint", "state"),
    "git": ("pull", "diff", "status", "tracked", "state"),
    "pull": ("restore", "fetch", "compare", "checkpoint", "state"),
    "snapshot": ("checkpoint", "clone", "state", "copy", "mirror"),
    "checkpoint": ("snapshot", "clone", "state", "restore", "preserve"),
    "clone": ("copy", "mirror", "duplicate", "state"),
    "copy": ("clone", "mirror", "duplicate"),
    "duplicate": ("clone", "copy", "mirror"),
    "machine": ("state", "runtime", "local", "raw"),
    "state": ("machine", "runtime", "checkpoint", "snapshot"),
    "raw": ("machine", "state", "unignored", "local"),
    "ignored": ("gitignore", "untracked", "local", "raw"),
    "gitignore": ("ignored", "untracked", "local", "raw"),
    "untracked": ("ignored", "local", "raw", "machine"),
    "number": ("numeric", "literal", "integer", "float", "amount"),
    "numbers": ("numeric", "literal", "integer", "float", "amount"),
    "numeric": ("number", "literal", "integer", "float"),
    "literal": ("number", "numeric", "string", "token"),
    "grammar": ("syntax", "language", "token", "query"),
    "language": ("grammar", "syntax", "token", "query"),
    "sopwith": ("stop", "word", "signal", "query"),
    "stop": ("word", "signal", "query"),
    "word": ("stop", "signal", "query", "token"),
    "python": ("def", "class", "import", "return", "async", "await"),
    "javascript": ("function", "class", "import", "export", "return"),
}


@dataclass(frozen=True)
class EntryState:
    path: str
    kind: str
    sha256: str | None = None
    size: int | None = None
    link_target: str | None = None
    numeric_literals: tuple[str, ...] = ()
    sopwith_stop_words: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChangeSignal:
    action: str
    path: str
    kind: str
    before_kind: str | None = None
    after_kind: str | None = None
    before_sha256: str | None = None
    after_sha256: str | None = None
    before_size: int | None = None
    after_size: int | None = None
    before_numeric_literals: tuple[str, ...] = ()
    after_numeric_literals: tuple[str, ...] = ()
    numeric_literals: tuple[str, ...] = ()
    sopwith_stop_words: tuple[str, ...] = ()


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def ordered_unique(words: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for word in words:
        normalized = word.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def split_tokens(text: str) -> tuple[str, ...]:
    raw: list[str] = []
    for chunk in re.split(r"[^A-Za-z0-9]+", text):
        if not chunk:
            continue
        # Split a little bit of CamelCase without making path processing costly.
        expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", chunk)
        raw.extend(part for part in expanded.split() if part)
    return ordered_unique(raw)


def looks_like_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    if not data:
        return True
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def read_text_sample(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) > TEXT_SCAN_LIMIT:
        data = data[:TEXT_SCAN_LIMIT]
    if not looks_like_text(data):
        return ""
    return data.decode("utf-8", errors="replace")


def numeric_literals_from_text(text: str) -> tuple[str, ...]:
    return ordered_unique(re.findall(r"(?<![A-Za-z_])-?\d+(?:\.\d+)?(?![A-Za-z_])", text))


def identifier_tokens_from_text(text: str) -> tuple[str, ...]:
    return ordered_unique(re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", text))


def guess_stop_words(seeds: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()

    def visit(word: str) -> None:
        if word in seen:
            return
        seen.add(word)
        result.append(word)
        for related in GUESS_MAP.get(word, ()):
            visit(related)

    for seed in ordered_unique(token for seed in seeds for token in split_tokens(seed)):
        visit(seed)

    return tuple(result)


def sopwith_stop_words_for_entry(relative_path: str, path: Path | None = None, text: str = "") -> tuple[str, ...]:
    suffix = Path(relative_path).suffix.lower()
    seeds: list[str] = []
    seeds.extend(split_tokens(relative_path))
    seeds.extend(LANGUAGE_GRAMMAR_WORDS.get(suffix, ()))
    if text:
        seeds.extend(identifier_tokens_from_text(text)[:80])
        if numeric_literals_from_text(text):
            seeds.extend(("number", "literal"))
    return guess_stop_words(seeds)


def safe_relative_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def entry_state(root: Path, path: Path) -> EntryState:
    relative = safe_relative_path(path, root)
    if path.is_symlink():
        try:
            link_target = os.readlink(path)
        except OSError:
            link_target = ""
        stop_words = sopwith_stop_words_for_entry(relative)
        return EntryState(
            path=relative,
            kind="symlink",
            sha256=hashlib.sha256(f"symlink:{link_target}".encode("utf-8", errors="replace")).hexdigest(),
            size=None,
            link_target=link_target,
            sopwith_stop_words=stop_words,
        )

    if path.is_dir():
        stop_words = sopwith_stop_words_for_entry(relative)
        return EntryState(path=relative, kind="directory", sopwith_stop_words=stop_words)

    if path.is_file():
        text = read_text_sample(path)
        stop_words = sopwith_stop_words_for_entry(relative, path, text)
        return EntryState(
            path=relative,
            kind="file",
            sha256=sha256_file(path),
            size=path.stat().st_size,
            numeric_literals=numeric_literals_from_text(text),
            sopwith_stop_words=stop_words,
        )

    stop_words = sopwith_stop_words_for_entry(relative)
    return EntryState(path=relative, kind="other", sopwith_stop_words=stop_words)


def collect_entries(root: Path) -> dict[str, EntryState]:
    if not root.exists():
        return {}
    if not root.is_dir():
        raise RuntimeError(f"expected a directory: {root}")

    entries: dict[str, EntryState] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        state = entry_state(root, path)
        entries[state.path] = state
    return entries


def combine_words(*groups: Iterable[str]) -> tuple[str, ...]:
    return ordered_unique(word for group in groups for word in group)


def compare_dirs(before_root: Path | None, after_root: Path, include_unchanged: bool = False) -> tuple[ChangeSignal, ...]:
    before = collect_entries(before_root) if before_root and before_root.exists() else {}
    after = collect_entries(after_root)
    signals: list[ChangeSignal] = []

    for relative in sorted(set(before) | set(after)):
        old = before.get(relative)
        new = after.get(relative)

        if old is None and new is not None:
            signals.append(
                ChangeSignal(
                    action="added",
                    path=relative,
                    kind=new.kind,
                    after_kind=new.kind,
                    after_sha256=new.sha256,
                    after_size=new.size,
                    after_numeric_literals=new.numeric_literals,
                    numeric_literals=new.numeric_literals,
                    sopwith_stop_words=new.sopwith_stop_words,
                )
            )
            continue

        if old is not None and new is None:
            signals.append(
                ChangeSignal(
                    action="deleted",
                    path=relative,
                    kind=old.kind,
                    before_kind=old.kind,
                    before_sha256=old.sha256,
                    before_size=old.size,
                    before_numeric_literals=old.numeric_literals,
                    numeric_literals=old.numeric_literals,
                    sopwith_stop_words=old.sopwith_stop_words,
                )
            )
            continue

        assert old is not None and new is not None
        same = (
            old.kind == new.kind
            and old.sha256 == new.sha256
            and old.link_target == new.link_target
            and old.size == new.size
        )
        if same:
            if include_unchanged:
                signals.append(
                    ChangeSignal(
                        action="unchanged",
                        path=relative,
                        kind=new.kind,
                        before_kind=old.kind,
                        after_kind=new.kind,
                        before_sha256=old.sha256,
                        after_sha256=new.sha256,
                        before_size=old.size,
                        after_size=new.size,
                        before_numeric_literals=old.numeric_literals,
                        after_numeric_literals=new.numeric_literals,
                        numeric_literals=new.numeric_literals,
                        sopwith_stop_words=new.sopwith_stop_words,
                    )
                )
            continue

        action = "modified" if old.kind == new.kind else "type_changed"
        signals.append(
            ChangeSignal(
                action=action,
                path=relative,
                kind=new.kind,
                before_kind=old.kind,
                after_kind=new.kind,
                before_sha256=old.sha256,
                after_sha256=new.sha256,
                before_size=old.size,
                after_size=new.size,
                before_numeric_literals=old.numeric_literals,
                after_numeric_literals=new.numeric_literals,
                numeric_literals=combine_words(new.numeric_literals, old.numeric_literals),
                sopwith_stop_words=combine_words(new.sopwith_stop_words, old.sopwith_stop_words),
            )
        )

    return tuple(signals)


def checkpoint_name_for_source(source: Path, override: str | None = None) -> str:
    if override:
        if any(sep in override for sep in ("/", "\\")) or override in {"", ".", ".."}:
            raise RuntimeError(f"unsafe checkpoint name: {override!r}")
        return override
    return f"antigit_{source.name}_checkpoint"


def default_checkpoint_root(source: Path) -> Path:
    return source.parent / "checkpoint"


def resolve_checkpoint(source: Path, checkpoint_root: Path | None, name: str | None = None) -> tuple[Path, Path]:
    source = source.resolve()
    root = (checkpoint_root.resolve() if checkpoint_root else default_checkpoint_root(source).resolve())
    checkpoint = (root / checkpoint_name_for_source(source, name)).resolve()

    try:
        root.relative_to(source)
    except ValueError:
        pass
    else:
        raise RuntimeError("checkpoint root must not be inside the source project; anti-git must not write into the current directory")

    try:
        checkpoint.relative_to(source)
    except ValueError:
        pass
    else:
        raise RuntimeError("checkpoint destination must not be inside the source project; anti-git must not write into the current directory")

    return root, checkpoint


def copy_clone_atomic(source: Path, checkpoint_root: Path, checkpoint: Path) -> None:
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    temp_name = f".{checkpoint.name}.tmp.{os.getpid()}"
    backup_name = f".{checkpoint.name}.previous.{os.getpid()}"
    temp_path = checkpoint_root / temp_name
    backup_path = checkpoint_root / backup_name

    if temp_path.exists():
        shutil.rmtree(temp_path)
    if backup_path.exists():
        shutil.rmtree(backup_path)

    shutil.copytree(source, temp_path, symlinks=True, copy_function=shutil.copy2)

    try:
        if checkpoint.exists():
            checkpoint.rename(backup_path)
        temp_path.rename(checkpoint)
    except Exception:
        if temp_path.exists() and not checkpoint.exists():
            temp_path.rename(checkpoint)
        if backup_path.exists() and not checkpoint.exists():
            backup_path.rename(checkpoint)
        raise
    finally:
        if backup_path.exists():
            shutil.rmtree(backup_path)


def signal_to_payload(signal: ChangeSignal) -> dict:
    payload = {
        "event": "antigit.signal",
        "action": signal.action,
        "path": signal.path,
        "kind": signal.kind,
        "before_kind": signal.before_kind,
        "after_kind": signal.after_kind,
        "before_sha256": signal.before_sha256,
        "after_sha256": signal.after_sha256,
        "before_size": signal.before_size,
        "after_size": signal.after_size,
        "before_numeric_literals": list(signal.before_numeric_literals),
        "after_numeric_literals": list(signal.after_numeric_literals),
        "numeric_literals": list(signal.numeric_literals),
        "sopwith_stop_words": list(signal.sopwith_stop_words),
    }
    return {key: value for key, value in payload.items() if value not in (None, [], ())}


def summarize_signals(signals: Iterable[ChangeSignal]) -> dict[str, int]:
    summary = {"added": 0, "modified": 0, "deleted": 0, "type_changed": 0, "unchanged": 0}
    for signal in signals:
        summary.setdefault(signal.action, 0)
        summary[signal.action] += 1
    return summary


def count_files(root: Path) -> int:
    return sum(1 for path in root.rglob("*") if path.is_file() or path.is_symlink())


def print_json_lines(signals: Iterable[ChangeSignal]) -> None:
    for signal in signals:
        print(json.dumps(signal_to_payload(signal), sort_keys=True))


def print_plain_signals(signals: Iterable[ChangeSignal]) -> None:
    for signal in signals:
        numbers = ",".join(signal.numeric_literals[:12])
        words = ",".join(signal.sopwith_stop_words[:16])
        print(f"{signal.action:<12} {signal.kind:<10} {signal.path} numbers=[{numbers}] sopwith=[{words}]")


def cmd_snapshot(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve()
    if not source.exists() or not source.is_dir():
        print(f"error: source must be an existing directory: {source}", file=sys.stderr)
        return 2

    try:
        checkpoint_root, checkpoint = resolve_checkpoint(
            source,
            Path(args.checkpoint_root) if args.checkpoint_root else None,
            args.name,
        )
        signals = compare_dirs(checkpoint if checkpoint.exists() else None, source, include_unchanged=False)
        summary = summarize_signals(signals)
        file_count = count_files(source)

        if args.emit_signal:
            if args.json:
                print_json_lines(signals)
            else:
                print_plain_signals(signals)

        if not args.dry_run:
            copy_clone_atomic(source, checkpoint_root, checkpoint)

        event = "antigit.snapshot.dry_run" if args.dry_run else "antigit.snapshot.created"
        payload = {
            "event": event,
            "source_root": str(source),
            "checkpoint_root": str(checkpoint_root),
            "checkpoint_path": str(checkpoint),
            "checkpoint_name": checkpoint.name,
            "file_count": file_count,
            "changed_files": sum(summary.get(key, 0) for key in ("added", "modified", "deleted", "type_changed")),
            "summary": summary,
            "dry_run": bool(args.dry_run),
            "writes_to_source": False,
            "uses_gitignore": False,
        }

        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"{event}: {checkpoint}")
            print(f"source: {source}")
            print(f"checkpoint_name: {checkpoint.name}")
            print(f"file_count: {file_count}")
            print(f"changed_files_since_previous_checkpoint: {payload['changed_files']}")
            print("writes_to_source: false")
            print("uses_gitignore: false")
        return 0
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def cmd_signal(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve()
    if not source.exists() or not source.is_dir():
        print(f"error: source must be an existing directory: {source}", file=sys.stderr)
        return 2

    try:
        _, checkpoint = resolve_checkpoint(
            source,
            Path(args.checkpoint_root) if args.checkpoint_root else None,
            args.name,
        )
        if not checkpoint.exists():
            print(f"error: checkpoint does not exist: {checkpoint}", file=sys.stderr)
            return 2

        signals = compare_dirs(checkpoint, source, include_unchanged=args.include_unchanged)
        if args.json:
            print_json_lines(signals)
            payload = {
                "event": "antigit.signal.summary",
                "source_root": str(source),
                "checkpoint_path": str(checkpoint),
                "summary": summarize_signals(signals),
                "changed_files": sum(1 for signal in signals if signal.action != "unchanged"),
                "writes_to_source": False,
                "uses_gitignore": False,
            }
            print(json.dumps(payload, sort_keys=True))
        else:
            print_plain_signals(signals)
            summary = summarize_signals(signals)
            changed = sum(1 for signal in signals if signal.action != "unchanged")
            print(f"summary: {summary}")
            print(f"changed_files: {changed}")
            print("writes_to_source: false")
            print("uses_gitignore: false")
        return 0
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def cmd_diff(args: argparse.Namespace) -> int:
    before = Path(args.before).resolve()
    after = Path(args.after).resolve()
    if not before.exists() or not before.is_dir():
        print(f"error: before must be an existing directory: {before}", file=sys.stderr)
        return 2
    if not after.exists() or not after.is_dir():
        print(f"error: after must be an existing directory: {after}", file=sys.stderr)
        return 2

    signals = compare_dirs(before, after, include_unchanged=args.include_unchanged)
    if args.json:
        print_json_lines(signals)
        payload = {
            "event": "antigit.diff.summary",
            "before": str(before),
            "after": str(after),
            "summary": summarize_signals(signals),
            "changed_files": sum(1 for signal in signals if signal.action != "unchanged"),
            "writes_to_source": False,
            "uses_gitignore": False,
        }
        print(json.dumps(payload, sort_keys=True))
    else:
        print_plain_signals(signals)
        print(f"summary: {summarize_signals(signals)}")
    return 0


def cmd_guess_stop_words(args: argparse.Namespace) -> int:
    words = guess_stop_words(args.words)
    payload = {"event": "antigit.sopwith_stop_words", "sopwith_stop_words": list(words)}
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(" ".join(words))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Anti-git snapshots raw machine state by cloning a project directory into "
            "../checkpoint/antigit_<repo>_checkpoint without obeying .gitignore."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser(
        "snapshot",
        help="Clone the source project to ../checkpoint/antigit_<repo>_checkpoint.",
    )
    snapshot.add_argument("source", nargs="?", default=".", help="Project directory to clone. Defaults to the current directory.")
    snapshot.add_argument("--checkpoint-root", default=None, help="Directory that will contain the named checkpoint. Defaults to ../checkpoint from the source.")
    snapshot.add_argument("--name", default=None, help="Override the checkpoint directory name. Defaults to antigit_<repo>_checkpoint.")
    snapshot.add_argument("--emit-signal", action="store_true", help="Before replacing the checkpoint, emit changes versus the previous checkpoint.")
    snapshot.add_argument("--dry-run", action="store_true", help="Emit the plan without writing the checkpoint clone.")
    snapshot.add_argument("--json", action="store_true", help="Emit JSON lines.")
    snapshot.set_defaults(func=cmd_snapshot)

    signal = subparsers.add_parser(
        "signal",
        help="Compare the existing named checkpoint to the current source without writing anything.",
    )
    signal.add_argument("source", nargs="?", default=".", help="Project directory to compare. Defaults to the current directory.")
    signal.add_argument("--checkpoint-root", default=None, help="Directory that contains the named checkpoint. Defaults to ../checkpoint from the source.")
    signal.add_argument("--name", default=None, help="Override the checkpoint directory name. Defaults to antigit_<repo>_checkpoint.")
    signal.add_argument("--include-unchanged", action="store_true", help="Also emit unchanged entries.")
    signal.add_argument("--json", action="store_true", help="Emit JSON lines.")
    signal.set_defaults(func=cmd_signal)

    diff = subparsers.add_parser("diff", help="Compare any two directory trees.")
    diff.add_argument("before", help="Earlier directory tree.")
    diff.add_argument("after", help="Later directory tree.")
    diff.add_argument("--include-unchanged", action="store_true", help="Also emit unchanged entries.")
    diff.add_argument("--json", action="store_true", help="Emit JSON lines.")
    diff.set_defaults(func=cmd_diff)

    guess = subparsers.add_parser("guess-stop-words", help="Expand seed terms into deterministic sopwith stop words.")
    guess.add_argument("words", nargs="+", help="Seed words.")
    guess.add_argument("--json", action="store_true", help="Emit JSON.")
    guess.set_defaults(func=cmd_guess_stop_words)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv or sys.argv[1:]))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
