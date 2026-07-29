#!/usr/bin/env python3
"""Whole-repository Graphify smoke test for Main Computer (v9).

The script copies the complete repository source tree into an isolated
temporary corpus. By default it uses Git's tracked-plus-unignored working-tree
view, so live runtime databases, PID files, logs, secrets, dependencies, and
other ignored machine state do not masquerade as repository source. It then
runs Graphify against that staged repository, performs an explicit community
clustering pass, validates both the graph and its communities, exports a
browsable HTML cluster visualization with deterministic repository-context
labels and click-through cluster details, persists graph.json, graph.html, and a
human-readable cluster summary outside the temporary workspace, and exercises
query/explain/path.

By default the extraction is code-only, so it stays local and does not require
an LLM/API key. Pass --include-semantic-content to let Graphify also process
supported docs, PDFs, images, and other semantic inputs.

The source repository is never passed to Graphify and is not modified. Only
the requested graph, HTML, and JSON report output paths may be created or
replaced in the source repository.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REQUIRED_REPOSITORY_FILES = (
    "main_computer/rag_retriever.py",
    "main_computer/rag_harness.py",
    "main_computer/thinking_models.py",
)

EXPECTED_LABELS = (
    "DeterministicRagRetriever",
    "RagRetrieverConfig",
)

DEFAULT_EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "build",
    "dist",
    "htmlcov",
    "coverage",
    "graphify-out",
}

DEFAULT_EXCLUDED_FILE_NAMES = {
    ".coverage",
    "graphify-smoke-report.json",
    "graphify-smoke-graph.html",
    "graphify-repo-graph.json",
    "graphify-repo-graph.html",
    "graphify-repo-clusters.md",
}


class SmokeFailure(RuntimeError):
    """Raised when the smoke test cannot prove its required assertions."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a non-mutating Graphify smoke test against the entire "
            "Main Computer repository."
        )
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Repository root containing main_computer/ (default: current directory).",
    )
    parser.add_argument(
        "--graphify-cmd",
        metavar="COMMAND",
        help=(
            "Optional Graphify command prefix as one quoted string, for example: "
            '--graphify-cmd graphify or --graphify-cmd "python -m graphify". '
            "When omitted, the script tries the graphify executable and then "
            "the current Python interpreter with '-m graphify'."
        ),
    )
    parser.add_argument(
        "--allow-uvx",
        action="store_true",
        help=(
            "Allow fallback to 'uv tool run --from graphifyy graphify'. "
            "This may download packages and is therefore opt-in."
        ),
    )
    parser.add_argument(
        "--include-semantic-content",
        action="store_true",
        help=(
            "Process supported docs/PDFs/images as well as code. This removes "
            "--code-only and may require an LLM backend or API credentials."
        ),
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=max(1, min(8, os.cpu_count() or 1)),
        help="Graphify AST worker count (default: up to 8 logical CPUs).",
    )
    parser.add_argument(
        "--viz-node-limit",
        type=int,
        default=5_000,
        help=(
            "HTML node threshold used by Graphify export (default: 5000). "
            "Graphs above this threshold are exported as an aggregated "
            "community-level view, which is the preferred whole-repository view."
        ),
    )
    parser.add_argument(
        "--cluster-resolution",
        type=float,
        default=1.0,
        help=(
            "Graphify community-detection resolution (default: 1.0). "
            "Higher values generally produce more, smaller clusters."
        ),
    )
    parser.add_argument(
        "--work-parent",
        type=Path,
        help="Optional parent directory for the isolated temporary workspace.",
    )
    parser.add_argument(
        "--keep-work-dir",
        action="store_true",
        help="Keep the staged repository and raw Graphify output after success.",
    )
    parser.add_argument(
        "--staging-retries",
        type=int,
        default=3,
        help=(
            "Attempts per file when a source disappears or changes during "
            "whole-repository staging (default: 3)."
        ),
    )
    parser.add_argument(
        "--selection-mode",
        choices=("auto", "git", "all-files"),
        default="auto",
        help=(
            "Repository file selection. 'auto' prefers the native Git index and "
            "otherwise applies .gitignore through a temporary Git index; 'git' "
            "requires --repo to be the Git worktree root; 'all-files' performs "
            "the legacy filtered filesystem walk and may include mutable runtime "
            "state (default: auto)."
        ),
    )
    parser.add_argument(
        "--fail-on-source-change",
        action="store_true",
        help=(
            "Fail when a staged source file changes in the live worktree while "
            "the smoke test runs. By default such concurrent changes are reported "
            "as warnings because Graphify only receives the isolated staged copy."
        ),
    )
    parser.add_argument(
        "--graph-out",
        type=Path,
        help=(
            "Destination for the persistent graph JSON. Relative paths are "
            "resolved from --repo. Default: <repo>/graphify-repo-graph.json."
        ),
    )
    parser.add_argument(
        "--html-out",
        type=Path,
        help=(
            "Destination for the persistent graph HTML. Relative paths are "
            "resolved from --repo. Default: <repo>/graphify-repo-graph.html."
        ),
    )
    parser.add_argument(
        "--clusters-out",
        type=Path,
        help=(
            "Destination for the human-readable cluster summary. Relative paths "
            "are resolved from --repo. Default: <repo>/graphify-repo-clusters.md."
        ),
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="Optional destination for a machine-readable smoke report.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=900.0,
        help="Timeout in seconds for each Graphify command (default: 900).",
    )
    return parser.parse_args()


def normalize_path(value: Any) -> str:
    return str(value or "").replace("\\", "/")


def native_io_path(path: Path) -> str:
    """Return a Windows extended-length path for low-level file I/O."""
    rendered = os.path.abspath(os.fspath(path))
    if os.name != "nt" or rendered.startswith("\\\\?\\"):
        return rendered
    if rendered.startswith("\\\\"):
        return "\\\\?\\UNC\\" + rendered.lstrip("\\")
    return "\\\\?\\" + rendered


def path_exists_for_io(path: Path) -> bool:
    try:
        return os.path.exists(native_io_path(path))
    except OSError:
        return False


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(native_io_path(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_link_like_directory(path: Path) -> bool:
    """Detect symlinked directories and Windows junctions without following them."""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction()) if callable(is_junction) else False
    except OSError:
        return True


def remove_file_quietly(path: Path | None) -> None:
    if path is None:
        return
    try:
        os.unlink(native_io_path(path))
    except FileNotFoundError:
        pass
    except OSError:
        pass


def copy_repository_file(
    source: Path,
    destination: Path,
    *,
    relative: str,
    attempts: int,
) -> tuple[str | None, str | None]:
    """Copy one stable file atomically, returning (sha256, volatile_reason).

    Copying uses ordinary streams rather than Windows CopyFile2. This avoids
    opaque WinError 3 failures seen with shutil.copy2 while preserving exact
    bytes. A file that genuinely disappears or changes throughout all retry
    attempts is reported as volatile instead of being silently omitted.
    """
    attempts = max(1, attempts)
    last_change_reason = ""
    for attempt in range(1, attempts + 1):
        temporary: Path | None = None
        file_descriptor: int | None = None
        try:
            before = sha256_file(source)

            os.makedirs(native_io_path(destination.parent), exist_ok=True)
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.graphify-stage-",
                suffix=".tmp",
                dir=native_io_path(destination.parent),
            )
            temporary = Path(temporary_name)

            copied_digest = hashlib.sha256()
            with open(native_io_path(source), "rb") as input_handle:
                with os.fdopen(file_descriptor, "wb") as output_handle:
                    file_descriptor = None
                    while True:
                        chunk = input_handle.read(1024 * 1024)
                        if not chunk:
                            break
                        output_handle.write(chunk)
                        copied_digest.update(chunk)
                    output_handle.flush()

            after = sha256_file(source)
            copied = copied_digest.hexdigest()
            if before != after or copied != before:
                last_change_reason = (
                    f"changed while being copied on attempt {attempt}/{attempts}"
                )
                remove_file_quietly(temporary)
                if attempt < attempts:
                    time.sleep(0.05 * attempt)
                    continue
                return None, last_change_reason

            os.replace(
                native_io_path(temporary),
                native_io_path(destination),
            )
            temporary = None

            persisted = sha256_file(destination)
            if persisted != before:
                raise SmokeFailure(
                    "Staged copy hash mismatch after atomic replacement for "
                    f"{relative}: source={before}, staged={persisted}"
                )
            return before, None

        except FileNotFoundError as exc:
            if file_descriptor is not None:
                try:
                    os.close(file_descriptor)
                except OSError:
                    pass
            remove_file_quietly(temporary)

            if not path_exists_for_io(source):
                if attempt < attempts:
                    time.sleep(0.05 * attempt)
                    continue
                return None, (
                    f"disappeared during staging after {attempts} attempt(s)"
                )

            if attempt < attempts:
                time.sleep(0.05 * attempt)
                continue
            raise SmokeFailure(
                "Could not stage an existing repository file after "
                f"{attempts} attempt(s): {relative}\n"
                f"Source: {source}\nDestination: {destination}\n"
                f"OS error: {exc}"
            ) from exc

        except OSError as exc:
            if file_descriptor is not None:
                try:
                    os.close(file_descriptor)
                except OSError:
                    pass
            remove_file_quietly(temporary)

            if not path_exists_for_io(source):
                if attempt < attempts:
                    time.sleep(0.05 * attempt)
                    continue
                return None, (
                    f"became unavailable during staging after {attempts} attempt(s): "
                    f"{exc}"
                )

            if attempt < attempts and getattr(exc, "winerror", None) == 3:
                time.sleep(0.05 * attempt)
                continue

            detail = (
                f"{exc.__class__.__name__}: {exc}; "
                f"winerror={getattr(exc, 'winerror', None)!r}, "
                f"errno={getattr(exc, 'errno', None)!r}"
            )
            raise SmokeFailure(
                f"Could not stage repository file: {relative}\n"
                f"Source: {source}\nDestination: {destination}\n"
                f"OS error: {detail}"
            ) from exc

    return None, last_change_reason or "could not obtain a stable copy"


