#!/usr/bin/env python3
"""Safely remove one broken Mother validator from the current topology.

This is an emergency wrapper around the existing remove-node state machine.  It
adds broken-validator-specific guards before allowing that state machine to run:

* the target must currently be missing or not running:healthy;
* every survivor required for removal must be running:healthy;
* survivor-only voting must be sufficient (a broken target may never be needed
  to vote for its own removal);
* at least two validators must survive;
* RPC continuity must pass the existing read-only remove-node preflight;
* the compiled remove-node plan must remove the QBFT validator before deleting
  the target service.

``--dry-run`` performs the complete live read-only assessment, then persists a
local hash-bound authorization artifact.  It performs no live mutation.
``--execute`` requires explicit acknowledgement of that exact artifact, repeats
the live assessment, verifies that the safety-critical authorization binding has
not changed, then delegates mutation to the existing remove-node
prep/release/do/finalize implementation.  That executor already requires exact
validator-set proof plus fresh post-transition block advancement before target
service deletion.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common import atomic_files  # noqa: E402
from tools.mother.common.canonical import canonical_json  # noqa: E402
from tools.mother.common.deployment_node_remove_do_v2 import (  # noqa: E402
    MotherDeploymentNodeRemoveDoError,
    build_node_remove_do_release,
    execute_node_remove_do_release,
    inspect_node_remove_do_post_proof_resume,
    resume_node_remove_do_after_validator_proof,
    write_node_remove_do_release,
)
from tools.mother.common.deployment_node_remove_finalize import (  # noqa: E402
    MotherDeploymentNodeRemoveFinalizeError,
    finalize_node_remove,
)
from tools.mother.common.deployment_node_remove_prep import (  # noqa: E402
    MotherDeploymentNodeRemovePrepError,
    build_node_remove_prep_transaction,
    write_node_remove_prep_transaction,
)
from tools.mother.common.models import OperationIdentity  # noqa: E402
from tools.mother.common.paths import MotherPaths  # noqa: E402
from tools.mother.common.private_state import read_private_state, _secure_private_path  # noqa: E402
from tools.mother_preflight_paranoia import (  # noqa: E402
    MotherPreflightParanoiaError,
    run_preflight_paranoia,
)
from tools.mother_preflight_rpc_paranoia import (  # noqa: E402
    MotherPreflightRpcParanoiaError,
    run_preflight_rpc_paranoia,
)


KIND = "main_computer.mother.broken_validator_cleanup.v1"
ASSESSMENT_KIND = "main_computer.mother.broken_validator_cleanup_assessment.v1"
ASSESSMENT_DIRECTORY = ("evidence", "deployment-broken-validator-cleanup-assessment")
OPERATION_RECORD_KIND = "main_computer.mother.broken_validator_operation.v1"
OPERATION_RECORD_DIRECTORY = ("evidence", "deployment-broken-validator-operation")
OPERATION_TERMINAL_STATUSES = {"completed", "aborted"}
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class MotherBrokenValidatorCleanupError(RuntimeError):
    """Broken-validator cleanup could not establish a safe operation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _operation(network: str, phase: str) -> OperationIdentity:
    safe_network = str(network or "").strip()
    safe_phase = str(phase or "").strip().replace("_", "-")
    if not safe_network or not safe_phase:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_INVALID_ARGUMENT",
            "network and phase are required",
        )
    operation_id = f"mother-broken-validator-cleanup-{safe_phase}-{safe_network}-{_stamp()}"
    return OperationIdentity(
        operation_id=operation_id,
        request_id=f"{operation_id}-request",
        network=safe_network,
        operation_kind="MOTHER-OP-REMOVE-NODE",
    )



def _operation_record_path(runtime_state_root: str | Path, *, network: str, node: str) -> Path:
    for value, label in ((network, "network"), (node, "node")):
        if not _IDENTIFIER_RE.fullmatch(str(value or "")):
            raise MotherBrokenValidatorCleanupError(
                "MOTHER_BROKEN_VALIDATOR_CLEANUP_OPERATION_IDENTITY_INVALID",
                f"invalid broken-validator operation {label}: {value!r}",
            )
    paths = MotherPaths(runtime_state_root=Path(runtime_state_root))
    return paths.validate_contained(
        paths.evidence_root
        / OPERATION_RECORD_DIRECTORY[-1]
        / f"{network}-{node}.json"
    )


def _load_operation_record(
    runtime_state_root: str | Path,
    *,
    network: str,
    node: str,
) -> dict[str, Any] | None:
    path = _operation_record_path(runtime_state_root, network=network, node=node)
    if not path.exists():
        return None
    try:
        payload = path.read_bytes()
        document = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_OPERATION_RECORD_INVALID",
            f"broken-validator operation record is unreadable: {path}",
        ) from exc
    if not isinstance(document, dict) or canonical_json(document) != payload:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_OPERATION_RECORD_INVALID",
            "broken-validator operation record is not canonical Mother JSON",
        )
    if (
        document.get("kind") != OPERATION_RECORD_KIND
        or str(document.get("network") or "") != str(network)
        or str(document.get("target", {}).get("node") or "") != str(node)
    ):
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_OPERATION_RECORD_INVALID",
            "broken-validator operation record identity does not match the requested target",
        )
    return document


