from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from main_computer.gameplay_plugin_package import (
    GAMEPLAY_PLUGIN_MANIFEST_FILE,
    GameplayPluginPackage,
    read_gameplay_plugin_package,
    validate_gameplay_plugin_package_dir,
)


GAMEPLAY_PLUGIN_DIR = "plugins"
GAMEPLAY_PLUGIN_ENTRY_STATUS_VALID = "valid"
GAMEPLAY_PLUGIN_ENTRY_STATUS_INVALID = "invalid"
GAMEPLAY_PLUGIN_ACTIVATION_STATUS_INERT = "inert"


@dataclass(frozen=True)
class GameplayPluginRegistryEntry:
    """Project-level discovery record for one gameplay plugin package.

    Registry discovery validates package shape and safety, but it never stages
    or activates plugin content in the running game.
    """

    package_root: Path
    relative_package_root: str
    source_kind: str
    plugin_id: str
    status: str
    activation_status: str
    problems: tuple[str, ...]
    package: GameplayPluginPackage | None = None

    @property
    def valid(self) -> bool:
        return self.status == GAMEPLAY_PLUGIN_ENTRY_STATUS_VALID and not self.problems

    @property
    def activated(self) -> bool:
        return self.activation_status != GAMEPLAY_PLUGIN_ACTIVATION_STATUS_INERT

    @property
    def inert(self) -> bool:
        return self.activation_status == GAMEPLAY_PLUGIN_ACTIVATION_STATUS_INERT


@dataclass(frozen=True)
class GameplayPluginRegistry:
    """Validated discovery snapshot for gameplay plugin packages in one project."""

    project_root: Path
    plugins_root: Path
    entries: tuple[GameplayPluginRegistryEntry, ...]
    problems: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.problems and all(entry.valid for entry in self.entries)

    @property
    def valid_entries(self) -> tuple[GameplayPluginRegistryEntry, ...]:
        return tuple(entry for entry in self.entries if entry.valid)

    @property
    def invalid_entries(self) -> tuple[GameplayPluginRegistryEntry, ...]:
        return tuple(entry for entry in self.entries if not entry.valid)

    @property
    def plugin_ids(self) -> tuple[str, ...]:
        return tuple(entry.plugin_id for entry in self.entries if entry.plugin_id)

    def entry_for_plugin_id(self, plugin_id: str) -> GameplayPluginRegistryEntry | None:
        for entry in self.entries:
            if entry.plugin_id == plugin_id:
                return entry
        return None


def _relative_posix(root: Path, child: Path) -> str:
    return child.resolve().relative_to(root.resolve()).as_posix()


def _source_kind(relative_package_root: str) -> str:
    return relative_package_root.split("/", 1)[0] if relative_package_root else ""


def _manifest_paths(plugins_root: Path) -> list[Path]:
    if not plugins_root.exists():
        return []
    return sorted(
        path
        for path in plugins_root.rglob(GAMEPLAY_PLUGIN_MANIFEST_FILE)
        if path.is_file() and "content" not in path.relative_to(plugins_root).parts
    )


def _entry_for_package(project_root: Path, plugins_root: Path, package_root: Path) -> GameplayPluginRegistryEntry:
    package, load_problems = read_gameplay_plugin_package(package_root)
    semantic_problems = validate_gameplay_plugin_package_dir(package_root) if package is not None else []
    problems = tuple(dict.fromkeys([*load_problems, *semantic_problems]))
    relative_package_root = _relative_posix(plugins_root, package_root)
    plugin_id = package.plugin_id if package is not None else ""
    status = GAMEPLAY_PLUGIN_ENTRY_STATUS_INVALID if problems else GAMEPLAY_PLUGIN_ENTRY_STATUS_VALID

    return GameplayPluginRegistryEntry(
        package_root=package_root,
        relative_package_root=relative_package_root,
        source_kind=_source_kind(relative_package_root),
        plugin_id=plugin_id,
        status=status,
        activation_status=GAMEPLAY_PLUGIN_ACTIVATION_STATUS_INERT,
        problems=problems,
        package=package,
    )


def _duplicate_plugin_id_problems(entries: Iterable[GameplayPluginRegistryEntry]) -> dict[str, tuple[str, ...]]:
    by_id: dict[str, list[GameplayPluginRegistryEntry]] = defaultdict(list)
    for entry in entries:
        if entry.plugin_id:
            by_id[entry.plugin_id].append(entry)

    duplicate_problems: dict[str, tuple[str, ...]] = {}
    for plugin_id, matching_entries in by_id.items():
        if len(matching_entries) <= 1:
            continue
        locations = sorted(entry.relative_package_root for entry in matching_entries)
        problem = f"duplicate gameplay plugin id {plugin_id} in packages {locations}"
        for entry in matching_entries:
            duplicate_problems.setdefault(entry.relative_package_root, tuple())
            duplicate_problems[entry.relative_package_root] = (
                *duplicate_problems[entry.relative_package_root],
                problem,
            )
    return duplicate_problems


def discover_gameplay_plugin_registry(project_root: str | Path) -> GameplayPluginRegistry:
    """Discover and validate gameplay plugin packages for a game project.

    Discovery is read-only and inert: a valid registry means packages are shaped
    well enough for a future stager/importer to consider, not that their content
    is active in gameplay.
    """

    root = Path(project_root)
    plugins_root = root / GAMEPLAY_PLUGIN_DIR
    registry_problems: list[str] = []

    if not root.exists():
        return GameplayPluginRegistry(
            project_root=root,
            plugins_root=plugins_root,
            entries=tuple(),
            problems=(f"gameplay plugin project root does not exist: {root}",),
        )
    if not root.is_dir():
        return GameplayPluginRegistry(
            project_root=root,
            plugins_root=plugins_root,
            entries=tuple(),
            problems=(f"gameplay plugin project root must be a directory: {root}",),
        )
    if not plugins_root.exists():
        return GameplayPluginRegistry(
            project_root=root,
            plugins_root=plugins_root,
            entries=tuple(),
            problems=tuple(),
        )
    if not plugins_root.is_dir():
        return GameplayPluginRegistry(
            project_root=root,
            plugins_root=plugins_root,
            entries=tuple(),
            problems=(f"gameplay plugin root must be a directory: {plugins_root}",),
        )

    entries = tuple(
        _entry_for_package(root, plugins_root, manifest_path.parent)
        for manifest_path in _manifest_paths(plugins_root)
    )

    duplicate_problems = _duplicate_plugin_id_problems(entries)
    if duplicate_problems:
        entries = tuple(
            replace(
                entry,
                status=GAMEPLAY_PLUGIN_ENTRY_STATUS_INVALID,
                problems=(*entry.problems, *duplicate_problems.get(entry.relative_package_root, tuple())),
            )
            for entry in entries
        )

    for entry in entries:
        registry_problems.extend(entry.problems)

    return GameplayPluginRegistry(
        project_root=root,
        plugins_root=plugins_root,
        entries=entries,
        problems=tuple(dict.fromkeys(registry_problems)),
    )


def validate_gameplay_plugin_registry(project_root: str | Path) -> list[str]:
    """Return project-level gameplay plugin discovery/validation problems."""

    return list(discover_gameplay_plugin_registry(project_root).problems)


def assert_valid_gameplay_plugin_registry(project_root: str | Path) -> GameplayPluginRegistry:
    """Discover and require an inert, valid gameplay plugin registry."""

    registry = discover_gameplay_plugin_registry(project_root)
    if registry.problems:
        raise ValueError("Invalid gameplay plugin registry:\n- " + "\n- ".join(registry.problems))
    return registry
