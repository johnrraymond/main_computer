from __future__ import annotations

import hashlib
import os
from datetime import datetime
import shutil
import tempfile
import zipfile
from pathlib import Path


BLOCKED_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
    ".main-computer",
    ".main-computer-install-archives",
    ".main-computer-tools",
    ".proto-dev",
    ".venv",
    "venv",
    "env",
    ".env",
    ".tox",
    ".nox",
    "aider_web_context",
    "chat_console_shared_variables",
    "debug_asset_revisions",
    "node_modules",
    ".next",
    "dist",
    "build",
    ".eggs",
    ".tmp",
    "harness_output",
    "harness_output_pretty_docs",
    "harness_output_game_editor",
    "migration",
    "rag_smoke_logpack_runs",
    "release_reports",
    "debug_assets",
    ".main_computer_browser_profile",
    "cache",
    "spreadsheets",
    "tmp_diag_server_debug",
    "tools - Copy",
}

BLOCKED_DIR_NAME_PREFIXES = (
    "diagnostics_output",
    "golden_path_diag_",
    "harness_output_",
    "ollama_prompt_space_",
)

BLOCKED_DIR_NAME_SUFFIXES = (
    ".egg-info",
)

BLOCKED_EXACT_PATHS = {
    ".prod.lock",
    "aider.log",
    ".main-computer-install-archives",
    ".main-computer-tools",
    "energy_credits",
    "release_reports",
    "generated_component_docs/work",
    "generated_component_docs/archive",
    "generated_component_docs/doc-build.json",
    "generated_component_docs/doc-health.json",
    "generated_component_docs/graph.json",
    "main_computer/.main_computer_browser_profile",
    "main_computer/debug_assets",
    "contracts/cache",
    "contracts/out",
}

BLOCKED_PREFIXES = (
    ".main-computer-install-archives/",
    ".main-computer-tools/",
    "runtime/",
    "energy_credits/",
    "release_reports/",
    "aider.log/",
    "generated_component_docs/work/",
    "generated_component_docs/archive/",
    "tools/documentation/plan-",
    "main_computer/.main_computer_browser_profile/",
    "main_computer/debug_assets/",
    "contracts/cache/",
    "contracts/out/",
    "revision_control/",
    "tools/patching/",
)

BLOCKED_FILE_NAMES = {
    ".DS_Store",
    "Thumbs.db",
    "aider.log",
    "small_aider.log",
    "solidity-files-cache.json",
}

BLOCKED_EXTENSIONS = {".pyc", ".pyo", ".tmp", ".bak", ".pid"}

ALLOWED_EXACT_PATHS = {
    "runtime/main-computer-runtime.json",
}

ALLOWED_DIR_PATHS = {
    "runtime",
}


def normalize_repo_path(path: str | os.PathLike[str]) -> str:
    return os.fspath(path).replace("\\", "/").strip("/")


def repo_path_allowed(repo_path: str, *, is_dir: bool) -> bool:
    repo_path = normalize_repo_path(repo_path)
    if not repo_path:
        return True
    if repo_path in ALLOWED_EXACT_PATHS:
        return True
    if is_dir and repo_path in ALLOWED_DIR_PATHS:
        return True

    name = repo_path.rsplit("/", 1)[-1]
    if repo_path in BLOCKED_EXACT_PATHS:
        return False

    for prefix in BLOCKED_PREFIXES:
        if repo_path == prefix.rstrip("/") or repo_path.startswith(prefix):
            return False

    parts = [part for part in repo_path.split("/") if part]
    if any(part in BLOCKED_DIR_NAMES for part in parts):
        return False
    if any(
        part.startswith(prefix)
        for part in parts
        for prefix in BLOCKED_DIR_NAME_PREFIXES
    ):
        return False
    if any(
        part.endswith(suffix)
        for part in parts
        for suffix in BLOCKED_DIR_NAME_SUFFIXES
    ):
        return False

    if not is_dir:
        if name in BLOCKED_FILE_NAMES:
            return False
        if Path(name).suffix in BLOCKED_EXTENSIONS:
            return False

    return True


