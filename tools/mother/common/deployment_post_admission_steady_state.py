"""Canonical post-admission steady-state cleanup for the recovered A/C QBFT chain.

This phase consumes the passing quorum-recovery reconciliation, compiles exact
two-service Compose documents for A and C, and authorizes only C PATCH/deploy
followed by A PATCH/deploy.  It never votes, changes identity, resets chain
state, deletes volumes, opens public RPC, or uses SSH.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import time
from typing import Any
import urllib.parse

import yaml

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, resolve_coolify_controller
from .deployment_genesis_birth import (
    MotherDeploymentGenesisBirthError,
    _compose_semantic_sha256,
    _match_service_compose,
)
from .deployment_validator_admission_executor import _http, _service_record, _service_status
from .deployment_validator_quorum_recovery import (
    _component_health,
    _component_status_records,
    verify_validator_quorum_recovery_reconciliation,
)
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_TRANSACTION_KIND = "main_computer.mother.deployment_post_admission_steady_state_transaction.v1"
_RELEASE_KIND = "main_computer.mother.deployment_post_admission_steady_state_release.v1"
_CLAIM_KIND = "main_computer.mother.deployment_post_admission_steady_state_execution_claim.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_post_admission_steady_state_evidence.v1"
_RECONCILIATION_KIND = "main_computer.mother.deployment_post_admission_steady_state_reconciliation.v1"

_TRANSACTION_DIRECTORY = ("actions", "deployment-post-admission-steady-state-transactions")
_RELEASE_DIRECTORY = ("actions", "deployment-post-admission-steady-state-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-post-admission-steady-state-execution-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-post-admission-steady-state")
_STEADY_RECONCILIATION_DIRECTORY = ("evidence", "deployment-post-admission-steady-state-reconciliations")
_RECONCILIATION_DIRECTORY = ("evidence", "deployment-validator-quorum-recovery-reconciliations")
_QUORUM_RELEASE_DIRECTORY = ("actions", "deployment-validator-quorum-recovery-releases")

_MIN_RELEASE_SECONDS = 30
_MAX_RELEASE_SECONDS = 900

# The retained quorum guardians declare healthy only while their proof file is
# younger than 45 seconds.  Waiting 50 seconds between successful A guardian
# observations ensures the second observation cannot be satisfied by the
# pre-C-restart proof.
_GUARDIAN_HEALTH_FRESHNESS_SECONDS = 45
_GUARDIAN_REFRESH_WAIT_SECONDS = 50
_MONOTONIC = time.monotonic
_SLEEP = time.sleep

_CANONICAL_ORDER = ("mainnetc-super1", "mainneta-super1")
_TARGETS: dict[str, dict[str, Any]] = {
    "mainneta-super1": {
        "controller_id": "coolify-a",
        "source_compose_key": "initial_quorum_compose",
        "source_service_key": "initial",
        "init_service": "mother-genesis-init",
        "guardian": "mother-validator-quorum-recovery-initial-guardian",
        "source_stage": "first-genesis",
        "proof_volume": "mother-proof",
        "recognized_obsolete_components": (
            "mother-genesis-init",
            "mother-genesis-proof-guardian",
            "mother-validator-admission-guardian",
        ),
    },
    "mainnetc-super1": {
        "controller_id": "coolify-c",
        "source_compose_key": "replica_readiness_compose",
        "source_service_key": "replica",
        "init_service": "mother-replica-init",
        "guardian": "mother-validator-quorum-recovery-replica-guardian",
        "source_stage": "soft-replica",
        "proof_volume": "mother-sync-proof",
        "recognized_obsolete_components": (
            "mother-replica-init",
            "mother-replica-sync-guardian",
        ),
    },
}


class MotherDeploymentPostAdmissionSteadyStateError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _error(code: str, message: str) -> MotherDeploymentPostAdmissionSteadyStateError:
    return MotherDeploymentPostAdmissionSteadyStateError(code, message)


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9._-]+", value.strip()) is None:
        raise _error("MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_INVALID", f"{path} is invalid")
    return value.strip()


def _sha256(value: Any, path: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise _error("MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_INVALID", f"{path} must be SHA-256")
    return value


def _address(value: Any, path: str) -> str:
    if type(value) is not str or re.fullmatch(r"0x[0-9a-fA-F]{40}", value) is None:
        raise _error("MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_INVALID", f"{path} must be an Ethereum address")
    return value.lower()


def _parse_utc(value: Any, path: str) -> datetime:
    if type(value) is not str or not value:
        raise _error("MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_INVALID", f"{path} must be UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise _error("MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_INVALID", f"{path} is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise _error("MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_INVALID", f"{path} must be UTC")
    return parsed.astimezone(timezone.utc)


def _timestamp(value: str | None = None) -> str:
    parsed = datetime.now(timezone.utc) if value is None else _parse_utc(value, "created_at")
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _duration(value: int) -> int:
    if type(value) is not int or isinstance(value, bool) or not _MIN_RELEASE_SECONDS <= value <= _MAX_RELEASE_SECONDS:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_TTL_INVALID",
            f"expires_in_seconds must be between {_MIN_RELEASE_SECONDS} and {_MAX_RELEASE_SECONDS}",
        )
    return value


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _contains_sensitive(value: Any) -> bool:
    forbidden = {
        "access_token",
        "api_token",
        "credential",
        "mnemonic",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "seed",
    }
    if isinstance(value, Mapping):
        return any(str(key).lower() in forbidden or _contains_sensitive(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    return False


def _safe_message(value: Any) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text[:300] or "operation failed"


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    return hashlib.sha256(canonical_json({key: value for key, value in document.items() if key != field})).hexdigest()


def _root(paths: PrivateStatePaths, parts: tuple[str, str]) -> Path:
    return paths.root / parts[0] / parts[1]


def _ensure_root(paths: PrivateStatePaths, parts: tuple[str, str], operation: OperationIdentity) -> Path:
    current = paths.root
    for part in parts:
        current /= part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    return current


def _relative(paths: PrivateStatePaths, path: Path, label: str) -> str:
    try:
        return path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise _error("MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_PATH_UNSAFE", f"{label} is outside Mother state") from exc


def _resolve(paths: PrivateStatePaths, locator: Any, directory: tuple[str, str], label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise _error("MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_PATH_UNSAFE", f"{label} locator is unsafe")
    candidate = Path(locator)
    if (
        candidate.is_absolute()
        or PureWindowsPath(locator).is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise _error("MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_PATH_UNSAFE", f"{label} locator is unsafe")
    result = (paths.root / candidate).resolve(strict=False)
    expected = _root(paths, directory).resolve(strict=False)
    try:
        result.relative_to(expected)
    except ValueError as exc:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_PATH_UNSAFE",
            f"{label} is outside its canonical directory",
        ) from exc
    return result


def _canonical_under(
    paths: PrivateStatePaths,
    path: Path,
    directory: tuple[str, str],
    label: str,
) -> tuple[dict[str, Any], bytes, str]:
    candidate = path.resolve(strict=False)
    expected = _root(paths, directory).resolve(strict=False)
    try:
        candidate.relative_to(expected)
        raw = candidate.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_INVALID",
            f"{label} is unreadable or outside its canonical directory",
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_INVALID",
            f"{label} is not canonical JSON",
        )
    return value, raw, hashlib.sha256(raw).hexdigest()


def _selection(selected_nodes: Iterable[str]) -> tuple[str, str]:
    values = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if values and (len(values) != 2 or set(values) != set(_CANONICAL_ORDER)):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_SELECTION_MISMATCH",
            "steady-state cleanup requires exactly mainnetc-super1 and mainneta-super1",
        )
    return _CANONICAL_ORDER


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _error("MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_INVALID", f"{path} must be an object")
    return value


def _load_yaml(compose: str, label: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(compose)
    except yaml.YAMLError as exc:
        raise _error("MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_COMPOSE_UNSUPPORTED", f"{label} is invalid YAML") from exc
    if type(value) is not dict:
        raise _error("MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_COMPOSE_UNSUPPORTED", f"{label} must be a Compose object")
    return value


def _remove_service_block(compose: str, service: str, label: str) -> str:
    pattern = re.compile(
        rf"(?ms)^  {re.escape(service)}:\n.*?(?=^  [A-Za-z0-9_.-]+:\n|^volumes:\n)"
    )
    matches = list(pattern.finditer(compose))
    if len(matches) != 1:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_COMPOSE_UNSUPPORTED",
            f"{label} does not contain exactly one canonical {service} block",
        )
    return compose[: matches[0].start()] + compose[matches[0].end() :]


def _remove_init_dependency(compose: str, node: str, init_service: str, label: str) -> str:
    marker = (
        "    depends_on:\n"
        f"      {init_service}:\n"
        "        condition: service_completed_successfully\n"
    )
    node_marker = f"  {node}:\n"
    node_matches = list(re.finditer(rf"(?m)^  {re.escape(node)}:\n", compose))
    if len(node_matches) != 1 or compose.count(marker) != 1:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_COMPOSE_UNSUPPORTED",
            f"{label} init dependency is not canonical",
        )
    node_start = node_matches[0].start()
    marker_start = compose.index(marker)
    if marker_start < node_start:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_COMPOSE_UNSUPPORTED",
            f"{label} init dependency is outside {node}",
        )
    return compose.replace(marker, "", 1)


def _replace_exact(compose: str, old: str, new: str, label: str) -> str:
    if compose.count(old) != 1:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_COMPOSE_UNSUPPORTED",
            f"{label} expected exact lineage marker {old!r}",
        )
    return compose.replace(old, new, 1)


def _steady_compose(source: str, node: str) -> tuple[str, tuple[str, ...]]:
    spec = _TARGETS[node]
    label = f"{node} recovered Compose"
    original = _load_yaml(source, label)
    services = _mapping(original.get("services"), f"{label}.services")
    volumes = _mapping(original.get("volumes"), f"{label}.volumes")
    expected_services = {spec["init_service"], node, spec["guardian"]}
    expected_volumes = {"mother-config", "mother-data", spec["proof_volume"]}
    if original.get("name") != node or set(services) != expected_services or set(volumes) != expected_volumes:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_LINEAGE_MISMATCH",
            f"{node} recovered Compose is not the exact supported quorum-recovery lineage",
        )
    node_service = _mapping(services.get(node), f"{label}.services.{node}")
    guardian_service = _mapping(services.get(spec["guardian"]), f"{label}.services.{spec['guardian']}")
    labels = _mapping(node_service.get("labels"), f"{label}.services.{node}.labels")
    if labels.get("main_computer.mother.node") != node or labels.get("main_computer.mother.stage") != spec["source_stage"]:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_LINEAGE_MISMATCH",
            f"{node} stage labels do not match the recovered lineage",
        )
    if node == "mainnetc-super1" and labels.get("main_computer.mother.validator-activation") != "blocked":
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_LINEAGE_MISMATCH",
            "C validator activation label is not the exact recovered value",
        )
    if any(key in guardian_service for key in ("ports", "expose")):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_GUARDIAN_EXPOSED",
            f"{node} retained guardian must remain internal-only",
        )

    updated = _remove_service_block(source, spec["init_service"], label)
    updated = _remove_init_dependency(updated, node, spec["init_service"], label)
    updated = _replace_exact(
        updated,
        f"      main_computer.mother.stage: {spec['source_stage']}",
        "      main_computer.mother.stage: post-admission-steady-state",
        label,
    )
    if node == "mainnetc-super1":
        updated = _replace_exact(
            updated,
            "      main_computer.mother.validator-activation: blocked",
            "      main_computer.mother.validator-activation: active",
            label,
        )

    final = _load_yaml(updated, f"{node} steady-state Compose")
    final_services = _mapping(final.get("services"), f"{node} steady-state services")
    final_volumes = _mapping(final.get("volumes"), f"{node} steady-state volumes")
    if set(final_services) != {node, spec["guardian"]} or set(final_volumes) != expected_volumes:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_COMPOSE_UNSUPPORTED",
            f"{node} steady-state Compose has an unexpected service or volume",
        )
    final_node = _mapping(final_services[node], f"{node} steady-state Besu")
    final_guardian = _mapping(final_services[spec["guardian"]], f"{node} steady-state guardian")
    final_labels = _mapping(final_node.get("labels"), f"{node} steady-state labels")
    if final_labels.get("main_computer.mother.stage") != "post-admission-steady-state":
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_COMPOSE_UNSUPPORTED",
            f"{node} steady-state label was not installed",
        )
    if node == "mainnetc-super1" and final_labels.get("main_computer.mother.validator-activation") != "active":
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_COMPOSE_UNSUPPORTED",
            "C steady-state validator label was not activated",
        )
    rendered = updated
    if (
        spec["init_service"] in rendered
        or "MC_MOTHER_VALIDATOR_PRIVATE_KEY" in rendered
        or "--static-nodes-file=/config/static-nodes.json" not in rendered
        or "mother-data:/var/lib/besu" not in rendered
        or "mother-config:/config:ro" not in rendered
        or "8545:8545" in rendered
        or "qbft_proposeValidatorVote" in rendered
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_COMPOSE_UNSUPPORTED",
            f"{node} steady-state Compose violates retained-chain invariants",
        )
    if any(key in final_guardian for key in ("ports", "expose")):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_GUARDIAN_EXPOSED",
            f"{node} retained guardian became externally exposed",
        )
    return updated, tuple(spec["recognized_obsolete_components"])


def _load_reconciliation(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    reconciliation_path: Path,
    *,
    selected_nodes: Iterable[str],
    max_age_seconds: int,
) -> tuple[dict[str, Any], str, dict[str, Any], Path, str]:
    _selection(selected_nodes)
    if private_state.binding.generation != 2:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_BINDING_MISMATCH",
            "post-admission steady-state cleanup requires Mother generation 2",
        )
    verify_validator_quorum_recovery_reconciliation(
        paths,
        private_state,
        Path(reconciliation_path),
        selected_nodes=("mainnetc-super1",),
        max_age_seconds=max_age_seconds,
    )
    reconciliation, _, reconciliation_sha = _canonical_under(
        paths,
        Path(reconciliation_path),
        _RECONCILIATION_DIRECTORY,
        "quorum recovery reconciliation",
    )
    if reconciliation.get("mother_binding") != _binding(private_state):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_BINDING_MISMATCH",
            "reconciliation does not match current Mother state",
        )
    summary = _mapping(reconciliation.get("summary"), "reconciliation.summary")
    required_summary = (
        "quorum_recovered",
        "validator_set_verified",
        "blocks_advancing",
        "latest_block_fresh",
        "initial_besu_running_healthy",
        "replica_besu_running_healthy",
        "initial_guardian_running_healthy",
        "replica_guardian_running_healthy",
        "component_scoped_health_reconciled",
        "complete",
    )
    if (
        any(summary.get(key) is not True for key in required_summary)
        or summary.get("validator_vote_performed") is not False
        or summary.get("live_mutation_performed") is not False
        or summary.get("next_phase") != "stage-post-admission-steady-state"
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_INVALID",
            "reconciliation does not prove the exact recovered steady-state precondition",
        )

    source_ref = _mapping(reconciliation.get("source_release"), "reconciliation.source_release")
    source_path = _resolve(
        paths,
        source_ref.get("locator"),
        _QUORUM_RELEASE_DIRECTORY,
        "quorum recovery source release",
    )
    source_release, _, source_file_sha = _canonical_under(
        paths,
        source_path,
        _QUORUM_RELEASE_DIRECTORY,
        "quorum recovery source release",
    )
    source_digest = _sha256(
        source_release.get("validator_quorum_recovery_release_sha256"),
        "source quorum release digest",
    )
    if (
        source_release.get("kind")
        != "main_computer.mother.deployment_validator_quorum_recovery_release.v1"
        or source_release.get("mother_binding") != _binding(private_state)
        or source_ref.get("sha256") != source_digest
        or source_ref.get("file_sha256") != source_file_sha
        or _digest_without(source_release, "validator_quorum_recovery_release_sha256") != source_digest
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_SOURCE_RELEASE_INVALID",
            "reconciliation source release binding is invalid",
        )

    plan = _mapping(source_release.get("execution_plan"), "source release execution plan")
    preconditions = _mapping(source_release.get("preconditions"), "source release preconditions")
    targets = reconciliation.get("targets")
    if type(targets) is not list or len(targets) != 2:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_INVALID",
            "reconciliation must contain exactly A and C targets",
        )
    by_node = {
        target.get("node"): target
        for target in targets
        if isinstance(target, Mapping) and type(target.get("node")) is str
    }
    if set(by_node) != set(_TARGETS):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_INVALID",
            "reconciliation target set is not exactly A and C",
        )
    for node, spec in _TARGETS.items():
        target = _mapping(by_node[node], f"reconciliation target {node}")
        required = [node, spec["guardian"]]
        source_compose = _mapping(plan.get(spec["source_compose_key"]), f"source release {node} Compose")
        if (
            target.get("controller_id") != spec["controller_id"]
            or target.get("required_components") != required
            or target.get("required_components_healthy") is not True
            or _mapping(target.get("compose_binding"), f"{node} compose binding").get("semantic_sha256")
            != source_compose.get("semantic_sha256")
        ):
            raise _error(
                "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_INVALID",
                f"reconciliation target {node} is not bound to the exact recovered Compose and components",
            )
        source_service = _mapping(preconditions.get(spec["source_service_key"]), f"source release {node} service")
        if source_service.get("node") != node or source_service.get("controller_id") != spec["controller_id"]:
            raise _error(
                "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_SOURCE_RELEASE_INVALID",
                f"source release target {node} changed",
            )

    validators = reconciliation.get("validator_set")
    if type(validators) is not list or len(validators) != 2:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_VALIDATOR_SET_INVALID",
            "reconciliation validator set must contain exactly two addresses",
        )
    normalized = sorted({_address(item, "reconciliation validator") for item in validators})
    if len(normalized) != 2 or normalized != sorted({_address(item, "source validator") for item in plan.get("validator_set", [])}):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_VALIDATOR_SET_INVALID",
            "reconciliation validator set does not match the recovered release",
        )
    if _contains_sensitive(reconciliation) or _contains_sensitive(source_release):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_INVALID",
            "cleanup source artifacts contain sensitive output",
        )
    return reconciliation, reconciliation_sha, source_release, source_path, source_file_sha


def build_post_admission_steady_state_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    reconciliation_path: Path,
    *,
    network: str = "mainnet",
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 86400,
    created_at: str | None = None,
) -> dict[str, Any]:
    selected = _selection(selected_nodes)
    reconciliation, reconciliation_sha, source_release, source_path, source_file_sha = _load_reconciliation(
        paths,
        private_state,
        Path(reconciliation_path),
        selected_nodes=selected,
        max_age_seconds=max_age_seconds,
    )
    if network != reconciliation.get("network") or network != source_release.get("network"):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_NETWORK_MISMATCH",
            "cleanup network does not match reconciliation",
        )
    plan = _mapping(source_release["execution_plan"], "source release execution plan")
    pre = _mapping(source_release["preconditions"], "source release preconditions")
    created_text = _timestamp(created_at)

    targets: dict[str, Any] = {}
    mutations: list[dict[str, Any]] = []
    for node in _CANONICAL_ORDER:
        spec = _TARGETS[node]
        source_compose = _mapping(plan[spec["source_compose_key"]], f"{node} recovered Compose")
        recovered_text = source_compose.get("canonical_text")
        if type(recovered_text) is not str:
            raise _error(
                "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_SOURCE_RELEASE_INVALID",
                f"{node} recovered Compose text is missing",
            )
        steady_text, obsolete = _steady_compose(recovered_text, node)
        steady_bytes = steady_text.encode("utf-8")
        service = _mapping(pre[spec["source_service_key"]], f"{node} service precondition")
        service_uuid = _identifier(service.get("service_uuid"), f"{node} service UUID")
        endpoint_uuid = urllib.parse.quote(service_uuid, safe="")
        body = {
            "name": node,
            "docker_compose_raw": base64.b64encode(steady_bytes).decode("ascii"),
        }
        targets[node] = {
            "node": node,
            "controller_id": spec["controller_id"],
            "service_uuid": service_uuid,
            "accepted_aggregate_statuses": ["degraded:unhealthy", "running:healthy"],
            "required_healthy_components": [node, spec["guardian"]],
            "recognized_obsolete_components": list(obsolete),
            "removed_compose_services": [spec["init_service"]],
            "recovered_compose": {
                "canonical_text": recovered_text,
                "sha256": hashlib.sha256(recovered_text.encode("utf-8")).hexdigest(),
                "semantic_sha256": _compose_semantic_sha256(recovered_text, f"{node} recovered Compose"),
            },
            "steady_state_compose": {
                "canonical_text": steady_text,
                "sha256": hashlib.sha256(steady_bytes).hexdigest(),
                "semantic_sha256": _compose_semantic_sha256(steady_text, f"{node} steady-state Compose"),
                "retained_services": [node, spec["guardian"]],
                "retained_guardian_name": spec["guardian"],
                "retained_guardian_semantics": True,
                "static_peer_configuration_retained": True,
                "chain_data_volume_retained": True,
                "identity_material_retained": True,
            },
        }
        base_ordinal = 1 if node == "mainnetc-super1" else 3
        mutations.extend(
            [
                {
                    "ordinal": base_ordinal,
                    "mutation_id": f"{node}.install-post-admission-steady-state",
                    "controller_id": spec["controller_id"],
                    "method": "PATCH",
                    "endpoint": f"/api/v1/services/{endpoint_uuid}",
                    "canonical_request_body": body,
                    "body_sha256": hashlib.sha256(canonical_json(body)).hexdigest(),
                    "success_statuses": [200, 201, 202],
                },
                {
                    "ordinal": base_ordinal + 1,
                    "mutation_id": f"{node}.deploy-post-admission-steady-state",
                    "controller_id": spec["controller_id"],
                    "method": "GET",
                    "endpoint": f"/api/v1/deploy?uuid={endpoint_uuid}&force=true",
                    "canonical_request_body": None,
                    "body_sha256": None,
                    "success_statuses": [200, 201, 202],
                },
            ]
        )

    source_digest = _sha256(
        source_release.get("validator_quorum_recovery_release_sha256"),
        "source quorum release digest",
    )
    validator_set = sorted({_address(item, "validator") for item in reconciliation["validator_set"]})
    transaction: dict[str, Any] = {
        "kind": _TRANSACTION_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "network": network,
        "mother_binding": _binding(private_state),
        "staged_scope": "replace-exact-recovered-compose-with-post-admission-steady-state",
        "reconciliation": {
            "locator": _relative(paths, Path(reconciliation_path), "quorum recovery reconciliation"),
            "sha256": reconciliation_sha,
        },
        "source_quorum_recovery_release": {
            "locator": _relative(paths, source_path, "quorum recovery source release"),
            "sha256": source_digest,
            "file_sha256": source_file_sha,
        },
        "chain": {
            "chain_id": reconciliation["chain_id"],
            "genesis_sha256": _sha256(reconciliation["genesis_sha256"], "genesis SHA-256"),
            "validator_set": validator_set,
            "quorum_recovered": True,
            "blocks_advancing": True,
        },
        "targets": targets,
        "execution_plan": {
            "restart_order": ["mainnetc-super1", "mainneta-super1"],
            "restart_strategy": "C-health-and-joint-block-proof-before-A",
            "mutations": sorted(mutations, key=lambda item: item["ordinal"]),
            "final_required_aggregate_status": "running:healthy",
            "final_required_components": {
                node: [node, _TARGETS[node]["guardian"]] for node in _CANONICAL_ORDER
            },
        },
        "authority": {
            "cleanup_authorized": False,
            "live_execution_authorized": False,
            "validator_vote_authorized": False,
            "identity_change_authorized": False,
            "genesis_change_authorized": False,
            "volume_deletion_authorized": False,
            "requested_use_limit": 0,
        },
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "allowed_controllers": ["coolify-c", "coolify-a"],
            "allowed_mutation_count": 4,
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
            "host_rpc_mapping_present": False,
            "validator_vote_present": False,
            "validator_vote_performed": False,
            "identity_rotation": False,
            "genesis_change": False,
            "chain_reset": False,
            "volume_deletion": False,
            "besu_data_deletion": False,
            "historical_evidence_deletion": False,
            "network_access_performed": False,
            "live_mutation_performed": False,
            "aggregate_precleanup_badge_authoritative": False,
            "required_component_health_authoritative": True,
        },
        "summary": {
            "clean": True,
            "transaction_verified_offline": True,
            "mutation_count": 4,
            "removed_compose_service_count": 2,
            "retained_service_count": 4,
            "validator_set_verified": True,
            "blocks_advancing_verified_by_reconciliation": True,
            "validator_vote_authorized": False,
            "live_execution_authorized": False,
            "next_phase": "release-post-admission-steady-state",
        },
        "post_admission_steady_state_transaction_sha256": None,
    }
    transaction["post_admission_steady_state_transaction_sha256"] = _digest_without(
        transaction,
        "post_admission_steady_state_transaction_sha256",
    )
    if _contains_sensitive(transaction):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_INVALID",
            "steady-state transaction contains sensitive material",
        )
    return transaction


def write_post_admission_steady_state_transaction(
    paths: PrivateStatePaths,
    transaction: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(transaction)
    digest = _digest_without(document, "post_admission_steady_state_transaction_sha256")
    if (
        document.get("kind") != _TRANSACTION_KIND
        or document.get("post_admission_steady_state_transaction_sha256") != digest
        or _contains_sensitive(document)
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_TRANSACTION_INVALID",
            "steady-state transaction is malformed",
        )
    payload = canonical_json(document)
    destination = _ensure_root(paths, _TRANSACTION_DIRECTORY, operation) / (
        f"{re.sub(r'[^0-9A-Za-z]+', '', document['created_at'])[:32]}-{digest[:16]}.json"
    )
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_post_admission_steady_state_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    document, _, byte_sha = _canonical_under(
        paths,
        Path(transaction_path),
        _TRANSACTION_DIRECTORY,
        "post-admission steady-state transaction",
    )
    if (
        document.get("kind") != _TRANSACTION_KIND
        or document.get("mother_binding") != _binding(private_state)
        or _contains_sensitive(document)
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_TRANSACTION_INVALID",
            "steady-state transaction is invalid",
        )
    digest = _digest_without(document, "post_admission_steady_state_transaction_sha256")
    if document.get("post_admission_steady_state_transaction_sha256") != digest:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_TRANSACTION_INVALID",
            "steady-state transaction digest mismatch",
        )
    created = _parse_utc(document.get("created_at"), "transaction.created_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - created).total_seconds())
    if age < -60 or age > max_age_seconds:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_TRANSACTION_STALE",
            "steady-state transaction is outside its verification age window",
        )
    reconciliation_ref = _mapping(document.get("reconciliation"), "transaction.reconciliation")
    reconciliation_path = _resolve(
        paths,
        reconciliation_ref.get("locator"),
        _RECONCILIATION_DIRECTORY,
        "quorum recovery reconciliation",
    )
    expected = build_post_admission_steady_state_transaction(
        paths,
        private_state,
        reconciliation_path,
        network=document["network"],
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
        created_at=document["created_at"],
    )
    if expected != document:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_TRANSACTION_INVALID",
            "steady-state transaction does not rebuild exactly",
        )
    return {
        "clean": True,
        "transaction_path": str(Path(transaction_path)),
        "post_admission_steady_state_transaction_sha256": digest,
        "byte_sha256": byte_sha,
        "age_seconds": max(0, age),
        "network": document["network"],
        "nodes": list(_CANONICAL_ORDER),
        "chain_id": document["chain"]["chain_id"],
        "genesis_sha256": document["chain"]["genesis_sha256"],
        "validator_set": list(document["chain"]["validator_set"]),
        "mutation_count": 4,
        "validator_vote_authorized": False,
        "live_execution_authorized": False,
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "mother_binding": dict(document["mother_binding"]),
    }


def build_post_admission_steady_state_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    acknowledged_transaction_sha256: str,
    selected_nodes: Iterable[str] = (),
    transaction_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
) -> dict[str, Any]:
    verified = verify_post_admission_steady_state_transaction(
        paths,
        private_state,
        Path(transaction_path),
        selected_nodes=selected_nodes,
        max_age_seconds=transaction_max_age_seconds,
    )
    acknowledged = _sha256(acknowledged_transaction_sha256, "acknowledged transaction SHA-256")
    if acknowledged != verified["post_admission_steady_state_transaction_sha256"]:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the steady-state transaction",
        )
    transaction, _, transaction_file_sha = _canonical_under(
        paths,
        Path(transaction_path),
        _TRANSACTION_DIRECTORY,
        "post-admission steady-state transaction",
    )
    ttl = _duration(expires_in_seconds)
    created_text = _timestamp(created_at)
    created = _parse_utc(created_text, "release.created_at")
    expires_text = (created + timedelta(seconds=ttl)).isoformat(timespec="seconds").replace("+00:00", "Z")
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "expires_at": expires_text,
        "network": transaction["network"],
        "mother_binding": _binding(private_state),
        "staged_scope": "authorize-one-use-post-admission-steady-state-cleanup",
        "transaction": {
            "locator": _relative(paths, Path(transaction_path), "steady-state transaction"),
            "sha256": acknowledged,
            "file_sha256": transaction_file_sha,
        },
        "reconciliation": dict(transaction["reconciliation"]),
        "chain": dict(transaction["chain"]),
        "targets": dict(transaction["targets"]),
        "execution_plan": dict(transaction["execution_plan"]),
        "authority": {
            "cleanup_authorized": True,
            "live_execution_authorized": False,
            "validator_vote_authorized": False,
            "identity_change_authorized": False,
            "genesis_change_authorized": False,
            "volume_deletion_authorized": False,
            "requested_use_limit": 1,
            "authorization_source": "explicit-operator-release",
        },
        "policy": {
            **dict(transaction["policy"]),
            "requested_use_limit": 1,
            "network_access_performed": False,
            "live_mutation_performed": False,
        },
        "summary": {
            "release_valid": True,
            "mutation_count": 4,
            "restart_order": list(_CANONICAL_ORDER),
            "validator_vote_authorized": False,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "next_phase_after_apply": "verify-post-admission-steady-state-evidence",
        },
        "post_admission_steady_state_release_sha256": None,
    }
    release["post_admission_steady_state_release_sha256"] = _digest_without(
        release,
        "post_admission_steady_state_release_sha256",
    )
    if _contains_sensitive(release):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RELEASE_INVALID",
            "steady-state release contains sensitive material",
        )
    return release


def write_post_admission_steady_state_release(
    paths: PrivateStatePaths,
    release: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(release)
    digest = _digest_without(document, "post_admission_steady_state_release_sha256")
    if (
        document.get("kind") != _RELEASE_KIND
        or document.get("post_admission_steady_state_release_sha256") != digest
        or _contains_sensitive(document)
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RELEASE_INVALID",
            "steady-state release is malformed",
        )
    payload = canonical_json(document)
    destination = _ensure_root(paths, _RELEASE_DIRECTORY, operation) / (
        f"{re.sub(r'[^0-9A-Za-z]+', '', document['created_at'])[:32]}-{digest[:16]}.json"
    )
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_post_admission_steady_state_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    document, _, byte_sha = _canonical_under(
        paths,
        Path(release_path),
        _RELEASE_DIRECTORY,
        "post-admission steady-state release",
    )
    if (
        document.get("kind") != _RELEASE_KIND
        or document.get("mother_binding") != _binding(private_state)
        or _contains_sensitive(document)
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RELEASE_INVALID",
            "steady-state release is invalid",
        )
    digest = _digest_without(document, "post_admission_steady_state_release_sha256")
    if document.get("post_admission_steady_state_release_sha256") != digest:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RELEASE_INVALID",
            "steady-state release digest mismatch",
        )
    created = _parse_utc(document.get("created_at"), "release.created_at")
    expires = _parse_utc(document.get("expires_at"), "release.expires_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - created).total_seconds())
    if age < -1 or reference > expires or age > max_age_seconds:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RELEASE_STALE",
            "steady-state release is expired or stale",
        )
    transaction_ref = _mapping(document.get("transaction"), "release.transaction")
    transaction_path = _resolve(
        paths,
        transaction_ref.get("locator"),
        _TRANSACTION_DIRECTORY,
        "post-admission steady-state transaction",
    )
    expected = build_post_admission_steady_state_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_transaction_sha256=_sha256(transaction_ref.get("sha256"), "transaction SHA-256"),
        selected_nodes=selected_nodes,
        transaction_max_age_seconds=transaction_max_age_seconds,
        expires_in_seconds=int((expires - created).total_seconds()),
        created_at=document["created_at"],
    )
    if expected != document:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RELEASE_INVALID",
            "steady-state release does not rebuild exactly",
        )
    return {
        "clean": True,
        "release_path": str(Path(release_path)),
        "post_admission_steady_state_release_sha256": digest,
        "byte_sha256": byte_sha,
        "created_at": document["created_at"],
        "expires_at": document["expires_at"],
        "network": document["network"],
        "nodes": list(_CANONICAL_ORDER),
        "mutation_count": 4,
        "cleanup_authorized": True,
        "validator_vote_authorized": False,
        "live_execution_authorized": False,
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "mother_binding": dict(document["mother_binding"]),
    }


def inspect_post_admission_steady_state_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
) -> dict[str, Any]:
    verified = verify_post_admission_steady_state_release(
        paths,
        private_state,
        Path(release_path),
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
    )
    acknowledged = _sha256(acknowledged_release_sha256, "acknowledged release SHA-256")
    if acknowledged != verified["post_admission_steady_state_release_sha256"]:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the steady-state release",
        )
    claim_path = _root(paths, _CLAIM_DIRECTORY) / f"{acknowledged}.json"
    return {
        **verified,
        "execute_requested": False,
        "executor_implemented": True,
        "release_already_claimed": claim_path.exists(),
        "live_execution_authorized": True,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "validator_vote_performed": False,
    }


def _inspect_live_target(
    *,
    controller: Any,
    target: Mapping[str, Any],
    expected_compose: str,
    accepted_statuses: Iterable[str],
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    phase: str,
) -> dict[str, Any]:
    node = target["node"]
    service_uuid = target["service_uuid"]
    inventory = _http(
        controller,
        "GET",
        "/api/v1/services",
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    record = _service_record(inventory["payload"], service_uuid, node) if inventory["ok"] else None
    aggregate = _service_status(record) if record is not None else ""
    endpoint = f"/api/v1/services/{service_uuid}"
    detail = _http(
        controller,
        "GET",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    if not inventory["ok"] or aggregate not in set(accepted_statuses) or not detail["ok"]:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_PRECONDITION_FAILED",
            f"{node} is outside the exact cleanup precondition",
        )
    try:
        binding = _match_service_compose(detail["payload"], expected_compose, f"{node} exact cleanup Compose")
    except MotherDeploymentGenesisBirthError as exc:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_COMPOSE_MISMATCH",
            _safe_message(exc),
        ) from exc
    required = tuple(target["required_healthy_components"])
    component_ok, components = _component_health(detail["payload"], required)
    if not component_ok:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_COMPONENT_UNHEALTHY",
            f"{node} Besu or retained guardian is not running:healthy",
        )
    names = sorted({item.get("name", "") for item in _component_status_records(detail["payload"]) if item.get("name")})
    return {
        "phase": phase,
        "node": node,
        "controller_id": target["controller_id"],
        "service_uuid": service_uuid,
        "aggregate_service_status": aggregate,
        "required_components": list(required),
        "component_statuses": components,
        "component_names": names,
        "compose_binding": {
            "mode": binding["mode"],
            "semantic_sha256": binding["semantic_sha256"],
        },
        "inventory_response_sha256": inventory["response_sha256"],
        "detail_response_sha256": detail["response_sha256"],
        "verified": True,
    }


def _wait_target_state(
    *,
    controller: Any,
    target: Mapping[str, Any],
    expected_compose: str,
    require_aggregate_healthy: bool,
    require_obsolete_absent: bool,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
    observations: list[dict[str, Any]],
    phase: str,
) -> dict[str, Any]:
    deadline = _MONOTONIC() + max_wait_seconds
    last = ""
    while True:
        try:
            observation = _inspect_live_target(
                controller=controller,
                target=target,
                expected_compose=expected_compose,
                accepted_statuses=(
                    ("running:healthy",)
                    if require_aggregate_healthy
                    else tuple(target["accepted_aggregate_statuses"])
                ),
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                phase=phase,
            )
            last = observation["aggregate_service_status"]
            obsolete = set(target["recognized_obsolete_components"])
            present = sorted(obsolete.intersection(observation["component_names"]))
            observation["recognized_obsolete_components_present"] = present
            observation["obsolete_components_absent"] = not present
            observation["aggregate_service_healthy"] = last == "running:healthy"
            observation["observed_at"] = _timestamp()
            if (not require_aggregate_healthy or last == "running:healthy") and (
                not require_obsolete_absent or not present
            ):
                observations.append(observation)
                return observation
            observations.append(observation)
        except MotherDeploymentPostAdmissionSteadyStateError as exc:
            observations.append(
                {
                    "phase": phase,
                    "node": target["node"],
                    "aggregate_service_status": last,
                    "verified": False,
                    "failure_code": exc.code,
                    "failure_message": _safe_message(exc),
                    "observed_at": _timestamp(),
                }
            )
        if _MONOTONIC() >= deadline:
            raise _error(
                "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_NOT_HEALTHY",
                f"{target['node']} did not reach the exact steady-state condition (last aggregate status {last!r})",
            )
        _SLEEP(max(0.0, poll_interval_seconds))


def _wait_guardian_refresh_window() -> int:
    started = _MONOTONIC()
    _SLEEP(_GUARDIAN_REFRESH_WAIT_SECONDS)
    elapsed = _MONOTONIC() - started
    if elapsed < _GUARDIAN_REFRESH_WAIT_SECONDS:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_GUARDIAN_REFRESH_NOT_PROVEN",
            "A guardian was not re-observed beyond its proof freshness window",
        )
    return int(elapsed)


def _healthy_component(observation: Mapping[str, Any] | None, name: str) -> bool:
    if not isinstance(observation, Mapping):
        return False
    records = observation.get("component_statuses")
    if type(records) is not list:
        return False
    matches = [
        item
        for item in records
        if isinstance(item, Mapping) and item.get("name") == name
    ]
    return len(matches) == 1 and matches[0].get("status") == "running:healthy"


def _successful_observation(
    observations: Any,
    *,
    phase: str,
    target: Mapping[str, Any],
    compose_key: str,
    require_aggregate_healthy: bool,
    require_obsolete_absent: bool,
) -> Mapping[str, Any] | None:
    if type(observations) is not list:
        return None
    matches = [
        item
        for item in observations
        if isinstance(item, Mapping)
        and item.get("phase") == phase
        and item.get("node") == target.get("node")
        and item.get("verified") is True
    ]
    if len(matches) != 1:
        return None
    observation = matches[0]
    compose = target.get(compose_key)
    binding = observation.get("compose_binding")
    if (
        not isinstance(compose, Mapping)
        or not isinstance(binding, Mapping)
        or binding.get("semantic_sha256") != compose.get("semantic_sha256")
        or observation.get("controller_id") != target.get("controller_id")
        or observation.get("service_uuid") != target.get("service_uuid")
        or observation.get("required_components") != target.get("required_healthy_components")
    ):
        return None
    for name in target.get("required_healthy_components", []):
        if type(name) is not str or not _healthy_component(observation, name):
            return None
    if require_aggregate_healthy and observation.get("aggregate_service_healthy") is not True:
        return None
    if require_obsolete_absent and observation.get("obsolete_components_absent") is not True:
        return None
    return observation


def _mutation_sequence_verified(
    release: Mapping[str, Any],
    receipts: Any,
) -> bool:
    plan = release.get("execution_plan")
    mutations = plan.get("mutations") if isinstance(plan, Mapping) else None
    if type(mutations) is not list or type(receipts) is not list or len(mutations) != 4 or len(receipts) != 4:
        return False
    for mutation, receipt in zip(mutations, receipts, strict=True):
        if not isinstance(mutation, Mapping) or not isinstance(receipt, Mapping):
            return False
        expected = {
            "ordinal": mutation.get("ordinal"),
            "mutation_id": mutation.get("mutation_id"),
            "controller_id": mutation.get("controller_id"),
            "method": mutation.get("method"),
            "endpoint": mutation.get("endpoint"),
            "body_sha256": mutation.get("body_sha256"),
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            return False
        if receipt.get("status") != "succeeded" or receipt.get("live_write_acknowledged") is not True:
            return False
    return True


def _derive_execution_facts(
    release: Mapping[str, Any],
    receipts: Any,
    observations: Any,
    guardian_refresh_gate: Any,
    failure: Any,
) -> dict[str, bool]:
    targets = release.get("targets")
    if not isinstance(targets, Mapping):
        targets = {}
    a_target = targets.get("mainneta-super1")
    c_target = targets.get("mainnetc-super1")
    if not isinstance(a_target, Mapping):
        a_target = {}
    if not isinstance(c_target, Mapping):
        c_target = {}

    c_after_restart = _successful_observation(
        observations,
        phase="C-steady-state-and-aggregate-health",
        target=c_target,
        compose_key="steady_state_compose",
        require_aggregate_healthy=True,
        require_obsolete_absent=True,
    )
    a_before_refresh = _successful_observation(
        observations,
        phase="A-guardian-before-refresh-window",
        target=a_target,
        compose_key="recovered_compose",
        require_aggregate_healthy=False,
        require_obsolete_absent=False,
    )
    a_after_refresh = _successful_observation(
        observations,
        phase="A-guardian-after-refresh-window",
        target=a_target,
        compose_key="recovered_compose",
        require_aggregate_healthy=False,
        require_obsolete_absent=False,
    )
    a_final = _successful_observation(
        observations,
        phase="mainneta-super1-final-steady-state",
        target=a_target,
        compose_key="steady_state_compose",
        require_aggregate_healthy=True,
        require_obsolete_absent=True,
    )
    c_final = _successful_observation(
        observations,
        phase="mainnetc-super1-final-steady-state",
        target=c_target,
        compose_key="steady_state_compose",
        require_aggregate_healthy=True,
        require_obsolete_absent=True,
    )

    phase_order = {
        item.get("phase"): index
        for index, item in enumerate(observations if type(observations) is list else [])
        if isinstance(item, Mapping) and item.get("verified") is True
    }
    ordered_observations = (
        all(
            phase in phase_order
            for phase in (
                "C-steady-state-and-aggregate-health",
                "A-guardian-before-refresh-window",
                "A-guardian-after-refresh-window",
                "mainneta-super1-final-steady-state",
                "mainnetc-super1-final-steady-state",
            )
        )
        and phase_order["C-steady-state-and-aggregate-health"]
        < phase_order["A-guardian-before-refresh-window"]
        < phase_order["A-guardian-after-refresh-window"]
        < phase_order["mainneta-super1-final-steady-state"]
        < phase_order["mainnetc-super1-final-steady-state"]
    )

    gate = guardian_refresh_gate if isinstance(guardian_refresh_gate, Mapping) else {}
    gate_verified = (
        c_after_restart is not None
        and a_before_refresh is not None
        and a_after_refresh is not None
        and ordered_observations
        and gate.get("pre_observation_phase") == "A-guardian-before-refresh-window"
        and gate.get("post_observation_phase") == "A-guardian-after-refresh-window"
        and gate.get("guardian_health_freshness_seconds") == _GUARDIAN_HEALTH_FRESHNESS_SECONDS
        and gate.get("required_wait_seconds") == _GUARDIAN_REFRESH_WAIT_SECONDS
        and type(gate.get("observed_wait_seconds")) is int
        and not isinstance(gate.get("observed_wait_seconds"), bool)
        and gate.get("observed_wait_seconds") >= _GUARDIAN_REFRESH_WAIT_SECONDS
        and gate.get("verified") is True
    )
    mutation_sequence = _mutation_sequence_verified(release, receipts)

    chain = release.get("chain")
    validators = chain.get("validator_set") if isinstance(chain, Mapping) else None
    try:
        normalized_validators = (
            sorted({_address(item, "released validator") for item in validators})
            if type(validators) is list
            else []
        )
    except MotherDeploymentPostAdmissionSteadyStateError:
        normalized_validators = []
    exact_validator_set = len(normalized_validators) == 2

    a_besu = _healthy_component(a_final, "mainneta-super1")
    c_besu = _healthy_component(c_final, "mainnetc-super1")
    a_guardian_name = (
        a_target.get("required_healthy_components", [None, None])[1]
        if type(a_target.get("required_healthy_components")) is list
        and len(a_target.get("required_healthy_components")) == 2
        else ""
    )
    c_guardian_name = (
        c_target.get("required_healthy_components", [None, None])[1]
        if type(c_target.get("required_healthy_components")) is list
        and len(c_target.get("required_healthy_components")) == 2
        else ""
    )
    a_guardian = type(a_guardian_name) is str and _healthy_component(a_final, a_guardian_name)
    c_guardian = type(c_guardian_name) is str and _healthy_component(c_final, c_guardian_name)

    facts = {
        "post_admission_steady_state_installed": mutation_sequence and a_final is not None and c_final is not None,
        "initial_aggregate_service_running_healthy": (
            a_final is not None and a_final.get("aggregate_service_healthy") is True
        ),
        "replica_aggregate_service_running_healthy": (
            c_final is not None and c_final.get("aggregate_service_healthy") is True
        ),
        "initial_besu_running_healthy": a_besu,
        "replica_besu_running_healthy": c_besu,
        "initial_guardian_running_healthy": a_guardian,
        "replica_guardian_running_healthy": c_guardian,
        "obsolete_phase_components_absent": (
            a_final is not None
            and c_final is not None
            and a_final.get("obsolete_components_absent") is True
            and c_final.get("obsolete_components_absent") is True
        ),
        "validator_set_verified": exact_validator_set and a_guardian and c_guardian,
        "blocks_advancing": gate_verified and a_guardian and c_guardian,
        "latest_block_fresh": gate_verified and a_guardian and c_guardian,
        "C_restarted_before_A": mutation_sequence and c_after_restart is not None,
        "joint_blocks_resumed_before_A_restart": gate_verified,
    }
    facts["clean"] = failure is None and all(facts.values())
    facts["complete"] = facts["clean"]
    return facts

def _write_evidence(
    paths: PrivateStatePaths,
    evidence: Mapping[str, Any],
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(evidence)
    if document.get("kind") != _EVIDENCE_KIND or _contains_sensitive(document):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_EVIDENCE_INVALID",
            "steady-state evidence is malformed",
        )
    payload = canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    destination = _ensure_root(paths, _EVIDENCE_DIRECTORY, operation) / (
        f"{re.sub(r'[^0-9A-Za-z]+', '', document['completed_at'])[:32]}-{digest[:16]}.json"
    )
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def execute_post_admission_steady_state_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    max_wait_seconds: float = 360.0,
    poll_interval_seconds: float = 5.0,
    opener: Any = _DEFAULT_OPENER,
    operation: OperationIdentity,
) -> dict[str, Any]:
    inspected = inspect_post_admission_steady_state_release(
        paths,
        private_state,
        Path(release_path),
        acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
    )
    if inspected["release_already_claimed"]:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RELEASE_ALREADY_CONSUMED",
            "steady-state release already has an execution claim",
        )
    release, _, _ = _canonical_under(
        paths,
        Path(release_path),
        _RELEASE_DIRECTORY,
        "post-admission steady-state release",
    )
    digest = inspected["post_admission_steady_state_release_sha256"]
    claim = {
        "kind": _CLAIM_KIND,
        "schema_version": 1,
        "claimed_at": _timestamp(),
        "release": {
            "locator": _relative(paths, Path(release_path), "steady-state release"),
            "sha256": digest,
        },
        "requested_use_limit": 1,
        "operation_id": operation.operation_id,
    }
    claim_path = _ensure_root(paths, _CLAIM_DIRECTORY, operation) / f"{digest}.json"
    if claim_path.exists():
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RELEASE_ALREADY_CONSUMED",
            "steady-state release already has an execution claim",
        )
    atomic_files.durable_create(claim_path, canonical_json(claim), operation=operation)
    _secure_private_path(claim_path, is_directory=False, operation=operation)

    controllers = {
        "coolify-a": resolve_coolify_controller(private_state, release["network"], "coolify-a"),
        "coolify-c": resolve_coolify_controller(private_state, release["network"], "coolify-c"),
    }
    targets = release["targets"]
    preconditions: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    guardian_refresh_gate: dict[str, Any] | None = None
    failure: dict[str, str] | None = None
    started = _timestamp()
    try:
        for node in _CANONICAL_ORDER:
            target = targets[node]
            preconditions.append(
                _inspect_live_target(
                    controller=controllers[target["controller_id"]],
                    target=target,
                    expected_compose=target["recovered_compose"]["canonical_text"],
                    accepted_statuses=target["accepted_aggregate_statuses"],
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                    phase=f"{node}-recovered-precondition",
                )
            )

        mutations = release["execution_plan"]["mutations"]
        expected_ids = [
            "mainnetc-super1.install-post-admission-steady-state",
            "mainnetc-super1.deploy-post-admission-steady-state",
            "mainneta-super1.install-post-admission-steady-state",
            "mainneta-super1.deploy-post-admission-steady-state",
        ]
        if (
            type(mutations) is not list
            or len(mutations) != 4
            or [item.get("mutation_id") for item in mutations] != expected_ids
            or [item.get("method") for item in mutations] != ["PATCH", "GET", "PATCH", "GET"]
        ):
            raise _error(
                "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RELEASE_INVALID",
                "released mutation set is not the exact C-then-A cleanup sequence",
            )

        for mutation in mutations:
            controller = controllers[mutation["controller_id"]]
            body = (
                dict(mutation["canonical_request_body"])
                if isinstance(mutation["canonical_request_body"], Mapping)
                else None
            )
            response = _http(
                controller,
                mutation["method"],
                mutation["endpoint"],
                body=body,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            ok = response["status"] in mutation["success_statuses"]
            receipts.append(
                {
                    "ordinal": mutation["ordinal"],
                    "mutation_id": mutation["mutation_id"],
                    "controller_id": mutation["controller_id"],
                    "method": mutation["method"],
                    "endpoint": mutation["endpoint"],
                    "body_sha256": mutation["body_sha256"],
                    "response": {
                        "status": response["status"],
                        "response_sha256": response["response_sha256"],
                        "byte_length": response["byte_length"],
                        "elapsed_ms": response["elapsed_ms"],
                        "ok": ok,
                    },
                    "live_write_acknowledged": ok,
                    "status": "succeeded" if ok else "failed",
                }
            )
            if not ok:
                raise _error(
                    "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_MUTATION_FAILED",
                    f"Coolify rejected {mutation['mutation_id']!r}",
                )

            if mutation["ordinal"] == 2:
                c_target = targets["mainnetc-super1"]
                _wait_target_state(
                    controller=controllers["coolify-c"],
                    target=c_target,
                    expected_compose=c_target["steady_state_compose"]["canonical_text"],
                    require_aggregate_healthy=True,
                    require_obsolete_absent=True,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    max_wait_seconds=max_wait_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                    opener=opener,
                    observations=observations,
                    phase="C-steady-state-and-aggregate-health",
                )
                a_target = targets["mainneta-super1"]
                _wait_target_state(
                    controller=controllers["coolify-a"],
                    target=a_target,
                    expected_compose=a_target["recovered_compose"]["canonical_text"],
                    require_aggregate_healthy=False,
                    require_obsolete_absent=False,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    max_wait_seconds=max_wait_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                    opener=opener,
                    observations=observations,
                    phase="A-guardian-before-refresh-window",
                )
                observed_wait = _wait_guardian_refresh_window()
                _wait_target_state(
                    controller=controllers["coolify-a"],
                    target=a_target,
                    expected_compose=a_target["recovered_compose"]["canonical_text"],
                    require_aggregate_healthy=False,
                    require_obsolete_absent=False,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    max_wait_seconds=max_wait_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                    opener=opener,
                    observations=observations,
                    phase="A-guardian-after-refresh-window",
                )
                guardian_refresh_gate = {
                    "guardian_health_freshness_seconds": _GUARDIAN_HEALTH_FRESHNESS_SECONDS,
                    "required_wait_seconds": _GUARDIAN_REFRESH_WAIT_SECONDS,
                    "observed_wait_seconds": observed_wait,
                    "pre_observation_phase": "A-guardian-before-refresh-window",
                    "post_observation_phase": "A-guardian-after-refresh-window",
                    "verified": True,
                }

            if mutation["ordinal"] == 4:
                for node in ("mainneta-super1", "mainnetc-super1"):
                    target = targets[node]
                    _wait_target_state(
                        controller=controllers[target["controller_id"]],
                        target=target,
                        expected_compose=target["steady_state_compose"]["canonical_text"],
                        require_aggregate_healthy=True,
                        require_obsolete_absent=True,
                        timeout=timeout,
                        max_response_bytes=max_response_bytes,
                        max_wait_seconds=max_wait_seconds,
                        poll_interval_seconds=poll_interval_seconds,
                        opener=opener,
                        observations=observations,
                        phase=f"{node}-final-steady-state",
                    )
    except MotherDeploymentPostAdmissionSteadyStateError as exc:
        failure = {"code": exc.code, "message": _safe_message(exc)}
    except Exception:
        failure = {
            "code": "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_UNEXPECTED_FAILURE",
            "message": "unexpected post-admission steady-state failure",
        }

    completed = _timestamp()
    succeeded = sum(item.get("status") == "succeeded" for item in receipts)
    live_mutation = any(item.get("live_write_acknowledged") is True for item in receipts)
    facts = _derive_execution_facts(
        release,
        receipts,
        observations,
        guardian_refresh_gate,
        failure,
    )
    complete = facts["complete"]
    evidence: dict[str, Any] = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "started_at": started,
        "completed_at": completed,
        "status": "pass" if complete else "failed",
        "mother_binding": dict(inspected["mother_binding"]),
        "network": release["network"],
        "nodes": list(_CANONICAL_ORDER),
        "release": {
            "locator": _relative(paths, Path(release_path), "steady-state release"),
            "sha256": digest,
        },
        "execution_claim": {
            "locator": _relative(paths, claim_path, "steady-state execution claim"),
        },
        "chain_id": release["chain"]["chain_id"],
        "genesis_sha256": release["chain"]["genesis_sha256"],
        "validator_set": list(release["chain"]["validator_set"]) if complete else None,
        "precondition_receipts": preconditions,
        "mutation_receipts": receipts,
        "health_observations": observations,
        "guardian_refresh_gate": guardian_refresh_gate,
        "failure": failure,
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "allowed_mutation_count": 4,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "host_rpc_mapping_present": False,
            "validator_vote_performed": False,
            "identity_rotation": False,
            "genesis_change": False,
            "chain_reset": False,
            "volume_deletion": False,
            "besu_data_deletion": False,
            "historical_evidence_deletion": False,
            "restart_order": list(_CANONICAL_ORDER),
            "secrets_in_output": False,
        },
        "summary": {
            **facts,
            "validator_vote_performed": False,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "planned_mutation_count": 4,
            "attempted_mutation_count": len(receipts),
            "succeeded_mutation_count": succeeded,
            "failed_mutation_count": sum(item.get("status") != "succeeded" for item in receipts),
            "network_access_performed": bool(preconditions or receipts or observations),
            "live_mutation_performed": live_mutation,
            "next_phase": "post-admission-steady-state-complete" if complete else "manual-review-required",
        },
    }
    evidence_path, evidence_sha = _write_evidence(paths, evidence, operation)
    evidence["evidence"] = {"path": str(evidence_path), "sha256": evidence_sha}
    return evidence



def _write_post_admission_steady_state_reconciliation(
    paths: PrivateStatePaths,
    reconciliation: Mapping[str, Any],
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(reconciliation)
    if document.get("kind") != _RECONCILIATION_KIND or _contains_sensitive(document):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_INVALID",
            "steady-state reconciliation is malformed",
        )
    payload = canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    destination = _ensure_root(paths, _STEADY_RECONCILIATION_DIRECTORY, operation) / (
        f"{re.sub(r'[^0-9A-Za-z]+', '', document['completed_at'])[:32]}-{digest[:16]}.json"
    )
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def _failed_mixed_state_source(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    max_age_seconds: int | None,
) -> tuple[dict[str, Any], str, Path, dict[str, Any], str, str]:
    evidence, _, evidence_sha = _canonical_under(
        paths,
        Path(evidence_path),
        _EVIDENCE_DIRECTORY,
        "failed post-admission steady-state evidence",
    )
    if evidence.get("kind") != _EVIDENCE_KIND or evidence.get("status") != "failed":
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_NOT_REQUIRED",
            "reconciliation requires failed steady-state execution evidence",
        )
    if evidence.get("mother_binding") != _binding(private_state):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_BINDING_MISMATCH",
            "failed evidence does not match current Mother state",
        )
    if max_age_seconds is not None:
        age = (
            datetime.now(timezone.utc)
            - _parse_utc(evidence.get("completed_at"), "evidence.completed_at")
        ).total_seconds()
        if age < -60 or age > max_age_seconds:
            raise _error(
                "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_STALE",
                "failed steady-state evidence is outside the reconciliation age window",
            )

    summary = evidence.get("summary")
    failure = evidence.get("failure")
    receipts = evidence.get("mutation_receipts")
    expected_ids = [
        "mainnetc-super1.install-post-admission-steady-state",
        "mainnetc-super1.deploy-post-admission-steady-state",
    ]
    if not (
        isinstance(summary, Mapping)
        and isinstance(failure, Mapping)
        and failure.get("code") == "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_NOT_HEALTHY"
        and summary.get("live_mutation_performed") is True
        and summary.get("attempted_mutation_count") == 2
        and summary.get("succeeded_mutation_count") == 2
        and summary.get("failed_mutation_count") == 0
        and summary.get("validator_vote_performed") is False
        and evidence.get("guardian_refresh_gate") is None
        and type(receipts) is list
        and len(receipts) == 2
        and [item.get("mutation_id") for item in receipts if isinstance(item, Mapping)]
        == expected_ids
        and [item.get("status") for item in receipts if isinstance(item, Mapping)]
        == ["succeeded", "succeeded"]
        and all(
            isinstance(item, Mapping)
            and item.get("live_write_acknowledged") is True
            for item in receipts
        )
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_UNSAFE_SOURCE",
            "failed evidence is not the exact C-only mutation and health-timeout state",
        )

    release_ref = evidence.get("release")
    if not isinstance(release_ref, Mapping):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_INVALID",
            "failed evidence has no release reference",
        )
    release_path = _resolve(
        paths,
        release_ref.get("locator"),
        _RELEASE_DIRECTORY,
        "source steady-state release",
    )
    release, _, release_file_sha = _canonical_under(
        paths,
        release_path,
        _RELEASE_DIRECTORY,
        "source steady-state release",
    )
    release_sha = _digest_without(release, "post_admission_steady_state_release_sha256")
    if not (
        release.get("kind") == _RELEASE_KIND
        and release.get("post_admission_steady_state_release_sha256") == release_sha
        and release_ref.get("sha256") == release_sha
        and release.get("mother_binding") == _binding(private_state)
        and release.get("network") == evidence.get("network")
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_BINDING_MISMATCH",
            "source release does not match the failed evidence and current Mother state",
        )

    claim_ref = evidence.get("execution_claim")
    if not isinstance(claim_ref, Mapping):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_INVALID",
            "failed evidence has no execution claim",
        )
    claim_path = _resolve(
        paths,
        claim_ref.get("locator"),
        _CLAIM_DIRECTORY,
        "source steady-state execution claim",
    )
    claim, _, _ = _canonical_under(
        paths,
        claim_path,
        _CLAIM_DIRECTORY,
        "source steady-state execution claim",
    )
    if not (
        claim.get("kind") == _CLAIM_KIND
        and claim.get("requested_use_limit") == 1
        and isinstance(claim.get("release"), Mapping)
        and claim["release"].get("sha256") == release_sha
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_BINDING_MISMATCH",
            "execution claim does not bind the consumed release",
        )

    plan = release.get("execution_plan")
    mutations = plan.get("mutations") if isinstance(plan, Mapping) else None
    if not (
        type(mutations) is list
        and len(mutations) == 4
        and [item.get("mutation_id") for item in mutations]
        == [
            "mainnetc-super1.install-post-admission-steady-state",
            "mainnetc-super1.deploy-post-admission-steady-state",
            "mainneta-super1.install-post-admission-steady-state",
            "mainneta-super1.deploy-post-admission-steady-state",
        ]
        and release.get("authority", {}).get("validator_vote_authorized") is False
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_INVALID",
            "source release mutation plan is not the exact C-then-A cleanup",
        )
    return evidence, evidence_sha, release_path, release, release_sha, release_file_sha


def _runtime_target_observation(
    *,
    controller: Any,
    target: Mapping[str, Any],
    expected_compose: str,
    expected_mode: str,
    require_aggregate_healthy: bool,
    require_obsolete_absent: bool,
    allow_exited_obsolete_records: bool = False,
    allow_degraded_aggregate_for_exited_obsolete_records: bool = False,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    node = target["node"]
    service_uuid = target["service_uuid"]
    inventory = _http(
        controller,
        "GET",
        "/api/v1/services",
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    detail = _http(
        controller,
        "GET",
        f"/api/v1/services/{service_uuid}",
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )

    inventory_status = ""
    detail_status = ""
    if inventory["ok"]:
        try:
            inventory_status = _service_status(
                _service_record(inventory["payload"], service_uuid, node)
            )
        except Exception:
            inventory_status = ""
    if detail["ok"]:
        try:
            detail_status = _service_status(
                _service_record(detail["payload"], service_uuid, node)
            )
        except Exception:
            detail_status = ""
    effective_status = inventory_status or detail_status
    status_source = (
        "inventory"
        if inventory_status
        else ("detail" if detail_status else "unavailable")
    )

    compose_matches = False
    compose_binding: dict[str, Any] | None = None
    compose_failure: str | None = None
    if detail["ok"]:
        try:
            binding = _match_service_compose(
                detail["payload"],
                expected_compose,
                f"{node} {expected_mode} Compose",
            )
            compose_matches = True
            compose_binding = {
                "mode": binding["mode"],
                "semantic_sha256": binding["semantic_sha256"],
            }
        except MotherDeploymentGenesisBirthError as exc:
            compose_failure = _safe_message(exc)

    required = tuple(target["required_healthy_components"])
    component_ok = False
    components: list[dict[str, Any]] = []
    component_records: list[dict[str, Any]] = []
    component_names: list[str] = []
    if detail["ok"]:
        component_ok, components = _component_health(detail["payload"], required)
        component_records = _component_status_records(detail["payload"])
        component_names = sorted(
            {
                item.get("name", "")
                for item in component_records
                if item.get("name")
            }
        )

    recognized_obsolete = set(target["recognized_obsolete_components"])
    required_names = set(required)
    obsolete_present = sorted(recognized_obsolete.intersection(component_names))
    unexpected_present = sorted(
        set(component_names).difference(required_names).difference(recognized_obsolete)
    )
    obsolete_statuses = sorted(
        (
            {
                "name": str(item.get("name", "")),
                "status": str(item.get("status", "")),
            }
            for item in component_records
            if item.get("name") in recognized_obsolete
        ),
        key=lambda item: item["name"],
    )
    obsolete_all_exited = bool(obsolete_statuses) and all(
        item["status"].strip().lower().startswith("exited")
        for item in obsolete_statuses
    )

    expected_services: set[str] = set()
    try:
        expected_document = yaml.safe_load(expected_compose)
        if isinstance(expected_document, Mapping):
            services = expected_document.get("services")
            if isinstance(services, Mapping):
                expected_services = {
                    str(name)
                    for name in services
                    if type(name) is str and name
                }
    except Exception:
        expected_services = set()
    obsolete_compose_services_absent = bool(expected_services) and not (
        recognized_obsolete.intersection(expected_services)
    )

    aggregate_healthy = effective_status == "running:healthy"
    status_conflict = bool(
        inventory_status
        and detail_status
        and inventory_status != detail_status
    )
    exited_obsolete_records_accepted = bool(
        allow_exited_obsolete_records
        and obsolete_present
        and obsolete_all_exited
        and obsolete_compose_services_absent
        and not unexpected_present
    )
    aggregate_requirement_met = bool(
        not require_aggregate_healthy
        or aggregate_healthy
        or (
            allow_degraded_aggregate_for_exited_obsolete_records
            and exited_obsolete_records_accepted
            and effective_status == "degraded:unhealthy"
        )
    )
    obsolete_requirement_met = bool(
        not require_obsolete_absent
        or not obsolete_present
        or exited_obsolete_records_accepted
    )
    component_scoped_verified = bool(
        inventory["ok"]
        and detail["ok"]
        and compose_matches
        and component_ok
        and not status_conflict
        and not unexpected_present
        and (not require_obsolete_absent or obsolete_compose_services_absent)
        and (
            not require_obsolete_absent
            or not obsolete_present
            or obsolete_all_exited
        )
    )
    verified = bool(
        component_scoped_verified
        and aggregate_requirement_met
        and obsolete_requirement_met
    )
    return {
        "node": node,
        "controller_id": target["controller_id"],
        "service_uuid": service_uuid,
        "expected_mode": expected_mode,
        "inventory_status": inventory_status,
        "detail_status": detail_status,
        "effective_aggregate_status": effective_status,
        "aggregate_status_source": status_source,
        "aggregate_status_conflict": status_conflict,
        "aggregate_service_healthy": aggregate_healthy,
        "required_components": list(required),
        "required_components_healthy": component_ok,
        "component_statuses": components,
        "component_names": component_names,
        "recognized_obsolete_components_present": obsolete_present,
        "recognized_obsolete_component_statuses": obsolete_statuses,
        "recognized_obsolete_component_records_all_exited": obsolete_all_exited,
        "unexpected_component_records_present": unexpected_present,
        "obsolete_components_absent": not obsolete_present,
        "obsolete_compose_services_absent": obsolete_compose_services_absent,
        "exited_obsolete_records_accepted": exited_obsolete_records_accepted,
        "component_scoped_verified": component_scoped_verified,
        "aggregate_badge_non_authoritative": bool(
            verified
            and not aggregate_healthy
            and exited_obsolete_records_accepted
        ),
        "compose_matches": compose_matches,
        "compose_binding": compose_binding,
        "compose_failure": compose_failure,
        "inventory_response": {
            "status": inventory["status"],
            "ok": inventory["ok"],
            "response_sha256": inventory["response_sha256"],
        },
        "detail_response": {
            "status": detail["status"],
            "ok": detail["ok"],
            "response_sha256": detail["response_sha256"],
        },
        "verified": verified,
        "observed_at": _timestamp(),
    }


def reconcile_post_admission_steady_state(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    operation: OperationIdentity,
) -> dict[str, Any]:
    _selection(selected_nodes)
    (
        evidence,
        evidence_sha,
        release_path,
        release,
        release_sha,
        release_file_sha,
    ) = _failed_mixed_state_source(
        paths,
        private_state,
        Path(evidence_path),
        max_age_seconds=max_age_seconds,
    )
    controllers = {
        "coolify-a": resolve_coolify_controller(
            private_state, release["network"], "coolify-a"
        ),
        "coolify-c": resolve_coolify_controller(
            private_state, release["network"], "coolify-c"
        ),
    }
    targets = release["targets"]
    c_target = targets["mainnetc-super1"]
    a_target = targets["mainneta-super1"]
    c_observation = _runtime_target_observation(
        controller=controllers["coolify-c"],
        target=c_target,
        expected_compose=c_target["steady_state_compose"]["canonical_text"],
        expected_mode="steady-state",
        require_aggregate_healthy=True,
        require_obsolete_absent=True,
        allow_exited_obsolete_records=True,
        allow_degraded_aggregate_for_exited_obsolete_records=True,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    a_observation = _runtime_target_observation(
        controller=controllers["coolify-a"],
        target=a_target,
        expected_compose=a_target["recovered_compose"]["canonical_text"],
        expected_mode="recovered",
        require_aggregate_healthy=False,
        require_obsolete_absent=False,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    a_status_accepted = (
        a_observation["effective_aggregate_status"]
        in set(a_target["accepted_aggregate_statuses"])
    )
    a_observation["accepted_recovered_aggregate_status"] = a_status_accepted
    a_observation["verified"] = bool(a_observation["verified"] and a_status_accepted)

    c_verified = c_observation["verified"] is True
    a_verified = a_observation["verified"] is True
    chain_continuity = bool(c_verified and a_verified)
    c_stale_records = list(
        c_observation["recognized_obsolete_components_present"]
    )
    a_stale_records = list(
        a_observation["recognized_obsolete_components_present"]
    )
    platform_stale_records_present = bool(c_stale_records or a_stale_records)
    c_strict_cleanup_complete = bool(
        c_observation["aggregate_service_healthy"]
        and c_observation["obsolete_components_absent"]
    )
    completed = _timestamp()
    document: dict[str, Any] = {
        "kind": _RECONCILIATION_KIND,
        "schema_version": 2,
        "completed_at": completed,
        "status": "pass" if chain_continuity else "manual-review-required",
        "mother_binding": _binding(private_state),
        "network": release["network"],
        "nodes": list(_CANONICAL_ORDER),
        "source_failed_evidence": {
            "locator": _relative(
                paths,
                Path(evidence_path),
                "failed post-admission steady-state evidence",
            ),
            "sha256": evidence_sha,
        },
        "source_release": {
            "locator": _relative(paths, release_path, "source steady-state release"),
            "sha256": release_sha,
            "file_sha256": release_file_sha,
        },
        "chain_id": release["chain"]["chain_id"],
        "genesis_sha256": release["chain"]["genesis_sha256"],
        "validator_set": list(release["chain"]["validator_set"]),
        "targets": [c_observation, a_observation],
        "proof_basis": {
            "C": (
                "exact steady-state Compose, healthy retained replica quorum guardian, "
                "obsolete services absent from Compose, and any retained Coolify "
                "component records restricted to recognized exited phase records"
            ),
            "A": (
                "exact recovered Compose plus healthy retained initial quorum guardian; "
                "A was not mutated by the failed execution"
            ),
            "chain_continuity": (
                "the exact retained guardians continuously verify chain identity, "
                "validator membership, peer state, and fresh block production"
            ),
            "aggregate_status": (
                "Coolify aggregate status is not authoritative when exact Compose and "
                "required component health match while only recognized exited records remain"
            ),
        },
        "policy": {
            "allowed_http_methods": ["GET"],
            "read_only": True,
            "network_access_performed": True,
            "live_mutation_performed": False,
            "validator_vote_performed": False,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "log_endpoints_queried": False,
            "release_reused": False,
            "aggregate_badge_authoritative": False,
            "recognized_exited_stale_records_tolerated": True,
            "running_obsolete_components_tolerated": False,
            "unexpected_component_records_tolerated": False,
            "direct_coolify_database_mutation_authorized": False,
            "service_record_deletion_authorized": False,
        },
        "summary": {
            "clean": chain_continuity,
            "source_execution_claim_consumed": True,
            "C_patch_and_deploy_previously_succeeded": True,
            "C_steady_state_verified": c_verified,
            "C_component_scoped_steady_state_verified": c_observation[
                "component_scoped_verified"
            ],
            "C_aggregate_service_healthy": c_observation[
                "aggregate_service_healthy"
            ],
            "C_obsolete_components_absent": c_observation[
                "obsolete_components_absent"
            ],
            "C_obsolete_compose_services_absent": c_observation[
                "obsolete_compose_services_absent"
            ],
            "C_stale_component_records_present": c_stale_records,
            "C_stale_component_records_all_exited": c_observation[
                "recognized_obsolete_component_records_all_exited"
            ],
            "C_unexpected_component_records_present": c_observation[
                "unexpected_component_records_present"
            ],
            "C_strict_aggregate_cleanup_complete": c_strict_cleanup_complete,
            "A_recovered_state_verified": a_verified,
            "A_not_restarted_by_failed_execution": True,
            "platform_stale_component_records_present": platform_stale_records_present,
            "aggregate_badge_non_authoritative_for_reconciliation": bool(
                c_observation["aggregate_badge_non_authoritative"]
            ),
            "validator_set_verified": chain_continuity,
            "blocks_advancing": chain_continuity,
            "latest_block_fresh": chain_continuity,
            "chain_continuity_verified": chain_continuity,
            "network_access_performed": True,
            "live_mutation_performed": False,
            "validator_vote_performed": False,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "complete": chain_continuity,
            "next_phase": (
                "stage-post-admission-steady-state-continuation"
                if chain_continuity
                else "manual-review-required"
            ),
        },
    }

    path, digest = _write_post_admission_steady_state_reconciliation(
        paths,
        document,
        operation,
    )
    return {
        **document,
        "reconciliation_artifact": {"path": str(path), "sha256": digest},
    }


def verify_post_admission_steady_state_reconciliation(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    reconciliation_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
) -> dict[str, Any]:
    _selection(selected_nodes)
    document, _, digest = _canonical_under(
        paths,
        Path(reconciliation_path),
        _STEADY_RECONCILIATION_DIRECTORY,
        "post-admission steady-state reconciliation",
    )
    if not (
        document.get("kind") == _RECONCILIATION_KIND
        and document.get("schema_version") == 2
        and document.get("status") == "pass"
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_INVALID",
            "reconciliation is not a passing canonical schema-v2 document",
        )
    age = (
        datetime.now(timezone.utc)
        - _parse_utc(
            document.get("completed_at"),
            "steady_state_reconciliation.completed_at",
        )
    ).total_seconds()
    if age < -60 or age > max_age_seconds:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_STALE",
            "steady-state reconciliation is outside the verification age window",
        )
    if document.get("mother_binding") != _binding(private_state):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_BINDING_MISMATCH",
            "steady-state reconciliation does not match current Mother state",
        )

    source = document.get("source_failed_evidence")
    if not isinstance(source, Mapping):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_INVALID",
            "reconciliation source evidence reference is missing",
        )
    source_path = _resolve(
        paths,
        source.get("locator"),
        _EVIDENCE_DIRECTORY,
        "source failed steady-state evidence",
    )
    (
        _,
        source_sha,
        release_path,
        release,
        release_sha,
        release_file_sha,
    ) = _failed_mixed_state_source(
        paths,
        private_state,
        source_path,
        max_age_seconds=None,
    )
    release_ref = document.get("source_release")
    if not (
        source.get("sha256") == source_sha
        and isinstance(release_ref, Mapping)
        and release_ref.get("locator")
        == _relative(paths, release_path, "source steady-state release")
        and release_ref.get("sha256") == release_sha
        and release_ref.get("file_sha256") == release_file_sha
        and document.get("chain_id") == release["chain"]["chain_id"]
        and document.get("genesis_sha256") == release["chain"]["genesis_sha256"]
        and document.get("validator_set") == release["chain"]["validator_set"]
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_BINDING_MISMATCH",
            "reconciliation source and chain commitments do not match the consumed release",
        )

    policy = document.get("policy")
    summary = document.get("summary")
    targets = document.get("targets")
    target_map = {
        item.get("node"): item
        for item in targets
        if isinstance(item, Mapping) and type(item.get("node")) is str
    } if type(targets) is list else {}
    c_target = target_map.get("mainnetc-super1")
    a_target = target_map.get("mainneta-super1")
    c_values = c_target if isinstance(c_target, Mapping) else {}
    a_values = a_target if isinstance(a_target, Mapping) else {}

    c_stale_records = (
        c_values.get("recognized_obsolete_components_present")
        if isinstance(c_target, Mapping)
        else None
    )
    a_stale_records = (
        a_values.get("recognized_obsolete_components_present")
        if isinstance(a_target, Mapping)
        else None
    )
    c_obsolete_safe = bool(
        isinstance(c_target, Mapping)
        and (
            c_values.get("obsolete_components_absent") is True
            or (
                type(c_stale_records) is list
                and bool(c_stale_records)
                and c_values.get(
                    "recognized_obsolete_component_records_all_exited"
                ) is True
                and c_values.get("obsolete_compose_services_absent") is True
                and c_values.get("exited_obsolete_records_accepted") is True
            )
        )
    )
    c_aggregate_safe = bool(
        isinstance(c_target, Mapping)
        and (
            c_values.get("aggregate_service_healthy") is True
            or c_values.get("aggregate_badge_non_authoritative") is True
        )
    )
    platform_stale_records_present = bool(
        (c_stale_records if type(c_stale_records) is list else [])
        or (a_stale_records if type(a_stale_records) is list else [])
    )
    c_strict_cleanup_complete = bool(
        isinstance(c_target, Mapping)
        and c_values.get("aggregate_service_healthy") is True
        and c_values.get("obsolete_components_absent") is True
    )

    if not (
        isinstance(policy, Mapping)
        and policy.get("allowed_http_methods") == ["GET"]
        and policy.get("read_only") is True
        and policy.get("network_access_performed") is True
        and policy.get("live_mutation_performed") is False
        and policy.get("validator_vote_performed") is False
        and policy.get("manual_ssh_required") is False
        and policy.get("public_endpoint_created") is False
        and policy.get("log_endpoints_queried") is False
        and policy.get("release_reused") is False
        and policy.get("aggregate_badge_authoritative") is False
        and policy.get("recognized_exited_stale_records_tolerated") is True
        and policy.get("running_obsolete_components_tolerated") is False
        and policy.get("unexpected_component_records_tolerated") is False
        and policy.get("direct_coolify_database_mutation_authorized") is False
        and policy.get("service_record_deletion_authorized") is False
        and isinstance(summary, Mapping)
        and summary.get("clean") is True
        and summary.get("source_execution_claim_consumed") is True
        and summary.get("C_patch_and_deploy_previously_succeeded") is True
        and summary.get("C_steady_state_verified") is True
        and summary.get("C_component_scoped_steady_state_verified") is True
        and summary.get("C_aggregate_service_healthy")
        is c_values.get("aggregate_service_healthy")
        and summary.get("C_obsolete_components_absent")
        is c_values.get("obsolete_components_absent")
        and summary.get("C_obsolete_compose_services_absent") is True
        and summary.get("C_stale_component_records_present") == c_stale_records
        and summary.get("C_stale_component_records_all_exited")
        is c_values.get("recognized_obsolete_component_records_all_exited")
        and summary.get("C_unexpected_component_records_present") == []
        and summary.get("C_strict_aggregate_cleanup_complete")
        is c_strict_cleanup_complete
        and summary.get("A_recovered_state_verified") is True
        and summary.get("A_not_restarted_by_failed_execution") is True
        and summary.get("platform_stale_component_records_present")
        is platform_stale_records_present
        and summary.get("aggregate_badge_non_authoritative_for_reconciliation")
        is c_values.get("aggregate_badge_non_authoritative")
        and summary.get("validator_set_verified") is True
        and summary.get("blocks_advancing") is True
        and summary.get("latest_block_fresh") is True
        and summary.get("chain_continuity_verified") is True
        and summary.get("live_mutation_performed") is False
        and summary.get("validator_vote_performed") is False
        and summary.get("complete") is True
        and summary.get("next_phase")
        == "stage-post-admission-steady-state-continuation"
        and len(target_map) == 2
        and isinstance(c_target, Mapping)
        and c_values.get("expected_mode") == "steady-state"
        and c_values.get("verified") is True
        and c_values.get("component_scoped_verified") is True
        and c_values.get("compose_matches") is True
        and c_values.get("required_components_healthy") is True
        and c_values.get("obsolete_compose_services_absent") is True
        and c_values.get("unexpected_component_records_present") == []
        and c_obsolete_safe
        and c_aggregate_safe
        and isinstance(a_target, Mapping)
        and a_values.get("expected_mode") == "recovered"
        and a_values.get("verified") is True
        and a_values.get("component_scoped_verified") is True
        and a_values.get("compose_matches") is True
        and a_values.get("required_components_healthy") is True
        and a_values.get("unexpected_component_records_present") == []
        and a_values.get("accepted_recovered_aggregate_status") is True
        and not _contains_sensitive(document)
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_RECONCILIATION_INVALID",
            "steady-state reconciliation failed component-scoped invariant verification",
        )
    return {
        "clean": True,
        "age_seconds": int(max(0, age)),
        "reconciliation_path": str(Path(reconciliation_path)),
        "reconciliation_sha256": digest,
        "network": document["network"],
        "nodes": list(document["nodes"]),
        "chain_id": document["chain_id"],
        "genesis_sha256": document["genesis_sha256"],
        "validator_set": list(document["validator_set"]),
        "C_steady_state_verified": True,
        "C_aggregate_service_healthy": c_values.get("aggregate_service_healthy"),
        "C_obsolete_components_absent": c_values.get("obsolete_components_absent"),
        "C_obsolete_compose_services_absent": True,
        "C_stale_component_records_present": list(c_stale_records or []),
        "C_stale_component_records_all_exited": c_values.get(
            "recognized_obsolete_component_records_all_exited"
        ),
        "aggregate_badge_non_authoritative": c_values.get(
            "aggregate_badge_non_authoritative"
        ),
        "A_recovered_state_verified": True,
        "chain_continuity_verified": True,
        "live_mutation_performed": False,
        "validator_vote_performed": False,
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "next_phase": "stage-post-admission-steady-state-continuation",
        "mother_binding": dict(document["mother_binding"]),
    }


def verify_post_admission_steady_state_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    _selection(selected_nodes)
    document, _, digest = _canonical_under(
        paths,
        Path(evidence_path),
        _EVIDENCE_DIRECTORY,
        "post-admission steady-state evidence",
    )
    if (
        document.get("kind") != _EVIDENCE_KIND
        or document.get("status") != "pass"
        or document.get("mother_binding") != _binding(private_state)
        or _contains_sensitive(document)
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_EVIDENCE_INVALID",
            "steady-state evidence is not a passing canonical document",
        )
    completed = _parse_utc(document.get("completed_at"), "evidence.completed_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - completed).total_seconds())
    if age < -60 or age > max_age_seconds:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_EVIDENCE_STALE",
            "steady-state evidence is outside its verification age window",
        )

    release_ref = _mapping(document.get("release"), "evidence.release")
    release_path = _resolve(
        paths,
        release_ref.get("locator"),
        _RELEASE_DIRECTORY,
        "post-admission steady-state release",
    )
    release, _, _ = _canonical_under(
        paths,
        release_path,
        _RELEASE_DIRECTORY,
        "post-admission steady-state release",
    )
    release_digest = _digest_without(
        release,
        "post_admission_steady_state_release_sha256",
    )
    if (
        release.get("kind") != _RELEASE_KIND
        or release.get("mother_binding") != _binding(private_state)
        or release.get("post_admission_steady_state_release_sha256") != release_digest
        or release_ref.get("sha256") != release_digest
        or _contains_sensitive(release)
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_EVIDENCE_INVALID",
            "steady-state evidence is not bound to its exact released authority",
        )

    released_chain = _mapping(release.get("chain"), "release.chain")
    released_validators = released_chain.get("validator_set")
    evidence_validators = document.get("validator_set")
    try:
        normalized_released = (
            sorted({_address(item, "released validator") for item in released_validators})
            if type(released_validators) is list
            else []
        )
        normalized_evidence = (
            sorted({_address(item, "evidence validator") for item in evidence_validators})
            if type(evidence_validators) is list
            else []
        )
    except MotherDeploymentPostAdmissionSteadyStateError as exc:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_EVIDENCE_INVALID",
            "steady-state evidence contains an invalid validator binding",
        ) from exc
    if (
        len(normalized_released) != 2
        or len(normalized_evidence) != 2
        or normalized_evidence != normalized_released
        or document.get("network") != release.get("network")
        or document.get("nodes") != list(_CANONICAL_ORDER)
        or document.get("chain_id") != released_chain.get("chain_id")
        or document.get("genesis_sha256") != released_chain.get("genesis_sha256")
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_EVIDENCE_INVALID",
            "steady-state evidence chain identity does not match the exact release",
        )

    policy = _mapping(document.get("policy"), "evidence.policy")
    summary = _mapping(document.get("summary"), "evidence.summary")
    receipts = document.get("mutation_receipts")
    observations = document.get("health_observations")
    facts = _derive_execution_facts(
        release,
        receipts,
        observations,
        document.get("guardian_refresh_gate"),
        document.get("failure"),
    )
    required_fact_keys = (
        "clean",
        "post_admission_steady_state_installed",
        "initial_aggregate_service_running_healthy",
        "replica_aggregate_service_running_healthy",
        "initial_besu_running_healthy",
        "replica_besu_running_healthy",
        "initial_guardian_running_healthy",
        "replica_guardian_running_healthy",
        "obsolete_phase_components_absent",
        "validator_set_verified",
        "blocks_advancing",
        "latest_block_fresh",
        "C_restarted_before_A",
        "joint_blocks_resumed_before_A_restart",
        "complete",
    )
    attempted = len(receipts) if type(receipts) is list else -1
    succeeded = (
        sum(
            isinstance(item, Mapping) and item.get("status") == "succeeded"
            for item in receipts
        )
        if type(receipts) is list
        else -1
    )
    failed = (
        sum(
            not isinstance(item, Mapping) or item.get("status") != "succeeded"
            for item in receipts
        )
        if type(receipts) is list
        else -1
    )
    if (
        any(facts.get(key) is not True or summary.get(key) is not facts.get(key) for key in required_fact_keys)
        or document.get("failure") is not None
        or summary.get("validator_vote_performed") is not False
        or summary.get("manual_ssh_required") is not False
        or summary.get("public_endpoint_created") is not False
        or summary.get("planned_mutation_count") != 4
        or summary.get("attempted_mutation_count") != attempted
        or summary.get("succeeded_mutation_count") != succeeded
        or summary.get("failed_mutation_count") != failed
        or summary.get("network_access_performed") is not True
        or summary.get("live_mutation_performed") is not True
        or summary.get("next_phase") != "post-admission-steady-state-complete"
        or policy.get("allowed_http_methods") != ["GET", "PATCH"]
        or policy.get("allowed_mutation_count") != 4
        or policy.get("validator_vote_performed") is not False
        or policy.get("manual_ssh_required") is not False
        or policy.get("public_endpoint_created") is not False
        or policy.get("volume_deletion") is not False
        or policy.get("besu_data_deletion") is not False
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_EVIDENCE_INVALID",
            "steady-state evidence failed receipt-derived invariant verification",
        )
    return {
        "clean": True,
        "evidence_path": str(Path(evidence_path)),
        "evidence_sha256": digest,
        "age_seconds": max(0, age),
        "network": release["network"],
        "nodes": list(_CANONICAL_ORDER),
        "chain_id": released_chain["chain_id"],
        "genesis_sha256": released_chain["genesis_sha256"],
        "validator_set": list(released_chain["validator_set"]),
        "aggregate_services_running_healthy": True,
        "retained_components_running_healthy": True,
        "obsolete_phase_components_absent": True,
        "blocks_advancing": True,
        "validator_vote_performed": False,
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "next_phase": "post-admission-steady-state-complete",
        "mother_binding": dict(document["mother_binding"]),
    }
