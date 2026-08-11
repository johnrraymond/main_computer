from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from typing import Any

from main_computer.gameplay_plugin_activation import (
    GAMEPLAY_PLUGIN_ACTIVATION_STATUS_DISABLED,
    GAMEPLAY_PLUGIN_ACTIVATION_STATUS_ENABLED,
    GameplayPluginActivationEntry,
    read_gameplay_plugin_activation_manifest,
)
from main_computer.gameplay_plugin_materializer import (
    GAMEPLAY_PLUGIN_MATERIALIZATION_KIND,
    GAMEPLAY_PLUGIN_MATERIALIZATION_RECEIPT_FILE,
    GAMEPLAY_PLUGIN_MATERIALIZATION_ROLLBACK_FILE,
    GAMEPLAY_PLUGIN_MATERIALIZATION_STATUS_MATERIALIZED,
)


GAMEPLAY_PLUGIN_RUNTIME_CATALOG_KIND = "gameplay-plugin-runtime-catalog"
GAMEPLAY_PLUGIN_RUNTIME_CATALOG_SCHEMA = "game.gameplayPluginRuntimeCatalog.v1"
GAMEPLAY_PLUGIN_RUNTIME_CATALOG_STATUS_READY = "ready"
GAMEPLAY_PLUGIN_RUNTIME_CATALOG_STATUS_REJECTED = "rejected"
GAMEPLAY_PLUGIN_RUNTIME_CATALOG_RUNTIME_STATUS = "runtime-neutral-catalog-only"


@dataclass(frozen=True)
class GameplayPluginRuntimeDocument:
    """One enabled generated gameplay content document visible to a future runtime loader.

    Runtime catalog documents are read-only summaries. Building a catalog parses
    generated JSON content, but it does not register scenarios with the browser,
    edit project.json, mutate saves, or execute plugin content.
    """

    plugin_id: str
    kind: str
    id: str
    title: str
    path: str
    template: str = ""
    stage_ids: tuple[str, ...] = ()
    encounter_ids: tuple[str, ...] = ()
    objective_types: tuple[str, ...] = ()
    actor_archetypes: tuple[str, ...] = ()
    receipt_ids: tuple[str, ...] = ()
    consequence_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class GameplayPluginRuntimeCatalogEntry:
    """One enabled materialized gameplay plugin entry in the runtime-neutral catalog."""

    plugin_id: str
    status: str
    generated_root: str
    receipt_path: str
    rollback_path: str
    target_layer: str
    entry_points: tuple[str, ...]
    scenarios: tuple[str, ...]
    encounters: tuple[str, ...]
    receipts: tuple[str, ...]
    consequences: tuple[str, ...]
    documents: tuple[GameplayPluginRuntimeDocument, ...]
    runtime_loaded: bool
    project_json_modified: bool
    activated_in_runtime: bool
    problems: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return (
            self.status == GAMEPLAY_PLUGIN_ACTIVATION_STATUS_ENABLED
            and self.runtime_loaded is False
            and self.project_json_modified is False
            and self.activated_in_runtime is False
            and not self.problems
        )

    @property
    def scenario_documents(self) -> tuple[GameplayPluginRuntimeDocument, ...]:
        return tuple(document for document in self.documents if document.kind == "scenario")

    @property
    def encounter_documents(self) -> tuple[GameplayPluginRuntimeDocument, ...]:
        return tuple(document for document in self.documents if document.kind == "encounter")

    @property
    def document_paths(self) -> tuple[str, ...]:
        return tuple(document.path for document in self.documents)