def looks_like_nested_install_root(path: Path) -> bool:
    """Return True for stale install roots accidentally left under the source checkout."""

    return (
        (path / "main-computer-env.ps1").is_file()
        and (
            (path / "run-main-computer.ps1").is_file()
            or (path / "runtime" / "start_stop" / "main-computer-launcher.json").is_file()
        )
    )


def path_is_relative_to(candidate: Path, root: Path) -> bool:
    """Return True when candidate is inside root, without requiring Python 3.9 Path.is_relative_to."""

    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def windows_long_path(path: str | os.PathLike[str]) -> str:
    """Return a filesystem path that can cross the classic Windows MAX_PATH limit."""

    text = os.fspath(path)
    if os.name != "nt":
        return text

    if text.startswith("\\\\?\\"):
        return text

    absolute = os.path.abspath(text)
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute.lstrip("\\")
    return "\\\\?\\" + absolute


def remove_tree(path: Path) -> None:
    shutil.rmtree(windows_long_path(path))


def ensure_within_root(root: Path, target: Path) -> None:
    root_resolved = root.resolve()
    target_resolved = target.resolve()
    try:
        target_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise RuntimeError(f"Refusing to extract outside install root: {target}") from exc


def iter_clean_tree_files(source_root: Path):
    """Yield allowed repository files without descending into ignored directories."""

    source_root = source_root.resolve()

    for dirpath, dirnames, filenames in os.walk(source_root):
        current_dir = Path(dirpath)
        if current_dir == source_root:
            current_relative = ""
        else:
            current_relative = normalize_repo_path(current_dir.relative_to(source_root))

        if current_relative and not repo_path_allowed(current_relative, is_dir=True):
            dirnames[:] = []
            continue

        kept_dirnames = []
        for dirname in sorted(dirnames):
            child_path = current_dir / dirname
            child_relative = normalize_repo_path(
                f"{current_relative}/{dirname}" if current_relative else dirname
            )
            if not repo_path_allowed(child_relative, is_dir=True):
                continue
            if looks_like_nested_install_root(child_path):
                continue
            kept_dirnames.append(dirname)
        dirnames[:] = kept_dirnames

        for filename in sorted(filenames):
            relative = normalize_repo_path(
                f"{current_relative}/{filename}" if current_relative else filename
            )
            if not repo_path_allowed(relative, is_dir=False):
                continue

            path = current_dir / filename
            if path.is_file():
                yield path, relative


