from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from typing import Any

from main_computer.gameplay_plugin_runtime_catalog import (
    GAMEPLAY_PLUGIN_RUNTIME_CATALOG_RUNTIME_STATUS,
    GAMEPLAY_PLUGIN_RUNTIME_CATALOG_STATUS_READY,
    GameplayPluginRuntimeCatalog,
    GameplayPluginRuntimeCatalogEntry,
    GameplayPluginRuntimeDocument,
    build_gameplay_plugin_runtime_catalog,
)


GAMEPLAY_PLUGIN_CATALOG_EXPORT_KIND = "gameplay-plugin-runtime-catalog-export"
GAMEPLAY_PLUGIN_CATALOG_EXPORT_SCHEMA = "game.gameplayPluginRuntimeCatalogExport.v1"
GAMEPLAY_PLUGIN_CATALOG_EXPORT_STATUS_READY = "ready"
GAMEPLAY_PLUGIN_CATALOG_EXPORT_STATUS_REJECTED = "rejected"
GAMEPLAY_PLUGIN_CATALOG_EXPORT_RUNTIME_STATUS = "generated-index-only"
GAMEPLAY_PLUGIN_CATALOG_EXPORT_PATH = "generated/gameplay-plugins/runtime-catalog.json"
GAMEPLAY_PLUGIN_CATALOG_EXPORT_LAYER = "generated/gameplay-plugins"


@dataclass(frozen=True)
class GameplayPluginCatalogExport:
    """Generated runtime-catalog index for enabled gameplay plugin content.

    The export is a deterministic JSON index for later browser/runtime work. It
    does not inject scenarios into project.json, register browser modules, mutate
    saves, or execute generated plugin content.
    """

    project_root: Path
    export_path: Path
    relative_export_path: str
    kind: str
    schema: str
    status: str
    runtime_status: str
    runtime_catalog_status: str
    runtime_catalog_schema: str
    runtime_catalog_kind: str
    runtime_loaded: bool
    project_json_modified: bool
    activated_in_runtime: bool
    wrote_file: bool
    payload: dict[str, Any]
    problems: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return (
            self.kind == GAMEPLAY_PLUGIN_CATALOG_EXPORT_KIND
            and self.schema == GAMEPLAY_PLUGIN_CATALOG_EXPORT_SCHEMA
            and self.status == GAMEPLAY_PLUGIN_CATALOG_EXPORT_STATUS_READY
            and self.runtime_status == GAMEPLAY_PLUGIN_CATALOG_EXPORT_RUNTIME_STATUS
            and self.runtime_loaded is False
            and self.project_json_modified is False
            and self.activated_in_runtime is False
            and not self.problems
        )

    @property
    def rejected(self) -> bool:
        return self.status == GAMEPLAY_PLUGIN_CATALOG_EXPORT_STATUS_REJECTED


