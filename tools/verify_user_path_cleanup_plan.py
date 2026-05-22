from __future__ import annotations

import argparse
import os
import difflib
import re
from pathlib import Path


IGNORE_DIR_PARTS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    ".venv",
    "venv",
}

GENERATED_OR_LOG_PREFIXES = (
    "aider.log/",
    "generated_component_docs/",
    "main_computer/.main_computer_browser_profile/",
    "main_computer/debug_assets/",
    "contracts/out/",
    "contracts/cache/",
    "tools/patching/reports/",
    "diagnostics_output/",
    "revision_control/",
    "tools/documentation/",
)

SOURCE_TARGETS = {
    "export-main-computer-test.ps1",
    "start-main-computer-docker-windows.ps1",
    "main_computer/config.py",
    "main_computer/git_tools.py",
    "main_computer/harness.py",
    "main_computer/rag_capability_filesystem_smoke.py",
    "main_computer/rag_smoke_logpack_ollama.py",
    "main_computer/rag_smoke_logpack_fast_ollama.py",
    "main_computer/rag_smoke_logpack_compact_ollama.py",
}

TEST_TARGETS = {
    "tests/test_dev_control_script.py",
    "tests/test_git_tools.py",
    "tests/test_recurrent_thinking.py",
    "tests/test_release_preflight.py",
}

TEXT_SUFFIXES = {
    ".py", ".ps1", ".md", ".txt", ".json", ".yml", ".yaml",
    ".toml", ".ini", ".cfg", ".html", ".css", ".js",
}


def repo_rel(path: Path, root: Path) -> str:
    return Path(os.path.relpath(os.fspath(path), os.fspath(root))).as_posix()


def is_probably_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in {
        "Dockerfile",
        ".dockerignore",
        ".gitignore",
    }


def is_ignored_generated_or_log(rel: str) -> bool:
    return any(rel.startswith(prefix) for prefix in GENERATED_OR_LOG_PREFIXES)


def should_skip(path: Path, root: Path) -> bool:
    rel = repo_rel(path, root)
    if any(part in IGNORE_DIR_PARTS for part in path.parts):
        return True
    if is_ignored_generated_or_log(rel):
        return False
    return not is_probably_text(path)


def compile_windows_user_path_pattern(username: str) -> re.Pattern[str]:
    escaped_user = re.escape(username)
    return re.compile(
        rf"(?i)(\b[A-Z]:(?:\\+|/)(?:Users|Documents and Settings)(?:\\+|/))"
        rf"{escaped_user}"
        rf"(?=(?:\\+|/|\b|[\"'`,)\]}}:;.\s]|$))"
    )


def sanitize_user_paths(text: str, *, username: str, replacement: str) -> str:
    pattern = compile_windows_user_path_pattern(username)
    return pattern.sub(lambda match: match.group(1) + replacement, text)


def sanitize_bare_username(text: str, *, username: str, replacement: str) -> str:
    pattern = re.compile(rf"(?i)(?<![A-Za-z0-9_]){re.escape(username)}(?![A-Za-z0-9_])")
    return pattern.sub(replacement, text)


def updated_text(
    text: str,
    *,
    username: str,
    replacement: str,
    include_bare_username: bool,
) -> str:
    text = sanitize_user_paths(text, username=username, replacement=replacement)
    if include_bare_username:
        text = sanitize_bare_username(text, username=username, replacement=replacement)
    return text


def diff_for_file(
    path: Path,
    root: Path,
    *,
    username: str,
    replacement: str,
    include_bare_username: bool,
) -> str:
    original = path.read_text(encoding="utf-8")
    updated = updated_text(
        original,
        username=username,
        replacement=replacement,
        include_bare_username=include_bare_username,
    )
    if updated == original:
        return ""

    rel = repo_rel(path, root)
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
    )


def username_in_text(text: str, username: str) -> bool:
    return username.lower() in text.lower()