@dataclass(frozen=True)
class GameplayPluginRuntimeCatalog:
    """Runtime-neutral catalog of enabled generated gameplay plugin content.

    This is the read-only bridge between filesystem activation metadata and a
    future browser/scenario runtime loader. It intentionally stops before runtime
    injection: no project.json updates, no browser loading, and no game-state
    mutation occur while building this catalog.
    """

    project_root: Path
    kind: str
    schema: str
    status: str
    runtime_status: str
    runtime_loaded: bool
    project_json_modified: bool
    activated_in_runtime: bool
    entries: tuple[GameplayPluginRuntimeCatalogEntry, ...]
    disabled_plugin_ids: tuple[str, ...]
    problems: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return (
            self.status == GAMEPLAY_PLUGIN_RUNTIME_CATALOG_STATUS_READY
            and self.kind == GAMEPLAY_PLUGIN_RUNTIME_CATALOG_KIND
            and self.schema == GAMEPLAY_PLUGIN_RUNTIME_CATALOG_SCHEMA
            and self.runtime_status == GAMEPLAY_PLUGIN_RUNTIME_CATALOG_RUNTIME_STATUS
            and self.runtime_loaded is False
            and self.project_json_modified is False
            and self.activated_in_runtime is False
            and not self.problems
            and all(entry.valid for entry in self.entries)
        )

    @property
    def rejected(self) -> bool:
        return self.status == GAMEPLAY_PLUGIN_RUNTIME_CATALOG_STATUS_REJECTED

    @property
    def enabled_plugin_ids(self) -> tuple[str, ...]:
        return tuple(entry.plugin_id for entry in self.entries)

    @property
    def entry_points(self) -> tuple[str, ...]:
        return _unique_strings(item for entry in self.entries for item in entry.entry_points)

    @property
    def scenario_ids(self) -> tuple[str, ...]:
        return _unique_strings(item for entry in self.entries for item in entry.scenarios)

    @property
    def encounter_ids(self) -> tuple[str, ...]:
        return _unique_strings(item for entry in self.entries for item in entry.encounters)

    @property
    def receipt_ids(self) -> tuple[str, ...]:
        return _unique_strings(item for entry in self.entries for item in entry.receipts)

    @property
    def consequence_ids(self) -> tuple[str, ...]:
        return _unique_strings(item for entry in self.entries for item in entry.consequences)

    @property
    def documents(self) -> tuple[GameplayPluginRuntimeDocument, ...]:
        return tuple(document for entry in self.entries for document in entry.documents)

    @property
    def document_paths(self) -> tuple[str, ...]:
        return tuple(document.path for document in self.documents)


def _unique_strings(values: Sequence[str] | Any) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return tuple()
    return _unique_strings(str(item or "").strip() for item in value)


def _is_safe_relative_posix_path(path: str) -> bool:
    if not path or "\\" in path or path.startswith("/"):
        return False
    parts = PurePosixPath(path).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        return False
    return True


def _safe_project_child(project_root: Path, relative_path: str) -> Path:
    if not _is_safe_relative_posix_path(relative_path):
        raise ValueError(f"unsafe gameplay plugin runtime catalog path {relative_path!r}")
    resolved_root = project_root.resolve()
    child = (project_root / Path(*PurePosixPath(relative_path).parts)).resolve()
    if child != resolved_root and resolved_root not in child.parents:
        raise ValueError(f"gameplay plugin runtime catalog path escapes project root: {relative_path!r}")
    return child


def _load_json(path: Path) -> tuple[Mapping[str, Any] | None, tuple[str, ...]]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, (f"gameplay plugin runtime catalog file is missing: {path}",)
    except json.JSONDecodeError as exc:
        return None, (f"gameplay plugin runtime catalog JSON is invalid at {path}: {exc}",)
    if not isinstance(loaded, Mapping):
        return None, (f"gameplay plugin runtime catalog JSON must be an object: {path}",)
    return loaded, tuple()


def _document_stage_ids(document: Mapping[str, Any]) -> tuple[str, ...]:
    stages = document.get("stages")
    if not isinstance(stages, list):
        return tuple()
    return _unique_strings(
        str(stage.get("id") or "").strip()
        for stage in stages
        if isinstance(stage, Mapping)
    )


def _document_encounter_ids(document: Mapping[str, Any]) -> tuple[str, ...]:
    ids: list[str] = list(_strings(document.get("encounterIds")))
    stages = document.get("stages")
    if isinstance(stages, list):
        ids.extend(
            str(stage.get("encounterId") or "").strip()
            for stage in stages
            if isinstance(stage, Mapping)
        )
    return _unique_strings(ids)


def _document_objective_types(document: Mapping[str, Any]) -> tuple[str, ...]:
    objectives = document.get("objectives")
    if not isinstance(objectives, list):
        return tuple()
    return _unique_strings(
        str(objective.get("type") or "").strip()
        for objective in objectives
        if isinstance(objective, Mapping)
    )


