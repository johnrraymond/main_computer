from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
from typing import Any

from main_computer.gameplay_plugin_import_plan import (
    GAMEPLAY_PLUGIN_IMPORT_TARGET_LAYER,
    GameplayPluginImportPlan,
    GameplayPluginImportOperation,
    assert_valid_gameplay_plugin_import_plan,
    build_gameplay_plugin_import_plan,
)


GAMEPLAY_PLUGIN_MATERIALIZATION_KIND = "gameplay-plugin-materialization"
GAMEPLAY_PLUGIN_MATERIALIZATION_STATUS_MATERIALIZED = "materialized"
GAMEPLAY_PLUGIN_MATERIALIZATION_STATUS_REJECTED = "rejected"
GAMEPLAY_PLUGIN_MATERIALIZATION_RECEIPT_FILE = "materialization-receipt.json"
GAMEPLAY_PLUGIN_MATERIALIZATION_ROLLBACK_FILE = "rollback-plan.json"
GAMEPLAY_PLUGIN_MATERIALIZATION_RUNTIME_STATUS = "filesystem-staged-only"


@dataclass(frozen=True)
class GameplayPluginMaterializedFile:
    """One generated-layer file written from a validated plugin import plan."""

    content_kind: str
    content_id: str
    source_path: str
    target_path: str
    bytes_written: int
    sha256: str


@dataclass(frozen=True)
class GameplayPluginMaterializationResult:
    """Filesystem-only materialization result for one validated gameplay plugin.

    Materialization writes validated plugin content into the generated-content
    layer and writes rollback metadata. It does not register the content with the
    game runtime, edit project.json, mutate saves, or activate the plugin.
    """

    project_root: Path
    plugin_id: str
    kind: str
    status: str
    target_layer: str
    runtime_status: str
    activated: bool
    runtime_loaded: bool
    project_json_modified: bool
    package_root: Path | None
    generated_root: Path | None
    relative_generated_root: str
    materialized_files: tuple[GameplayPluginMaterializedFile, ...]
    receipt_path: str
    rollback_path: str
    rollback_supported: bool
    rollback_mode: str
    problems: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return (
            self.status == GAMEPLAY_PLUGIN_MATERIALIZATION_STATUS_MATERIALIZED
            and self.activated is False
            and self.runtime_loaded is False
            and self.project_json_modified is False
            and not self.problems
        )

    @property
    def rejected(self) -> bool:
        return self.status == GAMEPLAY_PLUGIN_MATERIALIZATION_STATUS_REJECTED

    @property
    def target_paths(self) -> tuple[str, ...]:
        return tuple(file.target_path for file in self.materialized_files)

    @property
    def file_count(self) -> int:
        return len(self.materialized_files)


