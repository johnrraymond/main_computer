from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from typing import Any

from main_computer.gameplay_plugin_materializer import (
    GAMEPLAY_PLUGIN_MATERIALIZATION_RECEIPT_FILE,
    GAMEPLAY_PLUGIN_MATERIALIZATION_ROLLBACK_FILE,
    GAMEPLAY_PLUGIN_MATERIALIZATION_STATUS_MATERIALIZED,
)


GAMEPLAY_PLUGIN_ACTIVATION_KIND = "gameplay-plugin-activation-manifest"
GAMEPLAY_PLUGIN_ACTIVATION_SCHEMA = "game.gameplayPluginActivationManifest.v1"
GAMEPLAY_PLUGIN_ACTIVATION_STATUS_ENABLED = "enabled"
GAMEPLAY_PLUGIN_ACTIVATION_STATUS_DISABLED = "disabled"
GAMEPLAY_PLUGIN_ACTIVATION_STATUS_REJECTED = "rejected"
GAMEPLAY_PLUGIN_ACTIVATION_RUNTIME_STATUS = "filesystem-metadata-only"
GAMEPLAY_PLUGIN_ACTIVATION_MANIFEST_PATH = "generated/gameplay-plugins/activation-manifest.json"


@dataclass(frozen=True)
class GameplayPluginActivationEntry:
    """One project-local generated gameplay plugin activation record.

    Activation entries are metadata only. They mark materialized generated
    content as enabled/disabled for a future runtime loader, but they do not load
    browser code, edit project.json, mutate saves, or execute plugin content.
    """

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
    runtime_loaded: bool
    project_json_modified: bool
    activated_in_runtime: bool

    @property
    def enabled(self) -> bool:
        return self.status == GAMEPLAY_PLUGIN_ACTIVATION_STATUS_ENABLED

    @property
    def disabled(self) -> bool:
        return self.status == GAMEPLAY_PLUGIN_ACTIVATION_STATUS_DISABLED

    @property
    def safe(self) -> bool:
        return (
            self.runtime_loaded is False
            and self.project_json_modified is False
            and self.activated_in_runtime is False
        )


@dataclass(frozen=True)
class GameplayPluginActivationManifest:
    """Project-local activation metadata for materialized gameplay plugins."""

    project_root: Path
    manifest_path: Path
    relative_manifest_path: str
    schema: str
    kind: str
    runtime_status: str
    runtime_loaded: bool
    project_json_modified: bool
    entries: tuple[GameplayPluginActivationEntry, ...]
    problems: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return (
            self.schema == GAMEPLAY_PLUGIN_ACTIVATION_SCHEMA
            and self.kind == GAMEPLAY_PLUGIN_ACTIVATION_KIND
            and self.runtime_status == GAMEPLAY_PLUGIN_ACTIVATION_RUNTIME_STATUS
            and self.runtime_loaded is False
            and self.project_json_modified is False
            and not self.problems
            and all(entry.safe for entry in self.entries)
        )

    @property
    def enabled_plugin_ids(self) -> tuple[str, ...]:
        return tuple(entry.plugin_id for entry in self.entries if entry.enabled)

    @property
    def disabled_plugin_ids(self) -> tuple[str, ...]:
        return tuple(entry.plugin_id for entry in self.entries if entry.disabled)

    def entry_for_plugin_id(self, plugin_id: str) -> GameplayPluginActivationEntry | None:
        requested = str(plugin_id or "").strip()
        for entry in self.entries:
            if entry.plugin_id == requested:
                return entry
        return None


@dataclass(frozen=True)
class GameplayPluginActivationResult:
    """Result of enabling or disabling one materialized generated gameplay plugin."""

    project_root: Path
    plugin_id: str
    kind: str
    status: str
    manifest_path: str
    entry: GameplayPluginActivationEntry | None
    manifest: GameplayPluginActivationManifest
    runtime_status: str
    runtime_loaded: bool
    project_json_modified: bool
    activated_in_runtime: bool
    problems: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return (
            self.status in (
                GAMEPLAY_PLUGIN_ACTIVATION_STATUS_ENABLED,
                GAMEPLAY_PLUGIN_ACTIVATION_STATUS_DISABLED,
            )
            and self.entry is not None
            and self.manifest.valid
            and self.runtime_status == GAMEPLAY_PLUGIN_ACTIVATION_RUNTIME_STATUS
            and self.runtime_loaded is False
            and self.project_json_modified is False
            and self.activated_in_runtime is False
            and not self.problems
        )

    @property
    def rejected(self) -> bool:
        return self.status == GAMEPLAY_PLUGIN_ACTIVATION_STATUS_REJECTED


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return tuple()
    return tuple(str(item or "").strip() for item in value if str(item or "").strip())