def _write_operation_record(
    runtime_state_root: str | Path,
    *,
    network: str,
    node: str,
    status: str,
    operation_id: str | None = None,
    target_validator: object = None,
    assessment_evidence: Mapping[str, Any] | None = None,
    resume_command: str | None = None,
    last_error: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    existing = _load_operation_record(runtime_state_root, network=network, node=node)
    if existing and str(existing.get("status") or "") not in OPERATION_TERMINAL_STATUSES:
        existing_id = str(existing.get("operation_id") or "")
        if operation_id and existing_id and existing_id != operation_id:
            raise MotherBrokenValidatorCleanupError(
                "MOTHER_BROKEN_VALIDATOR_CLEANUP_OPERATION_OWNERSHIP_CONFLICT",
                "an unfinished broken-validator operation already owns this target",
            )
        operation_id = existing_id or operation_id
    if not operation_id:
        operation_id = f"mother-broken-validator-{network}-{node}-{_stamp().lower()}"
    if not _IDENTIFIER_RE.fullmatch(operation_id):
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_OPERATION_IDENTITY_INVALID",
            f"invalid broken-validator operation id: {operation_id!r}",
        )

    created_at = str(existing.get("created_at") or _utc_now()) if existing else _utc_now()
    record: dict[str, Any] = {
        "kind": OPERATION_RECORD_KIND,
        "schema_version": 1,
        "operation_id": operation_id,
        "network": network,
        "target": {
            "node": node,
            "validator_address": target_validator
            if target_validator is not None
            else (existing or {}).get("target", {}).get("validator_address"),
        },
        "status": str(status),
        "created_at": created_at,
        "updated_at": _utc_now(),
        "ownership": {
            "remove_node_artifacts_owned": True,
            "generic_cleanup_precedence_allowed": False,
        },
        "assessment_evidence": dict(assessment_evidence or (existing or {}).get("assessment_evidence") or {}),
        "resume_command": resume_command
        if resume_command is not None
        else (existing or {}).get("resume_command"),
        "last_error": dict(last_error) if isinstance(last_error, Mapping) else None,
    }
    payload = canonical_json(record)
    path = _operation_record_path(runtime_state_root, network=network, node=node)
    path.parent.mkdir(parents=True, exist_ok=True)
    op = _operation(network, f"write-operation-{status}")
    _secure_private_path(path.parent, is_directory=True, operation=op)
    atomic_files.durable_replace(path, payload, operation=op)
    _secure_private_path(path, is_directory=False, operation=op)
    return record


