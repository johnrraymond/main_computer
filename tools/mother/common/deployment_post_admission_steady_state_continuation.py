"""A-only continuation of post-admission steady-state cleanup.

This phase exists only after a passing schema-v2 mixed-state reconciliation has
proved that C already runs the canonical steady-state Compose while A remains on
the exact recovered Compose.  It never mutates C.  It refresh-gates on C's
retained quorum guardian, then performs exactly A Compose PATCH and A deploy.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import re
from pathlib import Path
from typing import Any

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, resolve_coolify_controller
from .deployment_post_admission_steady_state import (
    MotherDeploymentPostAdmissionSteadyStateError,
    _CANONICAL_ORDER,
    _CLAIM_DIRECTORY as _SOURCE_CLAIM_DIRECTORY,
    _EVIDENCE_DIRECTORY as _SOURCE_EVIDENCE_DIRECTORY,
    _GUARDIAN_HEALTH_FRESHNESS_SECONDS,
    _GUARDIAN_REFRESH_WAIT_SECONDS,
    _RELEASE_DIRECTORY as _SOURCE_RELEASE_DIRECTORY,
    _STEADY_RECONCILIATION_DIRECTORY,
    _TARGETS,
    _address,
    _binding,
    _canonical_under,
    _contains_sensitive,
    _digest_without,
    _duration,
    _ensure_root,
    _http,
    _mapping,
    _parse_utc,
    _relative,
    _resolve,
    _runtime_target_observation,
    _safe_message,
    _selection,
    _sha256,
    _timestamp,
    _wait_guardian_refresh_window,
    verify_post_admission_steady_state_reconciliation,
)
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_TRANSACTION_KIND = (
    "main_computer.mother.deployment_post_admission_steady_state_continuation_transaction.v1"
)
_RELEASE_KIND = (
    "main_computer.mother.deployment_post_admission_steady_state_continuation_release.v1"
)
_CLAIM_KIND = (
    "main_computer.mother.deployment_post_admission_steady_state_continuation_execution_claim.v1"
)
_EVIDENCE_KIND = (
    "main_computer.mother.deployment_post_admission_steady_state_continuation_evidence.v1"
)

_TRANSACTION_DIRECTORY = (
    "actions",
    "deployment-post-admission-steady-state-continuation-transactions",
)
_RELEASE_DIRECTORY = (
    "actions",
    "deployment-post-admission-steady-state-continuation-releases",
)
_CLAIM_DIRECTORY = (
    "actions",
    "deployment-post-admission-steady-state-continuation-execution-claims",
)
_EVIDENCE_DIRECTORY = (
    "evidence",
    "deployment-post-admission-steady-state-continuation",
)

_A = "mainneta-super1"
_C = "mainnetc-super1"
_EXPECTED_MUTATION_IDS = (
    f"{_A}.install-post-admission-steady-state-continuation",
    f"{_A}.deploy-post-admission-steady-state-continuation",
)


class MotherDeploymentPostAdmissionSteadyStateContinuationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _error(code: str, message: str) -> MotherDeploymentPostAdmissionSteadyStateContinuationError:
    return MotherDeploymentPostAdmissionSteadyStateContinuationError(code, message)


def _load_source_reconciliation(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    reconciliation_path: Path,
    *,
    selected_nodes: Iterable[str],
    max_age_seconds: int,
) -> tuple[dict[str, Any], str, dict[str, Any], Path, str]:
    _selection(selected_nodes)
    verified = verify_post_admission_steady_state_reconciliation(
        paths,
        private_state,
        Path(reconciliation_path),
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
    )
    reconciliation, _, reconciliation_file_sha = _canonical_under(
        paths,
        Path(reconciliation_path),
        _STEADY_RECONCILIATION_DIRECTORY,
        "post-admission steady-state reconciliation",
    )
    if (
        verified.get("clean") is not True
        or verified.get("next_phase")
        != "stage-post-admission-steady-state-continuation"
        or reconciliation_file_sha != verified.get("reconciliation_sha256")
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_RECONCILIATION_INVALID",
            "continuation requires the exact passing mixed-state reconciliation",
        )

    release_ref = _mapping(
        reconciliation.get("source_release"),
        "reconciliation.source_release",
    )
    source_release_path = _resolve(
        paths,
        release_ref.get("locator"),
        _SOURCE_RELEASE_DIRECTORY,
        "source steady-state release",
    )
    source_release, _, source_release_file_sha = _canonical_under(
        paths,
        source_release_path,
        _SOURCE_RELEASE_DIRECTORY,
        "source steady-state release",
    )
    source_release_sha = _sha256(
        source_release.get("post_admission_steady_state_release_sha256"),
        "source steady-state release digest",
    )
    if (
        release_ref.get("sha256") != source_release_sha
        or release_ref.get("file_sha256") != source_release_file_sha
        or _digest_without(
            source_release,
            "post_admission_steady_state_release_sha256",
        )
        != source_release_sha
        or source_release.get("mother_binding") != _binding(private_state)
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_SOURCE_INVALID",
            "source steady-state release binding is invalid",
        )

    summary = _mapping(reconciliation.get("summary"), "reconciliation.summary")
    if not (
        reconciliation.get("status") == "pass"
        and reconciliation.get("schema_version") == 2
        and summary.get("C_steady_state_verified") is True
        and summary.get("A_recovered_state_verified") is True
        and summary.get("chain_continuity_verified") is True
        and summary.get("validator_set_verified") is True
        and summary.get("blocks_advancing") is True
        and summary.get("latest_block_fresh") is True
        and summary.get("live_mutation_performed") is False
        and summary.get("validator_vote_performed") is False
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_RECONCILIATION_INVALID",
            "reconciliation does not prove C-steady/A-recovered continuity",
        )
    return (
        reconciliation,
        reconciliation_file_sha,
        source_release,
        source_release_path,
        source_release_file_sha,
    )


def _continuation_targets(source_release: Mapping[str, Any]) -> dict[str, Any]:
    source_targets = _mapping(source_release.get("targets"), "source release targets")
    c_source = _mapping(source_targets.get(_C), "source C target")
    a_source = _mapping(source_targets.get(_A), "source A target")
    return {
        _C: {
            "node": _C,
            "controller_id": "coolify-c",
            "service_uuid": c_source["service_uuid"],
            "required_healthy_components": list(
                c_source["required_healthy_components"]
            ),
            "recognized_obsolete_components": list(
                c_source["recognized_obsolete_components"]
            ),
            "steady_state_compose": dict(c_source["steady_state_compose"]),
        },
        _A: {
            "node": _A,
            "controller_id": "coolify-a",
            "service_uuid": a_source["service_uuid"],
            "accepted_recovered_aggregate_statuses": list(
                a_source["accepted_aggregate_statuses"]
            ),
            "required_healthy_components": list(
                a_source["required_healthy_components"]
            ),
            "recognized_obsolete_components": list(
                a_source["recognized_obsolete_components"]
            ),
            "recovered_compose": dict(a_source["recovered_compose"]),
            "steady_state_compose": dict(a_source["steady_state_compose"]),
        },
    }


def _continuation_mutations(source_release: Mapping[str, Any]) -> list[dict[str, Any]]:
    plan = _mapping(source_release.get("execution_plan"), "source execution plan")
    source_mutations = plan.get("mutations")
    if type(source_mutations) is not list:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_SOURCE_INVALID",
            "source mutation plan is missing",
        )
    by_id = {
        item.get("mutation_id"): item
        for item in source_mutations
        if isinstance(item, Mapping)
    }
    install = by_id.get(f"{_A}.install-post-admission-steady-state")
    deploy = by_id.get(f"{_A}.deploy-post-admission-steady-state")
    if not (
        isinstance(install, Mapping)
        and isinstance(deploy, Mapping)
        and install.get("controller_id") == "coolify-a"
        and install.get("method") == "PATCH"
        and deploy.get("controller_id") == "coolify-a"
        and deploy.get("method") == "GET"
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_SOURCE_INVALID",
            "source release does not contain the exact A cleanup mutations",
        )
    return [
        {
            "ordinal": 1,
            "mutation_id": _EXPECTED_MUTATION_IDS[0],
            "controller_id": "coolify-a",
            "method": "PATCH",
            "endpoint": install["endpoint"],
            "canonical_request_body": dict(
                _mapping(
                    install.get("canonical_request_body"),
                    "source A PATCH body",
                )
            ),
            "body_sha256": install["body_sha256"],
            "success_statuses": list(install["success_statuses"]),
        },
        {
            "ordinal": 2,
            "mutation_id": _EXPECTED_MUTATION_IDS[1],
            "controller_id": "coolify-a",
            "method": "GET",
            "endpoint": deploy["endpoint"],
            "canonical_request_body": None,
            "body_sha256": None,
            "success_statuses": list(deploy["success_statuses"]),
        },
    ]


def build_post_admission_steady_state_continuation_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    reconciliation_path: Path,
    *,
    network: str = "mainnet",
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 86400,
    created_at: str | None = None,
) -> dict[str, Any]:
    (
        reconciliation,
        reconciliation_sha,
        source_release,
        source_release_path,
        source_release_file_sha,
    ) = _load_source_reconciliation(
        paths,
        private_state,
        Path(reconciliation_path),
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
    )
    if network != reconciliation.get("network") or network != source_release.get(
        "network"
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_NETWORK_MISMATCH",
            "continuation network does not match reconciliation",
        )
    targets = _continuation_targets(source_release)
    mutations = _continuation_mutations(source_release)
    validator_set = sorted(
        {
            _address(item, "continuation validator")
            for item in reconciliation["validator_set"]
        }
    )
    transaction: dict[str, Any] = {
        "kind": _TRANSACTION_KIND,
        "schema_version": 1,
        "created_at": _timestamp(created_at),
        "network": network,
        "mother_binding": _binding(private_state),
        "staged_scope": "A-only-post-admission-steady-state-continuation",
        "reconciliation": {
            "locator": _relative(
                paths,
                Path(reconciliation_path),
                "steady-state reconciliation",
            ),
            "sha256": reconciliation_sha,
        },
        "source_consumed_release": {
            "locator": _relative(
                paths,
                source_release_path,
                "source consumed steady-state release",
            ),
            "sha256": source_release[
                "post_admission_steady_state_release_sha256"
            ],
            "file_sha256": source_release_file_sha,
        },
        "chain": {
            "chain_id": reconciliation["chain_id"],
            "genesis_sha256": reconciliation["genesis_sha256"],
            "validator_set": validator_set,
            "blocks_advancing": True,
        },
        "targets": targets,
        "execution_plan": {
            "precondition_modes": {
                _C: "steady-state",
                _A: "recovered",
            },
            "guardian_refresh_node": _C,
            "guardian_refresh_wait_seconds": _GUARDIAN_REFRESH_WAIT_SECONDS,
            "mutations": mutations,
            "final_mode": {
                _C: "steady-state",
                _A: "steady-state",
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
            "allowed_mutation_controllers": ["coolify-a"],
            "allowed_observation_controllers": ["coolify-a", "coolify-c"],
            "allowed_mutation_count": 2,
            "C_mutation_authorized": False,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "host_rpc_mapping_present": False,
            "validator_vote_present": False,
            "identity_rotation": False,
            "genesis_change": False,
            "chain_reset": False,
            "volume_deletion": False,
            "besu_data_deletion": False,
            "historical_evidence_deletion": False,
            "direct_coolify_database_mutation_authorized": False,
            "service_record_deletion_authorized": False,
            "recognized_exited_stale_records_tolerated": True,
            "network_access_performed": False,
            "live_mutation_performed": False,
        },
        "summary": {
            "clean": True,
            "transaction_verified_offline": True,
            "mutation_count": 2,
            "C_mutation_count": 0,
            "A_mutation_count": 2,
            "C_steady_state_verified_by_reconciliation": True,
            "A_recovered_state_verified_by_reconciliation": True,
            "chain_continuity_verified_by_reconciliation": True,
            "validator_vote_authorized": False,
            "live_execution_authorized": False,
            "next_phase": "release-post-admission-steady-state-continuation",
        },
        "post_admission_steady_state_continuation_transaction_sha256": None,
    }
    transaction[
        "post_admission_steady_state_continuation_transaction_sha256"
    ] = _digest_without(
        transaction,
        "post_admission_steady_state_continuation_transaction_sha256",
    )
    if _contains_sensitive(transaction):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_TRANSACTION_INVALID",
            "continuation transaction contains sensitive output",
        )
    return transaction


def write_post_admission_steady_state_continuation_transaction(
    paths: PrivateStatePaths,
    transaction: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(transaction)
    digest = _digest_without(
        document,
        "post_admission_steady_state_continuation_transaction_sha256",
    )
    if (
        document.get("kind") != _TRANSACTION_KIND
        or document.get(
            "post_admission_steady_state_continuation_transaction_sha256"
        )
        != digest
        or _contains_sensitive(document)
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_TRANSACTION_INVALID",
            "continuation transaction is malformed",
        )
    payload = canonical_json(document)
    destination = _ensure_root(paths, _TRANSACTION_DIRECTORY, operation) / (
        f"{re.sub(r'[^0-9A-Za-z]+', '', document['created_at'])[:32]}-"
        f"{digest[:16]}.json"
    )
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_post_admission_steady_state_continuation_transaction(
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
        "steady-state continuation transaction",
    )
    digest = _digest_without(
        document,
        "post_admission_steady_state_continuation_transaction_sha256",
    )
    if not (
        document.get("kind") == _TRANSACTION_KIND
        and document.get("mother_binding") == _binding(private_state)
        and document.get(
            "post_admission_steady_state_continuation_transaction_sha256"
        )
        == digest
        and not _contains_sensitive(document)
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_TRANSACTION_INVALID",
            "continuation transaction is invalid",
        )
    created = _parse_utc(document.get("created_at"), "transaction.created_at")
    reference = (
        now.astimezone(timezone.utc)
        if now is not None
        else datetime.now(timezone.utc)
    )
    age = int((reference - created).total_seconds())
    if age < -60 or age > max_age_seconds:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_TRANSACTION_STALE",
            "continuation transaction is outside its verification age window",
        )
    reconciliation_ref = _mapping(
        document.get("reconciliation"),
        "transaction.reconciliation",
    )
    reconciliation_path = _resolve(
        paths,
        reconciliation_ref.get("locator"),
        _STEADY_RECONCILIATION_DIRECTORY,
        "steady-state reconciliation",
    )
    expected = build_post_admission_steady_state_continuation_transaction(
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
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_TRANSACTION_INVALID",
            "continuation transaction does not rebuild exactly",
        )
    mutations = document["execution_plan"]["mutations"]
    if (
        [item["mutation_id"] for item in mutations]
        != list(_EXPECTED_MUTATION_IDS)
        or any(item["controller_id"] != "coolify-a" for item in mutations)
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_TRANSACTION_INVALID",
            "continuation transaction is not A-only",
        )
    return {
        "clean": True,
        "transaction_path": str(Path(transaction_path)),
        "post_admission_steady_state_continuation_transaction_sha256": digest,
        "byte_sha256": byte_sha,
        "age_seconds": max(0, age),
        "network": document["network"],
        "nodes": list(_CANONICAL_ORDER),
        "chain_id": document["chain"]["chain_id"],
        "genesis_sha256": document["chain"]["genesis_sha256"],
        "validator_set": list(document["chain"]["validator_set"]),
        "mutation_count": 2,
        "C_mutation_count": 0,
        "validator_vote_authorized": False,
        "live_execution_authorized": False,
        "mother_binding": dict(document["mother_binding"]),
    }


def build_post_admission_steady_state_continuation_release(
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
    verified = verify_post_admission_steady_state_continuation_transaction(
        paths,
        private_state,
        Path(transaction_path),
        selected_nodes=selected_nodes,
        max_age_seconds=transaction_max_age_seconds,
    )
    digest = verified[
        "post_admission_steady_state_continuation_transaction_sha256"
    ]
    if acknowledged_transaction_sha256 != digest:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_ACK_MISMATCH",
            "acknowledged continuation transaction digest does not match",
        )
    transaction, _, transaction_file_sha = _canonical_under(
        paths,
        Path(transaction_path),
        _TRANSACTION_DIRECTORY,
        "steady-state continuation transaction",
    )
    created = _timestamp(created_at)
    expires = (
        _parse_utc(created, "release.created_at")
        + timedelta(seconds=_duration(expires_in_seconds))
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created,
        "expires_at": expires,
        "network": transaction["network"],
        "mother_binding": dict(transaction["mother_binding"]),
        "transaction": {
            "locator": _relative(
                paths,
                Path(transaction_path),
                "continuation transaction",
            ),
            "sha256": digest,
            "file_sha256": transaction_file_sha,
        },
        "reconciliation": dict(transaction["reconciliation"]),
        "source_consumed_release": dict(transaction["source_consumed_release"]),
        "chain": dict(transaction["chain"]),
        "targets": dict(transaction["targets"]),
        "execution_plan": dict(transaction["execution_plan"]),
        "authority": {
            "authorization_source": "explicit-operator-release",
            "cleanup_authorized": True,
            "live_execution_authorized": True,
            "requested_use_limit": 1,
            "validator_vote_authorized": False,
            "identity_change_authorized": False,
            "genesis_change_authorized": False,
            "volume_deletion_authorized": False,
            "C_mutation_authorized": False,
        },
        "policy": dict(transaction["policy"]),
        "summary": {
            "clean": True,
            "mutation_count": 2,
            "C_mutation_count": 0,
            "A_mutation_count": 2,
            "one_use": True,
            "validator_vote_authorized": False,
            "next_phase": "apply-post-admission-steady-state-continuation",
        },
        "post_admission_steady_state_continuation_release_sha256": None,
    }
    release[
        "post_admission_steady_state_continuation_release_sha256"
    ] = _digest_without(
        release,
        "post_admission_steady_state_continuation_release_sha256",
    )
    if _contains_sensitive(release):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_RELEASE_INVALID",
            "continuation release contains sensitive output",
        )
    return release


def write_post_admission_steady_state_continuation_release(
    paths: PrivateStatePaths,
    release: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(release)
    digest = _digest_without(
        document,
        "post_admission_steady_state_continuation_release_sha256",
    )
    if (
        document.get("kind") != _RELEASE_KIND
        or document.get(
            "post_admission_steady_state_continuation_release_sha256"
        )
        != digest
        or _contains_sensitive(document)
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_RELEASE_INVALID",
            "continuation release is malformed",
        )
    payload = canonical_json(document)
    destination = _ensure_root(paths, _RELEASE_DIRECTORY, operation) / (
        f"{re.sub(r'[^0-9A-Za-z]+', '', document['created_at'])[:32]}-"
        f"{digest[:16]}.json"
    )
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_post_admission_steady_state_continuation_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    transaction_max_age_seconds: int = 86400,
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    document, _, byte_sha = _canonical_under(
        paths,
        Path(release_path),
        _RELEASE_DIRECTORY,
        "steady-state continuation release",
    )
    digest = _digest_without(
        document,
        "post_admission_steady_state_continuation_release_sha256",
    )
    if not (
        document.get("kind") == _RELEASE_KIND
        and document.get("mother_binding") == _binding(private_state)
        and document.get(
            "post_admission_steady_state_continuation_release_sha256"
        )
        == digest
        and not _contains_sensitive(document)
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_RELEASE_INVALID",
            "continuation release is invalid",
        )
    created = _parse_utc(document.get("created_at"), "release.created_at")
    expires = _parse_utc(document.get("expires_at"), "release.expires_at")
    reference = (
        now.astimezone(timezone.utc)
        if now is not None
        else datetime.now(timezone.utc)
    )
    age = int((reference - created).total_seconds())
    if age < -60 or age > max_age_seconds or reference > expires:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_RELEASE_EXPIRED",
            "continuation release is outside its active window",
        )
    transaction_ref = _mapping(
        document.get("transaction"),
        "release.transaction",
    )
    transaction_path = _resolve(
        paths,
        transaction_ref.get("locator"),
        _TRANSACTION_DIRECTORY,
        "continuation transaction",
    )
    transaction_verified = (
        verify_post_admission_steady_state_continuation_transaction(
            paths,
            private_state,
            transaction_path,
            selected_nodes=selected_nodes,
            max_age_seconds=transaction_max_age_seconds,
        )
    )
    transaction, _, transaction_file_sha = _canonical_under(
        paths,
        transaction_path,
        _TRANSACTION_DIRECTORY,
        "continuation transaction",
    )
    transaction_digest = transaction_verified[
        "post_admission_steady_state_continuation_transaction_sha256"
    ]
    if not (
        transaction_ref.get("sha256") == transaction_digest
        and transaction_ref.get("file_sha256") == transaction_file_sha
        and document.get("reconciliation") == transaction.get("reconciliation")
        and document.get("source_consumed_release")
        == transaction.get("source_consumed_release")
        and document.get("chain") == transaction.get("chain")
        and document.get("targets") == transaction.get("targets")
        and document.get("execution_plan") == transaction.get("execution_plan")
        and document.get("authority", {}).get("requested_use_limit") == 1
        and document.get("authority", {}).get("C_mutation_authorized") is False
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_RELEASE_INVALID",
            "continuation release does not match its verified transaction",
        )
    return {
        "clean": True,
        "release_path": str(Path(release_path)),
        "post_admission_steady_state_continuation_release_sha256": digest,
        "byte_sha256": byte_sha,
        "age_seconds": max(0, age),
        "expires_at": document["expires_at"],
        "network": document["network"],
        "nodes": list(_CANONICAL_ORDER),
        "chain_id": document["chain"]["chain_id"],
        "genesis_sha256": document["chain"]["genesis_sha256"],
        "validator_set": list(document["chain"]["validator_set"]),
        "mutation_count": 2,
        "C_mutation_count": 0,
        "live_execution_authorized": True,
        "validator_vote_authorized": False,
        "mother_binding": dict(document["mother_binding"]),
    }


def inspect_post_admission_steady_state_continuation_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    transaction_max_age_seconds: int = 86400,
    max_age_seconds: int = 300,
) -> dict[str, Any]:
    verified = verify_post_admission_steady_state_continuation_release(
        paths,
        private_state,
        Path(release_path),
        selected_nodes=selected_nodes,
        transaction_max_age_seconds=transaction_max_age_seconds,
        max_age_seconds=max_age_seconds,
    )
    digest = verified[
        "post_admission_steady_state_continuation_release_sha256"
    ]
    if acknowledged_release_sha256 != digest:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_ACK_MISMATCH",
            "acknowledged continuation release digest does not match",
        )
    claim_path = (
        paths.root
        / _CLAIM_DIRECTORY[0]
        / _CLAIM_DIRECTORY[1]
        / f"{digest}.json"
    )
    return {
        **verified,
        "release_already_claimed": claim_path.exists(),
        "network_access_performed": False,
        "live_mutation_performed": False,
        "validator_vote_performed": False,
    }


def _observe(
    *,
    controller: Any,
    target: Mapping[str, Any],
    expected_compose: str,
    expected_mode: str,
    require_recovered_status: bool,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    phase: str,
) -> dict[str, Any]:
    observation = _runtime_target_observation(
        controller=controller,
        target=target,
        expected_compose=expected_compose,
        expected_mode=expected_mode,
        require_aggregate_healthy=(expected_mode == "steady-state"),
        require_obsolete_absent=(expected_mode == "steady-state"),
        allow_exited_obsolete_records=(expected_mode == "steady-state"),
        allow_degraded_aggregate_for_exited_obsolete_records=(
            expected_mode == "steady-state"
        ),
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    if require_recovered_status:
        accepted = observation["effective_aggregate_status"] in set(
            target["accepted_recovered_aggregate_statuses"]
        )
        observation["accepted_recovered_aggregate_status"] = accepted
        observation["verified"] = bool(observation["verified"] and accepted)
    observation["phase"] = phase
    return observation


def _wait_observation(
    *,
    controller: Any,
    target: Mapping[str, Any],
    expected_compose: str,
    expected_mode: str,
    require_recovered_status: bool,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
    observations: list[dict[str, Any]],
    phase: str,
) -> dict[str, Any]:
    if max_wait_seconds < 0 or poll_interval_seconds < 0:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_INVALID",
            "wait and poll intervals must be non-negative",
        )
    start = __import__(
        "tools.mother.common.deployment_post_admission_steady_state",
        fromlist=["_MONOTONIC"],
    )._MONOTONIC()
    while True:
        observation = _observe(
            controller=controller,
            target=target,
            expected_compose=expected_compose,
            expected_mode=expected_mode,
            require_recovered_status=require_recovered_status,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            phase=phase,
        )
        observations.append(observation)
        if observation["verified"] is True:
            return observation
        steady_module = __import__(
            "tools.mother.common.deployment_post_admission_steady_state",
            fromlist=["_MONOTONIC", "_SLEEP"],
        )
        elapsed = steady_module._MONOTONIC() - start
        if elapsed >= max_wait_seconds:
            raise _error(
                "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_NOT_HEALTHY",
                f"{target['node']} did not reach exact {expected_mode} condition",
            )
        steady_module._SLEEP(
            min(
                poll_interval_seconds,
                max(0.0, max_wait_seconds - elapsed),
            )
        )


def _write_evidence(
    paths: PrivateStatePaths,
    evidence: Mapping[str, Any],
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(evidence)
    if document.get("kind") != _EVIDENCE_KIND or _contains_sensitive(document):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_EVIDENCE_INVALID",
            "continuation evidence is malformed",
        )
    payload = canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    destination = _ensure_root(paths, _EVIDENCE_DIRECTORY, operation) / (
        f"{re.sub(r'[^0-9A-Za-z]+', '', document['completed_at'])[:32]}-"
        f"{digest[:16]}.json"
    )
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def execute_post_admission_steady_state_continuation_release(
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
    inspected = inspect_post_admission_steady_state_continuation_release(
        paths,
        private_state,
        Path(release_path),
        acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=selected_nodes,
        transaction_max_age_seconds=transaction_max_age_seconds,
        max_age_seconds=max_age_seconds,
    )
    if inspected["release_already_claimed"]:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_RELEASE_ALREADY_CONSUMED",
            "continuation release already has an execution claim",
        )
    release, _, _ = _canonical_under(
        paths,
        Path(release_path),
        _RELEASE_DIRECTORY,
        "steady-state continuation release",
    )
    digest = inspected[
        "post_admission_steady_state_continuation_release_sha256"
    ]
    claim = {
        "kind": _CLAIM_KIND,
        "schema_version": 1,
        "claimed_at": _timestamp(),
        "release": {
            "locator": _relative(
                paths,
                Path(release_path),
                "continuation release",
            ),
            "sha256": digest,
        },
        "requested_use_limit": 1,
        "operation_id": operation.operation_id,
    }
    claim_path = (
        _ensure_root(paths, _CLAIM_DIRECTORY, operation) / f"{digest}.json"
    )
    if claim_path.exists():
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_RELEASE_ALREADY_CONSUMED",
            "continuation release already has an execution claim",
        )
    atomic_files.durable_create(
        claim_path,
        canonical_json(claim),
        operation=operation,
    )
    _secure_private_path(claim_path, is_directory=False, operation=operation)

    controllers = {
        "coolify-a": resolve_coolify_controller(
            private_state,
            release["network"],
            "coolify-a",
        ),
        "coolify-c": resolve_coolify_controller(
            private_state,
            release["network"],
            "coolify-c",
        ),
    }
    targets = release["targets"]
    preconditions: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    gate: dict[str, Any] | None = None
    failure: dict[str, str] | None = None
    started = _timestamp()

    try:
        c_target = targets[_C]
        a_target = targets[_A]
        c_pre = _observe(
            controller=controllers["coolify-c"],
            target=c_target,
            expected_compose=c_target["steady_state_compose"]["canonical_text"],
            expected_mode="steady-state",
            require_recovered_status=False,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            phase="C-guardian-before-refresh-window",
        )
        preconditions.append(c_pre)
        if c_pre["verified"] is not True:
            raise _error(
                "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_PRECONDITION_FAILED",
                "C is not in exact component-scoped steady state",
            )
        a_pre = _observe(
            controller=controllers["coolify-a"],
            target=a_target,
            expected_compose=a_target["recovered_compose"]["canonical_text"],
            expected_mode="recovered",
            require_recovered_status=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            phase="A-recovered-precondition",
        )
        preconditions.append(a_pre)
        if a_pre["verified"] is not True:
            raise _error(
                "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_PRECONDITION_FAILED",
                "A is not in the exact recovered state",
            )

        observed_wait = _wait_guardian_refresh_window()
        c_after = _wait_observation(
            controller=controllers["coolify-c"],
            target=c_target,
            expected_compose=c_target["steady_state_compose"]["canonical_text"],
            expected_mode="steady-state",
            require_recovered_status=False,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_wait_seconds=max_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            opener=opener,
            observations=observations,
            phase="C-guardian-after-refresh-window",
        )
        gate = {
            "node": _C,
            "guardian_health_freshness_seconds": (
                _GUARDIAN_HEALTH_FRESHNESS_SECONDS
            ),
            "required_wait_seconds": _GUARDIAN_REFRESH_WAIT_SECONDS,
            "observed_wait_seconds": observed_wait,
            "pre_observation_phase": "C-guardian-before-refresh-window",
            "post_observation_phase": "C-guardian-after-refresh-window",
            "verified": c_after["verified"] is True,
        }

        mutations = release["execution_plan"]["mutations"]
        if (
            type(mutations) is not list
            or [item.get("mutation_id") for item in mutations]
            != list(_EXPECTED_MUTATION_IDS)
            or any(item.get("controller_id") != "coolify-a" for item in mutations)
            or [item.get("method") for item in mutations] != ["PATCH", "GET"]
        ):
            raise _error(
                "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_RELEASE_INVALID",
                "released mutation set is not exact A-only continuation",
            )
        for mutation in mutations:
            body = (
                dict(mutation["canonical_request_body"])
                if isinstance(
                    mutation.get("canonical_request_body"),
                    Mapping,
                )
                else None
            )
            response = _http(
                controllers["coolify-a"],
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
                    "controller_id": "coolify-a",
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
                    "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_MUTATION_FAILED",
                    f"Coolify rejected {mutation['mutation_id']!r}",
                )

        a_final = _wait_observation(
            controller=controllers["coolify-a"],
            target=a_target,
            expected_compose=a_target["steady_state_compose"]["canonical_text"],
            expected_mode="steady-state",
            require_recovered_status=False,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_wait_seconds=max_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            opener=opener,
            observations=observations,
            phase="A-final-steady-state",
        )
        c_final = _wait_observation(
            controller=controllers["coolify-c"],
            target=c_target,
            expected_compose=c_target["steady_state_compose"]["canonical_text"],
            expected_mode="steady-state",
            require_recovered_status=False,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_wait_seconds=max_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            opener=opener,
            observations=observations,
            phase="C-final-steady-state",
        )
        if not (a_final["verified"] and c_final["verified"]):
            raise _error(
                "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_NOT_HEALTHY",
                "final component-scoped steady state was not proven",
            )
    except (
        MotherDeploymentPostAdmissionSteadyStateContinuationError,
        MotherDeploymentPostAdmissionSteadyStateError,
    ) as exc:
        failure = {
            "code": getattr(
                exc,
                "code",
                "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_FAILED",
            ),
            "message": _safe_message(exc),
        }
    except Exception:
        failure = {
            "code": "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_UNEXPECTED_FAILURE",
            "message": "unexpected steady-state continuation failure",
        }

    completed = _timestamp()
    mutation_sequence_ok = bool(
        [item.get("mutation_id") for item in receipts]
        == list(_EXPECTED_MUTATION_IDS)
        and all(item.get("status") == "succeeded" for item in receipts)
        and all(item.get("controller_id") == "coolify-a" for item in receipts)
    )
    phase_map = {
        item.get("phase"): item
        for item in observations
        if isinstance(item, Mapping)
    }
    a_final = phase_map.get("A-final-steady-state")
    c_final = phase_map.get("C-final-steady-state")
    gate_ok = bool(
        isinstance(gate, Mapping)
        and gate.get("verified") is True
        and gate.get("observed_wait_seconds", 0)
        >= _GUARDIAN_REFRESH_WAIT_SECONDS
    )
    final_ok = bool(
        isinstance(a_final, Mapping)
        and a_final.get("verified") is True
        and isinstance(c_final, Mapping)
        and c_final.get("verified") is True
    )
    complete = bool(
        failure is None
        and mutation_sequence_ok
        and gate_ok
        and final_ok
    )
    stale_records = sorted(
        set(
            (a_final or {}).get(
                "recognized_obsolete_components_present",
                [],
            )
        ).union(
            (c_final or {}).get(
                "recognized_obsolete_components_present",
                [],
            )
        )
    )
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
            "locator": _relative(
                paths,
                Path(release_path),
                "continuation release",
            ),
            "sha256": digest,
        },
        "execution_claim": {
            "locator": _relative(
                paths,
                claim_path,
                "continuation execution claim",
            ),
        },
        "chain_id": release["chain"]["chain_id"],
        "genesis_sha256": release["chain"]["genesis_sha256"],
        "validator_set": (
            list(release["chain"]["validator_set"]) if complete else None
        ),
        "precondition_receipts": preconditions,
        "mutation_receipts": receipts,
        "health_observations": observations,
        "guardian_refresh_gate": gate,
        "failure": failure,
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "allowed_mutation_controllers": ["coolify-a"],
            "allowed_mutation_count": 2,
            "C_mutation_performed": False,
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
            "direct_coolify_database_mutation_performed": False,
            "service_record_deletion_performed": False,
            "recognized_exited_stale_records_tolerated": True,
            "secrets_in_output": False,
        },
        "summary": {
            "clean": complete,
            "complete": complete,
            "A_steady_state_installed": mutation_sequence_ok and final_ok,
            "C_steady_state_preserved": (
                isinstance(c_final, Mapping)
                and c_final.get("verified") is True
            ),
            "C_mutation_count": 0,
            "A_mutation_count": len(receipts),
            "C_guardian_refresh_verified_before_A_restart": gate_ok,
            "validator_set_verified": complete,
            "blocks_advancing": gate_ok and final_ok,
            "latest_block_fresh": gate_ok and final_ok,
            "component_scoped_steady_state_verified": final_ok,
            "strict_aggregate_cleanup_complete": bool(
                final_ok
                and a_final.get("aggregate_service_healthy") is True
                and c_final.get("aggregate_service_healthy") is True
                and a_final.get("obsolete_components_absent") is True
                and c_final.get("obsolete_components_absent") is True
            )
            if final_ok
            else False,
            "platform_stale_component_records_present": bool(stale_records),
            "platform_stale_component_records": stale_records,
            "aggregate_badges_authoritative": False,
            "planned_mutation_count": 2,
            "attempted_mutation_count": len(receipts),
            "succeeded_mutation_count": sum(
                item.get("status") == "succeeded" for item in receipts
            ),
            "network_access_performed": bool(
                preconditions or observations or receipts
            ),
            "live_mutation_performed": any(
                item.get("live_write_acknowledged") is True
                for item in receipts
            ),
            "validator_vote_performed": False,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "next_phase": (
                "post-admission-steady-state-component-scoped-complete"
                if complete
                else "manual-review-required"
            ),
        },
    }
    evidence_path, evidence_sha = _write_evidence(
        paths,
        evidence,
        operation,
    )
    evidence["evidence"] = {
        "path": str(evidence_path),
        "sha256": evidence_sha,
    }
    return evidence


def verify_post_admission_steady_state_continuation_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
) -> dict[str, Any]:
    _selection(selected_nodes)
    document, _, evidence_file_sha = _canonical_under(
        paths,
        Path(evidence_path),
        _EVIDENCE_DIRECTORY,
        "steady-state continuation evidence",
    )
    if not (
        document.get("kind") == _EVIDENCE_KIND
        and document.get("status") == "pass"
        and document.get("mother_binding") == _binding(private_state)
        and not _contains_sensitive(document)
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_EVIDENCE_INVALID",
            "continuation evidence is not a passing canonical document",
        )
    completed = _parse_utc(
        document.get("completed_at"),
        "continuation_evidence.completed_at",
    )
    age = int((datetime.now(timezone.utc) - completed).total_seconds())
    if age < -60 or age > max_age_seconds:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_EVIDENCE_STALE",
            "continuation evidence is outside its verification age window",
        )

    release_ref = _mapping(document.get("release"), "evidence.release")
    release_path = _resolve(
        paths,
        release_ref.get("locator"),
        _RELEASE_DIRECTORY,
        "continuation release",
    )
    release, _, _ = _canonical_under(
        paths,
        release_path,
        _RELEASE_DIRECTORY,
        "continuation release",
    )
    release_sha = _digest_without(
        release,
        "post_admission_steady_state_continuation_release_sha256",
    )
    if not (
        release_ref.get("sha256") == release_sha
        and release.get(
            "post_admission_steady_state_continuation_release_sha256"
        )
        == release_sha
        and release.get("mother_binding") == _binding(private_state)
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_EVIDENCE_INVALID",
            "evidence release binding is invalid",
        )

    transaction_ref = _mapping(
        release.get("transaction"),
        "release.transaction",
    )
    transaction_path = _resolve(
        paths,
        transaction_ref.get("locator"),
        _TRANSACTION_DIRECTORY,
        "continuation transaction",
    )
    verified_transaction = (
        verify_post_admission_steady_state_continuation_transaction(
            paths,
            private_state,
            transaction_path,
            selected_nodes=selected_nodes,
            max_age_seconds=transaction_max_age_seconds,
        )
    )
    if transaction_ref.get("sha256") != verified_transaction[
        "post_admission_steady_state_continuation_transaction_sha256"
    ]:
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_EVIDENCE_INVALID",
            "evidence transaction binding is invalid",
        )

    claim_ref = _mapping(
        document.get("execution_claim"),
        "evidence.execution_claim",
    )
    claim_path = _resolve(
        paths,
        claim_ref.get("locator"),
        _CLAIM_DIRECTORY,
        "continuation execution claim",
    )
    claim, _, _ = _canonical_under(
        paths,
        claim_path,
        _CLAIM_DIRECTORY,
        "continuation execution claim",
    )
    if not (
        claim.get("kind") == _CLAIM_KIND
        and claim.get("requested_use_limit") == 1
        and claim.get("release", {}).get("sha256") == release_sha
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_EVIDENCE_INVALID",
            "continuation claim does not bind the release",
        )

    receipts = document.get("mutation_receipts")
    if not (
        type(receipts) is list
        and [item.get("mutation_id") for item in receipts]
        == list(_EXPECTED_MUTATION_IDS)
        and [item.get("method") for item in receipts] == ["PATCH", "GET"]
        and all(item.get("controller_id") == "coolify-a" for item in receipts)
        and all(item.get("status") == "succeeded" for item in receipts)
        and all(
            item.get("live_write_acknowledged") is True for item in receipts
        )
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_EVIDENCE_INVALID",
            "evidence mutation receipts are not exact A-only continuation",
        )

    preconditions = document.get("precondition_receipts")
    observations = document.get("health_observations")
    phase_map: dict[str, Mapping[str, Any]] = {}
    for item in (
        list(preconditions) if type(preconditions) is list else []
    ) + (list(observations) if type(observations) is list else []):
        if isinstance(item, Mapping) and type(item.get("phase")) is str:
            phase_map[item["phase"]] = item
    required_phases = {
        "C-guardian-before-refresh-window",
        "A-recovered-precondition",
        "C-guardian-after-refresh-window",
        "A-final-steady-state",
        "C-final-steady-state",
    }
    if not (
        required_phases.issubset(phase_map)
        and all(
            phase_map[phase].get("verified") is True
            for phase in required_phases
        )
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_EVIDENCE_INVALID",
            "evidence is missing an exact successful observation phase",
        )
    gate = _mapping(
        document.get("guardian_refresh_gate"),
        "evidence.guardian_refresh_gate",
    )
    summary = _mapping(document.get("summary"), "evidence.summary")
    policy = _mapping(document.get("policy"), "evidence.policy")
    if not (
        gate.get("node") == _C
        and gate.get("required_wait_seconds")
        == _GUARDIAN_REFRESH_WAIT_SECONDS
        and type(gate.get("observed_wait_seconds")) in {int, float}
        and not isinstance(gate.get("observed_wait_seconds"), bool)
        and gate.get("observed_wait_seconds")
        >= _GUARDIAN_REFRESH_WAIT_SECONDS
        and gate.get("verified") is True
        and document.get("chain_id") == release["chain"]["chain_id"]
        and document.get("genesis_sha256")
        == release["chain"]["genesis_sha256"]
        and document.get("validator_set")
        == release["chain"]["validator_set"]
        and policy.get("allowed_mutation_controllers") == ["coolify-a"]
        and policy.get("allowed_mutation_count") == 2
        and policy.get("C_mutation_performed") is False
        and policy.get("validator_vote_performed") is False
        and policy.get("manual_ssh_required") is False
        and policy.get("public_endpoint_created") is False
        and policy.get("direct_coolify_database_mutation_performed") is False
        and policy.get("service_record_deletion_performed") is False
        and summary.get("clean") is True
        and summary.get("complete") is True
        and summary.get("A_steady_state_installed") is True
        and summary.get("C_steady_state_preserved") is True
        and summary.get("C_mutation_count") == 0
        and summary.get("A_mutation_count") == 2
        and summary.get(
            "C_guardian_refresh_verified_before_A_restart"
        )
        is True
        and summary.get("validator_set_verified") is True
        and summary.get("blocks_advancing") is True
        and summary.get("latest_block_fresh") is True
        and summary.get("component_scoped_steady_state_verified") is True
        and summary.get("validator_vote_performed") is False
        and summary.get("next_phase")
        == "post-admission-steady-state-component-scoped-complete"
    ):
        raise _error(
            "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_EVIDENCE_INVALID",
            "continuation evidence failed exact invariant verification",
        )
    return {
        "clean": True,
        "age_seconds": max(0, age),
        "evidence_path": str(Path(evidence_path)),
        "evidence_sha256": evidence_file_sha,
        "network": document["network"],
        "nodes": list(document["nodes"]),
        "chain_id": document["chain_id"],
        "genesis_sha256": document["genesis_sha256"],
        "validator_set": list(document["validator_set"]),
        "A_steady_state_installed": True,
        "C_steady_state_preserved": True,
        "C_mutation_count": 0,
        "C_guardian_refresh_verified_before_A_restart": True,
        "component_scoped_steady_state_verified": True,
        "strict_aggregate_cleanup_complete": summary[
            "strict_aggregate_cleanup_complete"
        ],
        "platform_stale_component_records_present": summary[
            "platform_stale_component_records_present"
        ],
        "validator_vote_performed": False,
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "next_phase": (
            "post-admission-steady-state-component-scoped-complete"
        ),
        "mother_binding": dict(document["mother_binding"]),
    }