def _document_actor_archetypes(document: Mapping[str, Any]) -> tuple[str, ...]:
    participants = document.get("participants")
    if not isinstance(participants, list):
        return tuple()
    return _unique_strings(
        str(participant.get("actorArchetypeId") or "").strip()
        for participant in participants
        if isinstance(participant, Mapping)
    )


def _document_receipt_ids(document: Mapping[str, Any]) -> tuple[str, ...]:
    ids: list[str] = list(_strings(document.get("receiptIds")))
    stages = document.get("stages")
    if isinstance(stages, list):
        for stage in stages:
            if isinstance(stage, Mapping):
                ids.extend(_strings(stage.get("receiptIds")))
    outcomes = document.get("outcomes")
    if isinstance(outcomes, list):
        for outcome in outcomes:
            if isinstance(outcome, Mapping):
                ids.extend(_strings(outcome.get("receiptIds")))
    return _unique_strings(ids)


def _document_consequence_types(document: Mapping[str, Any]) -> tuple[str, ...]:
    outcomes = document.get("outcomes")
    if not isinstance(outcomes, list):
        return tuple()
    values: list[str] = []
    for outcome in outcomes:
        if isinstance(outcome, Mapping):
            values.extend(_strings(outcome.get("consequenceTypeIds")))
    return _unique_strings(values)


def _runtime_document_from_payload(
    plugin_id: str,
    path: str,
    expected_kind: str,
    expected_id: str,
    payload: Mapping[str, Any],
) -> GameplayPluginRuntimeDocument:
    return GameplayPluginRuntimeDocument(
        plugin_id=plugin_id,
        kind=str(payload.get("kind") or expected_kind).strip(),
        id=str(payload.get("id") or expected_id).strip(),
        title=str(payload.get("title") or "").strip(),
        path=path,
        template=str(payload.get("template") or "").strip(),
        stage_ids=_document_stage_ids(payload),
        encounter_ids=_document_encounter_ids(payload),
        objective_types=_document_objective_types(payload),
        actor_archetypes=_document_actor_archetypes(payload),
        receipt_ids=_document_receipt_ids(payload),
        consequence_types=_document_consequence_types(payload),
    )


def _validate_receipt(
    project_root: Path,
    entry: GameplayPluginActivationEntry,
) -> tuple[Mapping[str, Any] | None, list[str]]:
    problems: list[str] = []
    try:
        receipt_path = _safe_project_child(project_root, entry.receipt_path)
        rollback_path = _safe_project_child(project_root, entry.rollback_path)
        generated_root = _safe_project_child(project_root, entry.generated_root)
    except ValueError as exc:
        return None, [str(exc)]

    if not receipt_path.exists():
        problems.append(f"enabled gameplay plugin receipt is missing: {entry.receipt_path}")
    if not rollback_path.exists():
        problems.append(f"enabled gameplay plugin rollback plan is missing: {entry.rollback_path}")
    if not generated_root.exists():
        problems.append(f"enabled gameplay plugin generated root is missing: {entry.generated_root}")
    if problems:
        return None, problems

    receipt, receipt_problems = _load_json(receipt_path)
    problems.extend(receipt_problems)
    if receipt is None:
        return None, problems

    if receipt.get("kind") != GAMEPLAY_PLUGIN_MATERIALIZATION_KIND:
        problems.append(f"enabled gameplay plugin materialization receipt kind is unsupported: {entry.plugin_id}")
    if receipt.get("status") != GAMEPLAY_PLUGIN_MATERIALIZATION_STATUS_MATERIALIZED:
        problems.append(f"enabled gameplay plugin materialization receipt is not materialized: {entry.plugin_id}")
    if receipt.get("pluginId") != entry.plugin_id:
        problems.append(f"enabled gameplay plugin receipt plugin id mismatch: {entry.plugin_id}")
    if receipt.get("generatedRoot") != entry.generated_root:
        problems.append(f"enabled gameplay plugin receipt generated root mismatch: {entry.plugin_id}")
    if receipt.get("receiptPath") != entry.receipt_path:
        problems.append(f"enabled gameplay plugin receipt path mismatch: {entry.plugin_id}")
    if receipt.get("rollbackPath") != entry.rollback_path:
        problems.append(f"enabled gameplay plugin rollback path mismatch: {entry.plugin_id}")
    if receipt.get("activated") is not False:
        problems.append(f"enabled gameplay plugin receipt must not claim runtime activation: {entry.plugin_id}")
    if receipt.get("runtimeLoaded") is not False:
        problems.append(f"enabled gameplay plugin receipt must not claim runtime loading: {entry.plugin_id}")
    if receipt.get("projectJsonModified") is not False:
        problems.append(f"enabled gameplay plugin receipt must not claim project.json mutation: {entry.plugin_id}")

    files = receipt.get("files")
    if not isinstance(files, list) or not files:
        problems.append(f"enabled gameplay plugin materialization receipt must list generated files: {entry.plugin_id}")

    return receipt, problems


