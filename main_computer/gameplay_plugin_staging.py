from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from main_computer.gameplay_plugin_registry import (
    GAMEPLAY_PLUGIN_ACTIVATION_STATUS_INERT,
    GameplayPluginRegistryEntry,
    assert_valid_gameplay_plugin_registry,
    discover_gameplay_plugin_registry,
)


GAMEPLAY_PLUGIN_STAGING_PLAN_KIND = "gameplay-plugin-staging-plan"
GAMEPLAY_PLUGIN_STAGING_STATUS_DRY_RUN = "dry-run"
GAMEPLAY_PLUGIN_STAGING_STATUS_REJECTED = "rejected"


@dataclass(frozen=True)
class GameplayPluginStagedContent:
    """One declared content item a staging plan would add.

    Staging plans are intentionally dry-run snapshots. They report what an
    importer would add later, but they do not write project content, activate
    runtime state, or mutate save data.
    """

    kind: str
    id: str
    path: str
    title: str = ""


@dataclass(frozen=True)
class GameplayPluginStagingPlan:
    """Dry-run staging plan for one validated gameplay plugin package."""

    project_root: Path
    plugin_id: str
    package_root: Path | None
    relative_package_root: str
    kind: str
    status: str
    activation_status: str
    dry_run: bool
    activated: bool
    entry_points: tuple[str, ...]
    scenarios: tuple[GameplayPluginStagedContent, ...]
    encounters: tuple[GameplayPluginStagedContent, ...]
    receipts: tuple[GameplayPluginStagedContent, ...]
    consequences: tuple[GameplayPluginStagedContent, ...]
    required_encounter_templates: tuple[str, ...]
    required_actor_archetypes: tuple[str, ...]
    required_objective_types: tuple[str, ...]
    required_consequence_types: tuple[str, ...]
    allowed_systems: tuple[str, ...]
    allowed_destinations: tuple[str, ...]
    rollback_mode: str
    rollback_supported: bool
    problems: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return (
            self.status == GAMEPLAY_PLUGIN_STAGING_STATUS_DRY_RUN
            and self.dry_run is True
            and self.activated is False
            and not self.problems
        )

    @property
    def rejected(self) -> bool:
        return self.status == GAMEPLAY_PLUGIN_STAGING_STATUS_REJECTED

    @property
    def content_count(self) -> int:
        return len(self.scenarios) + len(self.encounters) + len(self.receipts) + len(self.consequences)

    @property
    def content_paths(self) -> tuple[str, ...]:
        return tuple(
            item.path
            for item in (*self.scenarios, *self.encounters, *self.receipts, *self.consequences)
            if item.path
        )

    @property
    def scenario_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.scenarios)

    @property
    def encounter_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.encounters)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _records(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return tuple()
    return tuple(str(item or "").strip() for item in value if str(item or "").strip())


def _record_content(kind: str, records: Sequence[Mapping[str, Any]]) -> tuple[GameplayPluginStagedContent, ...]:
    items: list[GameplayPluginStagedContent] = []
    for record in records:
        item_id = str(record.get("id") or "").strip()
        path = str(record.get("path") or "").strip()
        title = str(record.get("title") or "").strip()
        if item_id or path:
            items.append(
                GameplayPluginStagedContent(
                    kind=kind,
                    id=item_id,
                    path=path,
                    title=title,
                )
            )
    return tuple(items)


def _empty_plan(project_root: Path, plugin_id: str, problems: Sequence[str]) -> GameplayPluginStagingPlan:
    return GameplayPluginStagingPlan(
        project_root=project_root,
        plugin_id=plugin_id,
        package_root=None,
        relative_package_root="",
        kind=GAMEPLAY_PLUGIN_STAGING_PLAN_KIND,
        status=GAMEPLAY_PLUGIN_STAGING_STATUS_REJECTED,
        activation_status=GAMEPLAY_PLUGIN_ACTIVATION_STATUS_INERT,
        dry_run=True,
        activated=False,
        entry_points=tuple(),
        scenarios=tuple(),
        encounters=tuple(),
        receipts=tuple(),
        consequences=tuple(),
        required_encounter_templates=tuple(),
        required_actor_archetypes=tuple(),
        required_objective_types=tuple(),
        required_consequence_types=tuple(),
        allowed_systems=tuple(),
        allowed_destinations=tuple(),
        rollback_mode="",
        rollback_supported=False,
        problems=tuple(dict.fromkeys(str(problem) for problem in problems if str(problem))),
    )


def _plan_from_entry(project_root: Path, entry: GameplayPluginRegistryEntry) -> GameplayPluginStagingPlan:
    if entry.package is None:
        return _empty_plan(project_root, entry.plugin_id, entry.problems or ("gameplay plugin package is unavailable",))
    if not entry.valid:
        return _empty_plan(project_root, entry.plugin_id, entry.problems or ("gameplay plugin package is invalid",))
    if entry.activation_status != GAMEPLAY_PLUGIN_ACTIVATION_STATUS_INERT:
        return _empty_plan(
            project_root,
            entry.plugin_id,
            (f"gameplay plugin {entry.plugin_id} is already active or staged: {entry.activation_status}",),
        )

    manifest = entry.package.manifest
    content = _mapping(manifest.get("content"))
    requires = _mapping(manifest.get("requires"))
    scope = _mapping(manifest.get("scope"))
    rollback = _mapping(manifest.get("rollback"))

    return GameplayPluginStagingPlan(
        project_root=project_root,
        plugin_id=entry.plugin_id,
        package_root=entry.package_root,
        relative_package_root=entry.relative_package_root,
        kind=GAMEPLAY_PLUGIN_STAGING_PLAN_KIND,
        status=GAMEPLAY_PLUGIN_STAGING_STATUS_DRY_RUN,
        activation_status=GAMEPLAY_PLUGIN_ACTIVATION_STATUS_INERT,
        dry_run=True,
        activated=False,
        entry_points=_strings(content.get("entryPoints")),
        scenarios=_record_content("scenario", _records(content.get("scenarios"))),
        encounters=_record_content("encounter", _records(content.get("encounters"))),
        receipts=_record_content("receipt", _records(content.get("receipts"))),
        consequences=_record_content("consequence", _records(content.get("consequences"))),
        required_encounter_templates=_strings(requires.get("encounterTemplates")),
        required_actor_archetypes=_strings(requires.get("actorArchetypes")),
        required_objective_types=_strings(requires.get("objectiveTypes")),
        required_consequence_types=_strings(requires.get("consequenceTypes")),
        allowed_systems=_strings(scope.get("allowedSystems")),
        allowed_destinations=_strings(scope.get("allowedDestinations")),
        rollback_mode=str(rollback.get("mode") or "").strip(),
        rollback_supported=rollback.get("supported") is True,
        problems=tuple(),
    )


def build_gameplay_plugin_staging_plan(project_root: str | Path, plugin_id: str) -> GameplayPluginStagingPlan:
    """Build a dry-run staging plan for one valid inert gameplay plugin.

    This function is deliberately non-activating. It discovers and validates the
    plugin registry, selects the requested package, and reports what a later
    importer/stager would add.
    """

    root = Path(project_root)
    requested_plugin_id = str(plugin_id or "").strip()
    if not requested_plugin_id:
        return _empty_plan(root, requested_plugin_id, ("gameplay plugin id is required for staging",))

    registry = discover_gameplay_plugin_registry(root)
    duplicate_or_registry_problems = list(registry.problems)
    if duplicate_or_registry_problems:
        entry = registry.entry_for_plugin_id(requested_plugin_id)
        if entry is None:
            return _empty_plan(root, requested_plugin_id, duplicate_or_registry_problems)
        if not entry.valid:
            return _empty_plan(root, requested_plugin_id, entry.problems or duplicate_or_registry_problems)
        # A valid selected plugin should not be staged from an ambiguous/broken
        # registry; keep this dry-run foundation strict until activation exists.
        return _empty_plan(root, requested_plugin_id, duplicate_or_registry_problems)

    entry = registry.entry_for_plugin_id(requested_plugin_id)
    if entry is None:
        return _empty_plan(root, requested_plugin_id, (f"gameplay plugin {requested_plugin_id} was not discovered",))

    return _plan_from_entry(root, entry)


def validate_gameplay_plugin_staging_plan(project_root: str | Path, plugin_id: str) -> list[str]:
    """Return staging-plan validation problems for a selected plugin."""

    return list(build_gameplay_plugin_staging_plan(project_root, plugin_id).problems)


def assert_valid_gameplay_plugin_staging_plan(project_root: str | Path, plugin_id: str) -> GameplayPluginStagingPlan:
    """Require a valid dry-run staging plan for one inert gameplay plugin."""

    plan = build_gameplay_plugin_staging_plan(project_root, plugin_id)
    if plan.problems or not plan.valid:
        raise ValueError("Invalid gameplay plugin staging plan:\n- " + "\n- ".join(plan.problems))
    # Re-run the strict registry assertion to make the activation boundary
    # explicit: staging currently only operates on a fully valid inert registry.
    assert_valid_gameplay_plugin_registry(project_root)
    return plan
