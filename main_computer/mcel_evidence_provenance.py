from __future__ import annotations

"""Deterministic source-state provenance for MCEL runtime and acceptance evidence."""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


PROVENANCE_SCHEMA = "mcel-repository-provenance-v2"
FINGERPRINT_ALGORITHM = "sha256-source-path-content-v2"
GIT_SCOPE = "git-tracked-and-unignored-source-v2"
SNAPSHOT_SCOPE = "snapshot-source-roots-v2"

# Snapshot exports do not retain .git metadata. These roots define the source
# authority in that mode. Mutable runtime state is deliberately not a source
# root; selected checked-in website sources remain included.
_SNAPSHOT_SOURCE_ROOTS = (
    PurePosixPath("main_computer"),
    PurePosixPath("tests"),
    PurePosixPath("pretty_docs"),
    PurePosixPath("contracts"),
    PurePosixPath("deploy"),
    PurePosixPath("docker"),
    PurePosixPath("tools"),
    PurePosixPath("scripts"),
    PurePosixPath("game_projects"),
    PurePosixPath("runtime/websites"),
)

_EXCLUDED_DIR_NAMES = frozenset(
    {
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
        ".eggs",
        ".main-computer",
        ".main_computer",
        ".main_computer_browser_profile",
        "aider_web_context",
        "debug_asset_revisions",
        "revision_control",
        "energy_credits",
        "rag_smoke_logpack_runs",
        ".smoke-runs",
        "compute_credits",
        "release_reports",
        "supercut_smoke_output",
        "tmp_diag_server_debug",
    }
)
_EXCLUDED_DIR_PREFIXES = (
    "diagnostics_output",
    "harness_output",
    "golden_path_diag_",
    "ollama_prompt_space",
)
_EXCLUDED_PREFIXES = (
    PurePosixPath("runtime/reports"),
    PurePosixPath("runtime/state"),
    PurePosixPath("runtime/start_stop"),
    PurePosixPath("runtime/logs"),
    PurePosixPath("runtime/cache"),
    PurePosixPath("runtime/caches"),
    PurePosixPath("runtime/tmp"),
    PurePosixPath("runtime/temp"),
    PurePosixPath("runtime/sessions"),
    PurePosixPath("runtime/browser"),
    PurePosixPath("runtime/uploads"),
    PurePosixPath("runtime/data"),
    PurePosixPath("tools/patching/reports"),
    PurePosixPath("deploy/local-platform/generated"),
    PurePosixPath("deploy/scheduler-lab/output"),
    PurePosixPath("contracts/cache"),
    PurePosixPath("contracts/out"),
    PurePosixPath("contracts/broadcast"),
    PurePosixPath("generated_component_docs"),
)
_EXCLUDED_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".tmp",
    ".temp",
    ".swp",
    ".swo",
    ".bak",
    ".backup",
    ".orig",
    ".rej",
    ".log",
    ".pid",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".zip",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".7z",
    ".rar",
)
_EXCLUDED_FILE_NAMES = frozenset(
    {
        ".ds_store",
        "thumbs.db",
        ".coverage",
        "ssh_password.local",
        "worker_multisession_keys.json",
        "main-computer-install.json",
        "main-computer-env.ps1",
        "run-main-computer.ps1",
        "manifest.json",
        "reference.patch",
    }
)


