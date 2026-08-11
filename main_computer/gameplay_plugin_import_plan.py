from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from main_computer.gameplay_plugin_registry import discover_gameplay_plugin_registry
from main_computer.gameplay_plugin_staging import (
    GameplayPluginStagedContent,
    build_gameplay_plugin_staging_plan,
)


GAMEPLAY_PLUGIN_IMPORT_PLAN_KIND = "gameplay-plugin-import-plan"
GAMEPLAY_PLUGIN_IMPORT_STATUS_PREVIEW = "preview"
GAMEPLAY_PLUGIN_IMPORT_STATUS_REJECTED = "rejected"
GAMEPLAY_PLUGIN_IMPORT_OPERATION_ADD_GENERATED_CONTENT = "add-generated-content"
GAMEPLAY_PLUGIN_IMPORT_TARGET_LAYER = "generated-plugin-content"


@dataclass(frozen=True)
class GameplayPluginCompiledDocument:
    """One validated plugin content document prepared for a future importer.

    Compiling a document is still read-only. It records the source document, the
    generated-content target path a future activation step would write to, and
    the template/primitive references needed by the runtime.
    """

    kind: str
    id: str
    title: str
    source_path: str
    target_path: str
    template: str = ""
    stage_ids: tuple[str, ...] = ()
    encounter_ids: tuple[str, ...] = ()
    objective_types: tuple[str, ...] = ()
    actor_archetypes: tuple[str, ...] = ()
    receipt_ids: tuple[str, ...] = ()
    consequence_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class GameplayPluginImportOperation:
    """One dry-run operation a later runtime importer would perform."""

    index: int
    operation: str
    target_layer: str
    content_kind: str
    content_id: str
    source_path: str
    target_path: str
    reversible: bool


@dataclass(frozen=True)
class GameplayPluginImportPlan:
    """Runtime-neutral activation/import preview for one validated plugin.

    The import plan is the next step after staging: it compiles declared plugin
    content into explicit generated-content operations, but it never writes those
    operations to the project or activates them in the running game.
    """

    project_root: Path
    plugin_id: str
    package_root: Path | None
    relative_package_root: str
    kind: str
    status: str
    dry_run: bool
    activated: bool
    target_layer: str
    entry_points: tuple[str, ...]
    documents: tuple[GameplayPluginCompiledDocument, ...]
    operations: tuple[GameplayPluginImportOperation, ...]
    receipt_ids: tuple[str, ...]
    consequence_ids: tuple[str, ...]
    required_encounter_templates: tuple[str, ...]
    required_actor_archetypes: tuple[str, ...]
    required_objective_types: tuple[str, ...]
    required_consequence_types: tuple[str, ...]
    allowed_systems: tuple[str, ...]
    allowed_destinations: tuple[str, ...]
    rollback_supported: bool
    rollback_mode: str
    provenance: Mapping[str, Any]
    problems: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return (
            self.status == GAMEPLAY_PLUGIN_IMPORT_STATUS_PREVIEW
            and self.dry_run is True
            and self.activated is False
            and not self.problems
        )

    @property
    def rejected(self) -> bool:
        return self.status == GAMEPLAY_PLUGIN_IMPORT_STATUS_REJECTED

    @property
    def scenario_ids(self) -> tuple[str, ...]:
        return tuple(document.id for document in self.documents if document.kind == "scenario")

    @property
    def encounter_ids(self) -> tuple[str, ...]:
        return tuple(document.id for document in self.documents if document.kind == "encounter")

    @property
    def target_paths(self) -> tuple[str, ...]:
        return tuple(operation.target_path for operation in self.operations)

    @property
    def operation_count(self) -> int:
        return len(self.operations)


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


