from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from typing import Any

from main_computer.gameplay_plugin_content import (
    assert_valid_gameplay_plugin_content_package,
    validate_gameplay_plugin_content_package,
)


GAMEPLAY_PLUGIN_MANIFEST_FILE = "manifest.json"
_DISCOVERABLE_CONTENT_DIRS = (
    "content/scenarios",
    "content/encounters",
)


@dataclass(frozen=True)
class GameplayPluginPackage:
    """Loaded gameplay plugin package.

    The package is a filesystem representation of the future AI-to-plugin
    boundary: a manifest plus declarative content files. Loading a package does
    not activate it in the game.
    """

    root: Path
    manifest_path: Path
    manifest: Mapping[str, Any]
    content_by_path: Mapping[str, Any]

    @property
    def plugin_id(self) -> str:
        return str(self.manifest.get("id") or "")

    @property
    def declared_content_paths(self) -> tuple[str, ...]:
        return tuple(_declared_content_paths(self.manifest))


def _is_safe_relative_posix_path(path: str) -> bool:
    if not path or "\\" in path or path.startswith("/"):
        return False
    parts = PurePosixPath(path).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        return False
    return True


def _package_path(root: Path, relative_path: str) -> Path:
    if not _is_safe_relative_posix_path(relative_path):
        raise ValueError(f"unsafe gameplay plugin path {relative_path!r}")
    resolved_root = root.resolve()
    resolved_child = (root / Path(*PurePosixPath(relative_path).parts)).resolve()
    if resolved_child != resolved_root and resolved_root not in resolved_child.parents:
        raise ValueError(f"gameplay plugin path escapes package root: {relative_path!r}")
    return resolved_child


def _load_json_object(path: Path, label: str, problems: list[str]) -> Mapping[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        problems.append(f"{label} is missing: {path}")
        return None
    except json.JSONDecodeError as exc:
        problems.append(f"{label} is not valid JSON: {exc}")
        return None
    if not isinstance(value, Mapping):
        problems.append(f"{label} must contain a JSON object")
        return None
    return value


def _records(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _declared_content_paths(manifest: Mapping[str, Any]) -> list[str]:
    content = manifest.get("content")
    if not isinstance(content, Mapping):
        return []
    paths: list[str] = []
    for collection_name in ("scenarios", "encounters"):
        for record in _records(content.get(collection_name)):
            path = str(record.get("path") or "").strip()
            if path:
                paths.append(path)
    return sorted(set(paths))


def _relative_posix(root: Path, child: Path) -> str:
    return child.resolve().relative_to(root.resolve()).as_posix()


def _discover_content_paths(root: Path) -> list[str]:
    paths: set[str] = set()
    for directory_name in _DISCOVERABLE_CONTENT_DIRS:
        directory = root / Path(*PurePosixPath(directory_name).parts)
        if not directory.exists():
            continue
        for child in directory.rglob("*.json"):
            if child.is_file():
                paths.add(_relative_posix(root, child))
    return sorted(paths)


def read_gameplay_plugin_package(package_root: str | Path) -> tuple[GameplayPluginPackage | None, list[str]]:
    """Load a gameplay plugin package from disk without activating it.

    The loader reads manifest.json and all declared/discoverable scenario and
    encounter JSON content under the package root. It returns problems instead
    of raising for malformed user/AI-authored packages so caller tooling can
    surface actionable validation reports.
    """

    root = Path(package_root)
    problems: list[str] = []
    if not root.exists():
        return None, [f"gameplay plugin package root does not exist: {root}"]
    if not root.is_dir():
        return None, [f"gameplay plugin package root must be a directory: {root}"]

    manifest_path = root / GAMEPLAY_PLUGIN_MANIFEST_FILE
    manifest = _load_json_object(manifest_path, "gameplay plugin manifest", problems)
    if manifest is None:
        return None, problems

    content_by_path: dict[str, Any] = {}
    content_paths = sorted(set(_declared_content_paths(manifest)) | set(_discover_content_paths(root)))
    for relative_path in content_paths:
        try:
            content_path = _package_path(root, relative_path)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        document = _load_json_object(content_path, f"gameplay plugin content {relative_path}", problems)
        if document is not None:
            content_by_path[relative_path] = document

    package = GameplayPluginPackage(
        root=root,
        manifest_path=manifest_path,
        manifest=manifest,
        content_by_path=content_by_path,
    )
    return package, problems


def validate_gameplay_plugin_package_dir(package_root: str | Path) -> list[str]:
    """Return load + semantic validation problems for a plugin package directory."""

    package, problems = read_gameplay_plugin_package(package_root)
    if package is None:
        return problems
    problems.extend(validate_gameplay_plugin_content_package(package.manifest, package.content_by_path))
    return problems


def assert_valid_gameplay_plugin_package_dir(package_root: str | Path) -> GameplayPluginPackage:
    """Load and semantically validate a plugin package directory.

    The returned package is still inert; validation proves it is shaped well
    enough for a future stager/importer, not that it should be activated.
    """

    package, load_problems = read_gameplay_plugin_package(package_root)
    problems = list(load_problems)
    if package is not None:
        problems.extend(validate_gameplay_plugin_content_package(package.manifest, package.content_by_path))
    if problems or package is None:
        raise ValueError("Invalid gameplay plugin package directory:\n- " + "\n- ".join(problems))
    assert_valid_gameplay_plugin_content_package(package.manifest, package.content_by_path)
    return package