def _safe_string(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _relative_posix(path: Path, repo: Path) -> PurePosixPath:
    return PurePosixPath(path.relative_to(repo).as_posix())


def _is_under(relative: PurePosixPath, prefix: PurePosixPath) -> bool:
    return relative == prefix or prefix in relative.parents


def _has_excluded_directory(relative: PurePosixPath) -> bool:
    for part in relative.parts[:-1]:
        lowered = part.lower()
        if lowered in _EXCLUDED_DIR_NAMES:
            return True
        if any(lowered.startswith(prefix) for prefix in _EXCLUDED_DIR_PREFIXES):
            return True
    return False


def _is_generated_game_project_path(relative: PurePosixPath) -> bool:
    parts = relative.parts
    if not parts or parts[0] != "game_projects":
        return False
    return any(part in {"assets", "builds", "data"} for part in parts[2:-1])


def is_repository_fingerprint_file(relative: PurePosixPath) -> bool:
    """Return whether a repository-relative source file participates."""

    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        return False
    if _has_excluded_directory(relative):
        return False
    if any(_is_under(relative, prefix) for prefix in _EXCLUDED_PREFIXES):
        return False
    if _is_generated_game_project_path(relative):
        return relative.name == ".gitkeep"

    lowered_name = relative.name.lower()
    if lowered_name in _EXCLUDED_FILE_NAMES:
        return False
    if lowered_name.endswith(_EXCLUDED_SUFFIXES):
        return False
    if lowered_name.endswith(".js.map"):
        return False
    if (
        len(relative.parts) >= 3
        and relative.parts[0:2] == ("runtime", "websites")
        and relative.name.startswith("__")
        and "probe" in relative.name.lower()
    ):
        return False
    if (
        relative.name == "docker-compose.yml"
        and ".main-computer" in relative.parts
        and "local-platform" in relative.parts
    ):
        return False
    return True


def _safe_relative_from_git(raw: str) -> PurePosixPath | None:
    normalized = PurePosixPath(raw.replace("\\", "/"))
    if normalized.is_absolute() or not normalized.parts or ".." in normalized.parts:
        return None
    return normalized


def _git_selected_relative_paths(root: Path) -> list[PurePosixPath] | None:
    """Return tracked plus unignored untracked files, or None when unavailable."""

    git_marker = root / ".git"
    git = shutil.which("git")
    if not git_marker.exists() or not git:
        return None

    try:
        completed = subprocess.run(
            [
                git,
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    selected: set[PurePosixPath] = set()
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        text = os.fsdecode(raw)
        relative = _safe_relative_from_git(text)
        if relative is None or not is_repository_fingerprint_file(relative):
            continue
        path = root.joinpath(*relative.parts)
        if path.is_symlink() or not path.is_file():
            continue
        selected.add(relative)
    return sorted(selected, key=PurePosixPath.as_posix)


def _walk_source_root(root: Path, source_root: PurePosixPath) -> Iterable[PurePosixPath]:
    base = root.joinpath(*source_root.parts)
    if not base.is_dir():
        return

    for current, dirnames, filenames in os.walk(base):
        current_path = Path(current)
        current_relative = _relative_posix(current_path, root)

        kept_dirs: list[str] = []
        for dirname in dirnames:
            candidate = PurePosixPath(current_relative, dirname, ".sentinel")
            if _has_excluded_directory(candidate):
                continue
            if any(_is_under(PurePosixPath(current_relative, dirname), prefix) for prefix in _EXCLUDED_PREFIXES):
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in filenames:
            path = current_path / filename
            if path.is_symlink() or not path.is_file():
                continue
            relative = _relative_posix(path, root)
            if is_repository_fingerprint_file(relative):
                yield relative


def _snapshot_selected_relative_paths(root: Path) -> list[PurePosixPath]:
    selected: set[PurePosixPath] = set()

    # Repository-root launchers, configuration, and documentation are source.
    for path in root.iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        relative = _relative_posix(path, root)
        if is_repository_fingerprint_file(relative):
            selected.add(relative)

    for source_root in _SNAPSHOT_SOURCE_ROOTS:
        selected.update(_walk_source_root(root, source_root))

    return sorted(selected, key=PurePosixPath.as_posix)


def _select_repository_files(
    root: Path,
) -> tuple[str, str, Sequence[PurePosixPath], list[str]]:
    git_paths = _git_selected_relative_paths(root)
    if git_paths is not None:
        return (
            GIT_SCOPE,
            "git-tracked-and-unignored",
            git_paths,
            [],
        )

    snapshot_paths = _snapshot_selected_relative_paths(root)
    return (
        SNAPSHOT_SCOPE,
        "snapshot-source-roots",
        snapshot_paths,
        [path.as_posix() for path in _SNAPSHOT_SOURCE_ROOTS],
    )


def iter_repository_fingerprint_files(repo: Path) -> Iterable[tuple[PurePosixPath, Path]]:
    """Yield stable repository-relative source files included in provenance."""

    root = repo.resolve()
    _scope, _method, relative_paths, _source_roots = _select_repository_files(root)
    for relative in relative_paths:
        yield relative, root.joinpath(*relative.parts)


def build_repository_provenance(repo: Path) -> dict[str, Any]:
    """Build a deterministic fingerprint of source state, not runtime state."""

    root = repo.resolve()
    scope, selection_method, relative_paths, source_roots = _select_repository_files(root)

    aggregate = hashlib.sha256()
    aggregate.update(
        (
            PROVENANCE_SCHEMA
            + "\0"
            + FINGERPRINT_ALGORITHM
            + "\0"
            + scope
            + "\0"
            + selection_method
            + "\n"
        ).encode("utf-8")
    )
    file_count = 0
    total_bytes = 0

    for relative in relative_paths:
        path = root.joinpath(*relative.parts)
        data = path.read_bytes()
        content_hash = hashlib.sha256(data).hexdigest()
        aggregate.update(relative.as_posix().encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(content_hash.encode("ascii"))
        aggregate.update(b"\n")
        file_count += 1
        total_bytes += len(data)

    return {
        "schema": PROVENANCE_SCHEMA,
        "algorithm": FINGERPRINT_ALGORITHM,
        "fingerprint": aggregate.hexdigest(),
        "fileCount": file_count,
        "totalBytes": total_bytes,
        "scope": scope,
        "selectionMethod": selection_method,
        "sourceRoots": source_roots,
    }


def extract_repository_provenance(evidence: Any) -> dict[str, Any] | None:
    """Extract a provenance object from a supported evidence envelope."""

    if not isinstance(evidence, Mapping):
        return None
    candidates = (
        evidence.get("repositoryProvenance"),
        evidence.get("repoProvenance"),
        evidence.get("repository"),
        evidence.get("sourceProvenance"),
    )
    source = evidence.get("source")
    if isinstance(source, Mapping):
        candidates += (
            source.get("repositoryProvenance"),
            source.get("repoProvenance"),
        )

    for candidate in candidates:
        if isinstance(candidate, Mapping):
            raw_roots = candidate.get("sourceRoots")
            source_roots = (
                [_safe_string(item) for item in raw_roots if _safe_string(item)]
                if isinstance(raw_roots, list)
                else []
            )
            return {
                "schema": _safe_string(candidate.get("schema")),
                "algorithm": _safe_string(candidate.get("algorithm")),
                "fingerprint": _safe_string(
                    candidate.get("fingerprint")
                    or candidate.get("sha256")
                    or candidate.get("repositoryFingerprint")
                ),
                "fileCount": int(candidate.get("fileCount") or 0),
                "totalBytes": int(candidate.get("totalBytes") or 0),
                "scope": _safe_string(candidate.get("scope")),
                "selectionMethod": _safe_string(candidate.get("selectionMethod")),
                "sourceRoots": source_roots,
            }
    return None


def compare_repository_provenance(
    evidence_provenance: Mapping[str, Any] | None,
    current_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Classify evidence binding without treating absent provenance as a match."""

    current_fingerprint = _safe_string(current_provenance.get("fingerprint"))
    current_scope = _safe_string(current_provenance.get("scope"))
    current_method = _safe_string(current_provenance.get("selectionMethod"))
    if not evidence_provenance:
        return {
            "status": "unbound",
            "exact": False,
            "currentFingerprint": current_fingerprint,
            "evidenceFingerprint": "",
            "currentScope": current_scope,
            "evidenceScope": "",
            "currentSelectionMethod": current_method,
            "evidenceSelectionMethod": "",
            "reason": "Evidence does not declare repository provenance.",
        }

    evidence_schema = _safe_string(evidence_provenance.get("schema"))
    evidence_algorithm = _safe_string(evidence_provenance.get("algorithm"))
    evidence_fingerprint = _safe_string(evidence_provenance.get("fingerprint"))
    evidence_scope = _safe_string(evidence_provenance.get("scope"))
    evidence_method = _safe_string(evidence_provenance.get("selectionMethod"))

    if (
        evidence_schema != PROVENANCE_SCHEMA
        or evidence_algorithm != FINGERPRINT_ALGORITHM
        or not evidence_fingerprint
    ):
        return {
            "status": "unsupported",
            "exact": False,
            "currentFingerprint": current_fingerprint,
            "evidenceFingerprint": evidence_fingerprint,
            "currentScope": current_scope,
            "evidenceScope": evidence_scope,
            "currentSelectionMethod": current_method,
            "evidenceSelectionMethod": evidence_method,
            "reason": "Evidence provenance schema or fingerprint algorithm is unsupported.",
        }

    exact = evidence_fingerprint == current_fingerprint
    return {
        "status": "exact" if exact else "mismatch",
        "exact": exact,
        "currentFingerprint": current_fingerprint,
        "evidenceFingerprint": evidence_fingerprint,
        "currentScope": current_scope,
        "evidenceScope": evidence_scope,
        "currentSelectionMethod": current_method,
        "evidenceSelectionMethod": evidence_method,
        "reason": (
            "Evidence was produced from this exact repository source state."
            if exact
            else "Evidence was produced from a different repository source state."
        ),
    }