def _unique_strings(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _empty_plan(project_root: Path, plugin_id: str, problems: Sequence[str]) -> GameplayPluginImportPlan:
    return GameplayPluginImportPlan(
        project_root=project_root,
        plugin_id=plugin_id,
        package_root=None,
        relative_package_root="",
        kind=GAMEPLAY_PLUGIN_IMPORT_PLAN_KIND,
        status=GAMEPLAY_PLUGIN_IMPORT_STATUS_REJECTED,
        dry_run=True,
        activated=False,
        target_layer=GAMEPLAY_PLUGIN_IMPORT_TARGET_LAYER,
        entry_points=tuple(),
        documents=tuple(),
        operations=tuple(),
        receipt_ids=tuple(),
        consequence_ids=tuple(),
        required_encounter_templates=tuple(),
        required_actor_archetypes=tuple(),
        required_objective_types=tuple(),
        required_consequence_types=tuple(),
        allowed_systems=tuple(),
        allowed_destinations=tuple(),
        rollback_supported=False,
        rollback_mode="",
        provenance={},
        problems=tuple(dict.fromkeys(str(problem) for problem in problems if str(problem))),
    )


def _safe_plugin_path_component(plugin_id: str) -> str:
    safe = "".join(character if character.isalnum() or character in "._-" else "-" for character in plugin_id)
    return safe.strip(".-_") or "plugin"


def _target_path(plugin_id: str, source_path: str) -> str:
    # The source path has already passed package validation. Rebuild it through
    # PurePosixPath to keep the compiled import path normalized and explicit.
    source_parts = PurePosixPath(source_path).parts
    return PurePosixPath(
        "generated",
        "gameplay-plugins",
        _safe_plugin_path_component(plugin_id),
        *source_parts,
    ).as_posix()


def _document_records(document: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    return _records(document.get(key))


def _document_strings(document: Mapping[str, Any], key: str) -> tuple[str, ...]:
    return _strings(document.get(key))


def _scenario_stage_ids(document: Mapping[str, Any]) -> tuple[str, ...]:
    return _unique_strings(str(stage.get("id") or "").strip() for stage in _document_records(document, "stages"))


def _scenario_encounter_ids(document: Mapping[str, Any]) -> tuple[str, ...]:
    ids: list[str] = list(_document_strings(document, "encounterIds"))
    for stage in _document_records(document, "stages"):
        encounter_id = str(stage.get("encounterId") or "").strip()
        if encounter_id:
            ids.append(encounter_id)
    return _unique_strings(ids)


def _scenario_receipt_ids(document: Mapping[str, Any]) -> tuple[str, ...]:
    ids: list[str] = list(_document_strings(document, "receiptIds"))
    for stage in _document_records(document, "stages"):
        ids.extend(_strings(stage.get("receiptIds")))
    return _unique_strings(ids)


def _encounter_objective_types(document: Mapping[str, Any]) -> tuple[str, ...]:
    return _unique_strings(
        str(objective.get("type") or "").strip() for objective in _document_records(document, "objectives")
    )


def _encounter_actor_archetypes(document: Mapping[str, Any]) -> tuple[str, ...]:
    return _unique_strings(
        str(participant.get("actorArchetypeId") or "").strip()
        for participant in _document_records(document, "participants")
    )


def _encounter_receipt_ids(document: Mapping[str, Any]) -> tuple[str, ...]:
    ids: list[str] = []
    for outcome in _document_records(document, "outcomes"):
        ids.extend(_strings(outcome.get("receiptIds")))
    return _unique_strings(ids)


def _encounter_consequence_types(document: Mapping[str, Any]) -> tuple[str, ...]:
    ids: list[str] = []
    for outcome in _document_records(document, "outcomes"):
        ids.extend(_strings(outcome.get("consequenceTypeIds")))
    return _unique_strings(ids)


def _compile_document(
    plugin_id: str,
    staged: GameplayPluginStagedContent,
    document: Mapping[str, Any],
) -> GameplayPluginCompiledDocument:
    kind = str(document.get("kind") or staged.kind).strip()
    source_path = staged.path
    title = str(document.get("title") or staged.title).strip()
    document_id = str(document.get("id") or staged.id).strip()

    if kind == "scenario":
        return GameplayPluginCompiledDocument(
            kind=kind,
            id=document_id,
            title=title,
            source_path=source_path,
            target_path=_target_path(plugin_id, source_path),
            stage_ids=_scenario_stage_ids(document),
            encounter_ids=_scenario_encounter_ids(document),
            receipt_ids=_scenario_receipt_ids(document),
        )

    if kind == "encounter":
        return GameplayPluginCompiledDocument(
            kind=kind,
            id=document_id,
            title=title,
            source_path=source_path,
            target_path=_target_path(plugin_id, source_path),
            template=str(document.get("template") or staged.id).strip(),
            objective_types=_encounter_objective_types(document),
            actor_archetypes=_encounter_actor_archetypes(document),
            receipt_ids=_encounter_receipt_ids(document),
            consequence_types=_encounter_consequence_types(document),
        )

    return GameplayPluginCompiledDocument(
        kind=kind,
        id=document_id,
        title=title,
        source_path=source_path,
        target_path=_target_path(plugin_id, source_path),
    )


def _operations_for_documents(
    documents: Sequence[GameplayPluginCompiledDocument],
) -> tuple[GameplayPluginImportOperation, ...]:
    return tuple(
        GameplayPluginImportOperation(
            index=index,
            operation=GAMEPLAY_PLUGIN_IMPORT_OPERATION_ADD_GENERATED_CONTENT,
            target_layer=GAMEPLAY_PLUGIN_IMPORT_TARGET_LAYER,
            content_kind=document.kind,
            content_id=document.id,
            source_path=document.source_path,
            target_path=document.target_path,
            reversible=True,
        )
        for index, document in enumerate(documents, start=1)
    )


def _records_ids(records: Sequence[GameplayPluginStagedContent]) -> tuple[str, ...]:
    return _unique_strings(record.id for record in records)


def build_gameplay_plugin_import_plan(project_root: str | Path, plugin_id: str) -> GameplayPluginImportPlan:
    """Build a dry-run runtime-neutral import preview for one plugin.

    The plan compiles already-validated plugin content into explicit operations
    that a later importer could apply to a generated-content layer. This
    foundation does not apply those operations.
    """

    root = Path(project_root)
    requested_plugin_id = str(plugin_id or "").strip()
    staging_plan = build_gameplay_plugin_staging_plan(root, requested_plugin_id)
    if not staging_plan.valid:
        return _empty_plan(root, requested_plugin_id, staging_plan.problems or ("gameplay plugin staging plan is invalid",))

    registry = discover_gameplay_plugin_registry(root)
    entry = registry.entry_for_plugin_id(requested_plugin_id)
    if entry is None or entry.package is None:
        return _empty_plan(root, requested_plugin_id, (f"gameplay plugin {requested_plugin_id} package is unavailable",))

    content_by_path = entry.package.content_by_path
    staged_documents = (*staging_plan.scenarios, *staging_plan.encounters)
    documents: list[GameplayPluginCompiledDocument] = []
    problems: list[str] = []
    for staged in staged_documents:
        document = content_by_path.get(staged.path)
        if not isinstance(document, Mapping):
            problems.append(f"gameplay plugin import plan missing loaded content for {staged.path}")
            continue
        documents.append(_compile_document(requested_plugin_id, staged, document))

    if problems:
        return _empty_plan(root, requested_plugin_id, problems)

    operations = _operations_for_documents(documents)
    manifest = entry.package.manifest

    return GameplayPluginImportPlan(
        project_root=root,
        plugin_id=requested_plugin_id,
        package_root=staging_plan.package_root,
        relative_package_root=staging_plan.relative_package_root,
        kind=GAMEPLAY_PLUGIN_IMPORT_PLAN_KIND,
        status=GAMEPLAY_PLUGIN_IMPORT_STATUS_PREVIEW,
        dry_run=True,
        activated=False,
        target_layer=GAMEPLAY_PLUGIN_IMPORT_TARGET_LAYER,
        entry_points=staging_plan.entry_points,
        documents=tuple(documents),
        operations=operations,
        receipt_ids=_records_ids(staging_plan.receipts),
        consequence_ids=_records_ids(staging_plan.consequences),
        required_encounter_templates=staging_plan.required_encounter_templates,
        required_actor_archetypes=staging_plan.required_actor_archetypes,
        required_objective_types=staging_plan.required_objective_types,
        required_consequence_types=staging_plan.required_consequence_types,
        allowed_systems=staging_plan.allowed_systems,
        allowed_destinations=staging_plan.allowed_destinations,
        rollback_supported=staging_plan.rollback_supported,
        rollback_mode=staging_plan.rollback_mode,
        provenance=_mapping(manifest.get("provenance")),
        problems=tuple(),
    )


def validate_gameplay_plugin_import_plan(project_root: str | Path, plugin_id: str) -> list[str]:
    """Return import-plan validation problems for a selected plugin."""

    return list(build_gameplay_plugin_import_plan(project_root, plugin_id).problems)


def assert_valid_gameplay_plugin_import_plan(project_root: str | Path, plugin_id: str) -> GameplayPluginImportPlan:
    """Require a valid non-activating import preview for one plugin."""

    plan = build_gameplay_plugin_import_plan(project_root, plugin_id)
    if plan.problems or not plan.valid:
        raise ValueError("Invalid gameplay plugin import plan:\n- " + "\n- ".join(plan.problems))
    return plan