def write_clean_tree_archive(source_root: Path, archive_path: Path) -> int:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    file_count = 0
    total_bytes = 0
    top_level_sizes: dict[str, int] = {}

    print(f"Building clean install export from: {source_root}", flush=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, relative in iter_clean_tree_files(source_root):
            size = path.stat().st_size
            top_level = relative.split("/", 1)[0] if relative else "."
            top_level_sizes[top_level] = top_level_sizes.get(top_level, 0) + size
            total_bytes += size
            archive.write(windows_long_path(path), relative)
            file_count += 1

    print(f"Install export bytes:   {total_bytes}", flush=True)
    for name, size in sorted(top_level_sizes.items(), key=lambda item: item[1], reverse=True)[:8]:
        print(f"  export top: {name} = {size} bytes", flush=True)

    return file_count


def extract_clean_tree_archive(archive_path: Path, destination_root: Path) -> None:
    destination_root.mkdir(parents=True, exist_ok=True)
    destination_root_resolved = destination_root.resolve()

    with zipfile.ZipFile(archive_path) as archive:
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            relative = normalize_repo_path(info.filename)
            if not relative:
                continue
            if not repo_path_allowed(relative, is_dir=info.is_dir()):
                continue

            target = destination_root_resolved / relative
            ensure_within_root(destination_root_resolved, target)

            if info.is_dir():
                os.makedirs(windows_long_path(target), exist_ok=True)
                continue

            os.makedirs(windows_long_path(target.parent), exist_ok=True)
            with archive.open(info, "r") as source, open(windows_long_path(target), "wb") as destination:
                shutil.copyfileobj(source, destination)


def install_archive_root(destination_root: Path) -> Path:
    """Return the sibling archive directory for preserved install refreshes."""

    destination_root = destination_root.resolve()
    return destination_root.parent / ".main-computer-install-archives" / destination_root.name


def install_tree_file_summary(root: Path) -> tuple[int, int]:
    """Return the file count and total bytes for an existing install tree."""

    file_count = 0
    total_bytes = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        current_dir = Path(dirpath)
        for filename in filenames:
            path = current_dir / filename
            if path.is_file():
                file_count += 1
                total_bytes += path.stat().st_size

    return file_count, total_bytes


def write_install_root_archive(source_root: Path, archive_path: Path) -> None:
    """Write a complete backup archive of an existing install root."""

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    source_root = source_root.resolve()

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for dirpath, _dirnames, filenames in os.walk(source_root):
            current_dir = Path(dirpath)
            for filename in sorted(filenames):
                path = current_dir / filename
                if not path.is_file():
                    continue
                relative = normalize_repo_path(path.relative_to(source_root))
                archive.write(windows_long_path(path), relative)


def verify_install_root_archive(archive_path: Path, *, expected_file_count: int, expected_total_bytes: int) -> str:
    """Verify an install-root backup zip by entry count, sizes, and full stream reads."""

    with zipfile.ZipFile(archive_path, "r") as archive:
        entries = [info for info in archive.infolist() if not info.is_dir()]
        entry_bytes = sum(int(info.file_size) for info in entries)

        if len(entries) != expected_file_count:
            raise RuntimeError(
                f"archive contains {len(entries)} file entries, expected {expected_file_count}"
            )

        if entry_bytes != expected_total_bytes:
            raise RuntimeError(
                f"archive reports {entry_bytes} uncompressed bytes, expected {expected_total_bytes}"
            )

        read_bytes = 0
        for info in entries:
            with archive.open(info, "r") as stream:
                while chunk := stream.read(1024 * 1024):
                    read_bytes += len(chunk)

        if read_bytes != expected_total_bytes:
            raise RuntimeError(
                f"archive stream read returned {read_bytes} bytes, expected {expected_total_bytes}"
            )

    return f"verified {expected_file_count} files and {expected_total_bytes} bytes"


def protect_existing_install_root(destination_root: Path) -> dict[str, object] | None:
    """Archive and move an existing install root before a fresh install copy."""

    destination_root = destination_root.resolve()
    if not destination_root.exists():
        return None

    if not destination_root.is_dir():
        raise RuntimeError(f"Install root exists but is not a directory: {destination_root}")

    archive_root = install_archive_root(destination_root)
    archive_root.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    attempt = 0
    while True:
        suffix = "" if attempt == 0 else f"-{attempt}"
        archive_base_name = f"{destination_root.name}-{timestamp}{suffix}"
        zip_path = archive_root / f"{archive_base_name}.zip"
        moved_path = archive_root / f"{archive_base_name}.moved"
        if not zip_path.exists() and not moved_path.exists():
            break
        attempt += 1

    file_count, total_bytes = install_tree_file_summary(destination_root)

    print("Existing install root found. Preserving before fresh install:", flush=True)
    print(f"  Source:  {destination_root}", flush=True)
    print(f"  Archive: {zip_path}", flush=True)
    print(f"  Move to: {moved_path}", flush=True)

    try:
        write_install_root_archive(destination_root, zip_path)
    except Exception as exc:
        if zip_path.exists():
            zip_path.unlink(missing_ok=True)
        raise RuntimeError(f"Could not archive existing install root; leaving it in place: {exc}") from exc

    try:
        verification = verify_install_root_archive(
            zip_path,
            expected_file_count=file_count,
            expected_total_bytes=total_bytes,
        )
    except Exception as exc:
        raise RuntimeError(f"Archive verification failed; leaving existing install root in place: {exc}") from exc

    print(f"  Zip verified: {verification}", flush=True)

    try:
        shutil.move(windows_long_path(destination_root), windows_long_path(moved_path))
    except Exception as exc:
        raise RuntimeError(
            "Archive zip was verified, but the existing install root could not be moved out of the way; "
            f"leaving it in place: {exc}"
        ) from exc

    if destination_root.exists():
        raise RuntimeError(
            f"Old install root still exists after preserved refresh move; aborting before fresh install: {destination_root}"
        )

    if not moved_path.is_dir():
        raise RuntimeError(f"Old install root move reported success, but the moved directory is missing: {moved_path}")

    print(
        "Preserved existing install root: "
        f"archive={zip_path}; moved={moved_path}; files={file_count}; bytes={total_bytes}",
        flush=True,
    )
    return {
        "zip_path": zip_path,
        "moved_path": moved_path,
        "file_count": file_count,
        "total_bytes": total_bytes,
    }


def copy_clean_tree(source_root: Path, destination_root: Path, *, auto_force: bool = False) -> None:
    source_root = source_root.resolve()
    destination_root = destination_root.resolve()

    if source_root == destination_root:
        print(f"Install root is the repository root; no copy needed: {destination_root}", flush=True)
        return

    if path_is_relative_to(destination_root, source_root):
        raise RuntimeError(
            "Install root cannot be inside RepoRoot. Choose a sibling or external install location. "
            "This prevents clean export recursion through debug install roots, archives, virtual "
            "environments, and WSL/Docker state."
        )

    if destination_root.exists():
        if auto_force:
            print(
                f"--auto-force-install was set. Removing existing install root without preserving an archive: {destination_root}",
                flush=True,
            )
            remove_tree(destination_root)
        else:
            protect_existing_install_root(destination_root)

    destination_root.mkdir(parents=True, exist_ok=True)
    staging_root = Path.home() / ".main-computer-tools" / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="install-export-", dir=staging_root) as temp_dir:
        archive_path = Path(temp_dir) / "main-computer-install-export.zip"
        file_count = write_clean_tree_archive(source_root, archive_path)
        print(f"Install export archive: {archive_path}", flush=True)
        print(f"Install export files:   {file_count}", flush=True)
        print(f"Extracting install tree to: {destination_root}", flush=True)
        extract_clean_tree_archive(archive_path, destination_root)