def _unique_strings(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _empty_result(project_root: Path, plugin_id: str, problems: Sequence[str]) -> GameplayPluginMaterializationResult:
    return GameplayPluginMaterializationResult(
        project_root=project_root,
        plugin_id=plugin_id,
        kind=GAMEPLAY_PLUGIN_MATERIALIZATION_KIND,
        status=GAMEPLAY_PLUGIN_MATERIALIZATION_STATUS_REJECTED,
        target_layer=GAMEPLAY_PLUGIN_IMPORT_TARGET_LAYER,
        runtime_status=GAMEPLAY_PLUGIN_MATERIALIZATION_RUNTIME_STATUS,
        activated=False,
        runtime_loaded=False,
        project_json_modified=False,
        package_root=None,
        generated_root=None,
        relative_generated_root="",
        materialized_files=tuple(),
        receipt_path="",
        rollback_path="",
        rollback_supported=False,
        rollback_mode="",
        problems=tuple(dict.fromkeys(str(problem) for problem in problems if str(problem))),
    )


def _safe_plugin_path_component(plugin_id: str) -> str:
    safe = "".join(character if character.isalnum() or character in "._-" else "-" for character in plugin_id)
    return safe.strip(".-_") or "plugin"


def _relative_posix(root: Path, child: Path) -> str:
    return child.resolve().relative_to(root.resolve()).as_posix()


def _is_safe_relative_posix_path(path: str) -> bool:
    if not path or "\\" in path or path.startswith("/"):
        return False
    parts = PurePosixPath(path).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        return False
    return True


def _safe_project_child(project_root: Path, relative_path: str) -> Path:
    if not _is_safe_relative_posix_path(relative_path):
        raise ValueError(f"unsafe gameplay plugin materialization path {relative_path!r}")
    resolved_root = project_root.resolve()
    child = (project_root / Path(*PurePosixPath(relative_path).parts)).resolve()
    if child != resolved_root and resolved_root not in child.parents:
        raise ValueError(f"gameplay plugin materialization path escapes project root: {relative_path!r}")
    return child


def _safe_package_child(package_root: Path, relative_path: str) -> Path:
    if not _is_safe_relative_posix_path(relative_path):
        raise ValueError(f"unsafe gameplay plugin source path {relative_path!r}")
    resolved_root = package_root.resolve()
    child = (package_root / Path(*PurePosixPath(relative_path).parts)).resolve()
    if child != resolved_root and resolved_root not in child.parents:
        raise ValueError(f"gameplay plugin source path escapes package root: {relative_path!r}")
    return child


def _generated_root_path(plugin_id: str) -> str:
    return PurePosixPath(
        "generated",
        "gameplay-plugins",
        _safe_plugin_path_component(plugin_id),
    ).as_posix()


def _metadata_path(plugin_id: str, file_name: str) -> str:
    return PurePosixPath(_generated_root_path(plugin_id), file_name).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_dumps(value: Mapping[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _operation_source_and_target(
    plan: GameplayPluginImportPlan,
    operation: GameplayPluginImportOperation,
) -> tuple[Path, Path]:
    if plan.package_root is None:
        raise ValueError(f"gameplay plugin {plan.plugin_id} package root is unavailable")
    source = _safe_package_child(plan.package_root, operation.source_path)
    target = _safe_project_child(plan.project_root, operation.target_path)
    generated_root = _safe_project_child(plan.project_root, _generated_root_path(plan.plugin_id))
    if generated_root != target and generated_root not in target.parents:
        raise ValueError(f"gameplay plugin target path is outside generated plugin layer: {operation.target_path!r}")
    return source, target


def _validate_materialization_targets(
    plan: GameplayPluginImportPlan,
    overwrite: bool,
) -> list[str]:
    problems: list[str] = []
    try:
        metadata_targets = [
            _safe_project_child(plan.project_root, _metadata_path(plan.plugin_id, GAMEPLAY_PLUGIN_MATERIALIZATION_RECEIPT_FILE)),
            _safe_project_child(plan.project_root, _metadata_path(plan.plugin_id, GAMEPLAY_PLUGIN_MATERIALIZATION_ROLLBACK_FILE)),
        ]
    except ValueError as exc:
        return [str(exc)]

    for operation in plan.operations:
        try:
            source, target = _operation_source_and_target(plan, operation)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if not source.exists():
            problems.append(f"gameplay plugin source file is missing: {operation.source_path}")
        if target.exists() and not overwrite:
            problems.append(f"generated gameplay plugin target already exists: {operation.target_path}")

    for target in metadata_targets:
        relative = _relative_posix(plan.project_root, target)
        if target.exists() and not overwrite:
            problems.append(f"generated gameplay plugin metadata already exists: {relative}")

    return problems


def _receipt_payload(
    plan: GameplayPluginImportPlan,
    materialized_files: Sequence[GameplayPluginMaterializedFile],
    receipt_path: str,
    rollback_path: str,
) -> dict[str, Any]:
    return {
        "schema": "game.gameplayPluginMaterializationReceipt.v1",
        "kind": GAMEPLAY_PLUGIN_MATERIALIZATION_KIND,
        "status": GAMEPLAY_PLUGIN_MATERIALIZATION_STATUS_MATERIALIZED,
        "runtimeStatus": GAMEPLAY_PLUGIN_MATERIALIZATION_RUNTIME_STATUS,
        "pluginId": plan.plugin_id,
        "targetLayer": plan.target_layer,
        "generatedRoot": _generated_root_path(plan.plugin_id),
        "receiptPath": receipt_path,
        "rollbackPath": rollback_path,
        "activated": False,
        "runtimeLoaded": False,
        "projectJsonModified": False,
        "entryPoints": list(plan.entry_points),
        "scenarios": list(plan.scenario_ids),
        "encounters": list(plan.encounter_ids),
        "receipts": list(plan.receipt_ids),
        "consequences": list(plan.consequence_ids),
        "files": [
            {
                "contentKind": file.content_kind,
                "contentId": file.content_id,
                "sourcePath": file.source_path,
                "targetPath": file.target_path,
                "bytesWritten": file.bytes_written,
                "sha256": file.sha256,
            }
            for file in materialized_files
        ],
        "rollback": {
            "supported": plan.rollback_supported,
            "mode": plan.rollback_mode,
            "disablePluginId": plan.plugin_id,
        },
        "provenance": dict(plan.provenance),
    }


def _rollback_payload(
    plan: GameplayPluginImportPlan,
    materialized_files: Sequence[GameplayPluginMaterializedFile],
    receipt_path: str,
    rollback_path: str,
) -> dict[str, Any]:
    return {
        "schema": "game.gameplayPluginRollbackPlan.v1",
        "kind": "gameplay-plugin-rollback-plan",
        "pluginId": plan.plugin_id,
        "targetLayer": plan.target_layer,
        "receiptPath": receipt_path,
        "rollbackPath": rollback_path,
        "supported": plan.rollback_supported,
        "mode": plan.rollback_mode or "disable-plugin",
        "runtimeSafe": True,
        "activated": False,
        "runtimeLoaded": False,
        "projectJsonModified": False,
        "disablePluginId": plan.plugin_id,
        "deleteGeneratedFiles": [file.target_path for file in materialized_files],
        "deleteMetadataFiles": [receipt_path, rollback_path],
        "preserveBaseContent": True,
        "preserveRuntimeState": True,
    }


def materialize_gameplay_plugin_generated_content(
    project_root: str | Path,
    plugin_id: str,
    *,
    overwrite: bool = False,
) -> GameplayPluginMaterializationResult:
    """Materialize validated plugin content into a generated-content layer.

    This is the first filesystem-writing step in the gameplay plugin pipeline,
    but it remains non-runtime: no project manifest is edited, no game state is
    mutated, and no generated scenario or encounter is activated.
    """

    root = Path(project_root)
    requested_plugin_id = str(plugin_id or "").strip()
    plan = build_gameplay_plugin_import_plan(root, requested_plugin_id)
    if not plan.valid:
        return _empty_result(root, requested_plugin_id, plan.problems or ("gameplay plugin import plan is invalid",))

    problems = _validate_materialization_targets(plan, overwrite=overwrite)
    if problems:
        return _empty_result(root, requested_plugin_id, problems)

    materialized_files: list[GameplayPluginMaterializedFile] = []
    for operation in plan.operations:
        source, target = _operation_source_and_target(plan, operation)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        materialized_files.append(
            GameplayPluginMaterializedFile(
                content_kind=operation.content_kind,
                content_id=operation.content_id,
                source_path=operation.source_path,
                target_path=operation.target_path,
                bytes_written=target.stat().st_size,
                sha256=_sha256(target),
            )
        )

    receipt_path = _metadata_path(requested_plugin_id, GAMEPLAY_PLUGIN_MATERIALIZATION_RECEIPT_FILE)
    rollback_path = _metadata_path(requested_plugin_id, GAMEPLAY_PLUGIN_MATERIALIZATION_ROLLBACK_FILE)

    receipt_target = _safe_project_child(root, receipt_path)
    rollback_target = _safe_project_child(root, rollback_path)
    receipt_target.parent.mkdir(parents=True, exist_ok=True)
    receipt_target.write_text(
        _json_dumps(_receipt_payload(plan, materialized_files, receipt_path, rollback_path)),
        encoding="utf-8",
    )
    rollback_target.write_text(
        _json_dumps(_rollback_payload(plan, materialized_files, receipt_path, rollback_path)),
        encoding="utf-8",
    )

    generated_root_path = _safe_project_child(root, _generated_root_path(requested_plugin_id))
    return GameplayPluginMaterializationResult(
        project_root=root,
        plugin_id=requested_plugin_id,
        kind=GAMEPLAY_PLUGIN_MATERIALIZATION_KIND,
        status=GAMEPLAY_PLUGIN_MATERIALIZATION_STATUS_MATERIALIZED,
        target_layer=plan.target_layer,
        runtime_status=GAMEPLAY_PLUGIN_MATERIALIZATION_RUNTIME_STATUS,
        activated=False,
        runtime_loaded=False,
        project_json_modified=False,
        package_root=plan.package_root,
        generated_root=generated_root_path,
        relative_generated_root=_relative_posix(root, generated_root_path),
        materialized_files=tuple(materialized_files),
        receipt_path=receipt_path,
        rollback_path=rollback_path,
        rollback_supported=plan.rollback_supported,
        rollback_mode=plan.rollback_mode,
        problems=tuple(),
    )


def validate_gameplay_plugin_materialization(
    project_root: str | Path,
    plugin_id: str,
    *,
    overwrite: bool = False,
) -> list[str]:
    """Return materialization problems without writing generated files."""

    root = Path(project_root)
    requested_plugin_id = str(plugin_id or "").strip()
    plan = build_gameplay_plugin_import_plan(root, requested_plugin_id)
    if not plan.valid:
        return list(plan.problems or ("gameplay plugin import plan is invalid",))
    return _validate_materialization_targets(plan, overwrite=overwrite)


def assert_valid_gameplay_plugin_materialization(
    project_root: str | Path,
    plugin_id: str,
    *,
    overwrite: bool = False,
) -> GameplayPluginMaterializationResult:
    """Materialize a valid plugin or raise with actionable problems."""

    result = materialize_gameplay_plugin_generated_content(
        project_root,
        plugin_id,
        overwrite=overwrite,
    )
    if result.problems or not result.valid:
        raise ValueError("Invalid gameplay plugin materialization:\n- " + "\n- ".join(result.problems))
    return result