def _operation_ref(record: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(record, Mapping):
        return None
    return {
        "kind": record.get("kind"),
        "operation_id": record.get("operation_id"),
        "target": dict(record.get("target") or {}),
        "status": record.get("status"),
    }

def _private_state(runtime_state_root: str | Path, *, network: str):
    paths = MotherPaths(runtime_state_root=Path(runtime_state_root)).resolve_private_state_paths()
    state = read_private_state(paths, operation=_operation(network, "read-private-state"))
    return paths, state


def _find_resumable_remove_do_evidence(
    paths: Any,
    private_state: Any,
    authorization: Mapping[str, Any],
) -> dict[str, Any] | None:
    target = authorization.get("target") if isinstance(authorization.get("target"), Mapping) else {}
    network = str(authorization.get("network") or "")
    node = str(target.get("node") or "")
    validator_address = str(target.get("validator_address") or "")
    service_uuid = str(target.get("service_uuid") or "")
    if not all((network, node, validator_address, service_uuid)):
        return None
    root = paths.root / "evidence" / "deployment-node-remove-do"
    if not root.is_dir():
        return None
    for candidate in sorted(root.glob("*.json"), key=lambda item: item.name, reverse=True):
        try:
            inspected = inspect_node_remove_do_post_proof_resume(
                paths,
                private_state,
                candidate,
                network=network,
                target_node=node,
                target_validator_address=validator_address,
                target_service_uuid=service_uuid,
            )
        except MotherDeploymentNodeRemoveDoError:
            continue
        authorized_survivors = sorted(
            (
                str(item.get("node") or ""),
                str(item.get("validator_address") or "").lower(),
                str(item.get("controller_id") or ""),
                str(item.get("service_uuid") or ""),
            )
            for item in authorization.get("survivors", [])
            if isinstance(item, Mapping)
        )
        inspected_survivors = sorted(
            (
                str(item.get("node") or ""),
                str(item.get("validator_address") or "").lower(),
                str(item.get("controller_id") or ""),
                str(item.get("service_uuid") or ""),
            )
            for item in inspected.get("survivors", [])
            if isinstance(item, Mapping)
        )
        authorized_post = authorization.get("remove_plan", {}).get("post_removal_topology", {})
        authorized_validator_set = sorted(
            str(item).lower() for item in authorized_post.get("validator_set", [])
        ) if isinstance(authorized_post, Mapping) else []
        inspected_validator_set = sorted(
            str(item).lower() for item in inspected.get("post_removal_validator_set", [])
        )
        if (
            inspected_survivors != authorized_survivors
            or inspected_validator_set != authorized_validator_set
        ):
            continue
        return inspected
    return None


def _status_is_healthy(value: object) -> bool:
    return str(value or "").strip().lower() == "running:healthy"


def _sha256_text(value: object, label: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_ASSESSMENT_INVALID",
            f"{label} is not a SHA-256 digest",
        )
    return text


def _authorization_payload(assessment: Mapping[str, Any]) -> dict[str, Any]:
    target = assessment.get("target") if isinstance(assessment.get("target"), Mapping) else {}
    safety = assessment.get("consensus_safety") if isinstance(assessment.get("consensus_safety"), Mapping) else {}
    remove_plan = assessment.get("remove_plan") if isinstance(assessment.get("remove_plan"), Mapping) else {}
    survivors = []
    for item in assessment.get("survivors", []):
        if not isinstance(item, Mapping):
            continue
        survivors.append({
            "node": item.get("node"),
            "validator_address": item.get("validator_address"),
            "controller_id": item.get("controller_id"),
            "service_uuid": item.get("service_uuid"),
        })
    survivors.sort(key=lambda item: str(item.get("node") or ""))
    return {
        "kind": "main_computer.mother.broken_validator_cleanup_authorization_binding.v2",
        "network": assessment.get("network"),
        "target": {
            "node": target.get("node"),
            "validator_address": target.get("validator_address"),
            "controller_id": target.get("controller_id"),
            "service_uuid": target.get("service_uuid"),
        },
        "survivors": survivors,
        "current_validator_count": safety.get("current_validator_count"),
        "post_removal_validator_count": safety.get("post_removal_validator_count"),
        "strict_majority_votes_required": safety.get("strict_majority_votes_required"),
        "healthy_survivor_vote_capacity": safety.get("healthy_survivor_vote_capacity"),
        "ordered_removal_plan": remove_plan.get("ordered_removal_plan"),
        "post_removal_topology": remove_plan.get("post_removal_topology"),
    }


def _legacy_authorization_payload(assessment: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(_authorization_payload(assessment))
    payload["kind"] = "main_computer.mother.broken_validator_cleanup_authorization_binding.v1"
    topology = assessment.get("topology_evidence") if isinstance(assessment.get("topology_evidence"), Mapping) else {}
    payload["topology_evidence_sha256"] = topology.get("sha256")
    return payload


def _authorization_sha256(assessment: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(_authorization_payload(assessment))).hexdigest()


def _legacy_authorization_sha256(assessment: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(_legacy_authorization_payload(assessment))).hexdigest()


def _assessment_paths(runtime_state_root: str | Path):
    return MotherPaths(runtime_state_root=Path(runtime_state_root)).resolve_private_state_paths()


def _write_assessment_evidence(
    runtime_state_root: str | Path,
    assessment: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    if assessment.get("status") != "pass" or assessment.get("consensus_safety", {}).get("safe_to_execute") is not True:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_ASSESSMENT_NOT_SAFE",
            "refusing to persist executable assessment because consensus safety did not pass",
        )
    document = dict(assessment)
    document["kind"] = ASSESSMENT_KIND
    document["mode"] = "dry-run"
    document["authorization_sha256"] = _authorization_sha256(document)
    document.pop("execute_command", None)
    document.pop("assessment_evidence", None)
    payload = canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()

    paths = _assessment_paths(runtime_state_root)
    root = paths.root.joinpath(*ASSESSMENT_DIRECTORY)
    root.mkdir(parents=True, exist_ok=True)
    _secure_private_path(root, is_directory=True, operation=operation)
    stamp = str(document.get("observed_at") or _utc_now()).replace("-", "").replace(":", "")
    stamp = stamp.replace("+0000", "Z").replace("+00:00", "Z")
    node = str(document.get("target", {}).get("node") or "validator")
    destination = root / f"{stamp}-{node}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherBrokenValidatorCleanupError(
                "MOTHER_BROKEN_VALIDATOR_CLEANUP_ASSESSMENT_CONFLICT",
                "assessment evidence destination already contains different bytes",
            )
    else:
        atomic_files.durable_create(destination, payload, operation=operation)
        _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def _verify_assessment_evidence(
    runtime_state_root: str | Path,
    evidence_path: str | Path,
    *,
    acknowledged_sha256: str,
    network: str,
    node: str,
    topology_evidence: str | Path,
    acknowledged_topology_sha256: str,
) -> tuple[dict[str, Any], str]:
    paths = _assessment_paths(runtime_state_root)
    path = Path(evidence_path).resolve(strict=False)
    allowed = paths.root.joinpath(*ASSESSMENT_DIRECTORY).resolve(strict=False)
    try:
        path.relative_to(allowed)
    except ValueError as exc:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_ASSESSMENT_PATH_INVALID",
            "assessment evidence is outside the broken-validator assessment directory",
        ) from exc
    try:
        payload = path.read_bytes()
        document = json.loads(payload.decode("utf-8"))
    except OSError as exc:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_ASSESSMENT_MISSING",
            f"assessment evidence does not exist: {path}",
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_ASSESSMENT_INVALID",
            "assessment evidence is not valid canonical JSON",
        ) from exc
    if not isinstance(document, dict) or canonical_json(document) != payload:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_ASSESSMENT_INVALID",
            "assessment evidence is not canonical Mother JSON",
        )
    digest = hashlib.sha256(payload).hexdigest()
    if digest != _sha256_text(acknowledged_sha256, "acknowledged assessment evidence sha256"):
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_ASSESSMENT_ACK_MISMATCH",
            "acknowledged assessment SHA-256 does not match the assessment evidence",
        )
    if document.get("kind") != ASSESSMENT_KIND or document.get("mode") != "dry-run":
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_ASSESSMENT_INVALID",
            "assessment evidence kind/mode is not executable",
        )
    if document.get("status") != "pass" or document.get("consensus_safety", {}).get("safe_to_execute") is not True:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_CONSENSUS_SAFETY_NOT_PROVEN",
            "assessment evidence did not authorize execution",
        )
    if str(document.get("network") or "") != str(network):
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_ASSESSMENT_BINDING_MISMATCH",
            "assessment network does not match requested network",
        )
    target = document.get("target") if isinstance(document.get("target"), Mapping) else {}
    if str(target.get("node") or "") != str(node):
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_ASSESSMENT_BINDING_MISMATCH",
            "assessment target does not match requested node",
        )
    # The persisted assessment authorizes semantic recovery state, not a forever-current
    # topology file path.  Execution supplies and independently validates a fresh topology
    # artifact, then re-runs the complete safety assessment before any mutation.
    stored_authorization = _sha256_text(document.get("authorization_sha256"), "assessment authorization sha256")
    expected_authorizations = {
        _authorization_sha256(document),
        _legacy_authorization_sha256(document),
    }
    if stored_authorization not in expected_authorizations:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_ASSESSMENT_INVALID",
            "assessment authorization binding does not match its contents",
        )
    _sha256_text(acknowledged_topology_sha256, "acknowledged topology evidence sha256")
    return document, digest