def managed_installs_root() -> Path:
    """Return the root that contains replaceable managed install slots."""

    return Path.home() / ".main-computer-tools" / "installs"


def is_managed_install_root(path: Path) -> bool:
    """Return True for a child directory below the managed installs root.

    Named slots such as debug1/debug2 live here and are replaceable code trees.
    Runtime state, control roots, logs, and venvs live outside this code tree.
    """

    root = managed_installs_root().resolve()
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return False

    return bool(relative.parts)


def default_install_root(repo_root: Path, runtime_profile: str, mode_key: str) -> Path:
    """Return the managed code install root for the Python-owned bootstrap.

    The managed slot is intentionally mode-scoped, not checkout-name scoped.
    A source directory named ``main_computer_test`` is still just the source tree;
    it must not leak ``test`` or ``dev`` into installed stack identity.  An
    env-free source checkout and an env-free unleashed install therefore share
    the same Docker-facing identity: ``main-computer-unleashed``.
    """

    mode_name = safe_name(mode_key).replace("_", "-") or "unleashed"
    return managed_installs_root() / f"main-computer-{mode_name}"


def safe_name(value: str) -> str:
    cleaned = []
    for char in value.strip().lower():
        if char.isalnum() or char in ("-", "_"):
            cleaned.append(char)
        elif char.isspace():
            cleaned.append("-")
    text = "".join(cleaned).strip("-_")
    return text or "main-computer"


def short_hash(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8", "replace")).hexdigest()[:8]
