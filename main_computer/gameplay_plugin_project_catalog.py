from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from typing import Any

from main_computer.gameplay_plugin_catalog_export import (
    GAMEPLAY_PLUGIN_CATALOG_EXPORT_KIND,
    GAMEPLAY_PLUGIN_CATALOG_EXPORT_PATH,
    GAMEPLAY_PLUGIN_CATALOG_EXPORT_RUNTIME_STATUS,
    GAMEPLAY_PLUGIN_CATALOG_EXPORT_SCHEMA,
    GAMEPLAY_PLUGIN_CATALOG_EXPORT_STATUS_READY,
)


GAMEPLAY_PLUGIN_PROJECT_CATALOG_KIND = "gameplay-plugin-project-catalog"
GAMEPLAY_PLUGIN_PROJECT_CATALOG_SCHEMA = "game.gameplayPluginProjectCatalog.v1"
GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_READY = "ready"
GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_ABSENT = "absent"
GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_REJECTED = "rejected"
GAMEPLAY_PLUGIN_PROJECT_CATALOG_RUNTIME_STATUS = "browser-static-project-data-only"
GAMEPLAY_PLUGIN_PROJECT_CATALOG_METADATA_KEY = "generatedGameplayPlugins"


@dataclass(frozen=True)
class GameplayPluginProjectCatalog:
    """Project-facing view of the exported generated gameplay catalog.

    This object is the bridge from the filesystem generated-plugin pipeline into
    static project data that browser code can inspect later. It does not activate
    plugin content, edit project.json, load browser modules, mutate saves, or
    execute generated gameplay.
    """

    project_root: Path
    project_id: str
    catalog_path: Path
    relative_catalog_path: str
    schema: str
    kind: str
    status: str
    runtime_status: str
    exported: bool
    runtime_loaded: bool
    project_json_modified: bool
    activated_in_runtime: bool
    payload: dict[str, Any]
    problems: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return (
            self.schema == GAMEPLAY_PLUGIN_PROJECT_CATALOG_SCHEMA
            and self.kind == GAMEPLAY_PLUGIN_PROJECT_CATALOG_KIND
            and self.status in {
                GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_READY,
                GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_ABSENT,
            }
            and self.runtime_status == GAMEPLAY_PLUGIN_PROJECT_CATALOG_RUNTIME_STATUS
            and self.runtime_loaded is False
            and self.project_json_modified is False
            and self.activated_in_runtime is False
            and not self.problems
        )

    @property
    def rejected(self) -> bool:
        return self.status == GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_REJECTED

    @property
    def enabled_plugin_ids(self) -> tuple[str, ...]:
        return _strings(self.payload.get("enabledPluginIds"))

    @property
    def entry_points(self) -> tuple[str, ...]:
        return _strings(self.payload.get("entryPoints"))

    @property
    def scenario_ids(self) -> tuple[str, ...]:
        return _strings(self.payload.get("scenarioIds"))

    @property
    def encounter_ids(self) -> tuple[str, ...]:
        return _strings(self.payload.get("encounterIds"))

    @property
    def document_paths(self) -> tuple[str, ...]:
        return _strings(self.payload.get("documentPaths"))

    @property
    def plugins(self) -> tuple[Mapping[str, Any], ...]:
        return _records(self.payload.get("plugins"))

    @property
    def documents(self) -> tuple[Mapping[str, Any], ...]:
        return _records(self.payload.get("documents"))

    @property
    def scenarios(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(document for document in self.documents if str(document.get("kind") or "") == "scenario")

    @property
    def encounters(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(document for document in self.documents if str(document.get("kind") or "") == "encounter")


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else tuple()
    if not isinstance(value, Sequence):
        return tuple()
    return tuple(dict.fromkeys(str(item or "").strip() for item in value if str(item or "").strip()))


def _records(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        return tuple()
    return tuple(item for item in value if isinstance(item, Mapping))


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))


def _objective_summaries(value: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for objective in _records(value):
        objective_type = str(objective.get("type") or "").strip()
        if not objective_type:
            continue
        objective_id = str(objective.get("id") or objective_type).strip()
        label = str(objective.get("label") or "").strip()
        record: dict[str, Any] = {
            "id": objective_id,
            "type": objective_type,
            "required": objective.get("required") is not False,
        }
        if label:
            record["label"] = label
        records.append(record)
    return records


def _participant_summaries(value: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for participant in _records(value):
        actor_archetype_id = str(participant.get("actorArchetypeId") or "").strip()
        if not actor_archetype_id:
            continue
        records.append(
            {
                "role": str(participant.get("role") or "").strip(),
                "actorArchetypeId": actor_archetype_id,
                "count": _bounded_int(participant.get("count"), 1, 1, 64),
            }
        )
    return records


def _location_summary(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: current
        for key, current in (
            ("systemId", str(value.get("systemId") or "").strip()),
            ("destinationId", str(value.get("destinationId") or "").strip()),
        )
        if current
    }


def _is_safe_relative_posix_path(value: str) -> bool:
    try:
        path = PurePosixPath(str(value or ""))
    except TypeError:
        return False
    if not path.parts or path.is_absolute():
        return False
    return all(part not in ("", ".", "..") for part in path.parts)


def _under_generated_plugin_layer(value: str) -> bool:
    if not _is_safe_relative_posix_path(value):
        return False
    root = PurePosixPath("generated/gameplay-plugins")
    path = PurePosixPath(value)
    return path.parts[: len(root.parts)] == root.parts


def _safe_project_child(project_root: Path, relative_path: str) -> Path:
    if not _under_generated_plugin_layer(relative_path):
        raise ValueError(f"unsafe generated gameplay catalog path: {relative_path}")
    root = project_root.resolve()
    candidate = (root / Path(*PurePosixPath(relative_path).parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"generated gameplay catalog path escaped project root: {relative_path}") from exc
    return candidate


def _base_payload(
    *,
    project_id: str,
    status: str,
    exported: bool,
    problems: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "schema": GAMEPLAY_PLUGIN_PROJECT_CATALOG_SCHEMA,
        "kind": GAMEPLAY_PLUGIN_PROJECT_CATALOG_KIND,
        "status": status,
        "runtimeStatus": GAMEPLAY_PLUGIN_PROJECT_CATALOG_RUNTIME_STATUS,
        "projectId": project_id,
        "exported": bool(exported),
        "catalogPath": GAMEPLAY_PLUGIN_CATALOG_EXPORT_PATH,
        "sourceCatalog": {
            "schema": GAMEPLAY_PLUGIN_CATALOG_EXPORT_SCHEMA,
            "kind": GAMEPLAY_PLUGIN_CATALOG_EXPORT_KIND,
            "status": "",
            "runtimeStatus": GAMEPLAY_PLUGIN_CATALOG_EXPORT_RUNTIME_STATUS,
        },
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
        "scenarios": [],
        "encounters": [],
        "plugins": [],
        "problems": list(dict.fromkeys(str(problem) for problem in problems if str(problem))),
    }


def _result(
    project_root: Path,
    project_id: str,
    status: str,
    payload: dict[str, Any],
    problems: Sequence[str] = (),
) -> GameplayPluginProjectCatalog:
    return GameplayPluginProjectCatalog(
        project_root=project_root,
        project_id=project_id,
        catalog_path=project_root / GAMEPLAY_PLUGIN_CATALOG_EXPORT_PATH,
        relative_catalog_path=GAMEPLAY_PLUGIN_CATALOG_EXPORT_PATH,
        schema=GAMEPLAY_PLUGIN_PROJECT_CATALOG_SCHEMA,
        kind=GAMEPLAY_PLUGIN_PROJECT_CATALOG_KIND,
        status=status,
        runtime_status=GAMEPLAY_PLUGIN_PROJECT_CATALOG_RUNTIME_STATUS,
        exported=bool(payload.get("exported")),
        runtime_loaded=False,
        project_json_modified=False,
        activated_in_runtime=False,
        payload=payload,
        problems=tuple(dict.fromkeys(str(problem) for problem in problems if str(problem))),
    )


def _document_summary(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "pluginId": str(document.get("pluginId") or ""),
        "kind": str(document.get("kind") or ""),
        "id": str(document.get("id") or ""),
        "title": str(document.get("title") or ""),
        "path": str(document.get("path") or ""),
        "template": str(document.get("template") or ""),
        "stageIds": list(_strings(document.get("stageIds"))),
        "encounterIds": list(_strings(document.get("encounterIds"))),
        "objectiveTypes": list(_strings(document.get("objectiveTypes"))),
        "actorArchetypes": list(_strings(document.get("actorArchetypes"))),
        "receiptIds": list(_strings(document.get("receiptIds"))),
        "consequenceTypes": list(_strings(document.get("consequenceTypes"))),
        "objectives": _objective_summaries(document.get("objectives")),
        "participants": _participant_summaries(document.get("participants")),
        "location": _location_summary(document.get("location")),
    }


def _plugin_summary(plugin: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "pluginId": str(plugin.get("pluginId") or ""),
        "status": str(plugin.get("status") or ""),
        "generatedRoot": str(plugin.get("generatedRoot") or ""),
        "receiptPath": str(plugin.get("receiptPath") or ""),
        "rollbackPath": str(plugin.get("rollbackPath") or ""),
        "targetLayer": str(plugin.get("targetLayer") or ""),
        "entryPoints": list(_strings(plugin.get("entryPoints"))),
        "scenarioIds": list(_strings(plugin.get("scenarioIds"))),
        "encounterIds": list(_strings(plugin.get("encounterIds"))),
        "receiptIds": list(_strings(plugin.get("receiptIds"))),
        "consequenceIds": list(_strings(plugin.get("consequenceIds"))),
        "documentPaths": list(_strings(plugin.get("documentPaths"))),
        "runtimeLoaded": bool(plugin.get("runtimeLoaded")),
        "projectJsonModified": bool(plugin.get("projectJsonModified")),
        "activatedInRuntime": bool(plugin.get("activatedInRuntime")),
        "problems": list(_strings(plugin.get("problems"))),
    }


def _project_payload_from_export(project_id: str, export_payload: Mapping[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    problems: list[str] = []

    if export_payload.get("schema") != GAMEPLAY_PLUGIN_CATALOG_EXPORT_SCHEMA:
        problems.append("generated gameplay catalog export schema mismatch")
    if export_payload.get("kind") != GAMEPLAY_PLUGIN_CATALOG_EXPORT_KIND:
        problems.append("generated gameplay catalog export kind mismatch")
    if export_payload.get("status") != GAMEPLAY_PLUGIN_CATALOG_EXPORT_STATUS_READY:
        problems.append("generated gameplay catalog export is not ready")
    if export_payload.get("runtimeStatus") != GAMEPLAY_PLUGIN_CATALOG_EXPORT_RUNTIME_STATUS:
        problems.append("generated gameplay catalog export runtime status mismatch")
    if export_payload.get("runtimeLoaded") is not False:
        problems.append("generated gameplay catalog export claims runtime loading")
    if export_payload.get("projectJsonModified") is not False:
        problems.append("generated gameplay catalog export claims project.json mutation")
    if export_payload.get("activatedInRuntime") is not False:
        problems.append("generated gameplay catalog export claims runtime activation")

    documents = tuple(_document_summary(document) for document in _records(export_payload.get("documents")))
    plugins = tuple(_plugin_summary(plugin) for plugin in _records(export_payload.get("plugins")))

    document_paths = _strings(export_payload.get("documentPaths"))
    for path in document_paths:
        if not _under_generated_plugin_layer(path):
            problems.append(f"generated gameplay catalog document path is unsafe: {path}")
    for document in documents:
        path = str(document.get("path") or "")
        if path and not _under_generated_plugin_layer(path):
            problems.append(f"generated gameplay catalog document path is unsafe: {path}")
        if document["kind"] not in {"scenario", "encounter"}:
            problems.append(f"generated gameplay catalog document has unsupported kind: {document['kind']}")
    for plugin in plugins:
        for key in ("generatedRoot", "receiptPath", "rollbackPath"):
            path = str(plugin.get(key) or "")
            if path and not _under_generated_plugin_layer(path):
                problems.append(f"generated gameplay catalog plugin {key} path is unsafe: {path}")
        if plugin.get("runtimeLoaded") is not False:
            problems.append(f"generated gameplay catalog plugin claims runtime loading: {plugin.get('pluginId')}")
        if plugin.get("projectJsonModified") is not False:
            problems.append(f"generated gameplay catalog plugin claims project.json mutation: {plugin.get('pluginId')}")
        if plugin.get("activatedInRuntime") is not False:
            problems.append(f"generated gameplay catalog plugin claims runtime activation: {plugin.get('pluginId')}")

    scenario_ids = tuple(document["id"] for document in documents if document["kind"] == "scenario" and document["id"])
    encounter_ids = tuple(document["id"] for document in documents if document["kind"] == "encounter" and document["id"])
    entry_points = _strings(export_payload.get("entryPoints"))
    missing_entry_points = [entry_point for entry_point in entry_points if entry_point not in scenario_ids]
    for entry_point in missing_entry_points:
        problems.append(f"generated gameplay catalog entry point is not a generated scenario: {entry_point}")

    status = (
        GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_REJECTED
        if problems
        else GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_READY
    )
    payload = _base_payload(project_id=project_id, status=status, exported=True, problems=problems)
    payload.update(
        {
            "sourceCatalog": {
                "schema": str(export_payload.get("schema") or ""),
                "kind": str(export_payload.get("kind") or ""),
                "status": str(export_payload.get("status") or ""),
                "runtimeStatus": str(export_payload.get("runtimeStatus") or ""),
            },
            "enabledPluginIds": list(_strings(export_payload.get("enabledPluginIds"))),
            "disabledPluginIds": list(_strings(export_payload.get("disabledPluginIds"))),
            "entryPoints": list(entry_points),
            "scenarioIds": list(_strings(export_payload.get("scenarioIds")) or scenario_ids),
            "encounterIds": list(_strings(export_payload.get("encounterIds")) or encounter_ids),
            "receiptIds": list(_strings(export_payload.get("receiptIds"))),
            "consequenceIds": list(_strings(export_payload.get("consequenceIds"))),
            "documentPaths": list(document_paths),
            "documents": list(documents),
            "scenarios": [document for document in documents if document["kind"] == "scenario"],
            "encounters": [document for document in documents if document["kind"] == "encounter"],
            "plugins": list(plugins),
            "problems": list(dict.fromkeys(problems)),
        }
    )
    return payload, tuple(dict.fromkeys(problems))


def read_gameplay_plugin_project_catalog(
    project_root: str | Path,
    *,
    project_id: str = "",
) -> GameplayPluginProjectCatalog:
    """Read the exported generated gameplay catalog for static project data.

    Missing exports are treated as a valid empty catalog. Present exports are
    validated and summarized without loading generated gameplay into the running
    browser runtime.
    """

    root = Path(project_root)
    resolved_project_id = str(project_id or root.name or "").strip()
    try:
        catalog_path = _safe_project_child(root, GAMEPLAY_PLUGIN_CATALOG_EXPORT_PATH)
    except ValueError as exc:
        payload = _base_payload(
            project_id=resolved_project_id,
            status=GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_REJECTED,
            exported=False,
            problems=[str(exc)],
        )
        return _result(root, resolved_project_id, GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_REJECTED, payload, [str(exc)])

    if not catalog_path.exists():
        payload = _base_payload(
            project_id=resolved_project_id,
            status=GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_ABSENT,
            exported=False,
        )
        return _result(root, resolved_project_id, GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_ABSENT, payload)

    try:
        export_payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except Exception as exc:
        problem = f"generated gameplay catalog export could not be read: {exc}"
        payload = _base_payload(
            project_id=resolved_project_id,
            status=GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_REJECTED,
            exported=True,
            problems=[problem],
        )
        return _result(root, resolved_project_id, GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_REJECTED, payload, [problem])

    if not isinstance(export_payload, Mapping):
        problem = "generated gameplay catalog export must be a JSON object"
        payload = _base_payload(
            project_id=resolved_project_id,
            status=GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_REJECTED,
            exported=True,
            problems=[problem],
        )
        return _result(root, resolved_project_id, GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_REJECTED, payload, [problem])

    payload, problems = _project_payload_from_export(resolved_project_id, export_payload)
    status = (
        GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_REJECTED
        if problems
        else GAMEPLAY_PLUGIN_PROJECT_CATALOG_STATUS_READY
    )
    return _result(root, resolved_project_id, status, payload, problems)


def validate_gameplay_plugin_project_catalog(project_root: str | Path, *, project_id: str = "") -> list[str]:
    """Return generated project-catalog exposure problems without mutating files."""

    return list(read_gameplay_plugin_project_catalog(project_root, project_id=project_id).problems)


def apply_gameplay_plugin_project_catalog(
    project_root: str | Path,
    project: Mapping[str, Any],
    *,
    project_id: str = "",
    catalog: GameplayPluginProjectCatalog | None = None,
) -> dict[str, Any]:
    """Return a project payload copy with generated gameplay catalog metadata.

    The returned object is suitable for static browser project data. It is a
    detached copy and does not write project.json or load generated content.
    """

    root = Path(project_root)
    catalog = catalog or read_gameplay_plugin_project_catalog(root, project_id=project_id)
    try:
        result = json.loads(json.dumps(project if isinstance(project, Mapping) else {}))
    except TypeError:
        result = dict(project if isinstance(project, Mapping) else {})
    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata[GAMEPLAY_PLUGIN_PROJECT_CATALOG_METADATA_KEY] = catalog.payload
    result["metadata"] = metadata
    return result


def assert_valid_gameplay_plugin_project_catalog(
    project_root: str | Path,
    *,
    project_id: str = "",
) -> GameplayPluginProjectCatalog:
    """Read a valid generated project catalog or raise with actionable problems."""

    catalog = read_gameplay_plugin_project_catalog(project_root, project_id=project_id)
    if catalog.problems or not catalog.valid:
        raise ValueError("Invalid gameplay plugin project catalog:\n- " + "\n- ".join(catalog.problems))
    return catalog