def _load_generated_documents(
    project_root: Path,
    entry: GameplayPluginActivationEntry,
    receipt: Mapping[str, Any],
) -> tuple[tuple[GameplayPluginRuntimeDocument, ...], list[str]]:
    documents: list[GameplayPluginRuntimeDocument] = []
    problems: list[str] = []

    try:
        generated_root = _safe_project_child(project_root, entry.generated_root)
    except ValueError as exc:
        return tuple(), [str(exc)]

    files = receipt.get("files")
    if not isinstance(files, list):
        return tuple(), [f"enabled gameplay plugin materialization receipt files must be a list: {entry.plugin_id}"]

    seen_paths: set[str] = set()
    seen_documents: set[tuple[str, str]] = set()

    for file_record in files:
        if not isinstance(file_record, Mapping):
            problems.append(f"enabled gameplay plugin generated file record must be an object: {entry.plugin_id}")
            continue

        content_kind = str(file_record.get("contentKind") or "").strip()
        content_id = str(file_record.get("contentId") or "").strip()
        target_path = str(file_record.get("targetPath") or "").strip()
        if not content_kind:
            problems.append(f"enabled gameplay plugin generated file is missing contentKind: {entry.plugin_id}")
        if not content_id:
            problems.append(f"enabled gameplay plugin generated file is missing contentId: {entry.plugin_id}")
        if not _is_safe_relative_posix_path(target_path):
            problems.append(f"enabled gameplay plugin generated file has unsafe path: {target_path!r}")
            continue

        if target_path in seen_paths:
            problems.append(f"enabled gameplay plugin generated file path is duplicated: {target_path}")
        seen_paths.add(target_path)

        try:
            target = _safe_project_child(project_root, target_path)
        except ValueError as exc:
            problems.append(str(exc))
            continue

        if generated_root != target and generated_root not in target.parents:
            problems.append(f"enabled gameplay plugin generated file is outside plugin root: {target_path}")
            continue
        if not target.exists():
            problems.append(f"enabled gameplay plugin generated file is missing: {target_path}")
            continue

        payload, load_problems = _load_json(target)
        problems.extend(load_problems)
        if payload is None:
            continue

        payload_kind = str(payload.get("kind") or "").strip()
        payload_id = str(payload.get("id") or "").strip()
        if payload_kind != content_kind:
            problems.append(
                f"enabled gameplay plugin generated file kind mismatch for {target_path}: "
                f"receipt={content_kind!r} document={payload_kind!r}"
            )
        if payload_id != content_id:
            problems.append(
                f"enabled gameplay plugin generated file id mismatch for {target_path}: "
                f"receipt={content_id!r} document={payload_id!r}"
            )

        key = (content_kind, content_id)
        if key in seen_documents:
            problems.append(f"enabled gameplay plugin generated document is duplicated: {content_kind}:{content_id}")
        seen_documents.add(key)

        documents.append(
            _runtime_document_from_payload(
                entry.plugin_id,
                target_path,
                content_kind,
                content_id,
                payload,
            )
        )

    scenario_ids = tuple(document.id for document in documents if document.kind == "scenario")
    encounter_ids = tuple(document.id for document in documents if document.kind == "encounter")

    for scenario_id in entry.scenarios:
        if scenario_id not in scenario_ids:
            problems.append(f"enabled gameplay plugin scenario is missing from generated catalog documents: {scenario_id}")
    for encounter_id in entry.encounters:
        if encounter_id not in encounter_ids:
            problems.append(f"enabled gameplay plugin encounter is missing from generated catalog documents: {encounter_id}")

    for entry_point in entry.entry_points:
        if entry_point not in scenario_ids:
            problems.append(f"enabled gameplay plugin entry point is not a generated scenario: {entry_point}")

    return tuple(documents), problems


