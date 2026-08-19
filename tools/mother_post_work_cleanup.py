#!/usr/bin/env python3
"""Final post-work cleanup for completed Mother add/remove helper containers.

This script is intentionally separate from ``mother_deploy.py``.  It is meant to
be called manually or by a harness *after* add-node or remove-node has already
written clean completion/topology evidence.  It does not decide whether add or
remove succeeded; it only removes/excludes known one-shot Mother helpers that can
otherwise keep the parent Coolify service unhealthy after the real work is done.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable, Mapping
import urllib.request

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.coolify_state import resolve_coolify_controller
from tools.mother.common.deployment_completed_helper_cleanup import (
    DEFAULT_REQUIRED_COMPONENT_NAMES,
    MotherDeploymentCompletedHelperCleanupError,
    _compose_text_candidates_from_service_payload,
    _delete_application,
    _delete_service_application_with_fallbacks,
    _http,
    _patch_application_status_exclusion_with_fallbacks,
    _patch_service_compose,
    _patch_service_compose_reconcile,
    _remove_completed_helpers_from_compose,
    _request_service_redeploy_refresh,
    _service_detail,
)
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import PrivateStateReadResult, read_private_state


_KIND = "main_computer.mother.post_work_completed_helper_cleanup.v1"
_EVIDENCE_SUBDIR = "post-work-completed-helper-cleanup"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_UUID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")

POST_WORK_HELPER_NAMES = frozenset(
    {
        "mother-genesis-init",
        "mother-genesis-proof-guardian",
        "mother-replica-init",
        "mother-replica-sync-guardian",
        "mother-superseded-service-cleanup",
        "mother-validator-admission-guardian",
        "mother-validator-activation-init",
        "mother-add-node-validator-activation-guardian",
    }
)

POST_WORK_HELPER_PREFIXES = (
    "mother-add-node-validator-admission-voter-",
    "mother-node-remove-voter-",
)

# These transient helpers have repeatedly shown up as remote Docker orphans
# after Coolify model/Compose cleanup.  Coolify service-detail payloads cannot
# prove that their concrete containers are absent on the host, so cleanup emits
# idempotent operator prune commands for these families instead of silently
# certifying the stack clean.
OPERATOR_HOST_PRUNE_HELPER_NAMES = frozenset(
    {
        "mother-validator-activation-init",
        "mother-add-node-validator-activation-guardian",
        "mother-replica-sync-guardian",
    }
)
OPERATOR_HOST_PRUNE_HELPER_PREFIXES = (
    "mother-add-node-validator-admission-voter-",
    "mother-node-remove-voter-",
)

RETIRED_GENESIS_PROOF_GUARDIAN_SHIM_NAME = "mother-genesis-proof-guardian"
RETIRED_GENESIS_PROOF_GUARDIAN_SHIM_IMAGE = "alpine:3.20"
RETIRED_GENESIS_PROOF_GUARDIAN_SHIM_LABEL = "main_computer.mother.post_work_shim"

PRESERVED_HELPER_NAMES = frozenset(
    {
        "mother-validator-quorum-recovery-initial-guardian",
    }
)

DEFAULT_POST_WORK_REQUIRED_COMPONENT_NAMES = DEFAULT_REQUIRED_COMPONENT_NAMES


class MotherPostWorkCleanupError(RuntimeError):
    """Post-work helper cleanup failed before a trustworthy result could be produced."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


ProgressCallback = Callable[[str, str, Mapping[str, Any]], None]


def _progress_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    text = str(value)
    if not text:
        return '\"\"'
    if re.search(r"\s", text):
        return json.dumps(text, sort_keys=True)
    return text


def _emit_progress(
    progress: ProgressCallback | None,
    phase: str,
    message: str,
    **details: Any,
) -> None:
    if progress is None:
        return
    progress(phase, message, details)


def _stderr_progress(phase: str, message: str, details: Mapping[str, Any]) -> None:
    suffix = ""
    if details:
        suffix = " " + " ".join(
            f"{key}={_progress_value(value)}" for key, value in sorted(details.items())
        )
    print(f"{_utc_now()} mother-post-work-cleanup.{phase} {message}{suffix}", file=sys.stderr, flush=True)


def _identifier(value: object, field: str) -> str:
    if type(value) is not str or not value.strip() or not _IDENTIFIER_RE.fullmatch(value.strip()):
        raise MotherPostWorkCleanupError(
            "MOTHER_POST_WORK_CLEANUP_INVALID_ARGUMENT",
            f"invalid {field}",
        )
    return value.strip()


def _uuid(value: object, field: str) -> str:
    if type(value) is not str or not value.strip() or not _UUID_RE.fullmatch(value.strip()):
        raise MotherPostWorkCleanupError(
            "MOTHER_POST_WORK_CLEANUP_INVALID_ARGUMENT",
            f"invalid {field}",
        )
    return value.strip()


def _positive(value: float | int, field: str) -> float:
    if type(value) not in {int, float} or value <= 0:
        raise MotherPostWorkCleanupError(
            "MOTHER_POST_WORK_CLEANUP_INVALID_ARGUMENT",
            f"{field} must be positive",
        )
    return float(value)


def _nonnegative(value: float | int, field: str) -> float:
    if type(value) not in {int, float} or value < 0:
        raise MotherPostWorkCleanupError(
            "MOTHER_POST_WORK_CLEANUP_INVALID_ARGUMENT",
            f"{field} must be non-negative",
        )
    return float(value)


def _safe_scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return ""


def _status(value: object) -> str:
    return _safe_scalar(value).strip().lower()


def _healthy(status: str) -> bool:
    return "healthy" in status and "unhealthy" not in status


def _parent_degraded(status: str) -> bool:
    return bool(status) and not _healthy(status)


def _truthy_excluded(value: object) -> bool:
    if type(value) is bool:
        return value
    if type(value) is str:
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _labels(value: object) -> dict[str, str]:
    if isinstance(value, Mapping):
        return {str(key): _safe_scalar(item) for key, item in value.items()}
    if isinstance(value, list):
        labels: dict[str, str] = {}
        for item in value:
            if type(item) is not str:
                continue
            key, sep, raw_value = item.partition("=")
            if sep:
                labels[key.strip()] = raw_value.strip()
            elif key.strip():
                labels[key.strip()] = "true"
        return labels
    return {}


def _truthy_label(labels: Mapping[str, str], key: str) -> bool:
    value = labels.get(key)
    return type(value) is str and value.strip().lower() in {"1", "true", "yes", "y"}


def _is_retired_genesis_proof_guardian_shim_record(item: Mapping[str, Any]) -> bool:
    if item.get("name") != RETIRED_GENESIS_PROOF_GUARDIAN_SHIM_NAME:
        return False
    labels = _labels(item.get("labels"))
    status = _status(item.get("status"))
    if _truthy_label(labels, RETIRED_GENESIS_PROOF_GUARDIAN_SHIM_LABEL):
        return "unhealthy" not in status
    # Coolify service-detail payloads do not always echo Compose labels.  The
    # real genesis guardian uses python:3.12-alpine; the retired shim is the
    # deliberately tiny alpine:3.20 service installed by this script.
    return _safe_scalar(item.get("image")).strip() == RETIRED_GENESIS_PROOF_GUARDIAN_SHIM_IMAGE and _healthy(status)


def _is_retired_genesis_proof_guardian_shim_definition(name: str, definition: object) -> bool:
    if name != RETIRED_GENESIS_PROOF_GUARDIAN_SHIM_NAME or not isinstance(definition, Mapping):
        return False
    labels = _labels(definition.get("labels"))
    image = _safe_scalar(definition.get("image")).strip()
    return _truthy_label(labels, RETIRED_GENESIS_PROOF_GUARDIAN_SHIM_LABEL) or image == RETIRED_GENESIS_PROOF_GUARDIAN_SHIM_IMAGE


def _decode_coolify_compose_value(value: object) -> tuple[str, str] | None:
    if type(value) is not str or not value.strip():
        return None
    raw = value.strip()
    try:
        decoded = base64.b64decode(raw.encode("ascii"), validate=True).decode("utf-8")
        if "services:" in decoded or decoded.lstrip().startswith(("version:", "name:")):
            return decoded, "base64"
    except (binascii.Error, UnicodeDecodeError, ValueError):
        pass
    return raw, "plain"