def _assert_live_assessment_matches_authorization(
    persisted: Mapping[str, Any],
    live: Mapping[str, Any],
) -> None:
    persisted_semantic_sha = _authorization_sha256(persisted)
    live_semantic_sha = _authorization_sha256(live)
    if persisted_semantic_sha != live_semantic_sha:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_ASSESSMENT_LIVE_STATE_CHANGED",
            "live consensus/removal authorization state changed since dry-run; run a new --dry-run",
        )


def _parse_utc(value: object, label: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_TOPOLOGY_TIME_INVALID",
            f"{label} is missing",
        )
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_TOPOLOGY_TIME_INVALID",
            f"{label} is not an ISO-8601 timestamp",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _assert_topology_fresh(
    topology_evidence: str | Path,
    *,
    max_age_seconds: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    path = Path(topology_evidence).resolve(strict=False)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_TOPOLOGY_MISSING",
            f"topology evidence does not exist: {path}",
        ) from exc
    except json.JSONDecodeError as exc:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_TOPOLOGY_INVALID",
            f"topology evidence is not valid JSON: {path}",
        ) from exc
    if not isinstance(document, Mapping):
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_TOPOLOGY_INVALID",
            "topology evidence must be a JSON object",
        )
    timestamp_text = document.get("completed_at") or document.get("observed_at")
    timestamp = _parse_utc(timestamp_text, "topology evidence timestamp")
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = int((reference - timestamp).total_seconds())
    if age < -15:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_TOPOLOGY_TIME_INVALID",
            "topology evidence timestamp is too far in the future",
        )
    if age > int(max_age_seconds):
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_TOPOLOGY_STALE",
            f"topology evidence is stale: age_seconds={age}; max_age_seconds={int(max_age_seconds)}",
        )
    return {"path": str(path), "timestamp": str(timestamp_text), "age_seconds": max(0, age)}


def _phase_index(plan: list[Mapping[str, Any]], phase: str) -> int | None:
    for index, item in enumerate(plan):
        if str(item.get("phase") or "") == phase:
            return index
    return None


def _execution_command(
    *,
    python_executable: str,
    runtime_state_root: str | Path,
    network: str,
    node: str,
    topology_evidence: str | Path,
    topology_sha256: str,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    topology_max_age_seconds: int,
    rpc_will_work_post_remove: bool,
    assessment_evidence: str | Path,
    assessment_evidence_sha256: str,
) -> str:
    argv = [
        str(python_executable),
        str(REPO_ROOT / "tools" / "mother_broken_validator_cleanup.py"),
        "--execute",
        "--runtime-state-root",
        str(runtime_state_root),
        "--network",
        str(network),
        "--node",
        str(node),
        "--topology-evidence",
        str(topology_evidence),
        "--acknowledge-topology-evidence-sha256",
        str(topology_sha256),
        "--assessment-evidence",
        str(assessment_evidence),
        f"--acknowledge-assessment-evidence-sha256={assessment_evidence_sha256}",
        "--topology-max-age-seconds",
        str(int(topology_max_age_seconds)),
        "--timeout",
        str(float(timeout)),
        "--max-response-bytes",
        str(int(max_response_bytes)),
        "--max-wait-seconds",
        str(float(max_wait_seconds)),
        "--poll-interval-seconds",
        str(float(poll_interval_seconds)),
    ]
    if rpc_will_work_post_remove:
        argv.append("--rpc-will-work-post-remove")
    return subprocess.list2cmdline(argv)