def _catalog_entry_from_activation_entry(
    project_root: Path,
    entry: GameplayPluginActivationEntry,
) -> GameplayPluginRuntimeCatalogEntry:
    problems: list[str] = []
    if entry.status != GAMEPLAY_PLUGIN_ACTIVATION_STATUS_ENABLED:
        problems.append(f"gameplay plugin runtime catalog entry is not enabled: {entry.plugin_id}")
    if not entry.safe:
        problems.append(f"gameplay plugin runtime catalog entry must remain metadata-only: {entry.plugin_id}")

    receipt, receipt_problems = _validate_receipt(project_root, entry)
    problems.extend(receipt_problems)

    documents: tuple[GameplayPluginRuntimeDocument, ...] = tuple()
    if receipt is not None and not receipt_problems:
        documents, document_problems = _load_generated_documents(project_root, entry, receipt)
        problems.extend(document_problems)

    return GameplayPluginRuntimeCatalogEntry(
        plugin_id=entry.plugin_id,
        status=entry.status,
        generated_root=entry.generated_root,
        receipt_path=entry.receipt_path,
        rollback_path=entry.rollback_path,
        target_layer=entry.target_layer,
        entry_points=entry.entry_points,
        scenarios=entry.scenarios,
        encounters=entry.encounters,
        receipts=entry.receipts,
        consequences=entry.consequences,
        documents=documents,
        runtime_loaded=False,
        project_json_modified=False,
        activated_in_runtime=False,
        problems=tuple(dict.fromkeys(problems)),
    )


def build_gameplay_plugin_runtime_catalog(project_root: str | Path) -> GameplayPluginRuntimeCatalog:
    """Build a runtime-neutral read-only catalog from enabled generated plugins.

    The catalog is intentionally not a runtime loader. It reads activation
    metadata and generated content documents so later code can see what generated
    scenarios/encounters are available, but it does not mutate project files,
    edit project.json, register browser modules, or execute gameplay.
    """

    root = Path(project_root)
    activation_manifest = read_gameplay_plugin_activation_manifest(root)
    problems: list[str] = list(activation_manifest.problems)

    entries: list[GameplayPluginRuntimeCatalogEntry] = []
    for activation_entry in activation_manifest.entries:
        if activation_entry.status == GAMEPLAY_PLUGIN_ACTIVATION_STATUS_ENABLED:
            catalog_entry = _catalog_entry_from_activation_entry(root, activation_entry)
            entries.append(catalog_entry)
            problems.extend(catalog_entry.problems)
        elif activation_entry.status != GAMEPLAY_PLUGIN_ACTIVATION_STATUS_DISABLED:
            problems.append(f"gameplay plugin activation entry has unsupported runtime catalog status: {activation_entry.status}")

    status = (
        GAMEPLAY_PLUGIN_RUNTIME_CATALOG_STATUS_REJECTED
        if problems
        else GAMEPLAY_PLUGIN_RUNTIME_CATALOG_STATUS_READY
    )

    return GameplayPluginRuntimeCatalog(
        project_root=root,
        kind=GAMEPLAY_PLUGIN_RUNTIME_CATALOG_KIND,
        schema=GAMEPLAY_PLUGIN_RUNTIME_CATALOG_SCHEMA,
        status=status,
        runtime_status=GAMEPLAY_PLUGIN_RUNTIME_CATALOG_RUNTIME_STATUS,
        runtime_loaded=False,
        project_json_modified=False,
        activated_in_runtime=False,
        entries=tuple(entries),
        disabled_plugin_ids=activation_manifest.disabled_plugin_ids,
        problems=tuple(dict.fromkeys(str(problem) for problem in problems if str(problem))),
    )


def validate_gameplay_plugin_runtime_catalog(project_root: str | Path) -> list[str]:
    """Return runtime catalog problems without mutating project files."""

    return list(build_gameplay_plugin_runtime_catalog(project_root).problems)


def assert_valid_gameplay_plugin_runtime_catalog(project_root: str | Path) -> GameplayPluginRuntimeCatalog:
    """Build a valid runtime-neutral catalog or raise with actionable problems."""

    catalog = build_gameplay_plugin_runtime_catalog(project_root)
    if catalog.problems or not catalog.valid:
        raise ValueError("Invalid gameplay plugin runtime catalog:\n- " + "\n- ".join(catalog.problems))
    return catalog