def _all_compose_text_candidates_from_service_payload(payload: Any) -> tuple[tuple[str, str, str], ...]:
    """Return every Compose representation exposed by Coolify for safety checks.

    Writers should still round-trip only Coolify's raw/source Compose.  Safety
    checks must inspect raw and rendered Compose because remove-node's stale
    add-node-voter guard reads the service record it receives from Coolify; a
    helper left in a rendered Compose field can still block the next lifecycle
    mutation even when applications are excluded from status.
    """

    if not isinstance(payload, Mapping):
        return ()
    candidates: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for field in ("docker_compose_raw", "docker_compose", "dockerComposeRaw", "dockerCompose"):
        decoded = _decode_coolify_compose_value(payload.get(field))
        if decoded is None:
            continue
        compose_text, encoding = decoded
        digest = hashlib.sha256(compose_text.encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        candidates.append((compose_text, field, encoding))
    return tuple(candidates)


def _compose_service_names_from_service_payload(payload: Any) -> set[str]:
    """Return service names declared by any Coolify Compose view.

    Coolify service details may contain both editable/raw and rendered Compose
    fields.  Post-work cleanup uses this only to decide whether optional
    components such as Hub/FDB are actually part of the inspected service; a
    missing optional component that is not declared in Compose should not block
    cleanup of stale completed helpers.
    """

    names: set[str] = set()
    for compose_text, _source_field, _source_encoding in _all_compose_text_candidates_from_service_payload(payload):
        try:
            document = yaml.safe_load(compose_text)
        except yaml.YAMLError:
            continue
        if not isinstance(document, Mapping) or not isinstance(document.get("services"), Mapping):
            continue
        for service_name in document["services"]:
            if type(service_name) is str and service_name.strip():
                names.add(service_name.strip())
    return names


def _effective_required_component_names(
    *,
    node_name: str,
    configured_required_component_names: tuple[str, ...],
    application_names: set[str],
    compose_service_names: set[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return (required, optional_absent) component names for this service.

    The node's primary container is always required.  Additional configured
    components are required only when this Coolify service actually exposes
    them either as child applications or in Compose.  This keeps cleanup from
    deadlocking on services, such as add-node standby shells, that legitimately
    do not include Hub/FDB while stale post-work helpers still need removal.
    """

    required: list[str] = [node_name]
    optional_absent: list[str] = []
    for name in configured_required_component_names:
        if name == node_name:
            continue
        if name in application_names or name in compose_service_names:
            required.append(name)
        else:
            optional_absent.append(name)
    return tuple(dict.fromkeys(required)), tuple(dict.fromkeys(optional_absent))
def _compose_post_work_helper_records(
    payload: Any,
    *,
    required_set: set[str],
) -> list[dict[str, Any]]:
    """Return helper services still declared in any Coolify Compose view."""

    records: dict[str, dict[str, Any]] = {}
    for compose_text, source_field, source_encoding in _all_compose_text_candidates_from_service_payload(payload):
        try:
            document = yaml.safe_load(compose_text)
        except yaml.YAMLError:
            continue
        if not isinstance(document, Mapping) or not isinstance(document.get("services"), Mapping):
            continue
        for service_name, definition in document["services"].items():
            if type(service_name) is not str or not is_post_work_helper_name(service_name):
                continue
            if service_name in required_set or service_name in PRESERVED_HELPER_NAMES:
                continue
            if _is_retired_genesis_proof_guardian_shim_definition(service_name, definition):
                continue
            record = records.setdefault(
                service_name,
                {
                    "name": service_name,
                    "uuid": "",
                    "status": "compose-declared",
                    "image": "",
                    "labels": {},
                    "exclude_from_status": False,
                    "compose_declared": True,
                    "compose_source_fields": [],
                },
            )
            if isinstance(definition, Mapping):
                if not record["image"]:
                    record["image"] = _safe_scalar(definition.get("image"))
                if not record["labels"]:
                    record["labels"] = _labels(definition.get("labels"))
            record["compose_source_fields"].append(
                {
                    "field": source_field,
                    "encoding": source_encoding,
                    "sha256": hashlib.sha256(compose_text.encode("utf-8")).hexdigest(),
                }
            )
    return list(records.values())


def _depends_on_targets(value: object) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        return tuple(str(key).strip() for key in value if str(key).strip())
    if isinstance(value, list):
        return tuple(item.strip() for item in value if type(item) is str and item.strip())
    if type(value) is str and value.strip():
        return (value.strip(),)
    return ()


def _remove_helper_depends_on_entries(
    services: Mapping[str, Any],
    *,
    removed_service_names: set[str],
) -> tuple[str, ...]:
    service_names = {name for name in services if type(name) is str}
    removed_dependencies: list[str] = []

    def should_remove(target: str) -> bool:
        if target in service_names:
            return False
        return target in removed_service_names or is_post_work_helper_name(target)

    for _service_name, definition in list(services.items()):
        if not isinstance(definition, dict):
            continue
        depends_on = definition.get("depends_on")
        if isinstance(depends_on, dict):
            for target in list(depends_on):
                if type(target) is str and should_remove(target):
                    del depends_on[target]
                    if target not in removed_dependencies:
                        removed_dependencies.append(target)
            if not depends_on:
                definition.pop("depends_on", None)
        elif isinstance(depends_on, list):
            retained: list[Any] = []
            for target in depends_on:
                if type(target) is str and should_remove(target):
                    if target not in removed_dependencies:
                        removed_dependencies.append(target)
                    continue
                retained.append(target)
            if retained:
                definition["depends_on"] = retained
            else:
                definition.pop("depends_on", None)
        elif type(depends_on) is str and should_remove(depends_on):
            definition.pop("depends_on", None)
            if depends_on not in removed_dependencies:
                removed_dependencies.append(depends_on)

    return tuple(removed_dependencies)


def _compose_missing_depends_on_targets(compose_text: str) -> tuple[str, ...]:
    try:
        parsed = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise MotherPostWorkCleanupError(
            "MOTHER_POST_WORK_CLEANUP_COMPOSE_INVALID",
            "rewritten Coolify docker compose content is not valid YAML",
        ) from exc
    if not isinstance(parsed, Mapping) or not isinstance(parsed.get("services"), Mapping):
        raise MotherPostWorkCleanupError(
            "MOTHER_POST_WORK_CLEANUP_COMPOSE_INVALID",
            "rewritten Coolify docker compose content does not contain a services mapping",
        )

    services = parsed["services"]
    service_names = {name for name in services if type(name) is str}
    missing: list[str] = []
    for definition in services.values():
        if not isinstance(definition, Mapping):
            continue
        for target in _depends_on_targets(definition.get("depends_on")):
            if target not in service_names and target not in missing:
                missing.append(target)
    return tuple(missing)


def _validate_rewritten_compose_or_raise(compose_text: str) -> tuple[str, ...]:
    missing = _compose_missing_depends_on_targets(compose_text)
    if missing:
        raise MotherPostWorkCleanupError(
            "MOTHER_POST_WORK_CLEANUP_COMPOSE_INVALID_DEPENDS_ON",
            "rewritten Coolify docker compose content has depends_on targets with no service definitions: "
            + ", ".join(missing),
        )
    return missing


def _remove_post_work_helpers_from_compose_for_cleanup(
    compose_text: str,
    helper_names: tuple[str, ...],
) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    try:
        parsed = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_COMPOSE_INVALID",
            "Coolify docker compose content is not valid YAML",
        ) from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("services"), dict):
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_COMPOSE_INVALID",
            "Coolify docker compose content does not contain a services mapping",
        )

    explicit_helpers = {name for name in helper_names if name}
    services = parsed["services"]
    removed_service_names: list[str] = []
    removed_helper_names: list[str] = []

    for service_name, definition in list(services.items()):
        if not isinstance(service_name, str):
            continue
        matched_helper = service_name if service_name in explicit_helpers else None
        if matched_helper is None:
            continue
        if service_name in PRESERVED_HELPER_NAMES:
            continue
        if _is_retired_genesis_proof_guardian_shim_definition(service_name, definition):
            continue
        del services[service_name]
        removed_service_names.append(service_name)
        if matched_helper not in removed_helper_names:
            removed_helper_names.append(matched_helper)

    removed_dependency_names = _remove_helper_depends_on_entries(
        services,
        removed_service_names=set(removed_service_names),
    )

    cleaned = yaml.safe_dump(parsed, sort_keys=False)
    _validate_rewritten_compose_or_raise(cleaned)

    if not removed_service_names and not removed_dependency_names:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_COMPOSE_NO_MATCH",
            "no completed helper service names or stale helper depends_on references were present in the compose content",
        )

    return (
        cleaned,
        tuple(removed_service_names),
        tuple(removed_helper_names),
        tuple(removed_dependency_names),
    )


def _summary(document: Mapping[str, Any]) -> Mapping[str, Any]:
    value = document.get("summary")
    return value if isinstance(value, Mapping) else {}


def is_post_work_helper_name(name: object) -> bool:
    if type(name) is not str:
        return False
    return name in POST_WORK_HELPER_NAMES or any(name.startswith(prefix) for prefix in POST_WORK_HELPER_PREFIXES)


def _operator_host_prune_helper_name(name: object) -> bool:
    return (
        type(name) is str
        and (
            name in OPERATOR_HOST_PRUNE_HELPER_NAMES
            or any(name.startswith(prefix) for prefix in OPERATOR_HOST_PRUNE_HELPER_PREFIXES)
        )
    )


def _operator_host_prune_required(record: Mapping[str, Any]) -> bool:
    return _operator_host_prune_helper_name(record.get("name"))


def _shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _operator_host_cleanup_container_names(
    helpers: list[Mapping[str, Any]],
    *,
    service_uuid: str,
) -> list[str]:
    service = _safe_scalar(service_uuid)
    names: list[str] = []
    for item in helpers:
        helper = _safe_scalar(item.get("name"))
        if not helper:
            continue
        names.append(f"{helper}-{service}")
    return list(dict.fromkeys(names))


def _operator_host_cleanup_commands(
    helpers: list[Mapping[str, Any]],
    *,
    service_uuid: str,
) -> list[dict[str, Any]]:
    containers = _operator_host_cleanup_container_names(helpers, service_uuid=service_uuid)
    if not containers:
        return []
    quoted = " ".join(_shell_single_quote(item) for item in containers)
    grep_pattern = "|".join(re.escape(item) for item in containers)
    verify_pattern = grep_pattern or "__no_orphan_helpers__"
    return [
        {
            "shell": "bash",
            "description": (
                "Run on the Docker host for this Coolify service. "
                "This removes orphaned transient Mother helper containers that Coolify's API cannot prove absent."
            ),
            "command": "\n".join(
                [
                    f"for c in {quoted}; do",
                    '  docker rm -f "$c" 2>/dev/null || true',
                    "done",
                    "docker ps -a --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}' \\",
                    f"  | grep -Ei {_shell_single_quote(verify_pattern)} || true",
                ]
            ),
            "container_names": containers,
        }
    ]


def _operator_host_cleanup_commands_from_result(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    commands = result.get("operator_host_cleanup_commands")
    if not isinstance(commands, list):
        return []
    return [item for item in commands if isinstance(item, Mapping) and _safe_scalar(item.get("command")).strip()]


def _print_operator_host_cleanup_commands_footer(
    result: Mapping[str, Any],
    *,
    stream: Any = sys.stderr,
) -> None:
    commands = _operator_host_cleanup_commands_from_result(result)
    if not commands:
        return

    print("", file=stream)
    print("=== OPERATOR HOST CLEANUP REQUIRED ===", file=stream)
    print(
        "Run the following command(s) on the Docker host for this Coolify service.",
        file=stream,
    )
    for index, item in enumerate(commands, start=1):
        shell = _safe_scalar(item.get("shell")) or "shell"
        description = _safe_scalar(item.get("description"))
        if description:
            print(f"# {description}", file=stream)
        print(f"--- command {index}/{len(commands)} ({shell}) ---", file=stream)
        print(_safe_scalar(item.get("command")).rstrip(), file=stream)
        print(f"--- end command {index}/{len(commands)} ---", file=stream)
    try:
        stream.flush()
    except AttributeError:
        pass


def _application_records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    raw_items = payload.get("applications")
    if type(raw_items) is not list:
        return []
    records: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        name = _safe_scalar(item.get("name")).strip()
        uuid = _safe_scalar(item.get("uuid")).strip()
        status = _safe_scalar(item.get("status")).strip()
        image = _safe_scalar(item.get("image")).strip()
        if not name and not uuid:
            continue
        records.append(
            {
                "name": name,
                "uuid": uuid,
                "status": status,
                "image": image,
                "labels": _labels(item.get("labels")),
                "exclude_from_status": _truthy_excluded(item.get("exclude_from_status")),
            }
        )
    return records


def classify_post_work_helpers(
    *,
    payload: Any,
    node: str,
    required_component_names: tuple[str, ...] = DEFAULT_POST_WORK_REQUIRED_COMPONENT_NAMES,
) -> dict[str, Any]:
    """Classify helpers after a separate clean add/remove completion proof.

    Unlike the older completed-helper cleanup classifier, this intentionally
    treats still-running or unhealthy known Mother helper applications as cleanup
    candidates once the caller has supplied clean post-work evidence.
    """

    if not isinstance(payload, Mapping):
        raise MotherPostWorkCleanupError(
            "MOTHER_POST_WORK_CLEANUP_BAD_RESPONSE",
            "Coolify service detail payload is not an object",
        )

    node_name = _identifier(node, "node")

    parent = {
        "name": _safe_scalar(payload.get("name")),
        "uuid": _safe_scalar(payload.get("uuid")),
        "status": _safe_scalar(payload.get("status")),
        "description": _safe_scalar(payload.get("description")),
    }
    applications = _application_records(payload)
    by_name = {item["name"]: item for item in applications if item.get("name")}
    compose_service_names = _compose_service_names_from_service_payload(payload)
    required_names, optional_absent_required = _effective_required_component_names(
        node_name=node_name,
        configured_required_component_names=required_component_names,
        application_names=set(by_name),
        compose_service_names=compose_service_names,
    )
    required_set = set(required_names)

    required_components: list[dict[str, Any]] = []
    missing_required: list[str] = []
    unhealthy_required: list[dict[str, Any]] = []
    for name in required_names:
        record = by_name.get(name)
        if record is None:
            missing_required.append(name)
            required_components.append({"name": name, "uuid": "", "status": "missing", "image": ""})
            continue
        required_components.append(record)
        if not _healthy(_status(record.get("status"))):
            unhealthy_required.append(record)

    preserved_helpers = [item for item in applications if item.get("name") in PRESERVED_HELPER_NAMES]
    retired_shims = [item for item in applications if _is_retired_genesis_proof_guardian_shim_record(item)]
    retired_shim_names = {item.get("name") for item in retired_shims}
    already_excluded_helpers = [
        item
        for item in applications
        if is_post_work_helper_name(item.get("name")) and item.get("exclude_from_status") is True
    ]
    compose_helpers = _compose_post_work_helper_records(payload, required_set=required_set)
    compose_helper_names = {item.get("name") for item in compose_helpers if item.get("name")}
    cleanup_by_name: dict[str, dict[str, Any]] = {}
    for item in applications:
        name = _safe_scalar(item.get("name"))
        if (
            is_post_work_helper_name(name)
            and name not in required_set
            and name not in PRESERVED_HELPER_NAMES
            and name not in retired_shim_names
            and (
                item.get("exclude_from_status") is not True
                or name in compose_helper_names
                or _operator_host_prune_required(item)
            )
        ):
            cleanup_by_name[name] = {
                **item,
                "compose_declared": name in compose_helper_names,
                "operator_host_prune_required": _operator_host_prune_required(item) and name not in compose_helper_names,
            }
    for item in compose_helpers:
        name = _safe_scalar(item.get("name"))
        if not name:
            continue
        if name in cleanup_by_name:
            cleanup_by_name[name]["compose_declared"] = True
            cleanup_by_name[name]["compose_source_fields"] = item.get("compose_source_fields", [])
            continue
        cleanup_by_name[name] = item
    cleanup_candidates = list(cleanup_by_name.values())
    operator_host_prune_helpers = [
        item for item in cleanup_candidates if item.get("operator_host_prune_required") is True
    ]
    unclassified_unhealthy = [
        item
        for item in applications
        if "unhealthy" in _status(item.get("status"))
        and item.get("name") not in required_set
        and not is_post_work_helper_name(item.get("name"))
        and item.get("exclude_from_status") is not True
    ]
    unexpected_terminal = [
        item
        for item in applications
        if _status(item.get("status")).startswith(("exited", "dead", "created"))
        and item.get("name") not in required_set
        and not is_post_work_helper_name(item.get("name"))
        and item.get("exclude_from_status") is not True
    ]

    parent_status = _status(parent.get("status"))
    core_healthy = not missing_required and not unhealthy_required
    manual_review_required = bool(missing_required or unhealthy_required or unexpected_terminal or unclassified_unhealthy)
    clean = core_healthy and not cleanup_candidates and not manual_review_required and not _parent_degraded(parent_status)

    return {
        "parent": parent,
        "applications": applications,
        "required_components": required_components,
        "missing_required_components": missing_required,
        "optional_absent_required_components": list(optional_absent_required),
        "unhealthy_required_components": unhealthy_required,
        "post_work_helper_candidates": cleanup_candidates,
        "operator_host_prune_helpers": operator_host_prune_helpers,
        "preserved_helpers": preserved_helpers,
        "already_excluded_post_work_helpers": already_excluded_helpers,
        "compose_declared_post_work_helpers": compose_helpers,
        "retired_post_work_shims": retired_shims,
        "unexpected_terminal_components": unexpected_terminal,
        "unclassified_unhealthy_components": unclassified_unhealthy,
        "summary": {
            "clean": clean,
            "parent_status_clean": not _parent_degraded(parent_status),
            "core_required_components_healthy": core_healthy,
            "required_component_count": len(required_components),
            "optional_absent_required_component_count": len(optional_absent_required),
            "optional_absent_required_components": list(optional_absent_required),
            "post_work_helper_candidate_count": len(cleanup_candidates),
            "post_work_helper_candidate_missing_uuid_count": sum(1 for item in cleanup_candidates if not item.get("uuid")),
            "operator_host_prune_helper_count": len(operator_host_prune_helpers),
            "operator_host_cleanup_required": bool(operator_host_prune_helpers),
            "preserved_helper_count": len(preserved_helpers),
            "already_excluded_post_work_helper_count": len(already_excluded_helpers),
            "compose_declared_post_work_helper_count": len(compose_helpers),
            "retired_post_work_shim_count": len(retired_shims),
            "unexpected_terminal_component_count": len(unexpected_terminal),
            "unclassified_unhealthy_component_count": len(unclassified_unhealthy),
            "manual_review_required": manual_review_required,
        },
    }


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MotherPostWorkCleanupError(
            "MOTHER_POST_WORK_CLEANUP_COMPLETION_EVIDENCE_MISSING",
            "completion evidence file is missing",
        ) from exc
    except json.JSONDecodeError as exc:
        raise MotherPostWorkCleanupError(
            "MOTHER_POST_WORK_CLEANUP_COMPLETION_EVIDENCE_INVALID",
            "completion evidence is not JSON",
        ) from exc


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_completion_evidence(
    document: Any,
    *,
    workflow: str,
    node: str,
    service_uuid: str,
    require_node_state: str = "auto",
) -> dict[str, Any]:
    """Validate that an add/remove completion evidence document allows cleanup.

    The gate is intentionally conservative on proof state and intentionally
    flexible on node membership so the same script can clean both the target
    node and survivor/voter nodes.  Pass ``require_node_state=present`` or
    ``absent`` when a harness wants stricter topology membership checks.
    """

    if workflow not in {"add-node", "remove-node", "generic"}:
        raise MotherPostWorkCleanupError(
            "MOTHER_POST_WORK_CLEANUP_INVALID_ARGUMENT",
            "workflow must be add-node, remove-node, or generic",
        )
    if require_node_state not in {"auto", "present", "absent", "any"}:
        raise MotherPostWorkCleanupError(
            "MOTHER_POST_WORK_CLEANUP_INVALID_ARGUMENT",
            "require_node_state must be auto, present, absent, or any",
        )
    node_name = _identifier(node, "node")
    service = _uuid(service_uuid, "service_uuid")

    if not isinstance(document, Mapping):
        raise MotherPostWorkCleanupError(
            "MOTHER_POST_WORK_CLEANUP_COMPLETION_EVIDENCE_INVALID",
            "completion evidence is not an object",
        )

    summary = _summary(document)
    next_phase = _safe_scalar(document.get("next_phase") or summary.get("next_phase"))
    failures: list[str] = []
    if document.get("status") != "pass":
        failures.append("status is not pass")
    failure_value = document.get("failure")
    if failure_value is not None and failure_value != {}:
        failures.append("failure is present")
    topology_current = summary.get("topology_current") is True or summary.get("current_topology_marked_by_evidence") is True
    if summary.get("clean") is not True:
        failures.append("summary.clean is not true")
    if not topology_current:
        failures.append("summary.topology_current/current_topology_marked_by_evidence is not true")
    if summary.get("topology_stale") is True:
        failures.append("summary.topology_stale is true")
    add_node_done = next_phase == "add-node-prep-mainnet" or next_phase.startswith("add-node-single-node-finalized-")
    if workflow == "add-node" and not add_node_done:
        failures.append("add-node completion did not return to add-node prep or single-node finalized state")
    if workflow == "remove-node" and next_phase != "remove-node-finalized-mainnet":
        failures.append("remove-node completion did not reach remove-node-finalized-mainnet")

    topology = _mapping(document.get("final_topology") or document.get("current_topology"))
    services = _mapping(topology.get("services"))
    node_service = _mapping(services.get(node_name))
    node_present = node_name in services or node_name in tuple(_safe_scalar(item) for item in topology.get("nodes", []) if isinstance(topology.get("nodes"), list))
    node_service_uuid = _safe_scalar(node_service.get("service_uuid"))
    node_service_uuid_matches = bool(node_service_uuid) and node_service_uuid == service

    required_state = require_node_state
    if require_node_state == "auto":
        required_state = "present" if workflow == "add-node" else "any"
    if required_state == "present" and not node_present:
        failures.append("node is not present in final topology")
    if required_state == "absent" and node_present:
        failures.append("node is still present in final topology")
    if node_service_uuid and node_service_uuid != service:
        failures.append("final topology service UUID for node does not match target service UUID")

    if failures:
        raise MotherPostWorkCleanupError(
            "MOTHER_POST_WORK_CLEANUP_COMPLETION_EVIDENCE_UNTRUSTED",
            "; ".join(failures),
        )

    return {
        "accepted": True,
        "workflow": workflow,
        "kind": _safe_scalar(document.get("kind")),
        "status": _safe_scalar(document.get("status")),
        "next_phase": next_phase,
        "summary_clean": summary.get("clean") is True,
        "summary_topology_current": topology_current,
        "summary_topology_stale": summary.get("topology_stale") is True,
        "node": node_name,
        "service_uuid": service,
        "node_present_in_final_topology": bool(node_present),
        "node_service_uuid_matches": bool(node_service_uuid_matches),
        "require_node_state": require_node_state,
    }


def _load_and_validate_completion_evidence(
    path: str | Path,
    *,
    expected_sha256: str | None,
    workflow: str,
    node: str,
    service_uuid: str,
    require_node_state: str,
) -> dict[str, Any]:
    evidence_path = Path(path)
    observed_sha256 = _sha256_file(evidence_path)
    if expected_sha256 is not None and expected_sha256.strip().lower() != observed_sha256:
        raise MotherPostWorkCleanupError(
            "MOTHER_POST_WORK_CLEANUP_COMPLETION_EVIDENCE_SHA256_MISMATCH",
            "completion evidence SHA256 mismatch",
        )
    document = _read_json(evidence_path)
    validation = validate_completion_evidence(
        document,
        workflow=workflow,
        node=node,
        service_uuid=service_uuid,
        require_node_state=require_node_state,
    )
    return {
        "path": str(evidence_path),
        "sha256": observed_sha256,
        "validation": validation,
    }


def _operation(value: OperationIdentity) -> OperationIdentity:
    if not isinstance(value, OperationIdentity):
        raise TypeError("operation must be an OperationIdentity")
    return value


def _summary_status(summary: Mapping[str, Any]) -> str:
    if summary.get("manual_review_required") is True:
        return "manual-review-required"
    if summary.get("clean") is True:
        return "pass"
    if int(summary.get("post_work_helper_candidate_count") or 0) > 0:
        return "cleanup-required"
    return "manual-review-required"


def inspect_post_work_completed_helper_cleanup(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
    service_uuid: str,
    node: str,
    completion_evidence_path: str | Path,
    completion_evidence_sha256: str | None = None,
    workflow: str = "generic",
    require_node_state: str = "auto",
    required_component_names: tuple[str, ...] = DEFAULT_POST_WORK_REQUIRED_COMPONENT_NAMES,
    timeout: float = 30.0,
    max_response_bytes: int = 12 * 1024 * 1024,
    opener: Any = urllib.request.urlopen,
    operation: OperationIdentity,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    op = _operation(operation)
    network_id = _identifier(network, "network")
    controller_name = _identifier(controller_id, "controller_id")
    service = _uuid(service_uuid, "service_uuid")
    node_name = _identifier(node, "node")
    required = tuple(_identifier(name, "required_component_name") for name in required_component_names)
    request_timeout = _positive(timeout, "timeout")
    response_limit = int(max_response_bytes)
    if response_limit <= 0:
        raise MotherPostWorkCleanupError(
            "MOTHER_POST_WORK_CLEANUP_INVALID_ARGUMENT",
            "max_response_bytes must be positive",
        )

    _emit_progress(
        progress,
        "inspect",
        "validating completion evidence",
        node=node_name,
        service_uuid=service,
    )
    completion_evidence = _load_and_validate_completion_evidence(
        completion_evidence_path,
        expected_sha256=completion_evidence_sha256,
        workflow=workflow,
        node=node_name,
        service_uuid=service,
        require_node_state=require_node_state,
    )
    _emit_progress(
        progress,
        "inspect",
        "completion evidence accepted",
        workflow=workflow,
        require_node_state=require_node_state,
    )

    _emit_progress(
        progress,
        "inspect",
        "resolving Coolify controller",
        controller_id=controller_name,
        network=network_id,
    )
    controller = resolve_coolify_controller(
        private_state,
        network_id,
        controller_name,
        require_enabled=True,
        require_token=True,
    )
    _emit_progress(
        progress,
        "inspect",
        "fetching service detail",
        endpoint=f"/api/v1/services/{service}",
        timeout_seconds=request_timeout,
    )
    detail = _service_detail(
        controller,
        service,
        timeout=request_timeout,
        max_response_bytes=response_limit,
        opener=opener,
    )
    _emit_progress(
        progress,
        "inspect",
        "service detail fetched",
        http_status=detail.get("status"),
        ok=detail.get("ok"),
        elapsed_ms=detail.get("elapsed_ms"),
        byte_length=detail.get("byte_length"),
    )
    _emit_progress(progress, "inspect", "classifying service components", node=node_name)
    components = classify_post_work_helpers(
        payload=detail["payload"],
        node=node_name,
        required_component_names=required,
    )
    _emit_progress(
        progress,
        "inspect",
        "classification complete",
        clean=components["summary"].get("clean"),
        candidates=components["summary"].get("post_work_helper_candidate_count"),
        compose_declared=components["summary"].get("compose_declared_post_work_helper_count"),
        operator_host_cleanup_required=components["summary"].get("operator_host_cleanup_required"),
    )
    operator_host_commands = _operator_host_cleanup_commands(
        components.get("operator_host_prune_helpers", []),
        service_uuid=service,
    )
    summary = {
        **components["summary"],
        "live_mutation_performed": False,
        "application_delete_count": 0,
        "service_detail_http_status": detail["status"],
        "operator_host_cleanup_command_count": len(operator_host_commands),
        "completion_evidence_accepted": True,
    }
    status = "manual-review-required" if operator_host_commands else _summary_status(summary)
    return {
        "kind": _KIND,
        "schema_version": 1,
        "status": status,
        "network": network_id,
        "controller_id": controller_name,
        "service_uuid": service,
        "node": node_name,
        "operation_id": op.operation_id,
        "observed_at": _utc_now(),
        "mode": "inspect",
        "completion_evidence": completion_evidence,
        "http_observations": [
            {
                "method": "GET",
                "endpoint": f"/api/v1/services/{service}",
                "status": detail["status"],
                "ok": detail["ok"],
                "response_sha256": detail["response_sha256"],
                "byte_length": detail["byte_length"],
                "elapsed_ms": detail["elapsed_ms"],
            }
        ],
        "parent": components["parent"],
        "required_components": components["required_components"],
        "post_work_helper_candidates": components["post_work_helper_candidates"],
        "operator_host_prune_helpers": components.get("operator_host_prune_helpers", []),
        "operator_host_cleanup_commands": operator_host_commands,
        "preserved_helpers": components["preserved_helpers"],
        "already_excluded_post_work_helpers": components["already_excluded_post_work_helpers"],
        "retired_post_work_shims": components["retired_post_work_shims"],
        "unexpected_terminal_components": components["unexpected_terminal_components"],
        "unclassified_unhealthy_components": components["unclassified_unhealthy_components"],
        "summary": summary,
    }


def _delete_candidate(
    controller: Any,
    service_uuid: str,
    candidate: Mapping[str, Any],
    *,
    allow_nested_application_delete: bool,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    app_uuid = _safe_scalar(candidate.get("uuid"))
    if not app_uuid:
        return {
            "method": "DELETE",
            "endpoint": None,
            "status": None,
            "ok": False,
            "application_uuid": "",
            "application_name": _safe_scalar(candidate.get("name")),
            "application_status": _safe_scalar(candidate.get("status")),
            "error_code": "MOTHER_POST_WORK_CLEANUP_CANDIDATE_UUID_MISSING",
        }

    primary = _delete_application(
        controller,
        app_uuid,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    primary["application_name"] = _safe_scalar(candidate.get("name"))
    primary["application_status"] = _safe_scalar(candidate.get("status"))
    if primary["ok"] or not allow_nested_application_delete:
        return primary

    nested = _delete_service_application_with_fallbacks(
        controller,
        service_uuid,
        app_uuid,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    nested["application_name"] = _safe_scalar(candidate.get("name"))
    nested["application_status"] = _safe_scalar(candidate.get("status"))
    nested["primary_attempt"] = primary
    return nested


def _retired_genesis_proof_guardian_shim_service(service_uuid: str) -> dict[str, Any]:
    return {
        "image": RETIRED_GENESIS_PROOF_GUARDIAN_SHIM_IMAGE,
        "container_name": f"{RETIRED_GENESIS_PROOF_GUARDIAN_SHIM_NAME}-{service_uuid}",
        "command": [
            "sh",
            "-lc",
            (
                "printf '%s\\n' "
                "'retired Mother genesis proof guardian shim; no proof contract is served here' "
                "> /tmp/mother-retired-helper-shim; "
                "while true; do sleep 3600; done"
            ),
        ],
        "healthcheck": {
            "test": ["CMD", "sh", "-lc", "test -f /tmp/mother-retired-helper-shim"],
            "interval": "10s",
            "timeout": "5s",
            "retries": 12,
            "start_period": "5s",
        },
        "restart": "unless-stopped",
        "labels": {
            RETIRED_GENESIS_PROOF_GUARDIAN_SHIM_LABEL: "true",
            "main_computer.mother.helper": RETIRED_GENESIS_PROOF_GUARDIAN_SHIM_NAME,
            "main_computer.mother.retired_helper_shim": "true",
            "main_computer.mother.not_a_proof_guardian": "true",
        },
    }


def _install_retired_genesis_proof_guardian_shim_in_compose(
    compose_text: str,
    *,
    service_uuid: str,
) -> tuple[str, bool]:
    try:
        parsed = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_COMPOSE_INVALID",
            "Coolify docker compose content is not valid YAML",
        ) from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("services"), dict):
        raise MotherDeploymentCompletedHelperCleanupError(
            "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_COMPOSE_INVALID",
            "Coolify docker compose content does not contain a services mapping",
        )

    services = parsed["services"]
    replaced_existing = RETIRED_GENESIS_PROOF_GUARDIAN_SHIM_NAME in services
    services[RETIRED_GENESIS_PROOF_GUARDIAN_SHIM_NAME] = _retired_genesis_proof_guardian_shim_service(service_uuid)
    return yaml.safe_dump(parsed, sort_keys=False), replaced_existing


def _rewrite_compose_for_post_work_candidates(
    controller: Any,
    service_uuid: str,
    payload: Any,
    remove_candidates: list[Mapping[str, Any]],
    *,
    install_retired_genesis_proof_guardian_shim: bool,
    instant_deploy: bool,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    helper_names = tuple(
        _safe_scalar(candidate.get("name"))
        for candidate in remove_candidates
        if _safe_scalar(candidate.get("name"))
    )
    attempts: list[dict[str, Any]] = []
    best: tuple[str, str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...], bool] | None = None

    for compose_text, source_field, source_encoding in _compose_text_candidates_from_service_payload(payload):
        source_digest = hashlib.sha256(compose_text.encode("utf-8")).hexdigest()
        working = compose_text
        removed_services: tuple[str, ...] = ()
        removed_helpers: tuple[str, ...] = ()
        removed_dependencies: tuple[str, ...] = ()
        try:
            if helper_names:
                try:
                    working, removed_services, removed_helpers, removed_dependencies = (
                        _remove_post_work_helpers_from_compose_for_cleanup(working, helper_names)
                    )
                except MotherDeploymentCompletedHelperCleanupError as exc:
                    if not install_retired_genesis_proof_guardian_shim:
                        raise
                    attempts.append(
                        {
                            "source_field": source_field,
                            "source_encoding": source_encoding,
                            "source_sha256": source_digest,
                            "ok": False,
                            "phase": "remove-completed-helpers",
                            "error_code": exc.code,
                            "removed_service_count": 0,
                            "removed_helper_count": 0,
                            "removed_dependency_count": 0,
                        }
                    )

            shim_replaced_existing = False
            if install_retired_genesis_proof_guardian_shim:
                working, shim_replaced_existing = _install_retired_genesis_proof_guardian_shim_in_compose(
                    working,
                    service_uuid=service_uuid,
                )

            if not removed_helpers and not removed_dependencies and not install_retired_genesis_proof_guardian_shim:
                raise MotherDeploymentCompletedHelperCleanupError(
                    "MOTHER_DEPLOY_COMPLETED_HELPER_CLEANUP_COMPOSE_NO_MATCH",
                    "no completed helper service names or stale helper depends_on references were present in the compose content",
                )

            _validate_rewritten_compose_or_raise(working)
        except MotherDeploymentCompletedHelperCleanupError as exc:
            attempts.append(
                {
                    "source_field": source_field,
                    "source_encoding": source_encoding,
                    "source_sha256": source_digest,
                    "ok": False,
                    "error_code": exc.code,
                    "removed_service_count": 0,
                    "removed_helper_count": 0,
                    "removed_dependency_count": 0,
                    "installed_retired_genesis_proof_guardian_shim": False,
                }
            )
            continue

        attempts.append(
            {
                "source_field": source_field,
                "source_encoding": source_encoding,
                "source_sha256": source_digest,
                "ok": True,
                "removed_service_names": list(removed_services),
                "removed_helper_names": list(removed_helpers),
                "removed_dependency_names": list(removed_dependencies),
                "removed_service_count": len(removed_services),
                "removed_helper_count": len(removed_helpers),
                "removed_dependency_count": len(removed_dependencies),
                "installed_retired_genesis_proof_guardian_shim": bool(install_retired_genesis_proof_guardian_shim),
                "replaced_existing_retired_genesis_proof_guardian_service": bool(shim_replaced_existing),
            }
        )
        score = len(removed_helpers) + len(removed_dependencies) + (1 if install_retired_genesis_proof_guardian_shim else 0)
        if best is None or score > len(best[4]) + len(best[5]) + (1 if best[6] else 0):
            best = (
                working,
                source_field,
                source_encoding,
                removed_services,
                removed_helpers,
                removed_dependencies,
                shim_replaced_existing,
            )

    if best is None:
        return {
            "method": "PATCH",
            "endpoint": f"/api/v1/services/{service_uuid}",
            "status": None,
            "ok": False,
            "service_uuid": service_uuid,
            "instant_deploy": bool(instant_deploy),
            "error_code": "MOTHER_POST_WORK_CLEANUP_COMPOSE_NO_MATCH",
            "error_message": "no usable Coolify compose field could be rewritten for post-work cleanup",
            "compose_source_attempts": attempts,
            "removed_service_names": [],
            "removed_helper_names": [],
            "removed_dependency_names": [],
            "removed_service_count": 0,
            "removed_helper_count": 0,
            "removed_dependency_count": 0,
            "installed_retired_genesis_proof_guardian_shim": False,
        }

    cleaned, source_field, source_encoding, removed_services, removed_helpers, removed_dependencies, shim_replaced_existing = best
    receipt = _patch_service_compose(
        controller,
        service_uuid,
        cleaned,
        instant_deploy=instant_deploy,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    return {
        **receipt,
        "source_field": source_field,
        "source_encoding": source_encoding,
        "compose_source_attempts": attempts,
        "removed_service_names": list(removed_services),
        "removed_helper_names": list(removed_helpers),
        "removed_dependency_names": list(removed_dependencies),
        "removed_service_count": len(removed_services),
        "removed_helper_count": len(removed_helpers),
        "removed_dependency_count": len(removed_dependencies),
        "installed_retired_genesis_proof_guardian_shim": bool(install_retired_genesis_proof_guardian_shim),
        "retired_genesis_proof_guardian_shim_service_name": (
            RETIRED_GENESIS_PROOF_GUARDIAN_SHIM_NAME if install_retired_genesis_proof_guardian_shim else ""
        ),
        "replaced_existing_retired_genesis_proof_guardian_service": bool(shim_replaced_existing),
        "retired_genesis_proof_guardian_shim_serves_proof": False,
        "retired_genesis_proof_guardian_shim_mounts_proof_volume": False,
    }

def _rewrite_compose_for_candidates(
    controller: Any,
    service_uuid: str,
    payload: Any,
    candidates: list[Mapping[str, Any]],
    *,
    instant_deploy: bool,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    helper_names = tuple(
        _safe_scalar(candidate.get("name"))
        for candidate in candidates
        if _safe_scalar(candidate.get("name"))
    )
    attempts: list[dict[str, Any]] = []
    best: tuple[str, str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]] | None = None

    for compose_text, source_field, source_encoding in _compose_text_candidates_from_service_payload(payload):
        source_digest = hashlib.sha256(compose_text.encode("utf-8")).hexdigest()
        try:
            cleaned, removed_services, removed_helpers, removed_dependencies = (
                _remove_post_work_helpers_from_compose_for_cleanup(compose_text, helper_names)
            )
        except MotherDeploymentCompletedHelperCleanupError as exc:
            attempts.append(
                {
                    "source_field": source_field,
                    "source_encoding": source_encoding,
                    "source_sha256": source_digest,
                    "ok": False,
                    "error_code": exc.code,
                    "removed_service_count": 0,
                    "removed_helper_count": 0,
                    "removed_dependency_count": 0,
                }
            )
            continue

        attempts.append(
            {
                "source_field": source_field,
                "source_encoding": source_encoding,
                "source_sha256": source_digest,
                "ok": True,
                "removed_service_names": list(removed_services),
                "removed_helper_names": list(removed_helpers),
                "removed_dependency_names": list(removed_dependencies),
                "removed_service_count": len(removed_services),
                "removed_helper_count": len(removed_helpers),
                "removed_dependency_count": len(removed_dependencies),
            }
        )
        score = len(removed_helpers) + len(removed_dependencies)
        if best is None or score > len(best[4]) + len(best[5]):
            best = (cleaned, source_field, source_encoding, removed_services, removed_helpers, removed_dependencies)

    if best is None:
        return {
            "method": "PATCH",
            "endpoint": f"/api/v1/services/{service_uuid}",
            "status": None,
            "ok": False,
            "service_uuid": service_uuid,
            "instant_deploy": bool(instant_deploy),
            "error_code": "MOTHER_POST_WORK_CLEANUP_COMPOSE_NO_MATCH",
            "error_message": "no post-work helper service names or stale helper depends_on references were present in any Coolify compose field",
            "compose_source_attempts": attempts,
            "removed_service_names": [],
            "removed_helper_names": [],
            "removed_dependency_names": [],
            "removed_service_count": 0,
            "removed_helper_count": 0,
            "removed_dependency_count": 0,
        }

    cleaned, source_field, source_encoding, removed_services, removed_helpers, removed_dependencies = best
    receipt = _patch_service_compose(
        controller,
        service_uuid,
        cleaned,
        instant_deploy=instant_deploy,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    return {
        **receipt,
        "source_field": source_field,
        "source_encoding": source_encoding,
        "compose_source_attempts": attempts,
        "removed_service_names": list(removed_services),
        "removed_helper_names": list(removed_helpers),
        "removed_dependency_names": list(removed_dependencies),
        "removed_service_count": len(removed_services),
        "removed_helper_count": len(removed_helpers),
        "removed_dependency_count": len(removed_dependencies),
    }

def _exclude_status_for_candidates(
    controller: Any,
    service_uuid: str,
    candidates: list[Mapping[str, Any]],
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for candidate in candidates:
        app_uuid = _safe_scalar(candidate.get("uuid"))
        if not app_uuid:
            continue
        receipt = _patch_application_status_exclusion_with_fallbacks(
            controller,
            service_uuid,
            app_uuid,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        receipt["application_name"] = _safe_scalar(candidate.get("name"))
        receipt["application_status"] = _safe_scalar(candidate.get("status"))
        receipts.append(receipt)
    return receipts


def execute_post_work_completed_helper_cleanup(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
    service_uuid: str,
    node: str,
    acknowledged_service_uuid: str,
    completion_evidence_path: str | Path,
    completion_evidence_sha256: str | None = None,
    workflow: str = "generic",
    require_node_state: str = "auto",
    required_component_names: tuple[str, ...] = DEFAULT_POST_WORK_REQUIRED_COMPONENT_NAMES,
    allow_nested_application_delete: bool = False,
    allow_compose_rewrite: bool = False,
    instant_deploy_compose_rewrite: bool = False,
    allow_retired_genesis_proof_guardian_shim: bool = False,
    allow_coolify_model_status_exclusion: bool = False,
    allow_service_redeploy_refresh: bool = False,
    force_service_redeploy_refresh: bool = True,
    max_wait_seconds: float = 120.0,
    poll_interval_seconds: float = 5.0,
    timeout: float = 30.0,
    max_response_bytes: int = 12 * 1024 * 1024,
    opener: Any = urllib.request.urlopen,
    operation: OperationIdentity,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    op = _operation(operation)
    network_id = _identifier(network, "network")
    controller_name = _identifier(controller_id, "controller_id")
    service = _uuid(service_uuid, "service_uuid")
    node_name = _identifier(node, "node")
    if _uuid(acknowledged_service_uuid, "acknowledged_service_uuid") != service:
        raise MotherPostWorkCleanupError(
            "MOTHER_POST_WORK_CLEANUP_ACK_REQUIRED",
            "acknowledged service UUID must match the target service UUID",
        )
    required = tuple(_identifier(name, "required_component_name") for name in required_component_names)
    request_timeout = _positive(timeout, "timeout")
    response_limit = int(max_response_bytes)
    if response_limit <= 0:
        raise MotherPostWorkCleanupError(
            "MOTHER_POST_WORK_CLEANUP_INVALID_ARGUMENT",
            "max_response_bytes must be positive",
        )
    wait_limit = _nonnegative(max_wait_seconds, "max_wait_seconds")
    poll_interval = _nonnegative(poll_interval_seconds, "poll_interval_seconds")

    _emit_progress(
        progress,
        "execute",
        "validating completion evidence",
        node=node_name,
        service_uuid=service,
    )
    completion_evidence = _load_and_validate_completion_evidence(
        completion_evidence_path,
        expected_sha256=completion_evidence_sha256,
        workflow=workflow,
        node=node_name,
        service_uuid=service,
        require_node_state=require_node_state,
    )
    _emit_progress(
        progress,
        "execute",
        "completion evidence accepted",
        workflow=workflow,
        require_node_state=require_node_state,
    )
    _emit_progress(
        progress,
        "execute",
        "resolving Coolify controller",
        controller_id=controller_name,
        network=network_id,
    )
    controller = resolve_coolify_controller(
        private_state,
        network_id,
        controller_name,
        require_enabled=True,
        require_token=True,
    )

    observations: list[dict[str, Any]] = []
    _emit_progress(
        progress,
        "execute",
        "fetching initial service detail",
        endpoint=f"/api/v1/services/{service}",
        timeout_seconds=request_timeout,
    )
    initial_detail = _service_detail(
        controller,
        service,
        timeout=request_timeout,
        max_response_bytes=response_limit,
        opener=opener,
    )
    observations.append(
        {
            "method": "GET",
            "endpoint": f"/api/v1/services/{service}",
            "status": initial_detail["status"],
            "ok": initial_detail["ok"],
            "response_sha256": initial_detail["response_sha256"],
            "byte_length": initial_detail["byte_length"],
            "elapsed_ms": initial_detail["elapsed_ms"],
        }
    )
    _emit_progress(
        progress,
        "execute",
        "initial service detail fetched",
        http_status=initial_detail.get("status"),
        ok=initial_detail.get("ok"),
        elapsed_ms=initial_detail.get("elapsed_ms"),
        byte_length=initial_detail.get("byte_length"),
    )
    _emit_progress(progress, "execute", "classifying initial service components", node=node_name)
    initial = classify_post_work_helpers(
        payload=initial_detail["payload"],
        node=node_name,
        required_component_names=required,
    )
    _emit_progress(
        progress,
        "execute",
        "initial classification complete",
        clean=initial["summary"].get("clean"),
        manual_review_required=initial["summary"].get("manual_review_required"),
        candidates=initial["summary"].get("post_work_helper_candidate_count"),
        compose_declared=initial["summary"].get("compose_declared_post_work_helper_count"),
        required_components_healthy=initial["summary"].get("core_required_components_healthy"),
    )
    if initial["summary"]["manual_review_required"] is True:
        _emit_progress(
            progress,
            "execute",
            "manual review required before mutation; stopping",
            parent_status_clean=initial["summary"].get("parent_status_clean"),
            required_components_healthy=initial["summary"].get("core_required_components_healthy"),
            candidates=initial["summary"].get("post_work_helper_candidate_count"),
        )
        summary = {
            **initial["summary"],
            "live_mutation_performed": False,
            "application_delete_count": 0,
            "compose_rewrite_performed": False,
            "completion_evidence_accepted": True,
        }
        return {
            "kind": _KIND,
            "schema_version": 1,
            "status": "manual-review-required",
            "network": network_id,
            "controller_id": controller_name,
            "service_uuid": service,
            "node": node_name,
            "operation_id": op.operation_id,
            "observed_at": _utc_now(),
            "mode": "execute",
            "completion_evidence": completion_evidence,
            "http_observations": observations,
            "parent": initial["parent"],
            "required_components": initial["required_components"],
            "post_work_helper_candidates": initial["post_work_helper_candidates"],
            "retired_post_work_shims": initial["retired_post_work_shims"],
            "unexpected_terminal_components": initial["unexpected_terminal_components"],
            "unclassified_unhealthy_components": initial["unclassified_unhealthy_components"],
            "summary": summary,
        }

    candidates = list(initial["post_work_helper_candidates"])
    shim_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("name") == RETIRED_GENESIS_PROOF_GUARDIAN_SHIM_NAME
        and allow_retired_genesis_proof_guardian_shim
    ]
    delete_candidates = [candidate for candidate in candidates if candidate not in shim_candidates]
    operator_host_prune_helpers = [
        candidate
        for candidate in delete_candidates
        if _operator_host_prune_helper_name(candidate.get("name"))
    ]
    _emit_progress(
        progress,
        "execute",
        "cleanup plan built",
        candidates=len(candidates),
        delete_candidates=len(delete_candidates),
        shim_candidates=len(shim_candidates),
        operator_host_prune_helpers=len(operator_host_prune_helpers),
        allow_compose_rewrite=allow_compose_rewrite,
        allow_service_redeploy_refresh=allow_service_redeploy_refresh,
    )

    delete_receipts: list[dict[str, Any]] = []
    for candidate in delete_candidates:
        _emit_progress(
            progress,
            "execute.delete",
            "deleting or excluding helper application",
            helper_name=_safe_scalar(candidate.get("name")),
            helper_uuid=_safe_scalar(candidate.get("uuid")),
            compose_declared=bool(candidate.get("compose_declared")),
        )
        receipt = _delete_candidate(
            controller,
            service,
            candidate,
            allow_nested_application_delete=allow_nested_application_delete,
            timeout=request_timeout,
            max_response_bytes=response_limit,
            opener=opener,
        )
        delete_receipts.append(receipt)
        _emit_progress(
            progress,
            "execute.delete",
            "helper application delete/exclude attempt complete",
            helper_name=_safe_scalar(candidate.get("name")),
            ok=receipt.get("ok"),
            status=receipt.get("status"),
            error_code=receipt.get("error_code", ""),
        )
        for attempt in receipt.get("attempts", []):
            if all(key in attempt for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")):
                observations.append({key: attempt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})
        if all(key in receipt for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")):
            observations.append({key: receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})

    compose_rewrite: dict[str, Any] | None = None
    if candidates and allow_compose_rewrite:
        _emit_progress(
            progress,
            "execute.compose",
            "rewriting service Compose",
            helper_count=len(delete_candidates),
            install_retired_genesis_shim=bool(shim_candidates),
            instant_deploy=instant_deploy_compose_rewrite,
        )
        if shim_candidates:
            compose_rewrite = _rewrite_compose_for_post_work_candidates(
                controller,
                service,
                initial_detail["payload"],
                delete_candidates,
                install_retired_genesis_proof_guardian_shim=True,
                instant_deploy=instant_deploy_compose_rewrite,
                timeout=request_timeout,
                max_response_bytes=response_limit,
                opener=opener,
            )
        else:
            compose_rewrite = _rewrite_compose_for_candidates(
                controller,
                service,
                initial_detail["payload"],
                delete_candidates,
                instant_deploy=instant_deploy_compose_rewrite,
                timeout=request_timeout,
                max_response_bytes=response_limit,
                opener=opener,
            )
        if compose_rewrite.get("status") is not None and all(
            key in compose_rewrite for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")
        ):
            observations.append({key: compose_rewrite[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})
        _emit_progress(
            progress,
            "execute.compose",
            "Compose rewrite complete",
            ok=compose_rewrite.get("ok"),
            status=compose_rewrite.get("status"),
            error_code=compose_rewrite.get("error_code", ""),
            refresh_scope=compose_rewrite.get("refresh_scope", ""),
        )
        if (
            compose_rewrite.get("ok") is not True
            and compose_rewrite.get("error_code") == "MOTHER_POST_WORK_CLEANUP_COMPOSE_NO_MATCH"
            and any(candidate.get("compose_declared") is True for candidate in candidates)
        ):
            helper_names = tuple(
                _safe_scalar(candidate.get("name"))
                for candidate in candidates
                if _safe_scalar(candidate.get("name"))
            )
            _emit_progress(
                progress,
                "execute.compose",
                "primary Compose rewrite did not match; trying reconcile patch",
                helper_count=len(helper_names),
            )
            compose_rewrite = _patch_service_compose_reconcile(
                controller,
                service,
                initial_detail["payload"],
                helper_names,
                instant_deploy=instant_deploy_compose_rewrite,
                timeout=request_timeout,
                max_response_bytes=response_limit,
                opener=opener,
            )
            if compose_rewrite.get("status") is not None and all(
                key in compose_rewrite for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")
            ):
                observations.append({key: compose_rewrite[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})
            _emit_progress(
                progress,
                "execute.compose",
                "Compose reconcile patch complete",
                ok=compose_rewrite.get("ok"),
                status=compose_rewrite.get("status"),
                error_code=compose_rewrite.get("error_code", ""),
                refresh_scope=compose_rewrite.get("refresh_scope", ""),
            )

    if compose_rewrite:
        known_operator_host_helper_names = {
            _safe_scalar(item.get("name"))
            for item in operator_host_prune_helpers
            if _safe_scalar(item.get("name"))
        }
        for helper_name in compose_rewrite.get("removed_dependency_names", []):
            helper = _safe_scalar(helper_name)
            if (
                helper
                and _operator_host_prune_helper_name(helper)
                and helper not in known_operator_host_helper_names
            ):
                operator_host_prune_helpers.append(
                    {
                        "name": helper,
                        "uuid": "",
                        "status": "compose-dependency-removed",
                        "image": "",
                        "labels": {},
                        "exclude_from_status": False,
                    }
                )
                known_operator_host_helper_names.add(helper)

    operator_host_commands = _operator_host_cleanup_commands(
        operator_host_prune_helpers,
        service_uuid=service,
    )

    status_exclusion_receipts: list[dict[str, Any]] = []
    if delete_candidates and allow_coolify_model_status_exclusion:
        _emit_progress(
            progress,
            "execute.status-exclusion",
            "patching Coolify status exclusion for helper applications",
            helper_count=len(delete_candidates),
        )
        status_exclusion_receipts = _exclude_status_for_candidates(
            controller,
            service,
            delete_candidates,
            timeout=request_timeout,
            max_response_bytes=response_limit,
            opener=opener,
        )
        _emit_progress(
            progress,
            "execute.status-exclusion",
            "status exclusion patch attempts complete",
            receipt_count=len(status_exclusion_receipts),
            success_count=sum(1 for item in status_exclusion_receipts if item.get("ok") is True),
        )
        for receipt in status_exclusion_receipts:
            for attempt in receipt.get("attempts", []):
                if all(key in attempt for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")):
                    observations.append({key: attempt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})

    service_redeploy_refresh: dict[str, Any] | None = None
    if candidates and allow_service_redeploy_refresh:
        _emit_progress(
            progress,
            "execute.redeploy",
            "requesting service redeploy refresh",
            force=bool(force_service_redeploy_refresh),
        )
        service_redeploy_refresh = _request_service_redeploy_refresh(
            controller,
            service,
            force=bool(force_service_redeploy_refresh),
            timeout=request_timeout,
            max_response_bytes=response_limit,
            opener=opener,
        )
        observations.append(
            {
                key: service_redeploy_refresh[key]
                for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")
            }
        )
        _emit_progress(
            progress,
            "execute.redeploy",
            "service redeploy refresh request complete",
            ok=service_redeploy_refresh.get("ok"),
            status=service_redeploy_refresh.get("status"),
            elapsed_ms=service_redeploy_refresh.get("elapsed_ms"),
        )

    final_detail = initial_detail
    final = initial
    started = time.monotonic()
    poll_count = 0
    _emit_progress(
        progress,
        "execute.poll",
        "polling service until clean or wait limit",
        max_wait_seconds=wait_limit,
        poll_interval_seconds=poll_interval,
    )
    while True:
        poll_count += 1
        elapsed = time.monotonic() - started
        _emit_progress(
            progress,
            "execute.poll",
            "fetching service detail for cleanup verification",
            poll=poll_count,
            elapsed_seconds=round(elapsed, 3),
            timeout_seconds=request_timeout,
        )
        final_detail = _service_detail(
            controller,
            service,
            timeout=request_timeout,
            max_response_bytes=response_limit,
            opener=opener,
        )
        observations.append(
            {
                "method": "GET",
                "endpoint": f"/api/v1/services/{service}",
                "status": final_detail["status"],
                "ok": final_detail["ok"],
                "response_sha256": final_detail["response_sha256"],
                "byte_length": final_detail["byte_length"],
                "elapsed_ms": final_detail["elapsed_ms"],
            }
        )
        final = classify_post_work_helpers(
            payload=final_detail["payload"],
            node=node_name,
            required_component_names=required,
        )
        _emit_progress(
            progress,
            "execute.poll",
            "verification poll classified service",
            poll=poll_count,
            clean=final["summary"].get("clean"),
            parent_status=final["parent"].get("status"),
            candidates=final["summary"].get("post_work_helper_candidate_count"),
            compose_declared=final["summary"].get("compose_declared_post_work_helper_count"),
            operator_host_cleanup_required=final["summary"].get("operator_host_cleanup_required"),
        )
        if final["summary"]["clean"]:
            _emit_progress(progress, "execute.poll", "service is clean; polling complete", poll=poll_count)
            break
        if time.monotonic() - started >= wait_limit:
            _emit_progress(
                progress,
                "execute.poll",
                "wait limit reached; stopping verification polling",
                poll=poll_count,
                elapsed_seconds=round(time.monotonic() - started, 3),
            )
            break
        if poll_interval > 0:
            sleep_seconds = min(poll_interval, max(0.0, wait_limit - (time.monotonic() - started)))
            _emit_progress(progress, "execute.poll", "sleeping before next poll", seconds=round(sleep_seconds, 3))
            time.sleep(sleep_seconds)
        else:
            _emit_progress(progress, "execute.poll", "poll interval is zero; stopping after one poll")
            break

    delete_ok = all(item.get("ok") is True for item in delete_receipts) if delete_receipts else True
    compose_ok = compose_rewrite is not None and compose_rewrite.get("ok") is True
    exclusion_ok = bool(status_exclusion_receipts) and all(item.get("ok") is True for item in status_exclusion_receipts)
    redeploy_ok = service_redeploy_refresh is not None and service_redeploy_refresh.get("ok") is True

    summary = {
        **final["summary"],
        "live_mutation_performed": bool(candidates),
        "initial_post_work_helper_candidate_count": len(candidates),
        "retired_genesis_proof_guardian_shim_candidate_count": len(shim_candidates),
        "retired_genesis_proof_guardian_shim_requested": bool(shim_candidates),
        "application_delete_count": len(delete_receipts),
        "application_delete_success_count": sum(1 for item in delete_receipts if item.get("ok") is True),
        "application_delete_succeeded": delete_ok,
        "compose_rewrite_performed": compose_rewrite is not None,
        "compose_rewrite_succeeded": compose_ok,
        "compose_rewrite_refresh_scope": _safe_scalar(compose_rewrite.get("refresh_scope")) if compose_rewrite else "",
        "compose_reconcile_performed": bool(compose_rewrite and compose_rewrite.get("refresh_scope") == "compose-reconcile"),
        "compose_reconcile_succeeded": bool(compose_rewrite and compose_rewrite.get("refresh_scope") == "compose-reconcile" and compose_rewrite.get("ok") is True),
        "retired_genesis_proof_guardian_shim_installed": bool(
            compose_rewrite and compose_rewrite.get("installed_retired_genesis_proof_guardian_shim") is True
        ),
        "coolify_model_status_exclusion_count": len(status_exclusion_receipts),
        "coolify_model_status_exclusion_succeeded": exclusion_ok,
        "service_redeploy_refresh_performed": service_redeploy_refresh is not None,
        "service_redeploy_refresh_succeeded": redeploy_ok,
        "operator_host_cleanup_required": bool(operator_host_commands),
        "operator_host_cleanup_command_count": len(operator_host_commands),
        "completion_evidence_accepted": True,
    }
    status = "manual-review-required" if operator_host_commands else ("pass" if summary["clean"] else "manual-review-required")
    _emit_progress(
        progress,
        "execute",
        "cleanup execution complete",
        status=status,
        clean=summary.get("clean"),
        compose_rewrite_performed=summary.get("compose_rewrite_performed"),
        service_redeploy_refresh_performed=summary.get("service_redeploy_refresh_performed"),
        operator_host_cleanup_required=summary.get("operator_host_cleanup_required"),
    )

    return {
        "kind": _KIND,
        "schema_version": 1,
        "status": status,
        "network": network_id,
        "controller_id": controller_name,
        "service_uuid": service,
        "node": node_name,
        "operation_id": op.operation_id,
        "observed_at": _utc_now(),
        "mode": "execute",
        "completion_evidence": completion_evidence,
        "http_observations": observations,
        "parent": final["parent"],
        "initial_parent": initial["parent"],
        "required_components": final["required_components"],
        "initial_post_work_helper_candidates": candidates,
        "initial_operator_host_prune_helpers": operator_host_prune_helpers,
        "operator_host_cleanup_commands": operator_host_commands,
        "post_work_helper_candidates": final["post_work_helper_candidates"],
        "operator_host_prune_helpers": final.get("operator_host_prune_helpers", []),
        "preserved_helpers": final["preserved_helpers"],
        "already_excluded_post_work_helpers": final["already_excluded_post_work_helpers"],
        "compose_declared_post_work_helpers": final["compose_declared_post_work_helpers"],
        "retired_post_work_shims": final["retired_post_work_shims"],
        "unexpected_terminal_components": final["unexpected_terminal_components"],
        "unclassified_unhealthy_components": final["unclassified_unhealthy_components"],
        "application_delete_receipts": delete_receipts,
        "compose_rewrite": compose_rewrite,
        "coolify_model_status_exclusion": {
            "enabled": bool(allow_coolify_model_status_exclusion),
            "receipts": status_exclusion_receipts,
            "patched_application_count": len(status_exclusion_receipts),
            "patched_application_success_count": sum(1 for item in status_exclusion_receipts if item.get("ok") is True),
            "patched_application_names": [
                item.get("application_name", "") for item in status_exclusion_receipts if item.get("application_name")
            ],
        },
        "service_redeploy_refresh": service_redeploy_refresh,
        "summary": summary,
    }


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> dict[str, str]:
    data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    with tmp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        try:
            import os

            os.fsync(handle.fileno())
        except OSError:
            pass
    tmp.replace(path)
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()}


def write_post_work_completed_helper_cleanup_evidence(
    *,
    runtime_state_root: str | Path,
    result: Mapping[str, Any],
) -> dict[str, str]:
    root = Path(runtime_state_root) / "mother" / "evidence" / _EVIDENCE_SUBDIR
    node = _identifier(_safe_scalar(result.get("node")), "node")
    network = _identifier(_safe_scalar(result.get("network")), "network")
    stamp = _utc_now().replace("-", "").replace(":", "")
    status = _safe_scalar(result.get("status")) or "unknown"
    filename = f"{stamp}-{network}-{node}-{status}.json"
    receipt = _atomic_write_json(root / filename, result)
    return receipt


def _operation_from_cli(network: str, workflow: str) -> OperationIdentity:
    operation_kind = "MOTHER-OP-REMOVE-NODE" if workflow == "remove-node" else "MOTHER-OP-ADD-NODE"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    operation_id = f"post-work-helper-cleanup-{stamp}"
    return OperationIdentity(
        operation_id=operation_id,
        request_id=operation_id,
        network=network,
        operation_kind=operation_kind,
    )


def _load_private_state(runtime_state_root: str | Path, *, operation: OperationIdentity) -> PrivateStateReadResult:
    paths = MotherPaths(runtime_state_root=runtime_state_root).resolve_private_state_paths()
    return read_private_state(paths, operation=operation)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect or execute final cleanup of known Mother helper applications "
            "after add-node/remove-node completion evidence is already clean."
        )
    )
    parser.add_argument("--runtime-state-root", required=True)
    parser.add_argument("--network", default="mainnet")
    parser.add_argument("--controller-id", required=True)
    parser.add_argument("--service-uuid", required=True)
    parser.add_argument("--node-name", required=True)
    parser.add_argument("--completion-evidence", required=True)
    parser.add_argument("--completion-evidence-sha256")
    parser.add_argument("--workflow", choices=("add-node", "remove-node", "generic"), default="generic")
    parser.add_argument("--require-node-state", choices=("auto", "present", "absent", "any"), default="auto")
    parser.add_argument(
        "--required-component-name",
        action="append",
        dest="required_component_names",
        default=None,
        help="Additional steady-state component that must be present and healthy. Can be repeated.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=12 * 1024 * 1024)
    parser.add_argument("--max-wait-seconds", type=float, default=120.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--acknowledge-service-uuid")
    parser.add_argument("--allow-nested-application-delete", action="store_true")
    parser.add_argument("--allow-compose-rewrite", action="store_true")
    parser.add_argument("--instant-deploy-compose-rewrite", action="store_true")
    parser.add_argument(
        "--allow-retired-genesis-proof-guardian-shim",
        action="store_true",
        help=(
            "Install a minimal healthy mother-genesis-proof-guardian shim instead of deleting that completed helper. "
            "The shim serves no proof files or ports, so any later proof dependency fails fast."
        ),
    )
    parser.add_argument("--allow-coolify-model-status-exclusion", action="store_true")
    parser.add_argument("--allow-service-redeploy-refresh", action="store_true")
    parser.add_argument("--no-force-service-redeploy-refresh", action="store_true")
    parser.add_argument(
        "--quiet-progress",
        action="store_true",
        help="Do not print timestamped cleanup progress lines to stderr.",
    )
    parser.add_argument("--write-evidence", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    progress: ProgressCallback | None = None if args.quiet_progress else _stderr_progress
    _emit_progress(
        progress,
        "cli",
        "starting post-work cleanup command",
        mode="execute" if args.execute else "inspect",
        node=args.node_name,
        controller_id=args.controller_id,
        service_uuid=args.service_uuid,
    )
    required = tuple(dict.fromkeys((*DEFAULT_POST_WORK_REQUIRED_COMPONENT_NAMES, *(args.required_component_names or ()))))
    operation = _operation_from_cli(args.network, args.workflow)
    _emit_progress(progress, "cli", "loading private state", runtime_state_root=args.runtime_state_root)
    private_state = _load_private_state(args.runtime_state_root, operation=operation)
    _emit_progress(progress, "cli", "private state loaded")

    try:
        if args.execute:
            if not args.acknowledge_service_uuid:
                raise MotherPostWorkCleanupError(
                    "MOTHER_POST_WORK_CLEANUP_ACK_REQUIRED",
                    "--acknowledge-service-uuid is required with --execute",
                )
            result = execute_post_work_completed_helper_cleanup(
                private_state,
                network=args.network,
                controller_id=args.controller_id,
                service_uuid=args.service_uuid,
                node=args.node_name,
                acknowledged_service_uuid=args.acknowledge_service_uuid,
                completion_evidence_path=args.completion_evidence,
                completion_evidence_sha256=args.completion_evidence_sha256,
                workflow=args.workflow,
                require_node_state=args.require_node_state,
                required_component_names=required,
                allow_nested_application_delete=args.allow_nested_application_delete,
                allow_compose_rewrite=args.allow_compose_rewrite,
                instant_deploy_compose_rewrite=args.instant_deploy_compose_rewrite,
                allow_retired_genesis_proof_guardian_shim=args.allow_retired_genesis_proof_guardian_shim,
                allow_coolify_model_status_exclusion=args.allow_coolify_model_status_exclusion,
                allow_service_redeploy_refresh=args.allow_service_redeploy_refresh,
                force_service_redeploy_refresh=not args.no_force_service_redeploy_refresh,
                max_wait_seconds=args.max_wait_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
                operation=operation,
                progress=progress,
            )
        else:
            result = inspect_post_work_completed_helper_cleanup(
                private_state,
                network=args.network,
                controller_id=args.controller_id,
                service_uuid=args.service_uuid,
                node=args.node_name,
                completion_evidence_path=args.completion_evidence,
                completion_evidence_sha256=args.completion_evidence_sha256,
                workflow=args.workflow,
                require_node_state=args.require_node_state,
                required_component_names=required,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
                operation=operation,
                progress=progress,
            )

        if args.write_evidence:
            _emit_progress(progress, "cli", "writing cleanup evidence", status=result.get("status"))
            result = {
                **result,
                "evidence": write_post_work_completed_helper_cleanup_evidence(
                    runtime_state_root=args.runtime_state_root,
                    result=result,
                ),
            }
            _emit_progress(
                progress,
                "cli",
                "cleanup evidence written",
                path=result.get("evidence", {}).get("path", ""),
                sha256=result.get("evidence", {}).get("sha256", ""),
            )

        _emit_progress(progress, "cli", "printing JSON result", status=result.get("status"))
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)
        _print_operator_host_cleanup_commands_footer(result, stream=sys.stderr)
        return 0 if result.get("status") in {"pass", "cleanup-required"} else 2
    except MotherPostWorkCleanupError as exc:
        payload = {
            "kind": _KIND,
            "schema_version": 1,
            "status": "failed",
            "failure": {
                "code": exc.code,
                "message": str(exc),
            },
            "observed_at": _utc_now(),
        }
        print(json.dumps(payload, indent=2, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
