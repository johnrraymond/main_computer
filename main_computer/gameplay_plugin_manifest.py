from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from main_computer.gameplay_template_registry import validate_gameplay_plugin_manifest_against_template_registry


GAMEPLAY_PLUGIN_MANIFEST_SCHEMA = "game.gameplayPluginManifest.v1"
GAMEPLAY_PLUGIN_MANIFEST_VERSION = "gameplay-plugin.manifest.v1"

_REQUIRED_CANNOT_MODIFY = {
    "engine-code",
    "renderer-code",
    "runtime-code",
    "save-schema",
    "runtime-state",
    "base-project-content",
    "base-scenarios",
    "executable-code",
}

_EXECUTABLE_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".cjs",
    ".css",
    ".html",
    ".js",
    ".jsx",
    ".mjs",
    ".ps1",
    ".py",
    ".sh",
    ".ts",
    ".tsx",
}


def _mapping(value: Any, label: str, problems: list[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        problems.append(f"{label} must be an object")
        return {}
    return value


def _records(value: Any, label: str, problems: list[str]) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        problems.append(f"{label} must be a list")
        return []
    result: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            problems.append(f"{label}[{index}] must be an object")
            continue
        result.append(item)
    return result


def _strings(value: Any, label: str, problems: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        problems.append(f"{label} must be a list")
        return []
    result: list[str] = []
    for index, item in enumerate(value):
        item_value = str(item or "").strip()
        if not item_value:
            problems.append(f"{label}[{index}] must be a non-empty string")
            continue
        result.append(item_value)
    if len(result) != len(set(result)):
        problems.append(f"{label} contains duplicate values")
    return result


def _path_is_safe(path: str) -> bool:
    if not path or "\\" in path or path.startswith("/"):
        return False
    parts = PurePosixPath(path).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        return False
    return True


def _path_is_executable(path: str) -> bool:
    suffixes = {suffix.lower() for suffix in PurePosixPath(path).suffixes}
    return bool(suffixes & _EXECUTABLE_EXTENSIONS)


def _check_content_path(path: str, label: str, problems: list[str]) -> None:
    if not _path_is_safe(path):
        problems.append(f"{label} must be a safe relative POSIX path")
        return
    if not path.startswith("content/"):
        problems.append(f"{label} must stay under content/")
    if _path_is_executable(path):
        problems.append(f"{label} must not reference executable or runtime code")


def _collect_content_ids(
    records: Sequence[Mapping[str, Any]],
    *,
    label: str,
    seen: dict[str, str],
    problems: list[str],
) -> set[str]:
    ids: set[str] = set()
    for index, record in enumerate(records):
        raw_id = str(record.get("id") or "").strip()
        if not raw_id:
            problems.append(f"{label}[{index}] is missing id")
            continue
        if raw_id in seen:
            problems.append(f"duplicate plugin content id {raw_id} in {label}; already declared in {seen[raw_id]}")
        seen[raw_id] = label
        ids.add(raw_id)
        path = str(record.get("path") or "").strip()
        if not path:
            problems.append(f"{label}[{index}] is missing path")
        else:
            _check_content_path(path, f"{label}[{index}].path", problems)
    return ids


def validate_gameplay_plugin_manifest(manifest: Mapping[str, Any] | None) -> list[str]:
    """Return semantic manifest validation problems.

    The JSON schema owns field-level shape. This validator owns safety and
    cross-reference rules that should remain true before any gameplay plugin can
    be staged or imported.
    """

    problems: list[str] = []
    if not isinstance(manifest, Mapping):
        return ["gameplay plugin manifest must be an object"]

    if manifest.get("schema") != GAMEPLAY_PLUGIN_MANIFEST_SCHEMA:
        problems.append(f"manifest.schema must be {GAMEPLAY_PLUGIN_MANIFEST_SCHEMA}")
    if manifest.get("manifestVersion") != GAMEPLAY_PLUGIN_MANIFEST_VERSION:
        problems.append(
            f"manifest.manifestVersion must be {GAMEPLAY_PLUGIN_MANIFEST_VERSION}"
        )

    scope = _mapping(manifest.get("scope"), "manifest.scope", problems)
    cannot_modify = set(_strings(scope.get("cannotModify"), "manifest.scope.cannotModify", problems))
    missing_cannot_modify = sorted(_REQUIRED_CANNOT_MODIFY - cannot_modify)
    if missing_cannot_modify:
        problems.append(
            "manifest.scope.cannotModify is missing required locked areas "
            f"{missing_cannot_modify}"
        )

    can_modify = set(_strings(scope.get("canModify"), "manifest.scope.canModify", problems))
    if can_modify != {"generated-content-only"}:
        problems.append(
            "manifest.scope.canModify must be exactly ['generated-content-only']"
        )

    permissions = _mapping(manifest.get("permissions"), "manifest.permissions", problems)
    for key in (
        "allowExecutableCode",
        "allowRuntimeStateMutation",
        "allowEngineFileMutation",
        "allowBaseContentMutation",
    ):
        if permissions.get(key) is not False:
            problems.append(f"manifest.permissions.{key} must be false")

    validation = _mapping(manifest.get("validation"), "manifest.validation", problems)
    for key in (
        "mustPassSchema",
        "mustPassReferenceCheck",
        "mustPassSimulation",
        "mustRejectExecutableCode",
        "mustSupportRollback",
    ):
        if validation.get(key) is not True:
            problems.append(f"manifest.validation.{key} must be true")

    rollback = _mapping(manifest.get("rollback"), "manifest.rollback", problems)
    if rollback.get("supported") is not True:
        problems.append("manifest.rollback.supported must be true")
    if rollback.get("mode") != "disable-plugin":
        problems.append("manifest.rollback.mode must be disable-plugin")

    content = _mapping(manifest.get("content"), "manifest.content", problems)
    seen: dict[str, str] = {}
    scenario_ids = _collect_content_ids(
        _records(content.get("scenarios"), "manifest.content.scenarios", problems),
        label="manifest.content.scenarios",
        seen=seen,
        problems=problems,
    )
    encounter_records = _records(content.get("encounters"), "manifest.content.encounters", problems)
    encounter_ids = _collect_content_ids(
        encounter_records,
        label="manifest.content.encounters",
        seen=seen,
        problems=problems,
    )
    for collection_name in ("dialogue", "evidence", "consequences", "receipts"):
        _collect_content_ids(
            _records(content.get(collection_name), f"manifest.content.{collection_name}", problems),
            label=f"manifest.content.{collection_name}",
            seen=seen,
            problems=problems,
        )

    entry_points = set(_strings(content.get("entryPoints"), "manifest.content.entryPoints", problems))
    missing_entry_points = sorted(entry_points - scenario_ids)
    if missing_entry_points:
        problems.append(
            "manifest.content.entryPoints references undeclared scenarios "
            f"{missing_entry_points}"
        )

    for index, scenario in enumerate(_records(content.get("scenarios"), "manifest.content.scenarios", problems)):
        for encounter_id in _strings(
            scenario.get("encounterIds"),
            f"manifest.content.scenarios[{index}].encounterIds",
            problems,
        ):
            if encounter_id not in encounter_ids:
                problems.append(
                    f"manifest.content.scenarios[{index}].encounterIds references missing "
                    f"encounter {encounter_id}"
                )

    requirements = _mapping(manifest.get("requires"), "manifest.requires", problems)
    encounter_templates = set(
        _strings(requirements.get("encounterTemplates"), "manifest.requires.encounterTemplates", problems)
    )
    actor_archetypes = set(
        _strings(requirements.get("actorArchetypes"), "manifest.requires.actorArchetypes", problems)
    )
    objective_types = set(
        _strings(requirements.get("objectiveTypes"), "manifest.requires.objectiveTypes", problems)
    )

    for index, encounter in enumerate(encounter_records):
        template = str(encounter.get("template") or "").strip()
        if template and template not in encounter_templates:
            problems.append(
                f"manifest.content.encounters[{index}].template references missing required template {template}"
            )
        for actor_id in _strings(
            encounter.get("actorArchetypeIds"),
            f"manifest.content.encounters[{index}].actorArchetypeIds",
            problems,
        ):
            if actor_id not in actor_archetypes:
                problems.append(
                    f"manifest.content.encounters[{index}].actorArchetypeIds references missing "
                    f"required archetype {actor_id}"
                )
        for objective_id in _strings(
            encounter.get("objectiveTypeIds"),
            f"manifest.content.encounters[{index}].objectiveTypeIds",
            problems,
        ):
            if objective_id not in objective_types:
                problems.append(
                    f"manifest.content.encounters[{index}].objectiveTypeIds references missing "
                    f"required objective type {objective_id}"
                )

    for index, file_record in enumerate(_records(content.get("files"), "manifest.content.files", problems)):
        path = str(file_record.get("path") or "").strip()
        if not path:
            problems.append(f"manifest.content.files[{index}] is missing path")
            continue
        _check_content_path(path, f"manifest.content.files[{index}].path", problems)

    problems.extend(validate_gameplay_plugin_manifest_against_template_registry(manifest))

    return problems


def assert_valid_gameplay_plugin_manifest(manifest: Mapping[str, Any] | None) -> None:
    """Raise ValueError when a gameplay plugin manifest is unsafe or incoherent."""

    problems = validate_gameplay_plugin_manifest(manifest)
    if problems:
        raise ValueError("Invalid gameplay plugin manifest:\n- " + "\n- ".join(problems))