def resolve_output_path(value: Path | None, *, repo: Path, default_name: str) -> Path:
    path = value if value is not None else Path(default_name)
    path = path.expanduser()
    if not path.is_absolute():
        path = repo / path
    return path.resolve()


def is_within(path: Path, parent: Path) -> bool:
    """Lexically compare absolute paths after link-like directories are filtered."""
    candidate = os.path.normcase(os.path.abspath(os.fspath(path)))
    container = os.path.normcase(os.path.abspath(os.fspath(parent)))
    try:
        return os.path.commonpath([candidate, container]) == container
    except ValueError:
        return False


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: float,
    check: bool = True,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            list(argv),
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env=dict(env) if env is not None else None,
        )
    except FileNotFoundError as exc:
        raise SmokeFailure(f"Command not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        rendered = " ".join(shlex.quote(part) for part in argv)
        raise SmokeFailure(
            f"Command timed out after {timeout:.1f}s: {rendered}"
        ) from exc

    proc.elapsed_seconds = time.monotonic() - started  # type: ignore[attr-defined]
    if check and proc.returncode != 0:
        rendered = " ".join(shlex.quote(part) for part in argv)
        raise SmokeFailure(
            f"Command failed with exit code {proc.returncode}: {rendered}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc


def candidate_commands(args: argparse.Namespace) -> list[list[str]]:
    if args.graphify_cmd:
        command = shlex.split(args.graphify_cmd)
        if not command:
            raise SmokeFailure("--graphify-cmd must not be empty.")
        return [command]

    candidates: list[list[str]] = []
    if shutil.which("graphify"):
        candidates.append(["graphify"])

    candidates.append([sys.executable, "-m", "graphify"])

    if args.allow_uvx and shutil.which("uv"):
        candidates.append(["uv", "tool", "run", "--from", "graphifyy", "graphify"])
    return candidates


def resolve_graphify_command(
    args: argparse.Namespace,
    *,
    cwd: Path,
) -> tuple[list[str], str]:
    failures: list[str] = []
    for command in candidate_commands(args):
        try:
            proc = run_command(
                [*command, "--version"],
                cwd=cwd,
                timeout=min(args.timeout, 30.0),
                check=False,
            )
        except SmokeFailure as exc:
            failures.append(f"{' '.join(command)} -> {exc}")
            continue

        output = (proc.stdout or proc.stderr).strip()
        if proc.returncode == 0:
            return command, output or "version command succeeded"
        failures.append(
            f"{' '.join(command)} -> exit {proc.returncode}: {output or 'no output'}"
        )

    detail = "\n".join(failures) or "No Graphify command candidates were available."
    raise SmokeFailure(
        "No usable Graphify command found.\n"
        f"{detail}\n"
        "Install graphifyy into the active interpreter, pass --graphify-cmd, "
        "or opt in to uv execution with --allow-uvx."
    )


def should_exclude_directory(
    path: Path,
    *,
    excluded_absolute_paths: set[Path],
) -> bool:
    if path.name in DEFAULT_EXCLUDED_DIRECTORY_NAMES:
        return True
    absolute = Path(os.path.abspath(os.fspath(path)))
    return any(
        absolute == item or is_within(absolute, item)
        for item in excluded_absolute_paths
    )


def _run_git(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: float = 60.0,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            list(argv),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env=dict(env) if env is not None else None,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise SmokeFailure(f"Could not execute Git command: {' '.join(argv)}: {exc}") from exc


def _decode_git_paths(payload: bytes) -> list[str]:
    paths: list[str] = []
    for raw in payload.split(b"\0"):
        if not raw:
            continue
        relative = raw.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise SmokeFailure(f"Unsafe path returned by Git file selection: {relative!r}")
        normalized = candidate.as_posix()
        if normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized:
            paths.append(normalized)
    return sorted(set(paths))


def _native_git_root(repo: Path) -> Path | None:
    if not shutil.which("git"):
        return None
    proc = _run_git(
        ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
        cwd=repo,
        timeout=30.0,
    )
    if proc.returncode != 0:
        return None
    rendered = proc.stdout.decode("utf-8", errors="replace").strip()
    if not rendered:
        return None
    return Path(rendered).expanduser().resolve()


def _native_git_file_list(repo: Path) -> list[str]:
    proc = _run_git(
        [
            "git",
            "-C",
            str(repo),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            ".",
        ],
        cwd=repo,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise SmokeFailure(f"Git file selection failed: {detail or 'unknown error'}")
    return _decode_git_paths(proc.stdout)


def _gitignore_snapshot_file_list(repo: Path, work_dir: Path) -> list[str]:
    """Apply repository .gitignore rules without modifying a non-Git snapshot."""
    if not shutil.which("git"):
        raise SmokeFailure(
            "Git is unavailable, so .gitignore-safe whole-repository selection "
            "cannot be constructed. Install Git or pass --selection-mode all-files."
        )

    temporary_repository = work_dir / "ignore-index"
    init = _run_git(
        ["git", "init", "--quiet", str(temporary_repository)],
        cwd=work_dir,
        timeout=30.0,
    )
    if init.returncode != 0:
        detail = init.stderr.decode("utf-8", errors="replace").strip()
        raise SmokeFailure(
            f"Could not create temporary Git ignore index: {detail or 'unknown error'}"
        )

    env = os.environ.copy()
    env["GIT_DIR"] = str(temporary_repository / ".git")
    env["GIT_WORK_TREE"] = str(repo)
    proc = _run_git(
        ["git", "ls-files", "-z", "--others", "--exclude-standard"],
        cwd=repo,
        env=env,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise SmokeFailure(
            f"Temporary .gitignore file selection failed: {detail or 'unknown error'}"
        )
    return _decode_git_paths(proc.stdout)


def _all_files_list(
    repo: Path,
    *,
    work_dir: Path,
) -> tuple[list[str], list[str]]:
    """Legacy broad walk used only when explicitly requested or Git is absent."""
    excluded_dirs = {work_dir.resolve()}
    selected: list[str] = []
    skipped_links: list[str] = []

    for root, dirnames, filenames in os.walk(repo, topdown=True, followlinks=False):
        root_path = Path(root)
        kept_dirs: list[str] = []
        for dirname in sorted(dirnames):
            candidate = root_path / dirname
            relative = candidate.relative_to(repo).as_posix()
            if is_link_like_directory(candidate):
                skipped_links.append(relative + "/")
                continue
            if should_exclude_directory(
                candidate,
                excluded_absolute_paths=excluded_dirs,
            ):
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in sorted(filenames):
            selected.append((root_path / filename).relative_to(repo).as_posix())

    return sorted(set(selected)), skipped_links


def select_repository_files(
    repo: Path,
    *,
    work_dir: Path,
    selection_mode: str,
) -> tuple[list[str], dict[str, Any], list[str]]:
    """Select the whole repository source tree with explicit provenance."""
    native_root = _native_git_root(repo)

    if selection_mode == "git":
        if native_root is None:
            raise SmokeFailure(
                "--selection-mode git requires --repo to be a Git worktree root."
            )
        if os.path.normcase(str(native_root)) != os.path.normcase(str(repo)):
            raise SmokeFailure(
                "--repo is inside a Git worktree but is not its root. "
                f"Expected: {native_root}; received: {repo}"
            )
        selected = _native_git_file_list(repo)
        return (
            selected,
            {
                "mode": "native_git_tracked_plus_unignored",
                "git_root": str(native_root),
                "respects_gitignore": True,
                "includes_tracked_ignored_files": True,
            },
            [],
        )

    if selection_mode == "auto" and native_root is not None:
        if os.path.normcase(str(native_root)) != os.path.normcase(str(repo)):
            raise SmokeFailure(
                "--repo must be the Git worktree root for whole-repository staging. "
                f"Expected: {native_root}; received: {repo}"
            )
        selected = _native_git_file_list(repo)
        return (
            selected,
            {
                "mode": "native_git_tracked_plus_unignored",
                "git_root": str(native_root),
                "respects_gitignore": True,
                "includes_tracked_ignored_files": True,
            },
            [],
        )

    if selection_mode == "auto" and shutil.which("git"):
        selected = _gitignore_snapshot_file_list(repo, work_dir)
        return (
            selected,
            {
                "mode": "temporary_gitignore_snapshot",
                "git_root": None,
                "respects_gitignore": True,
                "includes_tracked_ignored_files": False,
            },
            [],
        )

    if selection_mode == "auto":
        raise SmokeFailure(
            "Git is unavailable, so automatic .gitignore-safe whole-repository "
            "selection cannot be constructed. Install Git or explicitly pass "
            "--selection-mode all-files."
        )

    selected, skipped_links = _all_files_list(repo, work_dir=work_dir)
    return (
        selected,
        {
            "mode": "filtered_all_files_walk",
            "git_root": str(native_root) if native_root else None,
            "respects_gitignore": False,
            "includes_tracked_ignored_files": True,
            "warning": (
                "Filesystem-walk selection does not apply .gitignore. It can include "
                "mutable runtime state; prefer auto/git when Git is available."
            ),
        },
        skipped_links,
    )


def stage_repository(
    repo: Path,
    corpus: Path,
    *,
    excluded_output_paths: Iterable[Path],
    work_dir: Path,
    staging_retries: int,
    selection_mode: str,
) -> tuple[
    list[str],
    dict[str, str],
    list[str],
    list[dict[str, str]],
    dict[str, Any],
]:
    """Copy a stable whole-repository source snapshot into the isolated corpus."""
    excluded_output_resolved = {path.resolve() for path in excluded_output_paths}
    selected, selection, skipped_links = select_repository_files(
        repo,
        work_dir=work_dir,
        selection_mode=selection_mode,
    )

    copied: list[str] = []
    source_hashes: dict[str, str] = {}
    volatile_files: list[dict[str, str]] = []
    skipped_non_files: list[str] = []

    for relative in selected:
        normalized = normalize_path(relative).strip("/")
        candidate_path = Path(normalized)
        if not normalized or candidate_path.is_absolute() or ".." in candidate_path.parts:
            raise SmokeFailure(f"Unsafe repository-relative staging path: {relative!r}")

        source = repo / candidate_path
        destination = corpus / candidate_path

        try:
            if source.is_symlink():
                skipped_links.append(normalized)
                continue
            if not source.is_file():
                skipped_non_files.append(normalized)
                continue
            resolved = source.resolve()
        except (FileNotFoundError, OSError) as exc:
            volatile_files.append(
                {
                    "path": normalized,
                    "reason": f"became unavailable during enumeration: {exc}",
                }
            )
            continue

        if source.name in DEFAULT_EXCLUDED_FILE_NAMES:
            continue
        if resolved in excluded_output_resolved:
            continue
        if not is_within(resolved, repo):
            skipped_links.append(normalized)
            continue

        digest, volatile_reason = copy_repository_file(
            source,
            destination,
            relative=normalized,
            attempts=staging_retries,
        )
        if volatile_reason is not None:
            volatile_files.append({"path": normalized, "reason": volatile_reason})
            continue
        if digest is None:
            raise SmokeFailure(
                f"Internal staging error: no hash or skip reason for {normalized}"
            )

        copied.append(normalized)
        source_hashes[normalized] = digest

    selection = {
        **selection,
        "candidate_file_count": len(selected),
        "copied_file_count": len(copied),
        "skipped_non_file_count": len(skipped_non_files),
        "skipped_non_file_samples": skipped_non_files[:20],
    }

    for required in REQUIRED_REPOSITORY_FILES:
        if required not in source_hashes:
            reason = next(
                (
                    item["reason"]
                    for item in volatile_files
                    if item["path"] == required
                ),
                "not found, ignored, or excluded",
            )
            raise SmokeFailure(
                "Required repository file was not staged from the whole source tree: "
                f"{required} ({reason})"
            )

    if len(copied) < 50:
        raise SmokeFailure(
            f"Whole-repository staging copied only {len(copied)} files; expected at least 50."
        )

    return copied, source_hashes, sorted(set(skipped_links)), volatile_files, selection


def verify_source_unchanged(repo: Path, source_hashes: Mapping[str, str]) -> list[str]:
    modified: list[str] = []
    for relative, expected_hash in source_hashes.items():
        source = repo / relative
        try:
            if not source.is_file() or source.is_symlink():
                modified.append(relative)
                continue
            if sha256_file(source) != expected_hash:
                modified.append(relative)
        except OSError:
            modified.append(relative)
    return modified


def read_graph(
    graph_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read either Graphify graph serialization used by the pipeline.

    Raw ``extract --no-cluster`` output stores the edge collection under
    ``edges``.  ``cluster-only`` rebuilds the graph through NetworkX and writes
    the canonical node-link form under ``links``.  Both are official Graphify
    formats and must be accepted by the smoke validator.
    """
    try:
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SmokeFailure(f"Graphify did not create expected graph: {graph_path}") from exc
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"Graph JSON is malformed: {graph_path}: {exc}") from exc

    if not isinstance(graph, dict):
        raise SmokeFailure("Graph JSON top level must be an object.")

    nodes = graph.get("nodes")
    links = graph.get("links")
    raw_edges = graph.get("edges")
    if isinstance(links, list):
        edges = links
    elif isinstance(raw_edges, list):
        edges = raw_edges
    else:
        raise SmokeFailure(
            "Graph JSON must contain list-valued 'nodes' and either 'links' "
            "(clustered Graphify output) or 'edges' (raw extraction output)."
        )
    if not isinstance(nodes, list):
        raise SmokeFailure("Graph JSON must contain a list-valued 'nodes' collection.")
    return graph, nodes, edges


def node_text(node: Mapping[str, Any]) -> str:
    parts = [
        node.get("id"),
        node.get("label"),
        node.get("name"),
        node.get("title"),
        node.get("qualified_name"),
    ]
    return " ".join(str(part) for part in parts if part is not None)


def edge_endpoint(edge: Mapping[str, Any], key: str) -> str:
    value = edge.get(key)
    if isinstance(value, Mapping):
        value = value.get("id")
    return str(value or "")


def find_node_id(nodes: Iterable[dict[str, Any]], label: str) -> str | None:
    exact: list[str] = []
    partial: list[str] = []
    for node in nodes:
        node_id = str(node.get("id", ""))
        text = node_text(node)
        candidates = {
            str(node.get("label", "")),
            str(node.get("name", "")),
            str(node.get("title", "")),
            node_id,
        }
        if label in candidates:
            exact.append(node_id)
        elif label.lower() in text.lower():
            partial.append(node_id)
    values = [item for item in exact + partial if item]
    return values[0] if values else None


def local_path_exists(
    edges: Iterable[dict[str, Any]],
    source: str,
    target: str,
) -> bool:
    adjacency: dict[str, set[str]] = collections.defaultdict(set)
    for edge in edges:
        left = edge_endpoint(edge, "source")
        right = edge_endpoint(edge, "target")
        if left and right:
            adjacency[left].add(right)
            adjacency[right].add(left)

    queue = collections.deque([source])
    seen = {source}
    while queue:
        current = queue.popleft()
        if current == target:
            return True
        for neighbor in adjacency.get(current, ()):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return False


def internal_endpoint_tokens(repository_files: Iterable[str]) -> set[str]:
    """Return conservative Graphify ID hints for repository-owned source files."""
    tokens: set[str] = set()
    for relative in repository_files:
        normalized = normalize_path(relative).strip("/")
        suffix = Path(normalized).suffix.lower()
        if suffix:
            normalized = normalized[: -len(suffix)]
        parts = [part for part in normalized.split("/") if part]
        if not parts:
            continue
        token = re.sub(r"[^a-z0-9_]+", "_", "_".join(parts).lower()).strip("_")
        if token:
            tokens.add(token)
    return tokens


def looks_like_internal_endpoint(endpoint: str, internal_tokens: set[str]) -> bool:
    """Identify dangling IDs that appear to name repository-owned source."""
    normalized = normalize_path(endpoint).strip("/").lower()
    if not normalized:
        return True

    flattened = re.sub(r"[^a-z0-9_]+", "_", normalized).strip("_")
    expected = {label.lower() for label in EXPECTED_LABELS}
    if normalized in expected or flattened in expected:
        return True

    for token in internal_tokens:
        if (
            flattened == token
            or flattened.startswith(token + "_")
            or flattened.endswith("_" + token)
        ):
            return True
    return False


def repo_relative_source_path(value: Any, *, corpus: Path) -> str:
    normalized = normalize_path(value).strip()
    if not normalized:
        return ""

    corpus_normalized = normalize_path(corpus.resolve()).rstrip("/")
    lower_value = normalized.lower()
    lower_corpus = corpus_normalized.lower()
    if lower_value == lower_corpus:
        return ""
    prefix = lower_corpus + "/"
    if lower_value.startswith(prefix):
        return normalized[len(corpus_normalized) + 1 :].lstrip("/")

    marker = "/corpus/"
    marker_index = lower_value.find(marker)
    if marker_index >= 0:
        return normalized[marker_index + len(marker) :].lstrip("/")

    return normalized.lstrip("./")



def normalize_community_id(value: Any) -> str:
    if isinstance(value, Mapping):
        value = (
            value.get("id")
            if value.get("id") is not None
            else value.get("community")
        )
    if value is None:
        return ""
    rendered = str(value).strip()
    return rendered


def community_sort_key(value: str) -> tuple[int, int | str]:
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, value)


def summarize_communities(
    graph: Mapping[str, Any],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    graph_path: Path,
    corpus: Path,
) -> dict[str, Any]:
    """Build a schema-tolerant summary of Graphify community assignments."""
    node_by_id = {
        str(node.get("id", "")): node
        for node in nodes
        if str(node.get("id", ""))
    }
    members_by_community: dict[str, list[str]] = collections.defaultdict(list)
    labels: dict[str, str] = {}

    raw_labels = graph.get("community_labels")
    if isinstance(raw_labels, Mapping):
        for key, value in raw_labels.items():
            cid = normalize_community_id(key)
            if cid and value not in (None, ""):
                labels[cid] = str(value)

    labels_path = graph_path.parent / ".graphify_labels.json"
    if labels_path.is_file():
        try:
            sidecar = json.loads(labels_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            sidecar = {}
        if isinstance(sidecar, Mapping):
            for key, value in sidecar.items():
                cid = normalize_community_id(key)
                if cid and value not in (None, ""):
                    labels[cid] = str(value)

    raw_communities = graph.get("communities")
    if isinstance(raw_communities, Mapping):
        iterable = raw_communities.items()
    elif isinstance(raw_communities, list):
        iterable = enumerate(raw_communities)
    else:
        iterable = ()

    for fallback_id, payload in iterable:
        cid = normalize_community_id(fallback_id)
        members: Any = payload
        if isinstance(payload, Mapping):
            explicit_id = (
                payload.get("id")
                if payload.get("id") is not None
                else payload.get("community")
            )
            if explicit_id is not None:
                cid = normalize_community_id(explicit_id)
            label = payload.get("label") or payload.get("name") or payload.get("title")
            if cid and label not in (None, ""):
                labels[cid] = str(label)
            members = (
                payload.get("members")
                if payload.get("members") is not None
                else payload.get("nodes")
            )
            if members is None:
                members = payload.get("node_ids")

        if not cid:
            continue
        if isinstance(members, Mapping):
            members = list(members.keys())
        if not isinstance(members, (list, tuple, set)):
            continue
        for member in members:
            node_id = normalize_community_id(member)
            if node_id in node_by_id:
                members_by_community[cid].append(node_id)

    # Graphify versions may serialize the assignment only on each node.
    for node_id, node in node_by_id.items():
        cid = ""
        for key in ("community", "community_id", "cluster", "cluster_id"):
            if node.get(key) is not None:
                cid = normalize_community_id(node.get(key))
                if cid:
                    break
        if cid:
            members_by_community[cid].append(node_id)

    for cid in list(members_by_community):
        members_by_community[cid] = sorted(set(members_by_community[cid]))
        if not members_by_community[cid]:
            del members_by_community[cid]

    if len(members_by_community) < 2:
        raise SmokeFailure(
            "Graph clustering produced fewer than two populated communities. "
            "The HTML exporter cannot show a useful cluster view."
        )

    degree: collections.Counter[str] = collections.Counter()
    cross_community_edges = 0
    node_to_community = {
        node_id: cid
        for cid, members in members_by_community.items()
        for node_id in members
    }
    for edge in edges:
        source = edge_endpoint(edge, "source")
        target = edge_endpoint(edge, "target")
        if source in node_by_id and target in node_by_id:
            degree[source] += 1
            degree[target] += 1
            left = node_to_community.get(source)
            right = node_to_community.get(target)
            if left and right and left != right:
                cross_community_edges += 1

    clusters: list[dict[str, Any]] = []
    for cid, members in members_by_community.items():
        ranked = sorted(
            members,
            key=lambda node_id: (
                -degree.get(node_id, 0),
                node_text(node_by_id[node_id]).lower(),
                node_id,
            ),
        )
        representatives = []
        for node_id in ranked[:8]:
            node = node_by_id[node_id]
            representatives.append(
                {
                    "id": node_id,
                    "label": (
                        str(node.get("label") or node.get("name") or node_id)
                    ),
                    "source_file": repo_relative_source_path(
                        node.get("source_file"),
                        corpus=corpus,
                    ),
                    "degree": degree.get(node_id, 0),
                }
            )
        clusters.append(
            {
                "id": cid,
                "label": labels.get(cid, f"Community {cid}"),
                "node_count": len(members),
                "representative_nodes": representatives,
            }
        )

    clusters.sort(
        key=lambda item: (
            -int(item["node_count"]),
            community_sort_key(str(item["id"])),
        )
    )
    assigned_nodes = len(node_to_community)
    return {
        "cluster_count": len(clusters),
        "assigned_node_count": assigned_nodes,
        "unassigned_node_count": max(0, len(nodes) - assigned_nodes),
        "largest_cluster_size": max(item["node_count"] for item in clusters),
        "smallest_cluster_size": min(item["node_count"] for item in clusters),
        "cross_cluster_edge_count": cross_community_edges,
        "clusters": clusters,
    }



def node_community_id(node: Mapping[str, Any]) -> str:
    for key in ("community", "community_id", "cluster", "cluster_id"):
        if node.get(key) is not None:
            cid = normalize_community_id(node.get(key))
            if cid:
                return cid
    return ""


def compact_repository_path(value: str, *, max_parts: int = 4) -> str:
    normalized = normalize_path(value).strip("/")
    if not normalized:
        return ""
    parts = [part for part in normalized.split("/") if part]
    if len(parts) <= max_parts:
        return "/".join(parts)
    return "…/" + "/".join(parts[-max_parts:])


def is_placeholder_community_label(label: str, cid: str) -> bool:
    rendered = str(label or "").strip()
    return not rendered or rendered.lower() == f"community {cid}".lower()


def clean_representative_label(value: Any) -> str:
    rendered = re.sub(r"\s+", " ", str(value or "")).strip()
    if not rendered:
        return ""
    if len(rendered) > 96:
        rendered = rendered[:93].rstrip() + "..."
    return rendered


def enrich_community_summary(
    graph_path: Path,
    community_summary: Mapping[str, Any],
    *,
    corpus: Path,
) -> dict[str, Any]:
    """Add deterministic, human-readable repository context to every cluster."""
    _graph, nodes, edges = read_graph(graph_path)
    node_by_id = {
        str(node.get("id", "")): node
        for node in nodes
        if str(node.get("id", ""))
    }
    node_to_community = {
        node_id: node_community_id(node)
        for node_id, node in node_by_id.items()
        if node_community_id(node)
    }
    members_by_community: dict[str, list[str]] = collections.defaultdict(list)
    for node_id, cid in node_to_community.items():
        members_by_community[cid].append(node_id)

    degree: collections.Counter[str] = collections.Counter()
    relations_by_community: dict[str, collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    neighbors_by_community: dict[str, collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    for edge in edges:
        source = edge_endpoint(edge, "source")
        target = edge_endpoint(edge, "target")
        if source not in node_by_id or target not in node_by_id:
            continue
        degree[source] += 1
        degree[target] += 1
        left = node_to_community.get(source, "")
        right = node_to_community.get(target, "")
        relation = str(edge.get("relation") or "related")
        if left:
            relations_by_community[left][relation] += 1
        if right and right != left:
            relations_by_community[right][relation] += 1
        if left and right and left != right:
            neighbors_by_community[left][right] += 1
            neighbors_by_community[right][left] += 1

    raw_clusters = community_summary.get("clusters", [])
    base_clusters = {
        str(cluster.get("id", "")): dict(cluster)
        for cluster in raw_clusters
        if str(cluster.get("id", ""))
    }
    all_cids = sorted(
        set(base_clusters) | set(members_by_community),
        key=community_sort_key,
    )

    enriched: list[dict[str, Any]] = []
    labels: dict[str, str] = {}
    for cid in all_cids:
        cluster = dict(base_clusters.get(cid, {"id": cid}))
        members = sorted(set(members_by_community.get(cid, [])))
        source_counts: collections.Counter[str] = collections.Counter()
        directory_counts: collections.Counter[str] = collections.Counter()
        area_counts: collections.Counter[str] = collections.Counter()

        for node_id in members:
            source = repo_relative_source_path(
                node_by_id[node_id].get("source_file"),
                corpus=corpus,
            )
            source = normalize_path(source).strip("/")
            if not source:
                continue
            source_counts[source] += 1
            parts = [part for part in source.split("/") if part]
            if parts:
                area_counts[parts[0]] += 1
            parent_parts = parts[:-1]
            for depth in range(1, min(4, len(parent_parts)) + 1):
                directory_counts["/".join(parent_parts[:depth])] += 1

        source_total = sum(source_counts.values())
        dominant_path = ""
        if len(source_counts) == 1:
            dominant_path = next(iter(source_counts))
        elif directory_counts:
            minimum_support = max(2, int(source_total * 0.30 + 0.999))
            supported = [
                (path, count)
                for path, count in directory_counts.items()
                if count >= minimum_support
            ]
            if supported:
                dominant_path = max(
                    supported,
                    key=lambda item: (
                        item[0].count("/"),
                        item[1],
                        -len(item[0]),
                        item[0],
                    ),
                )[0]
            else:
                dominant_path = directory_counts.most_common(1)[0][0]
        elif area_counts:
            dominant_path = area_counts.most_common(1)[0][0]

        ranked_ids = sorted(
            members,
            key=lambda node_id: (
                -degree.get(node_id, 0),
                node_text(node_by_id[node_id]).lower(),
                node_id,
            ),
        )
        representative_nodes: list[dict[str, Any]] = []
        seen_labels: set[str] = set()
        for node_id in ranked_ids:
            node = node_by_id[node_id]
            label = clean_representative_label(
                node.get("label") or node.get("name") or node_id
            )
            if not label or len(label) < 2:
                continue
            dedupe = label.casefold()
            if dedupe in seen_labels:
                continue
            seen_labels.add(dedupe)
            representative_nodes.append(
                {
                    "id": node_id,
                    "label": label,
                    "source_file": repo_relative_source_path(
                        node.get("source_file"),
                        corpus=corpus,
                    ),
                    "degree": degree.get(node_id, 0),
                }
            )
            if len(representative_nodes) >= 8:
                break

        existing_label = str(cluster.get("label") or "").strip()
        hub_label = (
            existing_label
            if not is_placeholder_community_label(existing_label, cid)
            else (
                representative_nodes[0]["label"]
                if representative_nodes
                else f"Community {cid}"
            )
        )
        path_label = compact_repository_path(dominant_path)
        if path_label and hub_label and hub_label.casefold() not in path_label.casefold():
            display_label = f"{path_label} · {hub_label} [C{cid}]"
        elif path_label:
            display_label = f"{path_label} [C{cid}]"
        else:
            display_label = f"{hub_label} [C{cid}]"
        if len(display_label) > 126:
            display_label = display_label[:118].rstrip() + f"… [C{cid}]"

        top_source_files = [
            {"path": path, "node_count": count}
            for path, count in source_counts.most_common(6)
        ]
        top_paths = [
            {"path": path, "node_count": count}
            for path, count in directory_counts.most_common(5)
        ]
        top_relations = [
            {"relation": relation, "edge_count": count}
            for relation, count in relations_by_community[cid].most_common(6)
        ]
        connected_clusters = [
            {"community": other, "edge_count": count}
            for other, count in neighbors_by_community[cid].most_common(8)
        ]

        cluster.update(
            {
                "id": cid,
                "label": display_label,
                "graphify_label": existing_label or f"Community {cid}",
                "node_count": len(members),
                "dominant_path": dominant_path,
                "dominant_area": (
                    area_counts.most_common(1)[0][0] if area_counts else ""
                ),
                "representative_nodes": representative_nodes,
                "top_source_files": top_source_files,
                "top_paths": top_paths,
                "top_relations": top_relations,
                "connected_clusters": connected_clusters,
            }
        )
        labels[cid] = display_label
        enriched.append(cluster)

    enriched.sort(
        key=lambda item: (
            -int(item.get("node_count", 0)),
            community_sort_key(str(item.get("id", ""))),
        )
    )
    result = dict(community_summary)
    result["clusters"] = enriched
    result["labels"] = labels
    result["labeling_mode"] = "deterministic_hub_plus_repository_context"
    if enriched:
        result["largest_cluster_size"] = max(
            int(item.get("node_count", 0)) for item in enriched
        )
        result["smallest_cluster_size"] = min(
            int(item.get("node_count", 0)) for item in enriched
        )
    return result


def write_cluster_labels_sidecar(
    community_summary: Mapping[str, Any],
    graph_path: Path,
) -> dict[str, Any]:
    """Write meaningful labels where Graphify's HTML exporter expects them."""
    labels = {
        str(cluster["id"]): str(cluster["label"])
        for cluster in community_summary.get("clusters", [])
    }
    destination = graph_path.parent / ".graphify_labels.json"
    payload = json.dumps(labels, indent=2, sort_keys=True) + "\n"
    destination.write_text(payload, encoding="utf-8", newline="\n")
    raw = destination.read_bytes()
    return {
        "path": str(destination),
        "label_count": len(labels),
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def write_cluster_summary(
    community_summary: Mapping[str, Any],
    destination: Path,
) -> dict[str, Any]:
    lines = [
        "# Graphify repository clusters",
        "",
        f"- Clusters: **{community_summary['cluster_count']}**",
        f"- Assigned nodes: **{community_summary['assigned_node_count']}**",
        f"- Unassigned nodes: **{community_summary['unassigned_node_count']}**",
        f"- Cross-cluster edges: **{community_summary['cross_cluster_edge_count']}**",
        "",
        "| Cluster | Nodes | Dominant path | Representative nodes |",
        "|---|---:|---|---|",
    ]
    for cluster in community_summary["clusters"]:
        label = str(cluster["label"]).replace("|", r"\|").replace("\n", " ")
        representatives = []
        for node in cluster["representative_nodes"]:
            node_label = str(node["label"]).replace("|", r"\|").replace("\n", " ")
            source_file = str(node.get("source_file") or "")
            if source_file:
                representatives.append(f"`{node_label}` ({source_file})")
            else:
                representatives.append(f"`{node_label}`")
        dominant_path = str(cluster.get("dominant_path") or "").replace(
            "|", r"\|"
        )
        lines.append(
            f"| {label} (`{cluster['id']}`) | {cluster['node_count']} | "
            f"`{dominant_path}` | "
            + "<br>".join(representatives)
            + " |"
        )

    payload = "\n".join(lines) + "\n"
    os.makedirs(native_io_path(destination.parent), exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=native_io_path(destination.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
        os.replace(native_io_path(temporary), native_io_path(destination))
    except Exception:
        remove_file_quietly(temporary)
        raise

    raw = destination.read_bytes()
    return {
        "path": str(destination),
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "cluster_count": community_summary["cluster_count"],
    }


def validate_graph(
    graph_path: Path,
    *,
    repository_files: Iterable[str],
    corpus: Path,
) -> tuple[dict[str, Any], str, str]:
    graph, nodes, edges = read_graph(graph_path)
    assertions: dict[str, Any] = {}

    if len(nodes) < 100:
        raise SmokeFailure(
            f"Whole-repository graph expected at least 100 nodes, found {len(nodes)}."
        )
    if len(edges) < 100:
        raise SmokeFailure(
            f"Whole-repository graph expected at least 100 edges, found {len(edges)}."
        )
    assertions["whole_repo_minimum_size"] = True

    node_ids = [str(node.get("id", "")) for node in nodes]
    if any(not node_id for node_id in node_ids):
        raise SmokeFailure("Every node must have a non-empty id.")
    if len(node_ids) != len(set(node_ids)):
        raise SmokeFailure("Node ids must be unique.")
    assertions["node_ids_unique"] = True

    node_id_set = set(node_ids)
    internal_tokens = internal_endpoint_tokens(repository_files)
    confidences: set[str] = set()
    relations: set[str] = set()
    source_files: set[str] = set()
    cross_file_edges = 0
    dangling_edges: list[dict[str, Any]] = []
    internal_looking_dangling_edges: list[dict[str, Any]] = []
    resolvable_edges: list[dict[str, Any]] = []

    node_source = {
        str(node["id"]): repo_relative_source_path(
            node.get("source_file"),
            corpus=corpus,
        )
        for node in nodes
        if node.get("id") is not None
    }

    for index, edge in enumerate(edges):
        missing = [
            key
            for key in ("source", "target", "relation", "confidence", "source_file")
            if edge.get(key) in (None, "")
        ]
        if missing:
            raise SmokeFailure(
                f"Edge {index} is missing required fields: {', '.join(missing)}"
            )

        source = edge_endpoint(edge, "source")
        target = edge_endpoint(edge, "target")
        missing_endpoints = [
            endpoint
            for endpoint in (source, target)
            if endpoint not in node_id_set
        ]
        if missing_endpoints:
            record = {
                "index": index,
                "source": source,
                "target": target,
                "relation": str(edge.get("relation", "")),
                "missing_endpoints": missing_endpoints,
                "source_file": repo_relative_source_path(
                    edge.get("source_file"),
                    corpus=corpus,
                ),
            }
            dangling_edges.append(record)
            if any(
                looks_like_internal_endpoint(endpoint, internal_tokens)
                for endpoint in missing_endpoints
            ):
                internal_looking_dangling_edges.append(record)
        else:
            resolvable_edges.append(edge)

        relation = str(edge["relation"]).lower()
        confidence = str(edge["confidence"]).upper()
        relations.add(relation)
        confidences.add(confidence)
        source_files.add(
            repo_relative_source_path(edge["source_file"], corpus=corpus)
        )

        left_file = node_source.get(source, "")
        right_file = node_source.get(target, "")
        if left_file and right_file and left_file != right_file:
            cross_file_edges += 1

    allowed_confidence = {"EXTRACTED", "INFERRED", "AMBIGUOUS"}
    unexpected = confidences - allowed_confidence
    if unexpected:
        raise SmokeFailure(
            "Unexpected edge confidence value(s): " + ", ".join(sorted(unexpected))
        )
    if "EXTRACTED" not in confidences:
        raise SmokeFailure("Expected at least one EXTRACTED edge.")
    assertions["edge_contract"] = True

    relation_blob = " ".join(relations)
    if "import" not in relation_blob:
        raise SmokeFailure("Expected at least one import relation.")
    if "contain" not in relation_blob:
        raise SmokeFailure("Expected at least one contains relation.")
    assertions["key_relations"] = True

    if cross_file_edges < 10:
        raise SmokeFailure(
            f"Whole-repository graph expected at least 10 cross-file edges, found {cross_file_edges}."
        )
    assertions["cross_file_edges"] = True

    all_source_paths = {
        repo_relative_source_path(node.get("source_file"), corpus=corpus)
        for node in nodes
        if node.get("source_file")
    } | source_files
    all_source_paths.discard("")

    for required in REQUIRED_REPOSITORY_FILES:
        if not any(path.endswith(required) for path in all_source_paths):
            raise SmokeFailure(f"Graph does not reference required source: {required}")
    assertions["required_sources_indexed"] = True

    indexed_top_levels = sorted(
        {
            path.split("/", 1)[0]
            for path in all_source_paths
            if "/" in path
        }
    )
    if len(indexed_top_levels) < 3:
        raise SmokeFailure(
            "Whole-repository graph indexed fewer than three top-level areas: "
            + ", ".join(indexed_top_levels)
        )
    if len(all_source_paths) < 25:
        raise SmokeFailure(
            f"Whole-repository graph references only {len(all_source_paths)} source files."
        )
    assertions["broad_repository_coverage"] = True

    ids: dict[str, str] = {}
    for label in EXPECTED_LABELS:
        node_id = find_node_id(nodes, label)
        if not node_id:
            raise SmokeFailure(f"Expected graph label not found: {label}")
        ids[label] = node_id
    assertions["expected_labels"] = True

    source_id = ids["DeterministicRagRetriever"]
    target_id = ids["RagRetrieverConfig"]
    if not local_path_exists(resolvable_edges, source_id, target_id):
        raise SmokeFailure(
            "No local graph path connects DeterministicRagRetriever "
            "to RagRetrieverConfig."
        )
    assertions["rag_path"] = True

    community_summary = summarize_communities(
        graph,
        nodes,
        edges,
        graph_path=graph_path,
        corpus=corpus,
    )
    assertions["multiple_communities"] = True
    assertions["community_assignments"] = True

    summary = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "edge_collection_key": (
            "links" if isinstance(graph.get("links"), list) else "edges"
        ),
        "indexed_source_file_count": len(all_source_paths),
        "indexed_top_level_areas": indexed_top_levels,
        "relations": sorted(relations),
        "confidences": sorted(confidences),
        "cross_file_edges": cross_file_edges,
        "resolvable_edge_count": len(resolvable_edges),
        "dangling_edge_count": len(dangling_edges),
        "dangling_edge_ratio": (
            round(len(dangling_edges) / len(edges), 6) if edges else 0.0
        ),
        "dangling_edge_samples": dangling_edges[:20],
        "internal_looking_dangling_edge_count": len(
            internal_looking_dangling_edges
        ),
        "internal_looking_dangling_edge_samples": (
            internal_looking_dangling_edges[:20]
        ),
        "communities": community_summary,
        "assertions": assertions,
    }
    return summary, source_id, target_id



def _replace_javascript_json_constant(
    document: str,
    *,
    name: str,
    next_token: str,
    value: Any,
) -> str:
    pattern = re.compile(
        rf"(const {re.escape(name)} = )(.*?)(;\s*\n{re.escape(next_token)})",
        re.DOTALL,
    )
    match = pattern.search(document)
    if not match:
        raise SmokeFailure(
            f"Could not locate Graphify HTML JavaScript constant {name}."
        )
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )
    return document[: match.start(2)] + encoded + document[match.end(2) :]

def enhance_cluster_html(
    path: Path,
    *,
    community_summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Turn Graphify's generic aggregate dots into self-describing clusters."""
    try:
        document = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SmokeFailure(f"Graphify did not create expected HTML: {path}") from exc

    nodes_match = re.search(
        r"const RAW_NODES = (.*?);\s*\nconst RAW_EDGES =",
        document,
        re.DOTALL,
    )
    legend_match = re.search(
        r"const LEGEND = (.*?);\s*\n",
        document,
        re.DOTALL,
    )
    if not nodes_match or not legend_match:
        raise SmokeFailure(
            "Graphify HTML does not contain the expected RAW_NODES/LEGEND payloads."
        )
    try:
        raw_nodes = json.loads(nodes_match.group(1))
        legend = json.loads(legend_match.group(1))
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"Could not parse Graphify HTML graph payload: {exc}") from exc
    if not isinstance(raw_nodes, list) or not isinstance(legend, list):
        raise SmokeFailure("Graphify HTML graph payload has an unexpected shape.")

    clusters = {
        str(cluster.get("id", "")): cluster
        for cluster in community_summary.get("clusters", [])
        if str(cluster.get("id", ""))
    }
    enriched_count = 0
    for node in raw_nodes:
        cid = str(node.get("id", ""))
        cluster = clusters.get(cid)
        if cluster is None:
            continue
        representatives = [
            str(item.get("label") or "")
            for item in cluster.get("representative_nodes", [])
            if str(item.get("label") or "")
        ]
        representative_files = [
            str(item.get("path") or "")
            for item in cluster.get("top_source_files", [])
            if str(item.get("path") or "")
        ]
        top_relations = [
            f"{item.get('relation')}: {item.get('edge_count')}"
            for item in cluster.get("top_relations", [])
        ]
        connected = [
            f"C{item.get('community')}: {item.get('edge_count')} edges"
            for item in cluster.get("connected_clusters", [])
        ]
        node["label"] = str(cluster["label"])
        node["title"] = html.escape(
            "\\n".join(
                [
                    str(cluster["label"]),
                    f"Members: {cluster.get('node_count', 0)} graph nodes",
                    f"Dominant path: {cluster.get('dominant_path') or '-'}",
                    "Representative symbols: "
                    + (", ".join(representatives[:5]) or "-"),
                    "Representative files: "
                    + (", ".join(representative_files[:4]) or "-"),
                ]
            )
        ).replace("\\n", "<br>")
        node["source_file"] = str(cluster.get("dominant_path") or "")
        node["file_type"] = "code cluster"
        node["member_count"] = int(cluster.get("node_count", 0))
        node["representative_nodes"] = representatives
        node["representative_files"] = representative_files
        node["top_relations"] = top_relations
        node["connected_clusters"] = connected
        enriched_count += 1

    for item in legend:
        cid = str(item.get("cid", ""))
        if cid in clusters:
            item["label"] = str(clusters[cid]["label"])
            item["count"] = int(clusters[cid].get("node_count", item.get("count", 0)))

    if enriched_count < 2:
        raise SmokeFailure(
            "Fewer than two aggregate HTML nodes matched validated communities."
        )

    document = _replace_javascript_json_constant(
        document,
        name="RAW_NODES",
        next_token="const RAW_EDGES =",
        value=raw_nodes,
    )
    document = _replace_javascript_json_constant(
        document,
        name="LEGEND",
        next_token="// HTML-escape helper",
        value=legend,
    )

    mapping_old = (
        "  _source_file: n.source_file, _file_type: n.file_type, _degree: n.degree,\n"
    )
    mapping_new = (
        "  _source_file: n.source_file, _file_type: n.file_type, _degree: n.degree,\n"
        "  _member_count: n.member_count || 0,\n"
        "  _representative_nodes: n.representative_nodes || [],\n"
        "  _representative_files: n.representative_files || [],\n"
        "  _top_relations: n.top_relations || [],\n"
        "  _connected_clusters: n.connected_clusters || [],\n"
    )
    if mapping_old not in document:
        raise SmokeFailure("Graphify HTML node mapping marker was not found.")
    document = document.replace(mapping_old, mapping_new, 1)

    info_old = """    <div class="field">Type: ${esc(n._file_type || 'unknown')}</div>
    <div class="field">Community: ${esc(n._community_name)}</div>
    <div class="field">Source: ${esc(n._source_file || '-')}</div>
    <div class="field">Degree: ${n._degree}</div>
"""
    info_new = """    <div class="field">Kind: ${esc(n._file_type || 'code cluster')}</div>
    <div class="field">Members: ${n._member_count} graph nodes</div>
    <div class="field">Dominant path: ${esc(n._source_file || '-')}</div>
    <div class="field">Connected clusters: ${neighborIds.length}</div>
    ${n._representative_nodes.length ? `<div class="field" style="margin-top:8px"><b>Representative symbols</b><br>${n._representative_nodes.slice(0,8).map(esc).join('<br>')}</div>` : ''}
    ${n._representative_files.length ? `<div class="field" style="margin-top:8px"><b>Representative files</b><br>${n._representative_files.slice(0,6).map(esc).join('<br>')}</div>` : ''}
    ${n._top_relations.length ? `<div class="field" style="margin-top:8px"><b>Top relations</b><br>${n._top_relations.slice(0,6).map(esc).join('<br>')}</div>` : ''}
"""

    if info_old not in document:
        raise SmokeFailure("Graphify HTML info-panel marker was not found.")
    document = document.replace(info_old, info_new, 1)

    search_old = (
        "const matches = RAW_NODES.filter(n => "
        "n.label.toLowerCase().includes(q)).slice(0, 20);"
    )
    search_new = (
        "const matches = RAW_NODES.filter(n => "
        "[n.label, n.source_file, ...(n.representative_nodes || []), "
        "...(n.representative_files || [])].join(' ').toLowerCase().includes(q)"
        ").slice(0, 50);"
    )
    if search_old not in document:
        raise SmokeFailure("Graphify HTML search marker was not found.")
    document = document.replace(search_old, search_new, 1)

    document = document.replace(
        'placeholder="Search nodes..."',
        'placeholder="Search clusters, paths, symbols..."',
        1,
    )
    document = document.replace("<h3>Node Info</h3>", "<h3>Cluster Info</h3>", 1)
    help_marker = '<div id="search-results"></div>'
    help_block = (
        '<div id="search-results"></div>'
        '<div style="padding:8px 12px;border-bottom:1px solid #2a2a4e;'
        'font-size:11px;color:#9ca3af;line-height:1.4">'
        '<b>Each dot is a code cluster.</b> Its size reflects member count. '
        'Click a dot to see representative symbols, files, relations, and '
        'connected clusters.</div>'
    )
    if help_marker not in document:
        raise SmokeFailure("Graphify HTML search-results marker was not found.")
    document = document.replace(help_marker, help_block, 1)

    path.write_text(document, encoding="utf-8", newline="\n")
    raw = path.read_bytes()
    return {
        "enriched_cluster_count": enriched_count,
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "cluster_details_embedded": True,
        "dot_semantics": "one dot equals one code community/cluster",
    }


def validate_html(
    path: Path,
    *,
    community_summary: Mapping[str, Any],
    exporter_output: str,
) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise SmokeFailure(f"Graphify did not create expected HTML: {path}") from exc

    if len(raw) < 1024:
        raise SmokeFailure(
            f"Graph HTML is unexpectedly small ({len(raw)} bytes): {path}"
        )

    text = raw.decode("utf-8", errors="replace")
    lowered = text.lower()
    if "<html" not in lowered or "</html>" not in lowered:
        raise SmokeFailure(f"Graph HTML is not a complete HTML document: {path}")
    if "communities" not in lowered:
        raise SmokeFailure("Graph HTML does not expose a Communities view.")
    if "each dot is a code cluster" not in lowered:
        raise SmokeFailure("Graph HTML does not explain what aggregate dots represent.")
    if "representative symbols" not in lowered:
        raise SmokeFailure("Graph HTML does not expose representative cluster symbols.")

    cluster_labels = [
        str(cluster["label"])
        for cluster in community_summary.get("clusters", [])
    ]
    matched_labels = [label for label in cluster_labels if label and label in text]
    if not matched_labels:
        raise SmokeFailure(
            "Graph HTML does not contain any of the validated community labels."
        )

    output_lower = exporter_output.lower()
    visualization_mode = (
        "aggregated_communities"
        if "aggregated" in output_lower
        else "full_node_graph"
    )
    return {
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "community_labels_present": matched_labels[:20],
        "visualization_mode": visualization_mode,
    }


def command_record(
    name: str,
    argv: Sequence[str],
    proc: subprocess.CompletedProcess[str],
) -> dict[str, Any]:
    return {
        "name": name,
        "argv": list(argv),
        "returncode": proc.returncode,
        "elapsed_seconds": round(
            float(getattr(proc, "elapsed_seconds", 0.0)),
            3,
        ),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def write_report(report: Mapping[str, Any], destination: Path | None) -> None:
    payload = json.dumps(report, indent=2, sort_keys=True)
    print(payload)
    if destination is not None:
        os.makedirs(native_io_path(destination.parent), exist_ok=True)
        with open(native_io_path(destination), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload + "\n")


def persist_exact_copy(
    source: Path,
    destination: Path,
    *,
    label: str,
) -> dict[str, Any]:
    if not path_exists_for_io(source):
        raise SmokeFailure(f"Graphify did not create expected {label}: {source}")

    digest, volatile_reason = copy_repository_file(
        source,
        destination,
        relative=f"generated {label}",
        attempts=3,
    )
    if volatile_reason is not None or digest is None:
        raise SmokeFailure(
            f"Could not persist stable {label}: "
            f"{volatile_reason or 'no digest returned'}"
        )

    destination_hash = sha256_file(destination)
    if digest != destination_hash:
        raise SmokeFailure(f"Persisted {label} does not match Graphify output.")
    return {
        "path": str(destination),
        "source_path": str(source),
        "size_bytes": os.stat(native_io_path(destination)).st_size,
        "sha256": destination_hash,
    }


def main() -> int:
    args = parse_args()
    repo = args.repo.expanduser().resolve()
    if not (repo / "main_computer").is_dir():
        print(
            f"ERROR: repository root does not contain main_computer/: {repo}",
            file=sys.stderr,
        )
        return 2
    if args.max_workers < 1:
        print("ERROR: --max-workers must be at least 1.", file=sys.stderr)
        return 2
    if args.viz_node_limit < 1:
        print("ERROR: --viz-node-limit must be at least 1.", file=sys.stderr)
        return 2
    if args.cluster_resolution <= 0:
        print("ERROR: --cluster-resolution must be greater than 0.", file=sys.stderr)
        return 2
    if args.staging_retries < 1:
        print("ERROR: --staging-retries must be at least 1.", file=sys.stderr)
        return 2

    parent = (
        args.work_parent.expanduser().resolve()
        if args.work_parent
        else None
    )
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)

    work_dir = Path(
        tempfile.mkdtemp(prefix="graphify-main-computer-repo-smoke-", dir=parent)
    ).resolve()
    corpus = work_dir / "corpus"
    output = work_dir / "output"
    corpus.mkdir(parents=True)
    output.mkdir(parents=True)

    graph_destination = resolve_output_path(
        args.graph_out,
        repo=repo,
        default_name="graphify-repo-graph.json",
    )
    html_destination = resolve_output_path(
        args.html_out,
        repo=repo,
        default_name="graphify-repo-graph.html",
    )
    clusters_destination = resolve_output_path(
        args.clusters_out,
        repo=repo,
        default_name="graphify-repo-clusters.md",
    )
    json_destination = (
        resolve_output_path(args.json_out, repo=repo, default_name="")
        if args.json_out is not None
        else None
    )

    output_destinations = [
        graph_destination,
        html_destination,
        clusters_destination,
        *([json_destination] if json_destination is not None else []),
    ]

    report: dict[str, Any] = {
        "status": "failed",
        "repo": str(repo),
        "scope": "entire_repository_source_tree",
        "content_mode": (
            "code_and_semantic_content"
            if args.include_semantic_content
            else "code_only"
        ),
        "work_dir": str(work_dir),
        "graphify_received_staged_copy_only": True,
        "source_repository_modified": None,
        "source_changed_during_run": None,
        "warnings": [],
        "output_artifacts": {
            "graph_json": str(graph_destination),
            "graph_html": str(html_destination),
            "cluster_summary": str(clusters_destination),
            "json_report": str(json_destination) if json_destination else None,
        },
        "commands": [],
        "staging_retries": args.staging_retries,
    }
    success = False
    source_hashes: dict[str, str] = {}

    try:
        (
            staged,
            source_hashes,
            skipped_links,
            volatile_files,
            selection,
        ) = stage_repository(
            repo,
            corpus,
            excluded_output_paths=output_destinations,
            work_dir=work_dir,
            staging_retries=args.staging_retries,
            selection_mode=args.selection_mode,
        )
        staged_top_level_counts = collections.Counter(
            relative.split("/", 1)[0] for relative in staged
        )
        report["repository_selection"] = selection
        report["staged_file_count"] = len(staged)
        report["staged_top_level_counts"] = dict(
            sorted(staged_top_level_counts.items())
        )
        report["staged_file_samples"] = staged[:25]
        report["skipped_links"] = skipped_links
        report["volatile_files"] = volatile_files
        report["volatile_file_count"] = len(volatile_files)
        report["staging_complete"] = not volatile_files
        if volatile_files:
            report["staging_warning"] = (
                f"{len(volatile_files)} file(s) disappeared or changed repeatedly "
                "while the repository snapshot was being staged. They are listed "
                "under volatile_files and were not silently included."
            )

        graphify_cmd, version = resolve_graphify_command(args, cwd=repo)
        report["graphify_command"] = graphify_cmd
        report["graphify_version"] = version

        extract_argv = [
            *graphify_cmd,
            "extract",
            str(corpus),
        ]
        if not args.include_semantic_content:
            extract_argv.append("--code-only")
        extract_argv.extend(
            [
                "--no-cluster",
                "--out",
                str(output),
                "--max-workers",
                str(args.max_workers),
                "--timing",
            ]
        )

        extract_proc = run_command(
            extract_argv,
            cwd=repo,
            timeout=args.timeout,
        )
        report["commands"].append(
            command_record("extract_entire_repository", extract_argv, extract_proc)
        )

        extract_warning_lines = [
            line.strip()
            for line in (extract_proc.stdout + "\n" + extract_proc.stderr).splitlines()
            if "warning:" in line.lower()
        ]
        report["graphify_extract_warnings"] = extract_warning_lines
        if extract_warning_lines:
            report["warnings"].extend(extract_warning_lines)

        graph_path = output / "graphify-out" / "graph.json"

        cluster_argv = [
            *graphify_cmd,
            "cluster-only",
            str(corpus),
            "--graph",
            str(graph_path),
            "--resolution",
            str(args.cluster_resolution),
            "--no-label",
            "--no-viz",
            "--timing",
        ]
        cluster_proc = run_command(
            cluster_argv,
            cwd=repo,
            timeout=args.timeout,
        )
        report["commands"].append(
            command_record(
                "cluster_entire_repository",
                cluster_argv,
                cluster_proc,
            )
        )

        graph_summary, source_id, target_id = validate_graph(
            graph_path,
            repository_files=staged,
            corpus=corpus,
        )
        graph_summary["communities"] = enrich_community_summary(
            graph_path,
            graph_summary["communities"],
            corpus=corpus,
        )
        report["graph"] = graph_summary
        report["cluster_labels_artifact"] = write_cluster_labels_sidecar(
            graph_summary["communities"],
            graph_path,
        )
        report["graph_artifact"] = persist_exact_copy(
            graph_path,
            graph_destination,
            label="clustered graph JSON",
        )
        report["cluster_summary_artifact"] = write_cluster_summary(
            graph_summary["communities"],
            clusters_destination,
        )
        if graph_summary["dangling_edge_count"]:
            report["warnings"].append(
                "Graphify emitted "
                f"{graph_summary['dangling_edge_count']} edge(s) with unresolved "
                "endpoint IDs. They are reported under graph.dangling_edge_* and "
                "do not fail this integration smoke test."
            )

        html_env = os.environ.copy()
        html_env["GRAPHIFY_VIZ_NODE_LIMIT"] = str(args.viz_node_limit)
        html_argv = [
            *graphify_cmd,
            "export",
            "html",
            "--graph",
            str(graph_path),
            "--node-limit",
            str(args.viz_node_limit),
        ]
        html_proc = run_command(
            html_argv,
            cwd=repo,
            timeout=args.timeout,
            env=html_env,
        )
        report["commands"].append(
            command_record("export_entire_repository_html", html_argv, html_proc)
        )

        generated_html = graph_path.parent / "graph.html"
        exporter_output = html_proc.stdout + "\n" + html_proc.stderr
        enrichment_summary = enhance_cluster_html(
            generated_html,
            community_summary=graph_summary["communities"],
        )
        generated_html_summary = validate_html(
            generated_html,
            community_summary=graph_summary["communities"],
            exporter_output=exporter_output,
        )
        persisted_html = persist_exact_copy(
            generated_html,
            html_destination,
            label="graph HTML",
        )
        copied_html_summary = validate_html(
            html_destination,
            community_summary=graph_summary["communities"],
            exporter_output=exporter_output,
        )
        if copied_html_summary["sha256"] != generated_html_summary["sha256"]:
            raise SmokeFailure("Persisted graph HTML does not match Graphify output.")
        report["html_artifact"] = {
            **persisted_html,
            **copied_html_summary,
            **enrichment_summary,
            "viz_node_limit": args.viz_node_limit,
            "cluster_count": graph_summary["communities"]["cluster_count"],
        }

        query_argv = [
            *graphify_cmd,
            "query",
            "DeterministicRagRetriever RagRetrieverConfig retrieve",
            "--graph",
            str(graph_path),
            "--budget",
            "1200",
        ]
        query_proc = run_command(
            query_argv,
            cwd=repo,
            timeout=args.timeout,
        )
        if not query_proc.stdout.strip():
            raise SmokeFailure("Graphify query returned empty stdout.")
        report["commands"].append(
            command_record("query", query_argv, query_proc)
        )

        explain_argv = [
            *graphify_cmd,
            "explain",
            source_id,
            "--graph",
            str(graph_path),
        ]
        explain_proc = run_command(
            explain_argv,
            cwd=repo,
            timeout=args.timeout,
        )
        if not (explain_proc.stdout.strip() or explain_proc.stderr.strip()):
            raise SmokeFailure("Graphify explain returned no output.")
        report["commands"].append(
            command_record("explain", explain_argv, explain_proc)
        )

        path_argv = [
            *graphify_cmd,
            "path",
            source_id,
            target_id,
            "--graph",
            str(graph_path),
        ]
        path_proc = run_command(
            path_argv,
            cwd=repo,
            timeout=args.timeout,
        )
        if not (path_proc.stdout.strip() or path_proc.stderr.strip()):
            raise SmokeFailure("Graphify path returned no output.")
        report["commands"].append(
            command_record("path", path_argv, path_proc)
        )

        modified = verify_source_unchanged(repo, source_hashes)
        report["source_repository_modified"] = bool(modified)
        report["source_changed_during_run"] = bool(modified)
        report["modified_source_files"] = modified
        if modified:
            message = (
                f"{len(modified)} staged source file(s) changed in the live "
                "worktree while the smoke test ran. Graphify only received the "
                "isolated staged copy; concurrent changes are listed under "
                "modified_source_files."
            )
            report["warnings"].append(message)
            if args.fail_on_source_change:
                raise SmokeFailure(message)

        report["status"] = (
            "passed_with_warnings" if report["warnings"] else "passed"
        )
        success = True
        write_report(report, json_destination)
        return 0

    except (SmokeFailure, OSError) as exc:
        if source_hashes:
            modified = verify_source_unchanged(repo, source_hashes)
            report["source_repository_modified"] = bool(modified)
            report["source_changed_during_run"] = bool(modified)
            report["modified_source_files"] = modified
        if isinstance(exc, OSError):
            detail = (
                f"{exc.__class__.__name__}: {exc}; "
                f"winerror={getattr(exc, 'winerror', None)!r}, "
                f"errno={getattr(exc, 'errno', None)!r}"
            )
            report["error"] = f"Operating-system error: {detail}"
        else:
            report["error"] = str(exc)
        write_report(report, json_destination)
        print(f"ERROR: {report['error']}", file=sys.stderr)
        return 1

    finally:
        if success and not args.keep_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
        elif not success:
            print(f"Failure artifacts kept at: {work_dir}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