def _unique_strings(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        return (values.strip(),) if values.strip() else ()
    try:
        iterator = iter(values)
    except TypeError:
        return ()
    return tuple(dict.fromkeys(str(value).strip() for value in iterator if str(value).strip()))


def _is_safe_relative_posix_path(value: str) -> bool:
    try:
        path = PurePosixPath(str(value or ""))
    except TypeError:
        return False
    if not path.parts or path.is_absolute():
        return False
    return all(part not in ("", ".", "..") for part in path.parts)


def _safe_export_path(project_root: Path, relative_path: str) -> Path:
    if not _is_safe_relative_posix_path(relative_path):
        raise ValueError(f"unsafe gameplay plugin catalog export path: {relative_path}")
    path = PurePosixPath(relative_path)
    required_root = PurePosixPath(GAMEPLAY_PLUGIN_CATALOG_EXPORT_LAYER)
    if path.parts[: len(required_root.parts)] != required_root.parts:
        raise ValueError(
            "gameplay plugin catalog export path must stay under "
            f"{GAMEPLAY_PLUGIN_CATALOG_EXPORT_LAYER}: {relative_path}"
        )
    root = project_root.resolve()
    candidate = (root / Path(*path.parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"gameplay plugin catalog export escaped project root: {relative_path}") from exc
    return candidate


def _document_payload(document: GameplayPluginRuntimeDocument) -> dict[str, Any]:
    return {
        "pluginId": document.plugin_id,
        "kind": document.kind,
        "id": document.id,
        "title": document.title,
        "path": document.path,
        "template": document.template,
        "stageIds": list(document.stage_ids),
        "encounterIds": list(document.encounter_ids),
        "objectiveTypes": list(document.objective_types),
        "actorArchetypes": list(document.actor_archetypes),
        "receiptIds": list(document.receipt_ids),
        "consequenceTypes": list(document.consequence_types),
    }


def _entry_payload(entry: GameplayPluginRuntimeCatalogEntry) -> dict[str, Any]:
    return {
        "pluginId": entry.plugin_id,
        "status": entry.status,
        "generatedRoot": entry.generated_root,
        "receiptPath": entry.receipt_path,
        "rollbackPath": entry.rollback_path,
        "targetLayer": entry.target_layer,
        "entryPoints": list(entry.entry_points),
        "scenarioIds": list(entry.scenarios),
        "encounterIds": list(entry.encounters),
        "receiptIds": list(entry.receipts),
        "consequenceIds": list(entry.consequences),
        "documentPaths": list(entry.document_paths),
        "documents": [_document_payload(document) for document in entry.documents],
        "runtimeLoaded": entry.runtime_loaded,
        "projectJsonModified": entry.project_json_modified,
        "activatedInRuntime": entry.activated_in_runtime,
        "problems": list(entry.problems),
    }


def _catalog_payload(catalog: GameplayPluginRuntimeCatalog) -> dict[str, Any]:
    documents = tuple(catalog.documents)
    return {
        "schema": GAMEPLAY_PLUGIN_CATALOG_EXPORT_SCHEMA,
        "kind": GAMEPLAY_PLUGIN_CATALOG_EXPORT_KIND,
        "status": GAMEPLAY_PLUGIN_CATALOG_EXPORT_STATUS_READY,
        "runtimeStatus": GAMEPLAY_PLUGIN_CATALOG_EXPORT_RUNTIME_STATUS,
        "runtimeCatalog": {
            "schema": catalog.schema,
            "kind": catalog.kind,
            "status": catalog.status,
            "runtimeStatus": catalog.runtime_status,
        },
        "generatedLayer": GAMEPLAY_PLUGIN_CATALOG_EXPORT_LAYER,
        "catalogPath": GAMEPLAY_PLUGIN_CATALOG_EXPORT_PATH,
        "runtimeLoaded": False,
        "projectJsonModified": False,
        "activatedInRuntime": False,
        "enabledPluginIds": list(catalog.enabled_plugin_ids),
        "disabledPluginIds": list(catalog.disabled_plugin_ids),
        "entryPoints": list(catalog.entry_points),
        "scenarioIds": list(catalog.scenario_ids),
        "encounterIds": list(catalog.encounter_ids),
        "receiptIds": list(catalog.receipt_ids),
        "consequenceIds": list(catalog.consequence_ids),
        "documentPaths": list(catalog.document_paths),
        "documents": [_document_payload(document) for document in documents],
        "plugins": [_entry_payload(entry) for entry in catalog.entries],
        "problems": [],
    }


def _empty_export(project_root: Path, problems: list[str], *, wrote_file: bool = False) -> GameplayPluginCatalogExport:
    export_path = project_root / GAMEPLAY_PLUGIN_CATALOG_EXPORT_PATH
    payload = {
        "schema": GAMEPLAY_PLUGIN_CATALOG_EXPORT_SCHEMA,
        "kind": GAMEPLAY_PLUGIN_CATALOG_EXPORT_KIND,
        "status": GAMEPLAY_PLUGIN_CATALOG_EXPORT_STATUS_REJECTED,
        "runtimeStatus": GAMEPLAY_PLUGIN_CATALOG_EXPORT_RUNTIME_STATUS,
        "catalogPath": GAMEPLAY_PLUGIN_CATALOG_EXPORT_PATH,
        "runtimeLoaded": False,
        "projectJsonModified": False,
        "activatedInRuntime": False,
        "enabledPluginIds": [],
        "disabledPluginIds": [],
        "entryPoints": [],
        "scenarioIds": [],
        "encounterIds": [],
        "receiptIds": [],
        "consequenceIds": [],
        "documentPaths": [],
        "documents": [],
        "plugins": [],
        "problems": list(dict.fromkeys(problems)),
    }
    return GameplayPluginCatalogExport(
        project_root=project_root,
        export_path=export_path,
        relative_export_path=GAMEPLAY_PLUGIN_CATALOG_EXPORT_PATH,
        kind=GAMEPLAY_PLUGIN_CATALOG_EXPORT_KIND,
        schema=GAMEPLAY_PLUGIN_CATALOG_EXPORT_SCHEMA,
        status=GAMEPLAY_PLUGIN_CATALOG_EXPORT_STATUS_REJECTED,
        runtime_status=GAMEPLAY_PLUGIN_CATALOG_EXPORT_RUNTIME_STATUS,
        runtime_catalog_status="",
        runtime_catalog_schema="",
        runtime_catalog_kind="",
        runtime_loaded=False,
        project_json_modified=False,
        activated_in_runtime=False,
        wrote_file=wrote_file,
        payload=payload,
        problems=tuple(dict.fromkeys(problems)),
    )


def build_gameplay_plugin_catalog_export(project_root: str | Path) -> GameplayPluginCatalogExport:
    """Build a deterministic generated plugin catalog payload without writing it."""

    root = Path(project_root)
    try:
        export_path = _safe_export_path(root, GAMEPLAY_PLUGIN_CATALOG_EXPORT_PATH)
    except ValueError as exc:
        return _empty_export(root, [str(exc)])

    catalog = build_gameplay_plugin_runtime_catalog(root)
    problems = list(catalog.problems)
    if not catalog.valid or catalog.status != GAMEPLAY_PLUGIN_RUNTIME_CATALOG_STATUS_READY:
        problems.append("gameplay plugin runtime catalog is not ready for export")
    if catalog.runtime_status != GAMEPLAY_PLUGIN_RUNTIME_CATALOG_RUNTIME_STATUS:
        problems.append(f"unexpected runtime catalog status: {catalog.runtime_status}")
    if catalog.runtime_loaded or catalog.project_json_modified or catalog.activated_in_runtime:
        problems.append("gameplay plugin runtime catalog already claims runtime activation")

    if problems:
        return _empty_export(root, list(dict.fromkeys(problems)))

    payload = _catalog_payload(catalog)
    return GameplayPluginCatalogExport(
        project_root=root,
        export_path=export_path,
        relative_export_path=GAMEPLAY_PLUGIN_CATALOG_EXPORT_PATH,
        kind=GAMEPLAY_PLUGIN_CATALOG_EXPORT_KIND,
        schema=GAMEPLAY_PLUGIN_CATALOG_EXPORT_SCHEMA,
        status=GAMEPLAY_PLUGIN_CATALOG_EXPORT_STATUS_READY,
        runtime_status=GAMEPLAY_PLUGIN_CATALOG_EXPORT_RUNTIME_STATUS,
        runtime_catalog_status=catalog.status,
        runtime_catalog_schema=catalog.schema,
        runtime_catalog_kind=catalog.kind,
        runtime_loaded=False,
        project_json_modified=False,
        activated_in_runtime=False,
        wrote_file=False,
        payload=payload,
        problems=tuple(),
    )


def validate_gameplay_plugin_catalog_export(project_root: str | Path) -> list[str]:
    """Return catalog export problems without writing files."""

    return list(build_gameplay_plugin_catalog_export(project_root).problems)


def write_gameplay_plugin_catalog_export(project_root: str | Path, *, overwrite: bool = False) -> GameplayPluginCatalogExport:
    """Write generated/gameplay-plugins/runtime-catalog.json for enabled plugins.

    Writing the catalog is filesystem-index materialization only. It does not
    activate browser/runtime content, edit project.json, mutate saves, or execute
    generated plugin content.
    """

    result = build_gameplay_plugin_catalog_export(project_root)
    if result.problems or not result.valid:
        return result

    if result.export_path.exists() and not overwrite:
        return _empty_export(
            result.project_root,
            [f"gameplay plugin catalog export already exists: {result.relative_export_path}"],
        )

    result.export_path.parent.mkdir(parents=True, exist_ok=True)
    result.export_path.write_text(
        json.dumps(result.payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return GameplayPluginCatalogExport(
        project_root=result.project_root,
        export_path=result.export_path,
        relative_export_path=result.relative_export_path,
        kind=result.kind,
        schema=result.schema,
        status=result.status,
        runtime_status=result.runtime_status,
        runtime_catalog_status=result.runtime_catalog_status,
        runtime_catalog_schema=result.runtime_catalog_schema,
        runtime_catalog_kind=result.runtime_catalog_kind,
        runtime_loaded=False,
        project_json_modified=False,
        activated_in_runtime=False,
        wrote_file=True,
        payload=result.payload,
        problems=tuple(),
    )


def assert_valid_gameplay_plugin_catalog_export(
    project_root: str | Path,
    *,
    write: bool = False,
    overwrite: bool = False,
) -> GameplayPluginCatalogExport:
    """Build or write a valid generated plugin catalog export, else raise."""

    result = (
        write_gameplay_plugin_catalog_export(project_root, overwrite=overwrite)
        if write
        else build_gameplay_plugin_catalog_export(project_root)
    )
    if result.problems or not result.valid:
        raise ValueError("Invalid gameplay plugin catalog export:\n- " + "\n- ".join(result.problems))
    return result