def _assess_consensus_safety(
    *,
    runtime_state_root: str | Path,
    network: str,
    node: str,
    topology_evidence: str | Path,
    acknowledged_topology_evidence_sha256: str,
    topology_max_age_seconds: int,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    rpc_will_work_post_remove: bool,
    python_executable: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    freshness = _assert_topology_fresh(
        topology_evidence,
        max_age_seconds=topology_max_age_seconds,
        now=now,
    )
    paths, private_state = _private_state(runtime_state_root, network=network)

    try:
        preflight = run_preflight_paranoia(
            operation="remove-node",
            runtime_state_root=runtime_state_root,
            network=network,
            node=node,
            topology_evidence=topology_evidence,
            acknowledged_topology_evidence_sha256=acknowledged_topology_evidence_sha256,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_wait_seconds=max_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            python_executable=python_executable or sys.executable,
        )
    except MotherPreflightParanoiaError as exc:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_PREFLIGHT_FAILED",
            str(exc),
        ) from exc

    try:
        rpc_preflight = run_preflight_rpc_paranoia(
            runtime_state_root=runtime_state_root,
            network=network,
            node=node,
            topology_evidence=topology_evidence,
            acknowledged_topology_evidence_sha256=acknowledged_topology_evidence_sha256,
            rpc_will_work_post_remove=rpc_will_work_post_remove,
        )
    except MotherPreflightRpcParanoiaError as exc:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_RPC_PREFLIGHT_FAILED",
            str(exc),
        ) from exc

    try:
        prep = build_node_remove_prep_transaction(
            paths,
            private_state,
            Path(topology_evidence),
            network=network,
            target_node=node,
            mode="soft",
            baseline_evidence_sha256=acknowledged_topology_evidence_sha256,
            baseline_max_age_seconds=topology_max_age_seconds,
            now=now,
        )
    except MotherDeploymentNodeRemovePrepError as exc:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_REMOVE_PLAN_FAILED",
            f"{exc.code}: {exc}",
        ) from exc

    observations = {
        str(item.get("node")): dict(item)
        for item in preflight.get("service_observations", [])
        if isinstance(item, Mapping) and item.get("node")
    }
    target_observation = observations.get(node)
    if not isinstance(target_observation, Mapping):
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_TARGET_NOT_OBSERVED",
            f"target validator {node!r} was not observed by the live preflight",
        )

    target_broken = bool(
        target_observation.get("service_missing") is True
        or not _status_is_healthy(target_observation.get("service_status"))
    )

    survivor_nodes = [str(item.get("node")) for item in prep.get("survivors", []) if isinstance(item, Mapping)]
    survivor_observations = [observations.get(item) for item in survivor_nodes]
    survivor_health_failures = [
        dict(item)
        for item in preflight.get("required_validator_health_failures", [])
        if isinstance(item, Mapping)
    ]
    survivors_all_healthy = bool(survivor_nodes) and all(
        isinstance(item, Mapping)
        and item.get("service_missing") is not True
        and _status_is_healthy(item.get("service_status"))
        for item in survivor_observations
    ) and not survivor_health_failures

    active_helpers_on_survivors = [
        dict(item)
        for item in preflight.get("active_cleanup_helpers", [])
        if isinstance(item, Mapping) and str(item.get("node")) in set(survivor_nodes)
    ]
    conflicting_voters_on_survivors = [
        dict(item)
        for item in preflight.get("blocking_conflicts", [])
        if isinstance(item, Mapping) and str(item.get("node")) in set(survivor_nodes)
    ]

    current_validators = list(prep.get("current_topology", {}).get("validator_set", []))
    post_validators = list(prep.get("post_removal_topology", {}).get("validator_set", []))
    current_count = len(current_validators)
    post_count = len(post_validators)

    # Besu QBFT validator membership changes require a strict majority.  The
    # existing remove-node release has an explicit 2->1 special case that adds
    # the target as a voter.  A broken-validator cleanup must never depend on
    # that target vote, so require the healthy survivors alone to meet the
    # strict-majority threshold and reject the 2->1 case outright.
    strict_majority_votes = (current_count // 2) + 1
    survivor_vote_capacity = len(survivor_nodes)
    target_vote_required_by_existing_remove_path = current_count == 2 and post_count == 1
    survivor_only_vote_sufficient = (
        survivor_vote_capacity >= strict_majority_votes
        and not target_vote_required_by_existing_remove_path
    )

    ordered_plan = [dict(item) for item in prep.get("ordered_removal_plan", []) if isinstance(item, Mapping)]
    vote_phase_index = _phase_index(ordered_plan, "remove-qbft-validator")
    delete_phase_index = _phase_index(ordered_plan, "detach-disable-archive-or-delete-service")
    validator_removed_before_service_delete = bool(
        prep.get("execution_plan", {}).get("service_deletion_is_first") is False
        and vote_phase_index is not None
        and delete_phase_index is not None
        and vote_phase_index < delete_phase_index
    )

    at_least_two_survivors = post_count >= 2
    rpc_continuity_safe = rpc_preflight.get("summary", {}).get("clean") is True
    no_survivor_helper_blockers = not active_helpers_on_survivors
    no_survivor_vote_conflicts = not conflicting_voters_on_survivors

    checks = {
        "target_is_broken": target_broken,
        "survivors_all_running_healthy": survivors_all_healthy,
        "at_least_two_validators_survive": at_least_two_survivors,
        "survivor_only_validator_vote_sufficient": survivor_only_vote_sufficient,
        "target_vote_not_required": not target_vote_required_by_existing_remove_path,
        "validator_removal_precedes_service_deletion": validator_removed_before_service_delete,
        "rpc_continuity_safe": rpc_continuity_safe,
        "no_active_cleanup_helpers_on_survivors": no_survivor_helper_blockers,
        "no_conflicting_validator_votes_on_survivors": no_survivor_vote_conflicts,
    }
    safe = all(checks.values())

    topology_info = preflight.get("topology_evidence") if isinstance(preflight.get("topology_evidence"), Mapping) else {}
    topology_path = str(topology_info.get("path") or topology_evidence)
    topology_sha = str(topology_info.get("sha256") or acknowledged_topology_evidence_sha256)

    return {
        "kind": KIND,
        "schema_version": 1,
        "observed_at": _utc_now(),
        "status": "pass" if safe else "consensus-safety-not-proven",
        "mode": "assessment",
        "network": network,
        "target": {
            "node": node,
            "validator_address": prep.get("target", {}).get("validator_address"),
            "controller_id": prep.get("target", {}).get("controller_id"),
            "service_uuid": prep.get("target", {}).get("service_uuid"),
            "live_observation": dict(target_observation),
        },
        "survivors": [dict(item) for item in prep.get("survivors", []) if isinstance(item, Mapping)],
        "consensus_safety": {
            "safe_to_execute": safe,
            "checks": checks,
            "current_validator_count": current_count,
            "post_removal_validator_count": post_count,
            "strict_majority_votes_required": strict_majority_votes,
            "healthy_survivor_vote_capacity": survivor_vote_capacity if survivors_all_healthy else 0,
            "target_vote_required_by_existing_remove_path": target_vote_required_by_existing_remove_path,
            "service_deletion_guard": (
                "existing remove-node executor requires verified validator-removal proof payloads, "
                "exact desired validator set, fresh block timestamp, and block advancement before target deletion"
            ),
        },
        "survivor_health_failures": survivor_health_failures,
        "active_cleanup_helpers_on_survivors": active_helpers_on_survivors,
        "conflicting_validator_votes_on_survivors": conflicting_voters_on_survivors,
        "rpc_preflight": rpc_preflight,
        "remove_plan": {
            "ordered_removal_plan": ordered_plan,
            "execution_plan": dict(prep.get("execution_plan", {})),
            "post_removal_topology": dict(prep.get("post_removal_topology", {})),
        },
        "topology_evidence": {
            "path": topology_path,
            "sha256": topology_sha,
            "timestamp": freshness["timestamp"],
            "age_seconds": freshness["age_seconds"],
        },
        "policy": {
            "read_only": True,
            "network_access_performed": True,
            "live_mutation_performed": False,
            "validator_vote_performed": False,
            "service_deletion_performed": False,
            "target_service_uuid_change_authorized": False,
        },
        "execute_command": None,
    }


def _execute_cleanup(
    assessment: Mapping[str, Any],
    *,
    runtime_state_root: str | Path,
    network: str,
    node: str,
    topology_evidence: str | Path,
    acknowledged_topology_evidence_sha256: str,
    topology_max_age_seconds: int,
    release_expires_in_seconds: int,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    source_assessment_evidence: Mapping[str, Any] | None = None,
    resume_do_evidence: str | Path | None = None,
) -> dict[str, Any]:
    if assessment.get("status") != "pass" or assessment.get("consensus_safety", {}).get("safe_to_execute") is not True:
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_CONSENSUS_SAFETY_NOT_PROVEN",
            "refusing mutation because consensus-safety assessment did not pass",
        )

    paths, private_state = _private_state(runtime_state_root, network=network)
    # The broken-validator wrapper may wait longer across the overall recovery,
    # but the delegated remove-do/service-delete contract is capped at 300s.
    remove_do_max_wait_seconds = min(float(max_wait_seconds), 300.0)

    tx_path: Path | None = None
    tx_sha: str | None = None
    release_path: Path | None = None
    release_sha: str | None = None
    try:
        if resume_do_evidence is not None:
            target = assessment.get("target") if isinstance(assessment.get("target"), Mapping) else {}
            do_evidence = resume_node_remove_do_after_validator_proof(
                paths,
                private_state,
                Path(resume_do_evidence),
                network=network,
                target_node=node,
                target_validator_address=str(target.get("validator_address") or ""),
                target_service_uuid=str(target.get("service_uuid") or ""),
                timeout=timeout,
                max_wait_seconds=remove_do_max_wait_seconds,
                poll_interval_seconds=poll_interval_seconds,
                allow_missing_service=True,
                max_response_bytes=max_response_bytes,
                operation=_operation(network, "resume-remove-post-proof"),
            )
        else:
            transaction = build_node_remove_prep_transaction(
                paths,
                private_state,
                Path(topology_evidence),
                network=network,
                target_node=node,
                mode="soft",
                baseline_evidence_sha256=acknowledged_topology_evidence_sha256,
                baseline_max_age_seconds=topology_max_age_seconds,
            )
            tx_path, tx_sha = write_node_remove_prep_transaction(
                paths,
                transaction,
                operation=_operation(network, "write-remove-prep"),
            )
            release = build_node_remove_do_release(
                paths,
                private_state,
                tx_path,
                acknowledged_prep_transaction_sha256=tx_sha,
                transaction_max_age_seconds=topology_max_age_seconds,
                baseline_max_age_seconds=topology_max_age_seconds,
                expires_in_seconds=release_expires_in_seconds,
            )
            release_path, release_sha = write_node_remove_do_release(
                paths,
                release,
                operation=_operation(network, "write-remove-release"),
            )
            do_evidence = execute_node_remove_do_release(
                paths,
                private_state,
                release_path,
                acknowledged_release_sha256=release_sha,
                max_age_seconds=release_expires_in_seconds,
                transaction_max_age_seconds=topology_max_age_seconds,
                baseline_max_age_seconds=topology_max_age_seconds,
                timeout=timeout,
                max_wait_seconds=remove_do_max_wait_seconds,
                poll_interval_seconds=poll_interval_seconds,
                allow_missing_service=True,
                preserve_static_node_precleanup_services=False,
                max_response_bytes=max_response_bytes,
                operation=_operation(network, "execute-remove"),
            )
    except (MotherDeploymentNodeRemovePrepError, MotherDeploymentNodeRemoveDoError) as exc:
        code = getattr(exc, "code", "MOTHER_BROKEN_VALIDATOR_CLEANUP_REMOVE_FAILED")
        raise MotherBrokenValidatorCleanupError(code, str(exc)) from exc

    result: dict[str, Any] = {
        "kind": KIND,
        "schema_version": 1,
        "completed_at": _utc_now(),
        "mode": "execute",
        "network": network,
        "target": dict(assessment.get("target", {})),
        "broken_validator_operation": dict(assessment.get("broken_validator_operation", {})),
        "consensus_safety": dict(assessment.get("consensus_safety", {})),
        "source_assessment_evidence": dict(source_assessment_evidence or {}),
        "prep_transaction": {"path": str(tx_path), "sha256": tx_sha} if tx_path is not None else None,
        "remove_release": {"path": str(release_path), "sha256": release_sha} if release_path is not None else None,
        "resume_source_remove_do_evidence": (
            {"path": str(Path(resume_do_evidence).resolve(strict=False))}
            if resume_do_evidence is not None
            else None
        ),
        "remove_do_evidence": do_evidence,
        "live_mutation_performed": do_evidence.get("live_mutation_performed") is True,
        "service_deletion_performed": do_evidence.get("service_deletion_performed") is True,
    }

    if do_evidence.get("status") != "pass" or do_evidence.get("next_phase") != "remove-node-finalize-mainnet":
        result.update({
            "status": "failed",
            "failure": do_evidence.get("failure") or {
                "code": "MOTHER_BROKEN_VALIDATOR_CLEANUP_REMOVE_NOT_COMPLETE",
                "message": "existing remove-node executor did not produce a complete removal proof",
            },
            "next_phase": "manual-review-required",
        })
        return result

    evidence_info = do_evidence.get("evidence")
    if not isinstance(evidence_info, Mapping) or not evidence_info.get("path"):
        raise MotherBrokenValidatorCleanupError(
            "MOTHER_BROKEN_VALIDATOR_CLEANUP_DO_EVIDENCE_MISSING",
            "remove-node executor passed without persisted evidence",
        )

    try:
        finalized = finalize_node_remove(
            paths,
            private_state,
            Path(str(evidence_info["path"])),
            network=network,
            max_age_seconds=topology_max_age_seconds,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            write_evidence=True,
            operation=_operation(network, "finalize-remove"),
        )
    except MotherDeploymentNodeRemoveFinalizeError as exc:
        raise MotherBrokenValidatorCleanupError(exc.code, str(exc)) from exc

    final_pass = finalized.get("status") == "pass" and finalized.get("summary", {}).get("complete") is True
    result.update({
        "status": "pass" if final_pass else "failed",
        "failure": None if final_pass else {
            "code": "MOTHER_BROKEN_VALIDATOR_CLEANUP_FINALIZE_NOT_COMPLETE",
            "message": "remove-node finalize did not prove a clean surviving topology",
        },
        "finalize_evidence": finalized,
        "next_phase": finalized.get("next_phase") if final_pass else "manual-review-required",
    })
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Safely remove one broken Mother validator. --dry-run performs GET-only/read-only "
            "consensus-safety assessment and writes a local hash-bound authorization artifact; "
            "--execute requires acknowledgement of that artifact, repeats the live assessment, "
            "and delegates the actual membership transition to the existing remove-node proof machinery."
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--runtime-state-root", required=True)
    parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    parser.add_argument("--node", required=True)
    parser.add_argument("--topology-evidence", required=True)
    parser.add_argument("--acknowledge-topology-evidence-sha256", required=True)
    parser.add_argument("--assessment-evidence")
    parser.add_argument("--acknowledge-assessment-evidence-sha256")
    # Backward compatibility for persisted operation resume commands created before
    # assessment-expiry enforcement was removed. The value is intentionally ignored;
    # execution still verifies the assessment SHA/bindings and revalidates live safety.
    parser.add_argument("--assessment-max-age-seconds", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--topology-max-age-seconds", type=int, default=900)
    parser.add_argument("--release-expires-in-seconds", type=int, default=900)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=12 * 1024 * 1024)
    parser.add_argument("--max-wait-seconds", type=float, default=900.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    parser.add_argument(
        "--rpc-will-work-post-remove",
        action="store_true",
        help="explicitly acknowledge RPC continuity only when removing the final Mother node from a controller",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    operation_record: dict[str, Any] | None = None
    operation_id: str | None = None
    resume_candidate: dict[str, Any] | None = None
    try:
        persisted_assessment: dict[str, Any] | None = None
        persisted_assessment_sha: str | None = None
        if args.dry_run:
            operation_record = _write_operation_record(
                args.runtime_state_root,
                network=args.network,
                node=args.node,
                status="assessing",
            )
            operation_id = str(operation_record["operation_id"])
        if args.execute:
            if not args.assessment_evidence or not args.acknowledge_assessment_evidence_sha256:
                raise MotherBrokenValidatorCleanupError(
                    "MOTHER_BROKEN_VALIDATOR_CLEANUP_ASSESSMENT_REQUIRED",
                    "--execute requires --assessment-evidence and --acknowledge-assessment-evidence-sha256 from a passing --dry-run",
                )
            persisted_assessment, persisted_assessment_sha = _verify_assessment_evidence(
                args.runtime_state_root,
                args.assessment_evidence,
                acknowledged_sha256=args.acknowledge_assessment_evidence_sha256,
                network=args.network,
                node=args.node,
                topology_evidence=args.topology_evidence,
                acknowledged_topology_sha256=args.acknowledge_topology_evidence_sha256,
            )
            persisted_operation = persisted_assessment.get("broken_validator_operation")
            if not isinstance(persisted_operation, Mapping) or not persisted_operation.get("operation_id"):
                raise MotherBrokenValidatorCleanupError(
                    "MOTHER_BROKEN_VALIDATOR_CLEANUP_OPERATION_RECORD_REQUIRED",
                    "assessment evidence is not bound to a broken-validator operation",
                )
            operation_id = str(persisted_operation["operation_id"])
            operation_record = _load_operation_record(
                args.runtime_state_root, network=args.network, node=args.node
            )
            if (
                not isinstance(operation_record, Mapping)
                or str(operation_record.get("operation_id") or "") != operation_id
                or str(operation_record.get("status") or "") in OPERATION_TERMINAL_STATUSES
            ):
                raise MotherBrokenValidatorCleanupError(
                    "MOTHER_BROKEN_VALIDATOR_CLEANUP_OPERATION_RECORD_REQUIRED",
                    "no matching unfinished broken-validator operation owns this execution",
                )
            resume_paths, resume_private_state = _private_state(args.runtime_state_root, network=args.network)
            resume_candidate = _find_resumable_remove_do_evidence(
                resume_paths, resume_private_state, persisted_assessment
            )

        assessment = (
            dict(persisted_assessment)
            if args.execute and resume_candidate is not None and persisted_assessment is not None
            else _assess_consensus_safety(
            runtime_state_root=args.runtime_state_root,
            network=args.network,
            node=args.node,
            topology_evidence=args.topology_evidence,
            acknowledged_topology_evidence_sha256=args.acknowledge_topology_evidence_sha256,
            topology_max_age_seconds=args.topology_max_age_seconds,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            max_wait_seconds=args.max_wait_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            rpc_will_work_post_remove=args.rpc_will_work_post_remove,
            )
        )
        if resume_candidate is not None and args.execute:
            assessment["mode"] = "post-validator-proof-resume"
            assessment["resume_remove_do_evidence"] = dict(resume_candidate)
        if args.dry_run:
            assert operation_id is not None
            assessment["mode"] = "dry-run"
            policy = assessment.get("policy") if isinstance(assessment.get("policy"), dict) else {}
            policy["live_read_only"] = True
            policy["local_evidence_write_performed"] = True
            assessment["policy"] = policy
            operation_status = "authorized" if assessment.get("status") == "pass" else "blocked"
            assessment["broken_validator_operation"] = {
                "kind": OPERATION_RECORD_KIND,
                "operation_id": operation_id,
                "target": {
                    "node": args.node,
                    "validator_address": assessment.get("target", {}).get("validator_address"),
                },
                "status": operation_status,
            }
            assessment_evidence_info: dict[str, Any] | None = None
            resume_command: str | None = None
            if assessment.get("status") == "pass":
                assessment_path, assessment_sha = _write_assessment_evidence(
                    args.runtime_state_root,
                    assessment,
                    operation=_operation(args.network, "write-assessment"),
                )
                assessment_evidence_info = {
                    "path": str(assessment_path),
                    "sha256": assessment_sha,
                }
                assessment["assessment_evidence"] = dict(assessment_evidence_info)
                resume_command = _execution_command(
                    python_executable=sys.executable,
                    runtime_state_root=args.runtime_state_root,
                    network=args.network,
                    node=args.node,
                    topology_evidence=assessment.get("topology_evidence", {}).get("path") or args.topology_evidence,
                    topology_sha256=assessment.get("topology_evidence", {}).get("sha256") or args.acknowledge_topology_evidence_sha256,
                    timeout=args.timeout,
                    max_response_bytes=args.max_response_bytes,
                    max_wait_seconds=args.max_wait_seconds,
                    poll_interval_seconds=args.poll_interval_seconds,
                    topology_max_age_seconds=args.topology_max_age_seconds,
                    rpc_will_work_post_remove=args.rpc_will_work_post_remove,
                    assessment_evidence=assessment_path,
                    assessment_evidence_sha256=assessment_sha,
                )
                assessment["execute_command"] = resume_command
            operation_record = _write_operation_record(
                args.runtime_state_root,
                network=args.network,
                node=args.node,
                status=operation_status,
                operation_id=operation_id,
                target_validator=assessment.get("target", {}).get("validator_address"),
                assessment_evidence=assessment_evidence_info,
                resume_command=resume_command,
            )
            assessment["broken_validator_operation"] = _operation_ref(operation_record)
            print(json.dumps(assessment, indent=2, sort_keys=True))
            if assessment.get("execute_command"):
                print()
                print("Consensus-safety dry-run passed and authorization evidence was persisted. Execute with:")
                print(assessment["execute_command"])
            else:
                print(
                    "MOTHER_BROKEN_VALIDATOR_CLEANUP_CONSENSUS_SAFETY_NOT_PROVEN: "
                    "no live mutation is authorized.",
                    file=sys.stderr,
                )
            return 0 if assessment.get("status") == "pass" else 3

        assert persisted_assessment is not None and persisted_assessment_sha is not None
        assert operation_id is not None
        if resume_candidate is None:
            _assert_live_assessment_matches_authorization(persisted_assessment, assessment)
        assessment["broken_validator_operation"] = dict(persisted_assessment["broken_validator_operation"])
        operation_record = _write_operation_record(
            args.runtime_state_root,
            network=args.network,
            node=args.node,
            status="executing",
            operation_id=operation_id,
            target_validator=assessment.get("target", {}).get("validator_address"),
            assessment_evidence={
                "path": str(Path(args.assessment_evidence).resolve(strict=False)),
                "sha256": persisted_assessment_sha,
            },
            resume_command=subprocess.list2cmdline([sys.executable, *sys.argv]),
        )

        result = _execute_cleanup(
            assessment,
            runtime_state_root=args.runtime_state_root,
            network=args.network,
            node=args.node,
            topology_evidence=args.topology_evidence,
            acknowledged_topology_evidence_sha256=args.acknowledge_topology_evidence_sha256,
            topology_max_age_seconds=args.topology_max_age_seconds,
            release_expires_in_seconds=args.release_expires_in_seconds,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            max_wait_seconds=args.max_wait_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            source_assessment_evidence={
                "path": str(Path(args.assessment_evidence).resolve(strict=False)),
                "sha256": persisted_assessment_sha,
                "authorization_sha256": persisted_assessment.get("authorization_sha256"),
            },
            resume_do_evidence=(
                resume_candidate.get("evidence_path")
                if isinstance(resume_candidate, Mapping)
                else None
            ),
        )
        final_operation_status = "completed" if result.get("status") == "pass" else "failed"
        operation_record = _write_operation_record(
            args.runtime_state_root,
            network=args.network,
            node=args.node,
            status=final_operation_status,
            operation_id=operation_id,
            target_validator=result.get("target", {}).get("validator_address"),
            assessment_evidence=result.get("source_assessment_evidence"),
            resume_command="" if final_operation_status == "completed" else (operation_record or {}).get("resume_command"),
            last_error=result.get("failure") if isinstance(result.get("failure"), Mapping) else None,
        )
        result["broken_validator_operation"] = _operation_ref(operation_record)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("status") == "pass" else 4
    except MotherBrokenValidatorCleanupError as exc:
        if operation_id is not None:
            try:
                operation_record = _write_operation_record(
                    args.runtime_state_root,
                    network=args.network,
                    node=args.node,
                    status="failed",
                    operation_id=operation_id,
                    resume_command=(operation_record or {}).get("resume_command"),
                    last_error={"code": exc.code, "message": str(exc)},
                )
            except Exception:
                pass
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