def _unique_strings(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _safe_plugin_path_component(plugin_id: str) -> str:
    safe = "".join(character if character.isalnum() or character in "._-" else "-" for character in plugin_id)
    return safe.strip(".-_") or "plugin"


def _is_safe_relative_posix_path(path: str) -> bool:
    if not path or "\\" in path or path.startswith("/"):
        return False
    parts = PurePosixPath(path).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        return False
    return True


def _safe_project_child(project_root: Path, relative_path: str) -> Path:
    if not _is_safe_relative_posix_path(relative_path):
        raise ValueError(f"unsafe gameplay plugin activation path {relative_path!r}")
    resolved_root = project_root.resolve()
    child = (project_root / Path(*PurePosixPath(relative_path).parts)).resolve()
    if child != resolved_root and resolved_root not in child.parents:
        raise ValueError(f"gameplay plugin activation path escapes project root: {relative_path!r}")
    return child


def _relative_posix(root: Path, child: Path) -> str:
    return child.resolve().relative_to(root.resolve()).as_posix()


def _generated_root_path(plugin_id: str) -> str:
    return PurePosixPath(
        "generated",
        "gameplay-plugins",
        _safe_plugin_path_component(plugin_id),
    ).as_posix()


def _receipt_path(plugin_id: str) -> str:
    return PurePosixPath(_generated_root_path(plugin_id), GAMEPLAY_PLUGIN_MATERIALIZATION_RECEIPT_FILE).as_posix()


def _rollback_path(plugin_id: str) -> str:
    return PurePosixPath(_generated_root_path(plugin_id), GAMEPLAY_PLUGIN_MATERIALIZATION_ROLLBACK_FILE).as_posix()


def _activation_manifest_path(project_root: Path) -> Path:
    return _safe_project_child(project_root, GAMEPLAY_PLUGIN_ACTIVATION_MANIFEST_PATH)


def _load_json(path: Path) -> tuple[Mapping[str, Any] | None, tuple[str, ...]]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, (f"gameplay plugin activation file is missing: {_relative_posix(path.parent, path)}",)
    except json.JSONDecodeError as exc:
        return None, (f"gameplay plugin activation JSON is invalid at {path}: {exc}",)
    if not isinstance(loaded, Mapping):
        return None, (f"gameplay plugin activation JSON must be an object: {path}",)
    return loaded, tuple()


def _json_dumps(value: Mapping[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _entry_payload(entry: GameplayPluginActivationEntry) -> dict[str, Any]:
    return {
        "pluginId": entry.plugin_id,
        "status": entry.status,
        "generatedRoot": entry.generated_root,
        "receiptPath": entry.receipt_path,
        "rollbackPath": entry.rollback_path,
        "targetLayer": entry.target_layer,
        "entryPoints": list(entry.entry_points),
        "scenarios": list(entry.scenarios),
        "encounters": list(entry.encounters),
        "receipts": list(entry.receipts),
        "consequences": list(entry.consequences),
        "runtimeLoaded": entry.runtime_loaded,
        "projectJsonModified": entry.project_json_modified,
        "activatedInRuntime": entry.activated_in_runtime,
    }


def _manifest_payload(manifest: GameplayPluginActivationManifest) -> dict[str, Any]:
    return {
        "schema": GAMEPLAY_PLUGIN_ACTIVATION_SCHEMA,
        "kind": GAMEPLAY_PLUGIN_ACTIVATION_KIND,
        "runtimeStatus": GAMEPLAY_PLUGIN_ACTIVATION_RUNTIME_STATUS,
        "runtimeLoaded": False,
        "projectJsonModified": False,
        "entries": [_entry_payload(entry) for entry in manifest.entries],
    }


def _entry_from_payload(value: Mapping[str, Any]) -> GameplayPluginActivationEntry:
    return GameplayPluginActivationEntry(
        plugin_id=str(value.get("pluginId") or "").strip(),
        status=str(value.get("status") or "").strip(),
        generated_root=str(value.get("generatedRoot") or "").strip(),
        receipt_path=str(value.get("receiptPath") or "").strip(),
        rollback_path=str(value.get("rollbackPath") or "").strip(),
        target_layer=str(value.get("targetLayer") or "").strip(),
        entry_points=_strings(value.get("entryPoints")),
        scenarios=_strings(value.get("scenarios")),
        encounters=_strings(value.get("encounters")),
        receipts=_strings(value.get("receipts")),
        consequences=_strings(value.get("consequences")),
        runtime_loaded=bool(value.get("runtimeLoaded")),
        project_json_modified=bool(value.get("projectJsonModified")),
        activated_in_runtime=bool(value.get("activatedInRuntime")),
    )


def _empty_manifest(project_root: Path, problems: Sequence[str] = ()) -> GameplayPluginActivationManifest:
    manifest_path = _activation_manifest_path(project_root)
    return GameplayPluginActivationManifest(
        project_root=project_root,
        manifest_path=manifest_path,
        relative_manifest_path=GAMEPLAY_PLUGIN_ACTIVATION_MANIFEST_PATH,
        schema=GAMEPLAY_PLUGIN_ACTIVATION_SCHEMA,
        kind=GAMEPLAY_PLUGIN_ACTIVATION_KIND,
        runtime_status=GAMEPLAY_PLUGIN_ACTIVATION_RUNTIME_STATUS,
        runtime_loaded=False,
        project_json_modified=False,
        entries=tuple(),
        problems=tuple(dict.fromkeys(str(problem) for problem in problems if str(problem))),
    )


def read_gameplay_plugin_activation_manifest(project_root: str | Path) -> GameplayPluginActivationManifest:
    """Read the project-local generated plugin activation manifest.

    A missing activation manifest is a valid empty manifest. Reading the manifest
    does not validate materialized plugin files again and does not activate
    runtime content.
    """

    root = Path(project_root)
    manifest_path = _activation_manifest_path(root)
    if not manifest_path.exists():
        return _empty_manifest(root)

    payload, problems = _load_json(manifest_path)
    if payload is None:
        return _empty_manifest(root, problems)

    entries_payload = payload.get("entries")
    entries = tuple(
        _entry_from_payload(item)
        for item in entries_payload
        if isinstance(item, Mapping)
    ) if isinstance(entries_payload, list) else tuple()

    manifest_problems: list[str] = []
    if payload.get("schema") != GAMEPLAY_PLUGIN_ACTIVATION_SCHEMA:
        manifest_problems.append("gameplay plugin activation manifest schema is unsupported")
    if payload.get("kind") != GAMEPLAY_PLUGIN_ACTIVATION_KIND:
        manifest_problems.append("gameplay plugin activation manifest kind is unsupported")
    if payload.get("runtimeStatus") != GAMEPLAY_PLUGIN_ACTIVATION_RUNTIME_STATUS:
        manifest_problems.append("gameplay plugin activation manifest runtime status must be filesystem metadata only")
    if payload.get("runtimeLoaded") is not False:
        manifest_problems.append("gameplay plugin activation manifest must not claim runtime loading")
    if payload.get("projectJsonModified") is not False:
        manifest_problems.append("gameplay plugin activation manifest must not claim project.json mutation")
    if not isinstance(entries_payload, list):
        manifest_problems.append("gameplay plugin activation manifest entries must be a list")

    seen: set[str] = set()
    duplicate_ids: set[str] = set()
    for entry in entries:
        if not entry.plugin_id:
            manifest_problems.append("gameplay plugin activation entry is missing pluginId")
        elif entry.plugin_id in seen:
            duplicate_ids.add(entry.plugin_id)
        seen.add(entry.plugin_id)
        if entry.status not in (GAMEPLAY_PLUGIN_ACTIVATION_STATUS_ENABLED, GAMEPLAY_PLUGIN_ACTIVATION_STATUS_DISABLED):
            manifest_problems.append(f"gameplay plugin activation entry has unsupported status: {entry.status}")
        if not entry.safe:
            manifest_problems.append(f"gameplay plugin activation entry must remain metadata-only: {entry.plugin_id}")
        if entry.generated_root and not _is_safe_relative_posix_path(entry.generated_root):
            manifest_problems.append(f"gameplay plugin activation entry has unsafe generated root: {entry.generated_root!r}")
        if entry.receipt_path and not _is_safe_relative_posix_path(entry.receipt_path):
            manifest_problems.append(f"gameplay plugin activation entry has unsafe receipt path: {entry.receipt_path!r}")
        if entry.rollback_path and not _is_safe_relative_posix_path(entry.rollback_path):
            manifest_problems.append(f"gameplay plugin activation entry has unsafe rollback path: {entry.rollback_path!r}")

    for plugin_id in sorted(duplicate_ids):
        manifest_problems.append(f"duplicate gameplay plugin activation entry: {plugin_id}")

    return GameplayPluginActivationManifest(
        project_root=root,
        manifest_path=manifest_path,
        relative_manifest_path=GAMEPLAY_PLUGIN_ACTIVATION_MANIFEST_PATH,
        schema=str(payload.get("schema") or ""),
        kind=str(payload.get("kind") or ""),
        runtime_status=str(payload.get("runtimeStatus") or ""),
        runtime_loaded=bool(payload.get("runtimeLoaded")),
        project_json_modified=bool(payload.get("projectJsonModified")),
        entries=entries,
        problems=tuple(dict.fromkeys([*problems, *manifest_problems])),
    )


def _validate_materialized_plugin(project_root: Path, plugin_id: str) -> tuple[Mapping[str, Any] | None, tuple[str, ...]]:
    requested_plugin_id = str(plugin_id or "").strip()
    if not requested_plugin_id:
        return None, ("gameplay plugin id is required for activation",)

    expected_generated_root = _generated_root_path(requested_plugin_id)
    expected_receipt_path = _receipt_path(requested_plugin_id)
    expected_rollback_path = _rollback_path(requested_plugin_id)

    try:
        receipt_file = _safe_project_child(project_root, expected_receipt_path)
        rollback_file = _safe_project_child(project_root, expected_rollback_path)
        generated_root = _safe_project_child(project_root, expected_generated_root)
    except ValueError as exc:
        return None, (str(exc),)

    if not receipt_file.exists():
        return None, (f"gameplay plugin is not materialized; missing receipt: {expected_receipt_path}",)
    if not rollback_file.exists():
        return None, (f"gameplay plugin is not materialized; missing rollback plan: {expected_rollback_path}",)

    receipt, receipt_problems = _load_json(receipt_file)
    if receipt is None:
        return None, receipt_problems

    problems: list[str] = list(receipt_problems)
    if receipt.get("pluginId") != requested_plugin_id:
        problems.append(f"materialization receipt plugin id does not match requested plugin: {requested_plugin_id}")
    if receipt.get("status") != GAMEPLAY_PLUGIN_MATERIALIZATION_STATUS_MATERIALIZED:
        problems.append("materialization receipt is not materialized")
    if receipt.get("activated") is not False:
        problems.append("materialization receipt must not already claim activation")
    if receipt.get("runtimeLoaded") is not False:
        problems.append("materialization receipt must not claim runtime loading")
    if receipt.get("projectJsonModified") is not False:
        problems.append("materialization receipt must not claim project.json mutation")
    if receipt.get("generatedRoot") != expected_generated_root:
        problems.append("materialization receipt generated root does not match expected plugin layer")
    if receipt.get("receiptPath") != expected_receipt_path:
        problems.append("materialization receipt path does not match expected plugin layer")
    if receipt.get("rollbackPath") != expected_rollback_path:
        problems.append("materialization receipt rollback path does not match expected plugin layer")
    if not generated_root.exists():
        problems.append(f"gameplay plugin generated root is missing: {expected_generated_root}")

    files = receipt.get("files")
    if not isinstance(files, list) or not files:
        problems.append("materialization receipt must list generated content files")
    else:
        for file_record in files:
            if not isinstance(file_record, Mapping):
                problems.append("materialization receipt file record must be an object")
                continue
            target_path = str(file_record.get("targetPath") or "").strip()
            if not _is_safe_relative_posix_path(target_path):
                problems.append(f"materialization receipt has unsafe generated file path: {target_path!r}")
                continue
            try:
                target_file = _safe_project_child(project_root, target_path)
            except ValueError as exc:
                problems.append(str(exc))
                continue
            if generated_root != target_file and generated_root not in target_file.parents:
                problems.append(f"materialized file is outside generated plugin root: {target_path}")
            if not target_file.exists():
                problems.append(f"materialized file is missing: {target_path}")

    return receipt, tuple(dict.fromkeys(problems))


def _entry_from_materialization_receipt(
    plugin_id: str,
    status: str,
    receipt: Mapping[str, Any],
) -> GameplayPluginActivationEntry:
    rollback = receipt.get("rollback") if isinstance(receipt.get("rollback"), Mapping) else {}
    return GameplayPluginActivationEntry(
        plugin_id=plugin_id,
        status=status,
        generated_root=str(receipt.get("generatedRoot") or _generated_root_path(plugin_id)),
        receipt_path=str(receipt.get("receiptPath") or _receipt_path(plugin_id)),
        rollback_path=str(receipt.get("rollbackPath") or _rollback_path(plugin_id)),
        target_layer=str(receipt.get("targetLayer") or ""),
        entry_points=_strings(receipt.get("entryPoints")),
        scenarios=_strings(receipt.get("scenarios")),
        encounters=_strings(receipt.get("encounters")),
        receipts=_strings(receipt.get("receipts")),
        consequences=_strings(receipt.get("consequences")),
        runtime_loaded=False,
        project_json_modified=False,
        activated_in_runtime=False,
    )


def _write_manifest(project_root: Path, entries: Sequence[GameplayPluginActivationEntry]) -> GameplayPluginActivationManifest:
    manifest = GameplayPluginActivationManifest(
        project_root=project_root,
        manifest_path=_activation_manifest_path(project_root),
        relative_manifest_path=GAMEPLAY_PLUGIN_ACTIVATION_MANIFEST_PATH,
        schema=GAMEPLAY_PLUGIN_ACTIVATION_SCHEMA,
        kind=GAMEPLAY_PLUGIN_ACTIVATION_KIND,
        runtime_status=GAMEPLAY_PLUGIN_ACTIVATION_RUNTIME_STATUS,
        runtime_loaded=False,
        project_json_modified=False,
        entries=tuple(entries),
        problems=tuple(),
    )
    manifest.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest.manifest_path.write_text(_json_dumps(_manifest_payload(manifest)), encoding="utf-8")
    return read_gameplay_plugin_activation_manifest(project_root)


def _replace_entry(
    entries: Sequence[GameplayPluginActivationEntry],
    replacement: GameplayPluginActivationEntry,
) -> tuple[GameplayPluginActivationEntry, ...]:
    output: list[GameplayPluginActivationEntry] = []
    replaced = False
    for entry in entries:
        if entry.plugin_id == replacement.plugin_id:
            output.append(replacement)
            replaced = True
        else:
            output.append(entry)
    if not replaced:
        output.append(replacement)
    return tuple(sorted(output, key=lambda entry: entry.plugin_id))


def set_gameplay_plugin_activation(
    project_root: str | Path,
    plugin_id: str,
    *,
    enabled: bool,
) -> GameplayPluginActivationResult:
    """Enable or disable a materialized generated gameplay plugin.

    This writes only a project-local activation manifest. It does not copy
    content, edit project.json, load browser/runtime state, execute gameplay, or
    mutate saves.
    """

    root = Path(project_root)
    requested_plugin_id = str(plugin_id or "").strip()
    existing_manifest = read_gameplay_plugin_activation_manifest(root)
    if existing_manifest.problems:
        return _rejected_activation(root, requested_plugin_id, existing_manifest.problems, existing_manifest)

    receipt, materialization_problems = _validate_materialized_plugin(root, requested_plugin_id)
    if receipt is None or materialization_problems:
        return _rejected_activation(root, requested_plugin_id, materialization_problems, existing_manifest)

    status = GAMEPLAY_PLUGIN_ACTIVATION_STATUS_ENABLED if enabled else GAMEPLAY_PLUGIN_ACTIVATION_STATUS_DISABLED
    entry = _entry_from_materialization_receipt(requested_plugin_id, status, receipt)
    updated_entries = _replace_entry(existing_manifest.entries, entry)
    updated_manifest = _write_manifest(root, updated_entries)

    if updated_manifest.problems:
        return _rejected_activation(root, requested_plugin_id, updated_manifest.problems, updated_manifest)

    written_entry = updated_manifest.entry_for_plugin_id(requested_plugin_id)
    return GameplayPluginActivationResult(
        project_root=root,
        plugin_id=requested_plugin_id,
        kind=GAMEPLAY_PLUGIN_ACTIVATION_KIND,
        status=status,
        manifest_path=updated_manifest.relative_manifest_path,
        entry=written_entry,
        manifest=updated_manifest,
        runtime_status=GAMEPLAY_PLUGIN_ACTIVATION_RUNTIME_STATUS,
        runtime_loaded=False,
        project_json_modified=False,
        activated_in_runtime=False,
        problems=tuple(),
    )


def _rejected_activation(
    project_root: Path,
    plugin_id: str,
    problems: Sequence[str],
    manifest: GameplayPluginActivationManifest | None = None,
) -> GameplayPluginActivationResult:
    existing_manifest = manifest if manifest is not None else read_gameplay_plugin_activation_manifest(project_root)
    return GameplayPluginActivationResult(
        project_root=project_root,
        plugin_id=plugin_id,
        kind=GAMEPLAY_PLUGIN_ACTIVATION_KIND,
        status=GAMEPLAY_PLUGIN_ACTIVATION_STATUS_REJECTED,
        manifest_path=GAMEPLAY_PLUGIN_ACTIVATION_MANIFEST_PATH,
        entry=None,
        manifest=existing_manifest,
        runtime_status=GAMEPLAY_PLUGIN_ACTIVATION_RUNTIME_STATUS,
        runtime_loaded=False,
        project_json_modified=False,
        activated_in_runtime=False,
        problems=tuple(dict.fromkeys(str(problem) for problem in problems if str(problem))),
    )


def enable_gameplay_plugin(project_root: str | Path, plugin_id: str) -> GameplayPluginActivationResult:
    """Mark a materialized gameplay plugin enabled in filesystem metadata only."""

    return set_gameplay_plugin_activation(project_root, plugin_id, enabled=True)


def disable_gameplay_plugin(project_root: str | Path, plugin_id: str) -> GameplayPluginActivationResult:
    """Mark a materialized gameplay plugin disabled in filesystem metadata only."""

    return set_gameplay_plugin_activation(project_root, plugin_id, enabled=False)


def validate_gameplay_plugin_activation(
    project_root: str | Path,
    plugin_id: str,
) -> list[str]:
    """Return activation problems without writing the activation manifest."""

    root = Path(project_root)
    manifest = read_gameplay_plugin_activation_manifest(root)
    problems: list[str] = list(manifest.problems)
    _receipt, materialization_problems = _validate_materialized_plugin(root, str(plugin_id or "").strip())
    problems.extend(materialization_problems)
    return list(dict.fromkeys(problems))


def assert_gameplay_plugin_activation_enabled(
    project_root: str | Path,
    plugin_id: str,
) -> GameplayPluginActivationResult:
    """Enable a materialized plugin or raise with actionable problems."""

    result = enable_gameplay_plugin(project_root, plugin_id)
    if result.problems or not result.valid:
        raise ValueError("Invalid gameplay plugin activation:\n- " + "\n- ".join(result.problems))
    return result


def assert_gameplay_plugin_activation_disabled(
    project_root: str | Path,
    plugin_id: str,
) -> GameplayPluginActivationResult:
    """Disable a materialized plugin or raise with actionable problems."""

    result = disable_gameplay_plugin(project_root, plugin_id)
    if result.problems or not result.valid:
        raise ValueError("Invalid gameplay plugin deactivation:\n- " + "\n- ".join(result.problems))
    return result