def collect_hits(
    root: Path,
    *,
    username: str,
) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
    source_hits: list[Path] = []
    test_hits: list[Path] = []
    ignored_hits: list[Path] = []
    unexpected_hits: list[Path] = []

    for current_dir, dirnames, filenames in os.walk(root):
        current = Path(current_dir)

        kept_dirs: list[str] = []
        for dirname in dirnames:
            child = current / dirname
            child_rel = repo_rel(child, root).rstrip("/") + "/"

            if dirname in IGNORE_DIR_PARTS:
                continue

            if is_ignored_generated_or_log(child_rel):
                continue

            kept_dirs.append(dirname)

        dirnames[:] = kept_dirs

        for filename in filenames:
            path = current / filename
            rel = repo_rel(path, root)

            if not is_probably_text(path):
                continue

            try:
                data = path.read_bytes()
            except OSError:
                continue

            if b"\x00" in data:
                continue

            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue

            if not username_in_text(text, username):
                continue

            if rel in SOURCE_TARGETS:
                source_hits.append(path)
            elif rel in TEST_TARGETS:
                test_hits.append(path)
            else:
                unexpected_hits.append(path)

    return source_hits, test_hits, ignored_hits, unexpected_hits


def print_hits(title: str, paths: list[Path], root: Path, *, username: str) -> None:
    print(f"\n## {title}: {len(paths)}")
    for path in paths:
        rel = repo_rel(path, root)
        text = path.read_text(encoding="utf-8", errors="replace")
        line_numbers = [
            str(index)
            for index, line in enumerate(text.splitlines(), 1)
            if username.lower() in line.lower()
        ]
        print(f"{rel}: {', '.join(line_numbers)}")


def write_updates(
    paths: list[Path],
    *,
    username: str,
    replacement: str,
    include_bare_username: bool,
) -> None:
    for path in paths:
        original = path.read_text(encoding="utf-8")
        updated = updated_text(
            original,
            username=username,
            replacement=replacement,
            include_bare_username=include_bare_username,
        )
        if updated != original:
            path.write_text(updated, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plan or apply cleanup of hardcoded local Windows usernames in repo files."
    )
    parser.add_argument(
        "--username",
        required=True,
        help="Local username to search for, for example the name after C:\\Users\\.",
    )
    parser.add_argument(
        "--replacement",
        default="you",
        help="Replacement username for examples. Default: you",
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="Include known test fixture files in the proposed/write set.",
    )
    parser.add_argument(
        "--include-bare-username",
        action="store_true",
        help="Also replace standalone bare username occurrences. Default only replaces Windows user paths.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually apply the proposed replacements.",
    )
    args = parser.parse_args()

    root = Path.cwd().resolve()
    username = args.username.strip()
    replacement = args.replacement.strip()

    if not username:
        raise SystemExit("--username must not be empty")
    if not replacement:
        raise SystemExit("--replacement must not be empty")

    source_hits, test_hits, ignored_hits, unexpected_hits = collect_hits(root, username=username)

    print_hits("source files I would patch", source_hits, root, username=username)
    print_hits("test files I would patch only with --include-tests", test_hits, root, username=username)
    print_hits("ignored generated/log/profile hits", ignored_hits, root, username=username)
    print_hits("unexpected hits that need human review", unexpected_hits, root, username=username)

    planned = list(source_hits)
    if args.include_tests:
        planned += test_hits

    print("\n## Proposed diff")
    any_diff = False
    for path in planned:
        diff = diff_for_file(
            path,
            root,
            username=username,
            replacement=replacement,
            include_bare_username=args.include_bare_username,
        )
        if diff:
            any_diff = True
            print(diff, end="" if diff.endswith("\n") else "\n")

    if not any_diff:
        print("(no proposed source/test changes)")

    if unexpected_hits:
        print("\nSTOP: unexpected non-generated username hits remain. Review those before writing.")
        return 2

    if args.write:
        write_updates(
            planned,
            username=username,
            replacement=replacement,
            include_bare_username=args.include_bare_username,
        )
        print("\nWROTE proposed replacements.")
    else:
        print("\nDry run only.")
        print("Re-run with --write to apply source replacements.")
        print("Use --include-tests --write only after approving test fixture changes.")
        print("Use --include-bare-username only if standalone username fixtures should also change.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())