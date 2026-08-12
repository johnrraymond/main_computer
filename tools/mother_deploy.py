#!/usr/bin/env python3
"""Plan, preflight, stage, release, and execute starter deployment artifacts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


_MAINNET_SOAK_OUT_OF_DATE_WARNING = (
    "WARNING: mainnet steady-state soak is a deprecated legacy testing path. "
    "The golden test path is operator-directed add/delete evidence; use "
    "the operator-directed evidence path for the selected topology and release."
)

_LEGACY_C2_TEST_PATH_DEPRECATED_WARNING = (
    "WARNING: stage-c2/apply-c2 commands are deprecated legacy fixture paths. "
    "The active testing path is operator-directed add/delete evidence, "
    "not the old fixture-driven path."
)


def _warn_mainnet_soak_out_of_date(command: str) -> None:
    print(f"{command}: {_MAINNET_SOAK_OUT_OF_DATE_WARNING}", file=sys.stderr)


def _warn_legacy_c2_testing_path_deprecated(command: str) -> None:
    print(f"{command}: {_LEGACY_C2_TEST_PATH_DEPRECATED_WARNING}", file=sys.stderr)


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.coolify_state import CoolifyObservationError, _DEFAULT_OPENER
from tools.mother.common.deployment_c2_state_extension import (
    MotherDeploymentC2StateExtensionError,
    build_c2_state_extension_release,
    execute_c2_state_extension_release,
    inspect_c2_state_extension_release,
    stage_c2_state_extension,
    verify_c2_state_extension_evidence,
    verify_c2_state_extension_release,
    verify_c2_state_extension_transaction,
    write_c2_state_extension_release,
    write_c2_state_extension_transaction,
)
from tools.mother.common.deployment_c2_standby import (
    MotherDeploymentC2StandbyError,
    build_c2_standby_identity_release,
    build_c2_standby_service_release,
    execute_c2_standby_identity_release,
    execute_c2_standby_service_release,
    inspect_c2_standby_identity_release,
    inspect_c2_standby_service_release,
    stage_c2_standby_identity_transaction,
    stage_c2_standby_service_transaction,
    verify_c2_standby_identity,
    verify_c2_standby_identity_release,
    verify_c2_standby_identity_transaction,
    verify_c2_standby_service,
    verify_c2_standby_service_release,
    verify_c2_standby_service_transaction,
    write_c2_standby_identity_release,
    write_c2_standby_service_release,
)
from tools.mother.common.deployment_c2_replica_standby import (
    MotherDeploymentC2ReplicaStandbyError,
    build_c2_replica_standby_release,
    execute_c2_replica_standby_release,
    inspect_c2_replica_standby_release,
    stage_c2_replica_standby_transaction,
    verify_c2_replica_standby,
    verify_c2_replica_standby_release,
    verify_c2_replica_standby_transaction,
    write_c2_replica_standby_release,
    write_c2_replica_standby_transaction,
)
from tools.mother.common.deployment_c2_replica_sync import (
    MotherDeploymentC2ReplicaSyncError,
    build_c2_replica_sync_release,
    build_c2_replica_sync_resume_release,
    diagnose_c2_replica_sync_materialization,
    execute_c2_replica_sync_release,
    execute_c2_replica_sync_resume_release,
    inspect_c2_replica_sync_release,
    inspect_c2_replica_sync_resume_release,
    verify_c2_replica_sync_evidence,
    verify_c2_replica_sync_release,
    verify_c2_replica_sync_resume_release,
    write_c2_replica_sync_release,
    write_c2_replica_sync_resume_release,
)
from tools.mother.common.deployment_c2_validator_admission import (
    MotherDeploymentC2ValidatorAdmissionError,
    build_c2_validator_admission_release,
    build_c2_validator_admission_transaction,
    execute_c2_validator_admission_release,
    inspect_c2_validator_admission_release,
    verify_c2_validator_admission_evidence,
    verify_c2_validator_admission_release,
    verify_c2_validator_admission_transaction,
    write_c2_validator_admission_release,
    write_c2_validator_admission_transaction,
)
from tools.mother.common.deployment_t3_post_admission_steady_state import (
    MotherDeploymentT3PostAdmissionSteadyStateError,
    build_t3_post_admission_steady_state_release,
    build_t3_post_admission_steady_state_transaction,
    execute_t3_post_admission_steady_state_release,
    inspect_t3_post_admission_steady_state_release,
    verify_t3_post_admission_steady_state_evidence,
    verify_t3_post_admission_steady_state_release,
    verify_t3_post_admission_steady_state_transaction,
    write_t3_post_admission_steady_state_release,
    write_t3_post_admission_steady_state_transaction,
)
from tools.mother.common.deployment_node_remove_prep import (
    MotherDeploymentNodeRemovePrepError,
    build_node_remove_prep_transaction,
    verify_node_remove_prep_transaction,
    write_node_remove_prep_transaction,
)
from tools.mother.common.deployment_node_remove_do import (
    MotherDeploymentNodeRemoveDoError,
    build_node_remove_do_release,
    execute_node_remove_do_release,
    verify_node_remove_do_evidence,
    verify_node_remove_do_release,
    write_node_remove_do_release,
)
from tools.mother.common.deployment_node_remove_finalize import (
    MotherDeploymentNodeRemoveFinalizeError,
    finalize_node_remove,
    verify_node_remove_finalize_evidence,
)
from tools.mother.common.deployment_node_add_prep import (
    MotherDeploymentNodeAddPrepError,
    build_node_add_prep_transaction,
    verify_node_add_prep_transaction,
    write_node_add_prep_transaction,
)
from tools.mother.common.deployment_node_add_do import (
    MotherDeploymentNodeAddDoError,
    build_node_add_do_release,
    execute_node_add_do_release,
    verify_node_add_do_evidence,
    verify_node_add_do_release,
    write_node_add_do_release,
)
from tools.mother.common.deployment_node_add_identity import (
    MotherDeploymentNodeAddIdentityError,
    build_node_add_identity_release,
    execute_node_add_identity_release,
    verify_node_add_identity_evidence,
    verify_node_add_identity_release,
    write_node_add_identity_release,
)
from tools.mother.common.deployment_node_add_single_node_bootstrap import (
    MotherDeploymentNodeAddSingleNodeBootstrapError,
    adopt_node_add_single_node_bootstrap_live_proof,
    build_node_add_single_node_bootstrap_release,
    execute_node_add_single_node_bootstrap_release,
    finalize_node_add_single_node_chain_and_hub_proof,
    verify_node_add_single_node_bootstrap_evidence,
    verify_node_add_single_node_bootstrap_release,
    verify_node_add_single_node_chain_and_hub_proof_evidence,
    write_node_add_single_node_bootstrap_release,
)
from tools.mother.common.deployment_node_add_replica_sync import (
    MotherDeploymentNodeAddReplicaSyncError,
    build_node_add_replica_sync_release,
    execute_node_add_replica_sync_release,
    verify_node_add_replica_sync_evidence,
    verify_node_add_replica_sync_release,
    write_node_add_replica_sync_release,
)
from tools.mother.common.deployment_node_add_rollback import (
    MotherDeploymentNodeAddRollbackError,
    build_node_add_rollback_release,
    execute_node_add_rollback_release,
    verify_node_add_rollback_evidence,
    verify_node_add_rollback_release,
    write_node_add_rollback_release,
)
from tools.mother.common.deployment_node_add_validator_admission import (
    MotherDeploymentNodeAddValidatorAdmissionError,
    build_node_add_validator_admission_release,
    execute_node_add_validator_admission_release,
    verify_node_add_validator_admission_evidence,
    verify_node_add_validator_admission_release,
    write_node_add_validator_admission_release,
)
from tools.mother.common.deployment_topology_rectification import (
    MotherDeploymentTopologyRectificationError,
    adopt_empty_current_topology,
    detect_topology_staleness,
    verify_empty_topology_rectification_evidence,
)
from tools.mother.common.deployment_identity_install import (
    MotherDeploymentIdentityInstallError,
    build_deployment_identity_install_transaction,
    verify_deployment_identity_install_transaction,
    write_deployment_identity_install_transaction,
)
from tools.mother.common.deployment_identity_executor import (
    MotherDeploymentIdentityExecutorError,
    execute_released_identity,
    inspect_released_identity,
)
from tools.mother.common.deployment_identity_release import (
    MotherDeploymentIdentityReleaseError,
    build_deployment_identity_release,
    verify_deployment_identity_release,
    write_deployment_identity_release,
)
from tools.mother.common.deployment_identity_rollback import (
    MotherDeploymentIdentityRollbackError,
    execute_identity_journal_rollback,
    execute_identity_mutation_rollback,
    inspect_identity_mutation_rollback,
    inspect_identity_rollback_journal,
    verify_identity_mutation_rollback,
    write_identity_mutation_rollback_verification,
)
from tools.mother.common.deployment_genesis_birth import (
    MotherDeploymentGenesisBirthError,
    build_genesis_birth_release,
    execute_genesis_birth_release,
    inspect_genesis_birth_release,
    verify_genesis_birth_evidence,
    verify_genesis_birth_release,
    write_genesis_birth_release,
)
from tools.mother.common.deployment_genesis import (
    MotherDeploymentGenesisError,
    build_deployment_genesis_transaction,
    verify_deployment_genesis_transaction,
    write_deployment_genesis_transaction,
)
from tools.mother.common.deployment_genesis_executor import (
    MotherDeploymentGenesisExecutorError,
    execute_released_genesis,
    inspect_released_genesis,
)
from tools.mother.common.deployment_genesis_rollback import (
    MotherDeploymentGenesisRollbackError,
    execute_genesis_journal_rollback,
    execute_genesis_mutation_rollback,
    inspect_genesis_mutation_rollback,
    inspect_genesis_rollback_journal,
    verify_genesis_mutation_rollback,
    write_genesis_mutation_rollback_verification,
)
from tools.mother.common.deployment_genesis_release import (
    DEFAULT_HUB_GIT_REF,
    DEFAULT_HUB_GIT_REPOSITORY,
    MotherDeploymentGenesisReleaseError,
    build_deployment_genesis_release,
    verify_deployment_genesis_release,
    write_deployment_genesis_release,
)
from tools.mother.common.deployment_executor import (
    MotherDeploymentExecutorError,
    execute_released_mutation,
    inspect_released_mutation,
)
from tools.mother.common.deployment_execution import (
    MotherDeploymentExecutionError,
    build_deployment_execution_request,
    verify_deployment_execution_request,
    write_deployment_execution_request,
)
from tools.mother.common.deployment_plan import (
    MotherDeploymentPlanError,
    build_starter_deployment_plan,
)
from tools.mother.common.deployment_preflight import (
    MotherDeploymentPreflightError,
    run_starter_deployment_preflight,
    verify_deployment_preflight_evidence,
    write_deployment_preflight_evidence,
)
from tools.mother.common.deployment_release import (
    MotherDeploymentReleaseError,
    build_deployment_mutation_release,
    verify_deployment_mutation_release,
    write_deployment_mutation_release,
)
from tools.mother.common.deployment_rollback import (
    MotherDeploymentRollbackError,
    execute_deployment_journal_rollback,
    execute_deployment_mutation_rollback,
    inspect_deployment_mutation_rollback,
    inspect_deployment_rollback_journal,
    verify_deployment_mutation_rollback,
)
from tools.mother.common.deployment_soft_replica import (
    MotherDeploymentSoftReplicaError,
    build_soft_replica_transaction,
    verify_soft_replica_transaction,
    write_soft_replica_transaction,
)
from tools.mother.common.deployment_soft_replica_release import (
    MotherDeploymentSoftReplicaReleaseError,
    build_soft_replica_release,
    verify_soft_replica_release,
    write_soft_replica_release,
)
from tools.mother.common.deployment_soft_replica_executor import (
    MotherDeploymentSoftReplicaExecutorError,
    execute_released_soft_replica,
    inspect_released_soft_replica,
)
from tools.mother.common.deployment_soft_replica_sync import (
    MotherDeploymentSoftReplicaSyncError,
    build_soft_replica_sync_release,
    execute_soft_replica_sync_release,
    inspect_soft_replica_sync_release,
    verify_soft_replica_sync_evidence,
    verify_soft_replica_sync_release,
    write_soft_replica_sync_release,
)
from tools.mother.common.deployment_validator_admission import (
    MotherDeploymentValidatorAdmissionError,
    build_validator_admission_transaction,
    verify_validator_admission_transaction,
    write_validator_admission_transaction,
)
from tools.mother.common.deployment_validator_admission_release import (
    MotherDeploymentValidatorAdmissionReleaseError,
    build_validator_admission_release,
    verify_validator_admission_release,
    write_validator_admission_release,
)
from tools.mother.common.deployment_validator_admission_executor import (
    MotherDeploymentValidatorAdmissionExecutorError,
    execute_validator_admission_release,
    inspect_validator_admission_release,
    verify_validator_admission_evidence,
)
from tools.mother.common.deployment_validator_quorum_recovery import (
    MotherDeploymentValidatorQuorumRecoveryError,
    build_validator_quorum_recovery_release,
    diagnose_validator_quorum_runtime,
    execute_validator_quorum_recovery_release,
    inspect_validator_quorum_recovery_release,
    reconcile_validator_quorum_recovery,
    verify_validator_quorum_recovery_evidence,
    verify_validator_quorum_recovery_reconciliation,
    verify_validator_quorum_recovery_release,
    write_validator_quorum_recovery_release,
)
from tools.mother.common.deployment_mainnet_soak import (
    MotherDeploymentMainnetSoakError,
    run_mainnet_steady_state_soak,
    verify_mainnet_steady_state_soak_evidence,
)
from tools.mother.common.deployment_validator_rpc_canary import (
    MotherDeploymentValidatorRpcCanaryError,
    build_validator_rpc_canary_transaction,
    inspect_validator_rpc_canary_identity_reservation,
    reserve_validator_rpc_canary_identity,
    verify_validator_rpc_canary_identity,
    verify_validator_rpc_canary_transaction,
    write_validator_rpc_canary_transaction,
)
from tools.mother.common.deployment_validator_rpc_canary_execution import (
    MotherDeploymentValidatorRpcCanaryExecutionError,
    build_validator_rpc_canary_release,
    execute_validator_rpc_canary_release,
    inspect_validator_rpc_canary_release,
    verify_validator_rpc_canary_evidence,
    verify_validator_rpc_canary_release,
    write_validator_rpc_canary_release,
)
from tools.mother.common.deployment_coolify_service_lifecycle_probe import (
    MotherDeploymentCoolifyServiceLifecycleProbeError,
    execute_coolify_service_lifecycle_probe,
    inspect_coolify_service_lifecycle_probe,
    verify_coolify_service_lifecycle_probe_evidence,
)
from tools.mother.common.deployment_completed_helper_cleanup import (
    MotherDeploymentCompletedHelperCleanupError,
    execute_completed_mother_helper_cleanup,
    inspect_completed_mother_helper_cleanup,
    verify_completed_mother_helper_cleanup_evidence,
)
from tools.mother.common.deployment_validator_rpc_canary_funding import (
    MotherDeploymentValidatorRpcCanaryFundingError,
    build_validator_rpc_canary_funding_release,
    build_validator_rpc_canary_funding_transaction,
    execute_validator_rpc_canary_funding_release,
    inspect_validator_rpc_canary_funding_release,
    verify_validator_rpc_canary_funding_evidence,
    verify_validator_rpc_canary_funding_release,
    verify_validator_rpc_canary_funding_transaction,
    write_validator_rpc_canary_funding_release,
    write_validator_rpc_canary_funding_transaction,
)
from tools.mother.common.deployment_post_admission_steady_state import (
    MotherDeploymentPostAdmissionSteadyStateError,
    build_post_admission_steady_state_release,
    build_post_admission_steady_state_transaction,
    execute_post_admission_steady_state_release,
    inspect_post_admission_steady_state_release,
    reconcile_post_admission_steady_state,
    verify_post_admission_steady_state_evidence,
    verify_post_admission_steady_state_reconciliation,
    verify_post_admission_steady_state_release,
    verify_post_admission_steady_state_transaction,
    write_post_admission_steady_state_release,
    write_post_admission_steady_state_transaction,
)
from tools.mother.common.deployment_post_admission_steady_state_continuation import (
    MotherDeploymentPostAdmissionSteadyStateContinuationError,
    build_post_admission_steady_state_continuation_release,
    build_post_admission_steady_state_continuation_transaction,
    execute_post_admission_steady_state_continuation_release,
    inspect_post_admission_steady_state_continuation_release,
    verify_post_admission_steady_state_continuation_evidence,
    verify_post_admission_steady_state_continuation_release,
    verify_post_admission_steady_state_continuation_transaction,
    write_post_admission_steady_state_continuation_release,
    write_post_admission_steady_state_continuation_transaction,
)
from tools.mother.common.deployment_standby import (
    MotherDeploymentStandbyError,
    run_deployment_standby_verification,
    verify_deployment_standby_evidence,
    write_deployment_standby_verification,
)
from tools.mother.common.deployment_transaction import (
    MotherDeploymentTransactionError,
    build_deployment_mutation_transaction,
    verify_deployment_mutation_transaction,
    write_deployment_mutation_transaction,
)
from tools.mother.common.errors import MotherError, exit_code_for
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import read_private_state


DEFAULT_RUNTIME_STATE_ROOT = Path("runtime/state")


def _operation(command: str, network: str, operation_id: str | None) -> OperationIdentity:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return OperationIdentity(
        operation_id=operation_id or f"mother-deploy-{command}-{network}-{stamp}",
        request_id=f"mother-deploy-cli-{command}",
        network=network,
        operation_kind="MOTHER-OP-PLAN",
    )


def _selected_nodes(raw_values: list[str]) -> tuple[str, ...]:
    selected: list[str] = []
    for raw in raw_values:
        selected.extend(item.strip() for item in raw.split(",") if item.strip())
    return tuple(selected)


def _compact_post_admission_artifact(value: Any) -> Any:
    """Remove duplicated heavy payloads from CLI output after durable persistence."""
    if isinstance(value, dict):
        return {
            key: _compact_post_admission_artifact(item)
            for key, item in value.items()
            if key not in {"canonical_text", "canonical_request_body"}
        }
    if isinstance(value, list):
        return [_compact_post_admission_artifact(item) for item in value]
    return value


def _compact_post_admission_execution(result: dict[str, Any]) -> dict[str, Any]:
    """Return the operational verdict while leaving full receipts in evidence on disk."""
    keys = (
        "kind",
        "started_at",
        "completed_at",
        "status",
        "network",
        "nodes",
        "release",
        "execution_claim",
        "chain_id",
        "genesis_sha256",
        "validator_set",
        "guardian_refresh_gate",
        "failure",
        "summary",
        "evidence",
    )
    return {key: result[key] for key in keys if key in result}


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--network", default="mainnet")
    parser.add_argument(
        "--node",
        action="append",
        default=[],
        help="optional target name; repeat or provide comma-separated names",
    )
    parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    parser.add_argument("--operation-id")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)


    add_node = subparsers.add_parser(
        "add-node",
        help="documented generic Mother node-add surface; prep is topology-diff driven and local-only",
        allow_abbrev=False,
    )
    add_node_subparsers = add_node.add_subparsers(dest="add_node_phase", required=True)
    add_node_prep = add_node_subparsers.add_parser(
        "prep",
        help="prepare an explicit add-node transaction from canonical topology evidence",
        allow_abbrev=False,
    )
    add_node_prep.add_argument("network", choices=["mainnet"])
    add_node_prep.add_argument("--node", required=True, help="explicit absent node name to add; never inferred from a deprecated fixture stage")
    add_node_prep.add_argument("--host", required=True, help="explicit target Coolify controller/host for the added node")
    add_node_prep.add_argument("--mode", default="soft", choices=["initial", "soft", "reactivate"])
    add_node_prep.add_argument("--baseline-evidence", required=True)
    add_node_prep.add_argument("--baseline-evidence-sha256", required=True)
    add_node_prep.add_argument("--baseline-max-age-seconds", type=int, default=86400)
    add_node_prep.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    add_node_prep.add_argument("--operation-id")
    add_node_prep.add_argument("--created-at")
    add_node_prep.add_argument("--write-transaction", action="store_true")

    add_node_do = add_node_subparsers.add_parser(
        "do",
        help="execute the released generic add-node standby-service creation phase",
        allow_abbrev=False,
    )
    add_node_do.add_argument("network", choices=["mainnet"])
    add_node_do.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    add_node_do.add_argument("--operation-id")
    add_node_do.add_argument("--release", required=True)
    add_node_do.add_argument("--acknowledge-release-sha256", required=True)
    add_node_do.add_argument("--max-age-seconds", type=int, default=900)
    add_node_do.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    add_node_do.add_argument("--baseline-max-age-seconds", type=int, default=86400)
    add_node_do.add_argument("--timeout", type=float, default=30.0)
    add_node_do.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    add_node_do.add_argument("--execute", action="store_true", help="required to perform live standby service creation")
    add_node_identity = add_node_subparsers.add_parser(
        "identity",
        help="execute the released generic add-node identity-install phase",
        allow_abbrev=False,
    )
    add_node_identity.add_argument("network", choices=["mainnet"])
    add_node_identity.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    add_node_identity.add_argument("--operation-id")
    add_node_identity.add_argument("--release", required=True)
    add_node_identity.add_argument("--acknowledge-release-sha256", required=True)
    add_node_identity.add_argument("--max-age-seconds", type=int, default=900)
    add_node_identity.add_argument("--add-do-max-age-seconds", type=int, default=86400)
    add_node_identity.add_argument("--add-do-release-max-age-seconds", type=int, default=86400)
    add_node_identity.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    add_node_identity.add_argument("--baseline-max-age-seconds", type=int, default=86400)
    add_node_identity.add_argument("--timeout", type=float, default=30.0)
    add_node_identity.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    add_node_identity.add_argument("--execute", action="store_true")

    add_node_single_node_bootstrap = add_node_subparsers.add_parser(
        "single-node-bootstrap",
        help="execute the released operator-directed empty-topology single-node chain+Hub bootstrap phase",
        allow_abbrev=False,
    )
    add_node_single_node_bootstrap.add_argument("network", choices=["mainnet"])
    add_node_single_node_bootstrap.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    add_node_single_node_bootstrap.add_argument("--operation-id")
    add_node_single_node_bootstrap.add_argument("--release", required=True)
    add_node_single_node_bootstrap.add_argument("--acknowledge-release-sha256", required=True)
    add_node_single_node_bootstrap.add_argument("--max-age-seconds", type=int, default=900)
    add_node_single_node_bootstrap.add_argument("--identity-max-age-seconds", type=int, default=86400)
    add_node_single_node_bootstrap.add_argument("--identity-release-max-age-seconds", type=int, default=86400)
    add_node_single_node_bootstrap.add_argument("--add-do-max-age-seconds", type=int, default=86400)
    add_node_single_node_bootstrap.add_argument("--add-do-release-max-age-seconds", type=int, default=86400)
    add_node_single_node_bootstrap.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    add_node_single_node_bootstrap.add_argument("--baseline-max-age-seconds", type=int, default=86400)
    add_node_single_node_bootstrap.add_argument("--timeout", type=float, default=30.0)
    add_node_single_node_bootstrap.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    add_node_single_node_bootstrap.add_argument("--max-wait-seconds", type=float, default=300.0)
    add_node_single_node_bootstrap.add_argument("--poll-interval-seconds", type=float, default=5.0)
    add_node_single_node_bootstrap.add_argument("--execute", action="store_true")

    add_node_single_node_chain_and_hub_proof = add_node_subparsers.add_parser(
        "single-node-chain-and-hub-proof",
        help="write the read-only final proof/topology artifact for an operator-directed single-node add",
        allow_abbrev=False,
    )
    add_node_single_node_chain_and_hub_proof.add_argument("network", choices=["mainnet"])
    add_node_single_node_chain_and_hub_proof.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    add_node_single_node_chain_and_hub_proof.add_argument("--operation-id")
    add_node_single_node_chain_and_hub_proof.add_argument("--bootstrap-evidence", required=True)
    add_node_single_node_chain_and_hub_proof.add_argument("--acknowledge-bootstrap-evidence-sha256", required=True)
    add_node_single_node_chain_and_hub_proof.add_argument("--max-age-seconds", type=int, default=86400)
    add_node_single_node_chain_and_hub_proof.add_argument("--release-max-age-seconds", type=int, default=86400)
    add_node_single_node_chain_and_hub_proof.add_argument("--identity-max-age-seconds", type=int, default=86400)
    add_node_single_node_chain_and_hub_proof.add_argument("--identity-release-max-age-seconds", type=int, default=86400)
    add_node_single_node_chain_and_hub_proof.add_argument("--add-do-max-age-seconds", type=int, default=86400)
    add_node_single_node_chain_and_hub_proof.add_argument("--add-do-release-max-age-seconds", type=int, default=86400)
    add_node_single_node_chain_and_hub_proof.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    add_node_single_node_chain_and_hub_proof.add_argument("--baseline-max-age-seconds", type=int, default=86400)
    add_node_single_node_chain_and_hub_proof.add_argument("--write-evidence", action="store_true")

    add_node_replica_sync = add_node_subparsers.add_parser(
        "replica-sync",
        help="execute the released generic add-node non-validator replica-sync phase",
        allow_abbrev=False,
    )
    add_node_replica_sync.add_argument("network", choices=["mainnet"])
    add_node_replica_sync.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    add_node_replica_sync.add_argument("--operation-id")
    add_node_replica_sync.add_argument("--release", required=True)
    add_node_replica_sync.add_argument("--acknowledge-release-sha256", required=True)
    add_node_replica_sync.add_argument("--max-age-seconds", type=int, default=900)
    add_node_replica_sync.add_argument("--identity-max-age-seconds", type=int, default=86400)
    add_node_replica_sync.add_argument("--identity-release-max-age-seconds", type=int, default=86400)
    add_node_replica_sync.add_argument("--add-do-max-age-seconds", type=int, default=86400)
    add_node_replica_sync.add_argument("--add-do-release-max-age-seconds", type=int, default=86400)
    add_node_replica_sync.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    add_node_replica_sync.add_argument("--baseline-max-age-seconds", type=int, default=86400)
    add_node_replica_sync.add_argument("--timeout", type=float, default=30.0)
    add_node_replica_sync.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    add_node_replica_sync.add_argument("--max-wait-seconds", type=float, default=300.0)
    add_node_replica_sync.add_argument("--poll-interval-seconds", type=float, default=5.0)
    add_node_replica_sync.add_argument("--execute", action="store_true")


    add_node_validator_admission = add_node_subparsers.add_parser(
        "validator-admission",
        help="execute the released generic add-node validator admission and activation phase",
        allow_abbrev=False,
    )
    add_node_validator_admission.add_argument("network", choices=["mainnet"])
    add_node_validator_admission.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    add_node_validator_admission.add_argument("--operation-id")
    add_node_validator_admission.add_argument("--release", required=True)
    add_node_validator_admission.add_argument("--acknowledge-release-sha256", required=True)
    add_node_validator_admission.add_argument("--max-age-seconds", type=int, default=900)
    add_node_validator_admission.add_argument("--replica-sync-max-age-seconds", type=int, default=86400)
    add_node_validator_admission.add_argument("--replica-sync-release-max-age-seconds", type=int, default=86400)
    add_node_validator_admission.add_argument("--identity-max-age-seconds", type=int, default=86400)
    add_node_validator_admission.add_argument("--identity-release-max-age-seconds", type=int, default=86400)
    add_node_validator_admission.add_argument("--add-do-max-age-seconds", type=int, default=86400)
    add_node_validator_admission.add_argument("--add-do-release-max-age-seconds", type=int, default=86400)
    add_node_validator_admission.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    add_node_validator_admission.add_argument("--baseline-max-age-seconds", type=int, default=86400)
    add_node_validator_admission.add_argument("--timeout", type=float, default=30.0)
    add_node_validator_admission.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    add_node_validator_admission.add_argument("--max-wait-seconds", type=float, default=300.0)
    add_node_validator_admission.add_argument("--poll-interval-seconds", type=float, default=5.0)
    add_node_validator_admission.add_argument("--execute", action="store_true")
    add_node_rollback = add_node_subparsers.add_parser(
        "rollback",
        help="execute a released pre-admission add-node rollback by deleting only the created standby service",
        allow_abbrev=False,
    )
    add_node_rollback.add_argument("network", choices=["mainnet"])
    add_node_rollback.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    add_node_rollback.add_argument("--operation-id")
    add_node_rollback.add_argument("--release", required=True)
    add_node_rollback.add_argument("--acknowledge-release-sha256", required=True)
    add_node_rollback.add_argument("--max-age-seconds", type=int, default=900)
    add_node_rollback.add_argument("--failed-evidence-max-age-seconds", type=int, default=86400)
    add_node_rollback.add_argument("--timeout", type=float, default=30.0)
    add_node_rollback.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    add_node_rollback.add_argument("--max-wait-seconds", type=float, default=300.0)
    add_node_rollback.add_argument("--poll-interval-seconds", type=float, default=5.0)
    add_node_rollback.add_argument("--execute", action="store_true")

    verify_node_add_prep = subparsers.add_parser(
        "verify-add-node-prep-transaction",
        help="verify a prepared generic Mother add-node transaction without live mutation",
        allow_abbrev=False,
    )
    verify_node_add_prep.add_argument("--network", default="mainnet", choices=["mainnet"])
    verify_node_add_prep.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    verify_node_add_prep.add_argument("--operation-id")
    verify_node_add_prep.add_argument("--transaction", required=True)
    verify_node_add_prep.add_argument("--max-age-seconds", type=int, default=86400)
    verify_node_add_prep.add_argument("--baseline-max-age-seconds", type=int, default=86400)

    release_node_add_do = subparsers.add_parser(
        "release-add-node-do",
        help="mint an explicit expiring release for a verified generic Mother add-node prep transaction",
        allow_abbrev=False,
    )
    release_node_add_do.add_argument("--network", default="mainnet", choices=["mainnet"])
    release_node_add_do.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    release_node_add_do.add_argument("--operation-id")
    release_node_add_do.add_argument("--transaction", required=True)
    release_node_add_do.add_argument("--acknowledge-node-add-prep-transaction-sha256", required=True)
    release_node_add_do.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    release_node_add_do.add_argument("--baseline-max-age-seconds", type=int, default=86400)
    release_node_add_do.add_argument("--expires-in-seconds", type=int, default=300)
    release_node_add_do.add_argument("--created-at")
    release_node_add_do.add_argument("--write-release", action="store_true")

    verify_node_add_do_release_parser = subparsers.add_parser(
        "verify-add-node-do-release",
        help="verify a generic Mother add-node do release before live service creation",
        allow_abbrev=False,
    )
    verify_node_add_do_release_parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    verify_node_add_do_release_parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    verify_node_add_do_release_parser.add_argument("--operation-id")
    verify_node_add_do_release_parser.add_argument("--release", required=True)
    verify_node_add_do_release_parser.add_argument("--max-age-seconds", type=int, default=900)
    verify_node_add_do_release_parser.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_node_add_do_release_parser.add_argument("--baseline-max-age-seconds", type=int, default=86400)

    verify_node_add_do_evidence_parser = subparsers.add_parser(
        "verify-add-node-do-evidence",
        help="verify generic Mother add-node do evidence after standby service creation",
        allow_abbrev=False,
    )
    verify_node_add_do_evidence_parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    verify_node_add_do_evidence_parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    verify_node_add_do_evidence_parser.add_argument("--operation-id")
    verify_node_add_do_evidence_parser.add_argument("--evidence", required=True)
    verify_node_add_do_evidence_parser.add_argument("--max-age-seconds", type=int, default=86400)
    verify_node_add_do_evidence_parser.add_argument("--release-max-age-seconds", type=int, default=86400)
    verify_node_add_do_evidence_parser.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_node_add_do_evidence_parser.add_argument("--baseline-max-age-seconds", type=int, default=86400)

    release_node_add_identity = subparsers.add_parser(
        "release-add-node-identity",
        help="mint an explicit expiring release for generic Mother add-node identity installation",
        allow_abbrev=False,
    )
    release_node_add_identity.add_argument("--network", default="mainnet", choices=["mainnet"])
    release_node_add_identity.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    release_node_add_identity.add_argument("--operation-id")
    release_node_add_identity.add_argument("--add-do-evidence", required=True)
    release_node_add_identity.add_argument("--acknowledge-add-node-do-evidence-sha256", required=True)
    release_node_add_identity.add_argument("--max-age-seconds", type=int, default=86400)
    release_node_add_identity.add_argument("--add-do-release-max-age-seconds", type=int, default=86400)
    release_node_add_identity.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    release_node_add_identity.add_argument("--baseline-max-age-seconds", type=int, default=86400)
    release_node_add_identity.add_argument("--expires-in-seconds", type=int, default=300)
    release_node_add_identity.add_argument("--created-at")
    release_node_add_identity.add_argument("--write-release", action="store_true")

    verify_node_add_identity_release_parser = subparsers.add_parser(
        "verify-add-node-identity-release",
        help="verify a generic Mother add-node identity release before live env installation",
        allow_abbrev=False,
    )
    verify_node_add_identity_release_parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    verify_node_add_identity_release_parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    verify_node_add_identity_release_parser.add_argument("--operation-id")
    verify_node_add_identity_release_parser.add_argument("--release", required=True)
    verify_node_add_identity_release_parser.add_argument("--max-age-seconds", type=int, default=900)
    verify_node_add_identity_release_parser.add_argument("--add-do-max-age-seconds", type=int, default=86400)
    verify_node_add_identity_release_parser.add_argument("--add-do-release-max-age-seconds", type=int, default=86400)
    verify_node_add_identity_release_parser.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_node_add_identity_release_parser.add_argument("--baseline-max-age-seconds", type=int, default=86400)

    verify_node_add_identity_evidence_parser = subparsers.add_parser(
        "verify-add-node-identity-evidence",
        help="verify generic Mother add-node identity evidence after environment installation",
        allow_abbrev=False,
    )
    verify_node_add_identity_evidence_parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    verify_node_add_identity_evidence_parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    verify_node_add_identity_evidence_parser.add_argument("--operation-id")
    verify_node_add_identity_evidence_parser.add_argument("--evidence", required=True)
    verify_node_add_identity_evidence_parser.add_argument("--max-age-seconds", type=int, default=86400)
    verify_node_add_identity_evidence_parser.add_argument("--release-max-age-seconds", type=int, default=86400)
    verify_node_add_identity_evidence_parser.add_argument("--add-do-max-age-seconds", type=int, default=86400)
    verify_node_add_identity_evidence_parser.add_argument("--add-do-release-max-age-seconds", type=int, default=86400)
    verify_node_add_identity_evidence_parser.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_node_add_identity_evidence_parser.add_argument("--baseline-max-age-seconds", type=int, default=86400)


    release_node_add_single_node_bootstrap = subparsers.add_parser(
        "release-add-node-single-node-bootstrap",
        help="mint an explicit expiring release for operator-directed empty-topology single-node chain+Hub bootstrap",
        allow_abbrev=False,
    )
    release_node_add_single_node_bootstrap.add_argument("--network", default="mainnet", choices=["mainnet"])
    release_node_add_single_node_bootstrap.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    release_node_add_single_node_bootstrap.add_argument("--operation-id")
    release_node_add_single_node_bootstrap.add_argument("--identity-evidence", required=True)
    release_node_add_single_node_bootstrap.add_argument("--acknowledge-add-node-identity-evidence-sha256", required=True)
    release_node_add_single_node_bootstrap.add_argument("--max-age-seconds", type=int, default=86400)
    release_node_add_single_node_bootstrap.add_argument("--identity-release-max-age-seconds", type=int, default=86400)
    release_node_add_single_node_bootstrap.add_argument("--add-do-max-age-seconds", type=int, default=86400)
    release_node_add_single_node_bootstrap.add_argument("--add-do-release-max-age-seconds", type=int, default=86400)
    release_node_add_single_node_bootstrap.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    release_node_add_single_node_bootstrap.add_argument("--baseline-max-age-seconds", type=int, default=86400)
    release_node_add_single_node_bootstrap.add_argument("--expires-in-seconds", type=int, default=300)
    release_node_add_single_node_bootstrap.add_argument("--hub-git-repository", default="https://github.com/johnrraymond/main_computer")
    release_node_add_single_node_bootstrap.add_argument("--hub-git-ref", default="main")
    release_node_add_single_node_bootstrap.add_argument("--created-at")
    release_node_add_single_node_bootstrap.add_argument("--write-release", action="store_true")

    verify_node_add_single_node_bootstrap_release_parser = subparsers.add_parser(
        "verify-add-node-single-node-bootstrap-release",
        help="verify an operator-directed single-node bootstrap release before live chain+Hub bootstrap",
        allow_abbrev=False,
    )
    verify_node_add_single_node_bootstrap_release_parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    verify_node_add_single_node_bootstrap_release_parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    verify_node_add_single_node_bootstrap_release_parser.add_argument("--operation-id")
    verify_node_add_single_node_bootstrap_release_parser.add_argument("--release", required=True)
    verify_node_add_single_node_bootstrap_release_parser.add_argument("--max-age-seconds", type=int, default=900)
    verify_node_add_single_node_bootstrap_release_parser.add_argument("--identity-max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_bootstrap_release_parser.add_argument("--identity-release-max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_bootstrap_release_parser.add_argument("--add-do-max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_bootstrap_release_parser.add_argument("--add-do-release-max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_bootstrap_release_parser.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_bootstrap_release_parser.add_argument("--baseline-max-age-seconds", type=int, default=86400)

    verify_node_add_single_node_bootstrap_evidence_parser = subparsers.add_parser(
        "verify-add-node-single-node-bootstrap-evidence",
        help="verify operator-directed single-node chain+Hub bootstrap evidence",
        allow_abbrev=False,
    )
    verify_node_add_single_node_bootstrap_evidence_parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    verify_node_add_single_node_bootstrap_evidence_parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    verify_node_add_single_node_bootstrap_evidence_parser.add_argument("--operation-id")
    verify_node_add_single_node_bootstrap_evidence_parser.add_argument("--evidence", required=True)
    verify_node_add_single_node_bootstrap_evidence_parser.add_argument("--max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_bootstrap_evidence_parser.add_argument("--release-max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_bootstrap_evidence_parser.add_argument("--identity-max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_bootstrap_evidence_parser.add_argument("--identity-release-max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_bootstrap_evidence_parser.add_argument("--add-do-max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_bootstrap_evidence_parser.add_argument("--add-do-release-max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_bootstrap_evidence_parser.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_bootstrap_evidence_parser.add_argument("--baseline-max-age-seconds", type=int, default=86400)

    adopt_node_add_single_node_bootstrap_live_proof_parser = subparsers.add_parser(
        "adopt-add-node-single-node-bootstrap-live-proof",
        help="write clean single-node bootstrap evidence from an already-healthy service without PATCH/deploy",
        allow_abbrev=False,
    )
    adopt_node_add_single_node_bootstrap_live_proof_parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    adopt_node_add_single_node_bootstrap_live_proof_parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    adopt_node_add_single_node_bootstrap_live_proof_parser.add_argument("--operation-id")
    adopt_node_add_single_node_bootstrap_live_proof_parser.add_argument("--evidence", required=True)
    adopt_node_add_single_node_bootstrap_live_proof_parser.add_argument("--max-age-seconds", type=int, default=86400)
    adopt_node_add_single_node_bootstrap_live_proof_parser.add_argument("--release-max-age-seconds", type=int, default=86400)
    adopt_node_add_single_node_bootstrap_live_proof_parser.add_argument("--identity-max-age-seconds", type=int, default=86400)
    adopt_node_add_single_node_bootstrap_live_proof_parser.add_argument("--identity-release-max-age-seconds", type=int, default=86400)
    adopt_node_add_single_node_bootstrap_live_proof_parser.add_argument("--add-do-max-age-seconds", type=int, default=86400)
    adopt_node_add_single_node_bootstrap_live_proof_parser.add_argument("--add-do-release-max-age-seconds", type=int, default=86400)
    adopt_node_add_single_node_bootstrap_live_proof_parser.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    adopt_node_add_single_node_bootstrap_live_proof_parser.add_argument("--baseline-max-age-seconds", type=int, default=86400)
    adopt_node_add_single_node_bootstrap_live_proof_parser.add_argument("--timeout", type=float, default=30.0)
    adopt_node_add_single_node_bootstrap_live_proof_parser.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)

    verify_node_add_single_node_chain_and_hub_proof_parser = subparsers.add_parser(
        "verify-add-node-single-node-chain-and-hub-proof-evidence",
        help="verify the read-only final single-node add proof/topology evidence",
        allow_abbrev=False,
    )
    verify_node_add_single_node_chain_and_hub_proof_parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    verify_node_add_single_node_chain_and_hub_proof_parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    verify_node_add_single_node_chain_and_hub_proof_parser.add_argument("--operation-id")
    verify_node_add_single_node_chain_and_hub_proof_parser.add_argument("--evidence", required=True)
    verify_node_add_single_node_chain_and_hub_proof_parser.add_argument("--max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_chain_and_hub_proof_parser.add_argument("--bootstrap-max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_chain_and_hub_proof_parser.add_argument("--release-max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_chain_and_hub_proof_parser.add_argument("--identity-max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_chain_and_hub_proof_parser.add_argument("--identity-release-max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_chain_and_hub_proof_parser.add_argument("--add-do-max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_chain_and_hub_proof_parser.add_argument("--add-do-release-max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_chain_and_hub_proof_parser.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_node_add_single_node_chain_and_hub_proof_parser.add_argument("--baseline-max-age-seconds", type=int, default=86400)



    detect_mother_topology_staleness_parser = subparsers.add_parser(
        "detect-mother-topology-staleness",
        help="read-only check that Mother topology evidence still matches live Coolify services",
        allow_abbrev=False,
    )
    detect_mother_topology_staleness_parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    detect_mother_topology_staleness_parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    detect_mother_topology_staleness_parser.add_argument("--operation-id")
    detect_mother_topology_staleness_parser.add_argument("--topology-evidence", required=True)
    detect_mother_topology_staleness_parser.add_argument("--acknowledge-topology-evidence-sha256", required=True)
    detect_mother_topology_staleness_parser.add_argument("--max-age-seconds", type=int, default=86400)
    detect_mother_topology_staleness_parser.add_argument("--timeout", type=float, default=30.0)
    detect_mother_topology_staleness_parser.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)

    adopt_empty_current_topology_parser = subparsers.add_parser(
        "adopt-empty-current-topology",
        help="write read-only empty-current-topology evidence after stale Mother topology is proven absent live",
        allow_abbrev=False,
    )
    adopt_empty_current_topology_parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    adopt_empty_current_topology_parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    adopt_empty_current_topology_parser.add_argument("--operation-id")
    adopt_empty_current_topology_parser.add_argument("--topology-evidence", required=True)
    adopt_empty_current_topology_parser.add_argument("--acknowledge-topology-evidence-sha256", required=True)
    adopt_empty_current_topology_parser.add_argument("--actual-node", action="append", default=[], help="operator-declared live node; currently unsupported except no values")
    adopt_empty_current_topology_parser.add_argument("--max-age-seconds", type=int, default=86400)
    adopt_empty_current_topology_parser.add_argument("--timeout", type=float, default=30.0)
    adopt_empty_current_topology_parser.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    adopt_empty_current_topology_parser.add_argument("--write-evidence", action="store_true")

    verify_empty_current_topology_parser = subparsers.add_parser(
        "verify-empty-current-topology-evidence",
        help="verify read-only empty-current-topology rectification evidence",
        allow_abbrev=False,
    )
    verify_empty_current_topology_parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    verify_empty_current_topology_parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    verify_empty_current_topology_parser.add_argument("--operation-id")
    verify_empty_current_topology_parser.add_argument("--evidence", required=True)
    verify_empty_current_topology_parser.add_argument("--max-age-seconds", type=int, default=86400)

    release_node_add_replica_sync = subparsers.add_parser(
        "release-add-node-replica-sync",
        help="mint an explicit expiring release for generic Mother add-node replica synchronization",
        allow_abbrev=False,
    )
    release_node_add_replica_sync.add_argument("--network", default="mainnet", choices=["mainnet"])
    release_node_add_replica_sync.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    release_node_add_replica_sync.add_argument("--operation-id")
    release_node_add_replica_sync.add_argument("--identity-evidence", required=True)
    release_node_add_replica_sync.add_argument("--acknowledge-add-node-identity-evidence-sha256", required=True)
    release_node_add_replica_sync.add_argument("--max-age-seconds", type=int, default=86400)
    release_node_add_replica_sync.add_argument("--identity-release-max-age-seconds", type=int, default=86400)
    release_node_add_replica_sync.add_argument("--add-do-max-age-seconds", type=int, default=86400)
    release_node_add_replica_sync.add_argument("--add-do-release-max-age-seconds", type=int, default=86400)
    release_node_add_replica_sync.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    release_node_add_replica_sync.add_argument("--baseline-max-age-seconds", type=int, default=86400)
    release_node_add_replica_sync.add_argument("--expires-in-seconds", type=int, default=300)
    release_node_add_replica_sync.add_argument("--created-at")
    release_node_add_replica_sync.add_argument("--write-release", action="store_true")

    verify_node_add_replica_sync_release_parser = subparsers.add_parser(
        "verify-add-node-replica-sync-release",
        help="verify a generic Mother add-node replica-sync release before live sync",
        allow_abbrev=False,
    )
    verify_node_add_replica_sync_release_parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    verify_node_add_replica_sync_release_parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    verify_node_add_replica_sync_release_parser.add_argument("--operation-id")
    verify_node_add_replica_sync_release_parser.add_argument("--release", required=True)
    verify_node_add_replica_sync_release_parser.add_argument("--max-age-seconds", type=int, default=900)
    verify_node_add_replica_sync_release_parser.add_argument("--identity-max-age-seconds", type=int, default=86400)
    verify_node_add_replica_sync_release_parser.add_argument("--identity-release-max-age-seconds", type=int, default=86400)
    verify_node_add_replica_sync_release_parser.add_argument("--add-do-max-age-seconds", type=int, default=86400)
    verify_node_add_replica_sync_release_parser.add_argument("--add-do-release-max-age-seconds", type=int, default=86400)
    verify_node_add_replica_sync_release_parser.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_node_add_replica_sync_release_parser.add_argument("--baseline-max-age-seconds", type=int, default=86400)

    verify_node_add_replica_sync_evidence_parser = subparsers.add_parser(
        "verify-add-node-replica-sync-evidence",
        help="verify generic Mother add-node replica-sync evidence after synchronization",
        allow_abbrev=False,
    )
    verify_node_add_replica_sync_evidence_parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    verify_node_add_replica_sync_evidence_parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    verify_node_add_replica_sync_evidence_parser.add_argument("--operation-id")
    verify_node_add_replica_sync_evidence_parser.add_argument("--evidence", required=True)
    verify_node_add_replica_sync_evidence_parser.add_argument("--max-age-seconds", type=int, default=86400)
    verify_node_add_replica_sync_evidence_parser.add_argument("--release-max-age-seconds", type=int, default=86400)
    verify_node_add_replica_sync_evidence_parser.add_argument("--identity-max-age-seconds", type=int, default=86400)
    verify_node_add_replica_sync_evidence_parser.add_argument("--identity-release-max-age-seconds", type=int, default=86400)
    verify_node_add_replica_sync_evidence_parser.add_argument("--add-do-max-age-seconds", type=int, default=86400)
    verify_node_add_replica_sync_evidence_parser.add_argument("--add-do-release-max-age-seconds", type=int, default=86400)
    verify_node_add_replica_sync_evidence_parser.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_node_add_replica_sync_evidence_parser.add_argument("--baseline-max-age-seconds", type=int, default=86400)


    release_node_add_validator_admission = subparsers.add_parser(
        "release-add-node-validator-admission",
        help="mint an explicit expiring release for generic add-node validator admission",
        allow_abbrev=False,
    )
    release_node_add_validator_admission.add_argument("--network", default="mainnet", choices=["mainnet"])
    release_node_add_validator_admission.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    release_node_add_validator_admission.add_argument("--operation-id")
    release_node_add_validator_admission.add_argument("--replica-sync-evidence", required=True)
    release_node_add_validator_admission.add_argument("--acknowledge-add-node-replica-sync-evidence-sha256", required=True)
    release_node_add_validator_admission.add_argument("--replica-sync-max-age-seconds", type=int, default=86400)
    release_node_add_validator_admission.add_argument("--replica-sync-release-max-age-seconds", type=int, default=86400)
    release_node_add_validator_admission.add_argument("--identity-max-age-seconds", type=int, default=86400)
    release_node_add_validator_admission.add_argument("--identity-release-max-age-seconds", type=int, default=86400)
    release_node_add_validator_admission.add_argument("--add-do-max-age-seconds", type=int, default=86400)
    release_node_add_validator_admission.add_argument("--add-do-release-max-age-seconds", type=int, default=86400)
    release_node_add_validator_admission.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    release_node_add_validator_admission.add_argument("--baseline-max-age-seconds", type=int, default=86400)
    release_node_add_validator_admission.add_argument("--expires-in-seconds", type=int, default=300)
    release_node_add_validator_admission.add_argument("--created-at")
    release_node_add_validator_admission.add_argument("--write-release", action="store_true")

    verify_node_add_validator_admission_release_parser = subparsers.add_parser(
        "verify-add-node-validator-admission-release",
        help="verify a generic add-node validator-admission release before live votes",
        allow_abbrev=False,
    )
    verify_node_add_validator_admission_release_parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    verify_node_add_validator_admission_release_parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    verify_node_add_validator_admission_release_parser.add_argument("--operation-id")
    verify_node_add_validator_admission_release_parser.add_argument("--release", required=True)
    verify_node_add_validator_admission_release_parser.add_argument("--max-age-seconds", type=int, default=900)
    verify_node_add_validator_admission_release_parser.add_argument("--replica-sync-max-age-seconds", type=int, default=86400)
    verify_node_add_validator_admission_release_parser.add_argument("--replica-sync-release-max-age-seconds", type=int, default=86400)
    verify_node_add_validator_admission_release_parser.add_argument("--identity-max-age-seconds", type=int, default=86400)
    verify_node_add_validator_admission_release_parser.add_argument("--identity-release-max-age-seconds", type=int, default=86400)
    verify_node_add_validator_admission_release_parser.add_argument("--add-do-max-age-seconds", type=int, default=86400)
    verify_node_add_validator_admission_release_parser.add_argument("--add-do-release-max-age-seconds", type=int, default=86400)
    verify_node_add_validator_admission_release_parser.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_node_add_validator_admission_release_parser.add_argument("--baseline-max-age-seconds", type=int, default=86400)

    verify_node_add_validator_admission_evidence_parser = subparsers.add_parser(
        "verify-add-node-validator-admission-evidence",
        help="verify generic add-node validator-admission evidence after live votes",
        allow_abbrev=False,
    )
    verify_node_add_validator_admission_evidence_parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    verify_node_add_validator_admission_evidence_parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    verify_node_add_validator_admission_evidence_parser.add_argument("--operation-id")
    verify_node_add_validator_admission_evidence_parser.add_argument("--evidence", required=True)
    verify_node_add_validator_admission_evidence_parser.add_argument("--max-age-seconds", type=int, default=86400)
    verify_node_add_validator_admission_evidence_parser.add_argument("--release-max-age-seconds", type=int, default=86400)
    verify_node_add_validator_admission_evidence_parser.add_argument("--replica-sync-max-age-seconds", type=int, default=86400)
    verify_node_add_validator_admission_evidence_parser.add_argument("--replica-sync-release-max-age-seconds", type=int, default=86400)
    verify_node_add_validator_admission_evidence_parser.add_argument("--identity-max-age-seconds", type=int, default=86400)
    verify_node_add_validator_admission_evidence_parser.add_argument("--identity-release-max-age-seconds", type=int, default=86400)
    verify_node_add_validator_admission_evidence_parser.add_argument("--add-do-max-age-seconds", type=int, default=86400)
    verify_node_add_validator_admission_evidence_parser.add_argument("--add-do-release-max-age-seconds", type=int, default=86400)
    verify_node_add_validator_admission_evidence_parser.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_node_add_validator_admission_evidence_parser.add_argument("--baseline-max-age-seconds", type=int, default=86400)

    release_node_add_rollback = subparsers.add_parser(
        "release-add-node-rollback",
        help="mint an explicit expiring release for failed pre-admission add-node rollback",
        allow_abbrev=False,
    )
    release_node_add_rollback.add_argument("--network", default="mainnet", choices=["mainnet"])
    release_node_add_rollback.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    release_node_add_rollback.add_argument("--operation-id")
    release_node_add_rollback.add_argument("--failed-evidence", required=True)
    release_node_add_rollback.add_argument("--acknowledge-failed-evidence-sha256", required=True)
    release_node_add_rollback.add_argument("--max-age-seconds", type=int, default=86400)
    release_node_add_rollback.add_argument("--expires-in-seconds", type=int, default=300)
    release_node_add_rollback.add_argument("--created-at")
    release_node_add_rollback.add_argument("--write-release", action="store_true")

    verify_node_add_rollback_release_parser = subparsers.add_parser(
        "verify-add-node-rollback-release",
        help="verify a generic Mother add-node rollback release before live service deletion",
        allow_abbrev=False,
    )
    verify_node_add_rollback_release_parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    verify_node_add_rollback_release_parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    verify_node_add_rollback_release_parser.add_argument("--operation-id")
    verify_node_add_rollback_release_parser.add_argument("--release", required=True)
    verify_node_add_rollback_release_parser.add_argument("--max-age-seconds", type=int, default=900)
    verify_node_add_rollback_release_parser.add_argument("--failed-evidence-max-age-seconds", type=int, default=86400)

    verify_node_add_rollback_evidence_parser = subparsers.add_parser(
        "verify-add-node-rollback-evidence",
        help="verify generic Mother add-node rollback evidence after service deletion",
        allow_abbrev=False,
    )
    verify_node_add_rollback_evidence_parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    verify_node_add_rollback_evidence_parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    verify_node_add_rollback_evidence_parser.add_argument("--operation-id")
    verify_node_add_rollback_evidence_parser.add_argument("--evidence", required=True)
    verify_node_add_rollback_evidence_parser.add_argument("--max-age-seconds", type=int, default=86400)
    verify_node_add_rollback_evidence_parser.add_argument("--release-max-age-seconds", type=int, default=86400)
    verify_node_add_rollback_evidence_parser.add_argument("--failed-evidence-max-age-seconds", type=int, default=86400)

    remove_node = subparsers.add_parser(
        "remove-node",
        help="documented Mother node-removal surface; prep is local-only, do requires an explicit release",
        allow_abbrev=False,
    )
    remove_node_subparsers = remove_node.add_subparsers(dest="remove_node_phase", required=True)
    remove_node_prep = remove_node_subparsers.add_parser(
        "prep",
        help="prepare an explicit node-removal transaction from canonical baseline evidence",
        allow_abbrev=False,
    )
    remove_node_prep.add_argument("network", choices=["mainnet"])
    remove_node_prep.add_argument("--node", required=True, help="explicit node name to remove; never inferred from host or ordinal")
    remove_node_prep.add_argument("--mode", default="soft", choices=["soft"])
    remove_node_prep.add_argument("--baseline-evidence", required=True)
    remove_node_prep.add_argument("--baseline-evidence-sha256", required=True)
    remove_node_prep.add_argument("--baseline-max-age-seconds", type=int, default=86400)
    remove_node_prep.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    remove_node_prep.add_argument("--operation-id")
    remove_node_prep.add_argument("--created-at")
    remove_node_prep.add_argument("--write-transaction", action="store_true")

    remove_node_do = remove_node_subparsers.add_parser(
        "do",
        help="execute a released Mother node-removal plan; mutates only after release acknowledgement",
        allow_abbrev=False,
    )
    remove_node_do.add_argument("network", choices=["mainnet"])
    remove_node_do.add_argument("--release", required=True)
    remove_node_do.add_argument("--acknowledge-release-sha256", required=True)
    remove_node_do.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    remove_node_do.add_argument("--operation-id")
    remove_node_do.add_argument("--max-age-seconds", type=int, default=300)
    remove_node_do.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    remove_node_do.add_argument("--baseline-max-age-seconds", type=int, default=86400)
    remove_node_do.add_argument("--timeout", type=float, default=30.0)
    remove_node_do.add_argument("--max-wait-seconds", type=float, default=300.0)
    remove_node_do.add_argument("--poll-interval-seconds", type=float, default=5.0)
    remove_node_do.add_argument("--allow-missing-service", action="store_true")
    remove_node_do.add_argument("--execute", action="store_true", help="required to perform live mutation")

    remove_node_finalize = remove_node_subparsers.add_parser(
        "finalize",
        help="finalize a completed Mother node-removal do evidence artifact with read-only checks",
        allow_abbrev=False,
    )
    remove_node_finalize.add_argument("network", choices=["mainnet"])
    remove_node_finalize.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    remove_node_finalize.add_argument("--operation-id")
    remove_node_finalize.add_argument("--do-evidence", required=True)
    remove_node_finalize.add_argument("--max-age-seconds", type=int, default=86400)
    remove_node_finalize.add_argument("--timeout", type=float, default=30.0)
    remove_node_finalize.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    remove_node_finalize.add_argument("--write-evidence", action="store_true")

    verify_node_remove_prep = subparsers.add_parser(
        "verify-remove-node-prep-transaction",
        help="verify a prepared Mother node-removal transaction without live mutation",
        allow_abbrev=False,
    )
    verify_node_remove_prep.add_argument("--network", default="mainnet", choices=["mainnet"])
    verify_node_remove_prep.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    verify_node_remove_prep.add_argument("--operation-id")
    verify_node_remove_prep.add_argument("--transaction", required=True)
    verify_node_remove_prep.add_argument("--max-age-seconds", type=int, default=86400)
    verify_node_remove_prep.add_argument("--baseline-max-age-seconds", type=int, default=86400)

    release_node_remove_do = subparsers.add_parser(
        "release-remove-node-do",
        help="mint an explicit expiring release for a verified Mother remove-node prep transaction",
        allow_abbrev=False,
    )
    release_node_remove_do.add_argument("--network", default="mainnet", choices=["mainnet"])
    release_node_remove_do.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    release_node_remove_do.add_argument("--operation-id")
    release_node_remove_do.add_argument("--transaction", required=True)
    release_node_remove_do.add_argument("--acknowledge-node-remove-prep-transaction-sha256", required=True)
    release_node_remove_do.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    release_node_remove_do.add_argument("--baseline-max-age-seconds", type=int, default=86400)
    release_node_remove_do.add_argument("--expires-in-seconds", type=int, default=300)
    release_node_remove_do.add_argument("--created-at")
    release_node_remove_do.add_argument("--write-release", action="store_true")

    verify_node_remove_do_release = subparsers.add_parser(
        "verify-remove-node-do-release",
        help="verify a Mother remove-node do release without live mutation",
        allow_abbrev=False,
    )
    verify_node_remove_do_release.add_argument("--network", default="mainnet", choices=["mainnet"])
    verify_node_remove_do_release.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    verify_node_remove_do_release.add_argument("--operation-id")
    verify_node_remove_do_release.add_argument("--release", required=True)
    verify_node_remove_do_release.add_argument("--max-age-seconds", type=int, default=300)
    verify_node_remove_do_release.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_node_remove_do_release.add_argument("--baseline-max-age-seconds", type=int, default=86400)

    verify_node_remove_do_evidence = subparsers.add_parser(
        "verify-remove-node-do-evidence",
        help="verify persisted Mother remove-node do evidence",
        allow_abbrev=False,
    )
    verify_node_remove_do_evidence.add_argument("--network", default="mainnet", choices=["mainnet"])
    verify_node_remove_do_evidence.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    verify_node_remove_do_evidence.add_argument("--operation-id")
    verify_node_remove_do_evidence.add_argument("--evidence", required=True)
    verify_node_remove_do_evidence.add_argument("--max-age-seconds", type=int, default=86400)
    verify_node_remove_do_evidence.add_argument("--release-max-age-seconds", type=int, default=86400)
    verify_node_remove_do_evidence.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_node_remove_do_evidence.add_argument("--baseline-max-age-seconds", type=int, default=86400)

    verify_node_remove_finalize = subparsers.add_parser(
        "verify-remove-node-finalize-evidence",
        help="verify Mother node-removal finalize evidence",
        allow_abbrev=False,
    )
    verify_node_remove_finalize.add_argument("--network", default="mainnet", choices=["mainnet"])
    verify_node_remove_finalize.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    verify_node_remove_finalize.add_argument("--operation-id")
    verify_node_remove_finalize.add_argument("--evidence", required=True)
    verify_node_remove_finalize.add_argument("--max-age-seconds", type=int, default=86400)
    verify_node_remove_finalize.add_argument("--do-max-age-seconds", type=int, default=86400)

    plan = subparsers.add_parser(
        "plan",
        help="build the starter add-node sequence without network access or mutation",
        allow_abbrev=False,
    )
    _common(plan)
    plan.add_argument(
        "--require-ready",
        action="store_true",
        help="return status 1 when the plan reports any execution blocker",
    )

    preflight = subparsers.add_parser(
        "preflight",
        help="verify live Coolify bindings and target absence using authenticated GET requests only",
        allow_abbrev=False,
    )
    _common(preflight)
    preflight.add_argument("--timeout", type=float, default=30.0)
    preflight.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    preflight.add_argument("--max-items", type=int, default=1000)
    preflight.add_argument(
        "--write-evidence",
        action="store_true",
        help="persist the canonical secret-free preflight report beneath Mother evidence",
    )
    preflight.add_argument(
        "--require-clean",
        action="store_true",
        help="return status 1 when live preflight reports any blocker",
    )
    verify = subparsers.add_parser(
        "verify-preflight",
        help="verify a persisted preflight against the current Mother generation and freshness window",
        allow_abbrev=False,
    )
    _common(verify)
    verify.add_argument("--evidence", required=True)
    verify.add_argument("--max-age-seconds", type=int, default=300)
    verify.add_argument(
        "--require-clean",
        action="store_true",
        help="return status 1 instead of status 2 when evidence verification fails",
    )

    prepare = subparsers.add_parser(
        "prepare-execution",
        help="bind the current plan and fresh preflight into a non-executing immutable request",
        allow_abbrev=False,
    )
    _common(prepare)
    prepare.add_argument("--evidence", required=True)
    prepare.add_argument("--max-age-seconds", type=int, default=300)
    prepare.add_argument("--created-at")
    prepare.add_argument(
        "--write-request",
        action="store_true",
        help="persist the canonical request beneath Mother actions",
    )

    verify_execution = subparsers.add_parser(
        "verify-execution",
        help="verify an immutable execution request and its still-fresh preflight evidence",
        allow_abbrev=False,
    )
    _common(verify_execution)
    verify_execution.add_argument("--request", required=True)
    verify_execution.add_argument("--max-age-seconds", type=int, default=300)

    stage = subparsers.add_parser(
        "stage-mutation",
        help="materialize the canonical non-executing Coolify write-set transaction",
        allow_abbrev=False,
    )
    _common(stage)
    stage.add_argument("--request", required=True)
    stage.add_argument("--max-age-seconds", type=int, default=300)
    stage.add_argument("--created-at")
    stage.add_argument(
        "--write-transaction",
        action="store_true",
        help="persist the canonical transaction beneath Mother actions",
    )

    verify_transaction = subparsers.add_parser(
        "verify-mutation",
        help="verify a staged mutation transaction against its request and fresh evidence",
        allow_abbrev=False,
    )
    _common(verify_transaction)
    verify_transaction.add_argument("--transaction", required=True)
    verify_transaction.add_argument("--max-age-seconds", type=int, default=300)

    release = subparsers.add_parser(
        "release-mutation",
        help="record an explicit, expiring operator release for one exact staged transaction",
        allow_abbrev=False,
    )
    _common(release)
    release.add_argument("--transaction", required=True)
    release.add_argument("--acknowledge-transaction-sha256", required=True)
    release.add_argument("--max-age-seconds", type=int, default=300)
    release.add_argument("--expires-in-seconds", type=int, default=300)
    release.add_argument("--created-at")
    release.add_argument(
        "--write-release",
        action="store_true",
        help="persist the canonical release beneath Mother actions",
    )

    verify_release = subparsers.add_parser(
        "verify-release",
        help="verify an expiring operator release against its exact transaction",
        allow_abbrev=False,
    )
    _common(verify_release)
    verify_release.add_argument("--release", required=True)
    verify_release.add_argument("--max-age-seconds", type=int, default=300)

    apply_mutation = subparsers.add_parser(
        "apply-mutation",
        help="consume one exact released transaction and execute its bounded Coolify POST set",
        allow_abbrev=False,
    )
    _common(apply_mutation)
    apply_mutation.add_argument("--release", required=True)
    apply_mutation.add_argument("--acknowledge-release-sha256", required=True)
    apply_mutation.add_argument("--max-age-seconds", type=int, default=300)
    apply_mutation.add_argument("--timeout", type=float, default=30.0)
    apply_mutation.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    apply_mutation.add_argument(
        "--execute",
        action="store_true",
        help="perform the released GET/POST sequence; without this flag the command is dry-run only",
    )

    rollback_mutation = subparsers.add_parser(
        "rollback-mutation",
        help="rollback the first standby-service mutation before any later deployment phase",
        allow_abbrev=False,
    )
    _common(rollback_mutation)
    rollback_mutation.add_argument("--execution", required=True)
    rollback_mutation.add_argument("--acknowledge-execution-sha256", required=True)
    rollback_mutation.add_argument("--timeout", type=float, default=30.0)
    rollback_mutation.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    rollback_mutation.add_argument(
        "--execute",
        action="store_true",
        help="perform exact GET/DELETE/GET rollback; without this flag the command is dry-run only",
    )

    recover_mutation_rollback = subparsers.add_parser(
        "recover-mutation-rollback",
        help="recover and rollback an interrupted first-step execution from its durable journal",
        allow_abbrev=False,
    )
    _common(recover_mutation_rollback)
    recover_mutation_rollback.add_argument("--journal", required=True)
    recover_mutation_rollback.add_argument("--acknowledge-journal-sha256", required=True)
    recover_mutation_rollback.add_argument("--timeout", type=float, default=30.0)
    recover_mutation_rollback.add_argument(
        "--max-response-bytes",
        type=int,
        default=4 * 1024 * 1024,
    )
    recover_mutation_rollback.add_argument(
        "--execute",
        action="store_true",
        help="perform exact recovery GET/DELETE/GET; without this flag the command is dry-run only",
    )

    verify_mutation_rollback = subparsers.add_parser(
        "verify-mutation-rollback",
        help="re-observe the exact created UUIDs and prove the first deployment step is absent",
        allow_abbrev=False,
    )
    _common(verify_mutation_rollback)
    verify_mutation_rollback.add_argument("--rollback-result", required=True)
    verify_mutation_rollback.add_argument("--timeout", type=float, default=30.0)
    verify_mutation_rollback.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)

    verify_standby = subparsers.add_parser(
        "verify-standby",
        help="GET-verify the exact environment and service UUIDs from a successful execution result",
        allow_abbrev=False,
    )
    _common(verify_standby)
    verify_standby.add_argument("--execution", required=True)
    verify_standby.add_argument("--timeout", type=float, default=30.0)
    verify_standby.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    verify_standby.add_argument("--observed-at")
    verify_standby.add_argument(
        "--write-evidence",
        action="store_true",
        help="persist canonical standby verification beneath Mother evidence",
    )
    verify_standby.add_argument(
        "--require-clean",
        action="store_true",
        help="return status 1 when the live standby verification reports any blocker",
    )

    verify_standby_evidence = subparsers.add_parser(
        "verify-standby-evidence",
        help="verify persisted standby evidence against the current Mother generation and freshness",
        allow_abbrev=False,
    )
    _common(verify_standby_evidence)
    verify_standby_evidence.add_argument("--evidence", required=True)
    verify_standby_evidence.add_argument("--max-age-seconds", type=int, default=300)

    stage_identity = subparsers.add_parser(
        "stage-identity",
        help="stage exact reserved-identity service-env writes without persisting secret values",
        allow_abbrev=False,
    )
    _common(stage_identity)
    stage_identity.add_argument("--standby-evidence", required=True)
    stage_identity.add_argument("--max-age-seconds", type=int, default=300)
    stage_identity.add_argument("--created-at")
    stage_identity.add_argument(
        "--write-transaction",
        action="store_true",
        help="persist the canonical secret-safe identity transaction beneath Mother actions",
    )

    verify_identity = subparsers.add_parser(
        "verify-identity-transaction",
        help="verify a staged reserved-identity transaction against current Mother state and standby evidence",
        allow_abbrev=False,
    )
    _common(verify_identity)
    verify_identity.add_argument("--transaction", required=True)
    verify_identity.add_argument("--max-age-seconds", type=int, default=300)

    release_identity = subparsers.add_parser(
        "release-identity",
        help="record an explicit expiring operator release for one exact identity transaction",
        allow_abbrev=False,
    )
    _common(release_identity)
    release_identity.add_argument("--transaction", required=True)
    release_identity.add_argument("--acknowledge-identity-transaction-sha256", required=True)
    release_identity.add_argument("--max-age-seconds", type=int, default=300)
    release_identity.add_argument("--expires-in-seconds", type=int, default=300)
    release_identity.add_argument("--created-at")
    release_identity.add_argument(
        "--write-release",
        action="store_true",
        help="persist the canonical identity release beneath Mother actions",
    )

    verify_identity_release = subparsers.add_parser(
        "verify-identity-release",
        help="verify an expiring identity release against its exact transaction",
        allow_abbrev=False,
    )
    _common(verify_identity_release)
    verify_identity_release.add_argument("--release", required=True)
    verify_identity_release.add_argument("--max-age-seconds", type=int, default=300)

    apply_identity = subparsers.add_parser(
        "apply-identity",
        help="consume one exact identity release and install the reserved service environment variables",
        allow_abbrev=False,
    )
    _common(apply_identity)
    apply_identity.add_argument("--release", required=True)
    apply_identity.add_argument("--acknowledge-release-sha256", required=True)
    apply_identity.add_argument("--max-age-seconds", type=int, default=300)
    apply_identity.add_argument("--timeout", type=float, default=30.0)
    apply_identity.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    apply_identity.add_argument(
        "--execute",
        action="store_true",
        help="perform the released GET/POST sequence; without this flag the command is dry-run only",
    )

    rollback_identity = subparsers.add_parser(
        "rollback-identity",
        help="inspect or execute rollback of one non-finalized identity installation",
        allow_abbrev=False,
    )
    _common(rollback_identity)
    rollback_identity.add_argument("--execution", required=True)
    rollback_identity.add_argument("--acknowledge-execution-sha256", required=True)
    rollback_identity.add_argument("--timeout", type=float, default=30.0)
    rollback_identity.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    rollback_identity.add_argument(
        "--execute",
        action="store_true",
        help="delete the exact created environment-variable UUIDs; otherwise inspect only",
    )

    recover_identity_rollback = subparsers.add_parser(
        "recover-identity-rollback",
        help="inspect or execute crash recovery from a durable identity rollback journal",
        allow_abbrev=False,
    )
    _common(recover_identity_rollback)
    recover_identity_rollback.add_argument("--journal", required=True)
    recover_identity_rollback.add_argument("--acknowledge-journal-sha256", required=True)
    recover_identity_rollback.add_argument("--timeout", type=float, default=30.0)
    recover_identity_rollback.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    recover_identity_rollback.add_argument(
        "--execute",
        action="store_true",
        help="recover exact created variables and execute their inverse DELETE operations",
    )

    verify_identity_rollback = subparsers.add_parser(
        "verify-identity-rollback",
        help="independently prove that an identity rollback restored all targeted keys to absence",
        allow_abbrev=False,
    )
    _common(verify_identity_rollback)
    verify_identity_rollback.add_argument("--rollback-result", required=True)
    verify_identity_rollback.add_argument("--timeout", type=float, default=30.0)
    verify_identity_rollback.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    verify_identity_rollback.add_argument("--observed-at")
    verify_identity_rollback.add_argument(
        "--write-evidence",
        action="store_true",
        help="persist rollback-cycle evidence required by the later genesis stage",
    )

    stage_genesis = subparsers.add_parser(
        "stage-genesis",
        help="compile one Mother-owned first genesis and the later soft-admission specification",
        allow_abbrev=False,
    )
    _common(stage_genesis)
    stage_genesis.add_argument("--identity-execution", required=True)
    stage_genesis.add_argument(
        "--identity-rollback-verification",
        required=True,
        help="proof that the same identity profile was applied, rolled back, verified absent, and then reapplied",
    )
    stage_genesis.add_argument("--created-at")
    stage_genesis.add_argument(
        "--write-transaction",
        action="store_true",
        help="persist the canonical secret-free genesis transaction beneath Mother actions",
    )

    verify_genesis = subparsers.add_parser(
        "verify-genesis-transaction",
        help="verify a staged first-genesis transaction against current Mother state and identity execution",
        allow_abbrev=False,
    )
    _common(verify_genesis)
    verify_genesis.add_argument("--transaction", required=True)

    release_genesis = subparsers.add_parser(
        "release-genesis",
        help="record an explicit expiring release for the exact A-side first-genesis deployment",
        allow_abbrev=False,
    )
    _common(release_genesis)
    release_genesis.add_argument("--transaction", required=True)
    release_genesis.add_argument("--acknowledge-genesis-transaction-sha256", required=True)
    release_genesis.add_argument(
        "--hub-git-repository",
        default=DEFAULT_HUB_GIT_REPOSITORY,
        help=(
            "credential-free HTTPS Git repository used to build the co-located Hub "
            f"(default: {DEFAULT_HUB_GIT_REPOSITORY})"
        ),
    )
    hub_git_source = release_genesis.add_mutually_exclusive_group()
    hub_git_source.add_argument(
        "--hub-git-ref",
        default=DEFAULT_HUB_GIT_REF,
        help=(
            "branch, tag, or lowercase commit ref used for the Hub build "
            f"(default: {DEFAULT_HUB_GIT_REF})"
        ),
    )
    hub_git_source.add_argument(
        "--hub-git-commit-sha",
        help=(
            "optional exact pushed lowercase 40-character commit; "
            "legacy pinning alias for --hub-git-ref"
        ),
    )
    release_genesis.add_argument("--expires-in-seconds", type=int, default=300)
    release_genesis.add_argument("--created-at")
    release_genesis.add_argument(
        "--write-release",
        action="store_true",
        help="persist the canonical first-genesis release beneath Mother actions",
    )

    verify_genesis_release = subparsers.add_parser(
        "verify-genesis-release",
        help="verify an expiring first-genesis release against its exact transaction",
        allow_abbrev=False,
    )
    _common(verify_genesis_release)
    verify_genesis_release.add_argument("--release", required=True)
    verify_genesis_release.add_argument("--max-age-seconds", type=int, default=300)

    apply_genesis = subparsers.add_parser(
        "apply-genesis",
        help="consume one exact first-genesis release, update only A, and request deployment",
        allow_abbrev=False,
    )
    _common(apply_genesis)
    apply_genesis.add_argument("--release", required=True)
    apply_genesis.add_argument("--acknowledge-release-sha256", required=True)
    apply_genesis.add_argument("--max-age-seconds", type=int, default=300)
    apply_genesis.add_argument("--timeout", type=float, default=30.0)
    apply_genesis.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    apply_genesis.add_argument(
        "--execute",
        action="store_true",
        help="perform the bounded A-side GET/PATCH/deploy sequence; otherwise inspect only",
    )

    rollback_genesis = subparsers.add_parser(
        "rollback-genesis",
        help="restore the exact stopped standby Compose for one successful first-genesis execution",
        allow_abbrev=False,
    )
    _common(rollback_genesis)
    rollback_genesis.add_argument("--execution", required=True)
    rollback_genesis.add_argument("--acknowledge-execution-sha256", required=True)
    rollback_genesis.add_argument("--timeout", type=float, default=30.0)
    rollback_genesis.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    rollback_genesis.add_argument("--max-wait-seconds", type=float, default=20.0)
    rollback_genesis.add_argument("--poll-interval-seconds", type=float, default=0.5)
    rollback_genesis.add_argument(
        "--execute",
        action="store_true",
        help="stop the exact service and restore its verified standby Compose; otherwise inspect only",
    )

    recover_genesis_rollback = subparsers.add_parser(
        "recover-genesis-rollback",
        help="recover an interrupted first-genesis mutation from its durable rollback journal",
        allow_abbrev=False,
    )
    _common(recover_genesis_rollback)
    recover_genesis_rollback.add_argument("--journal", required=True)
    recover_genesis_rollback.add_argument("--acknowledge-journal-sha256", required=True)
    recover_genesis_rollback.add_argument("--timeout", type=float, default=30.0)
    recover_genesis_rollback.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    recover_genesis_rollback.add_argument("--max-wait-seconds", type=float, default=20.0)
    recover_genesis_rollback.add_argument("--poll-interval-seconds", type=float, default=0.5)
    recover_genesis_rollback.add_argument("--execute", action="store_true")

    verify_genesis_rollback = subparsers.add_parser(
        "verify-genesis-rollback",
        help="independently verify stopped standby Compose and preserved identity keys",
        allow_abbrev=False,
    )
    _common(verify_genesis_rollback)
    verify_genesis_rollback.add_argument("--rollback-result", required=True)
    verify_genesis_rollback.add_argument("--timeout", type=float, default=30.0)
    verify_genesis_rollback.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    verify_genesis_rollback.add_argument("--observed-at")
    verify_genesis_rollback.add_argument("--write-evidence", action="store_true")

    release_birth = subparsers.add_parser(
        "release-genesis-birth",
        help="release an internal-only proof guardian for the exact successful first-genesis execution",
        allow_abbrev=False,
    )
    _common(release_birth)
    release_birth.add_argument("--execution", required=True)
    release_birth.add_argument("--acknowledge-genesis-execution-sha256", required=True)
    release_birth.add_argument(
        "--genesis-rollback-verification",
        required=True,
        help="proof that the same genesis was applied, rolled back, verified, and then reapplied",
    )
    release_birth.add_argument(
        "--superseded-service-uuid",
        help="exact older Coolify service UUID to remove before birth deployment",
    )
    release_birth.add_argument(
        "--acknowledge-superseded-service-removal",
        help="must equal REMOVE:<node>:<service_uuid> when superseded removal is requested",
    )
    release_birth.add_argument("--expires-in-seconds", type=int, default=300)
    release_birth.add_argument("--created-at")
    release_birth.add_argument("--write-release", action="store_true")

    verify_birth_release = subparsers.add_parser(
        "verify-genesis-birth-release",
        help="verify an expiring internal-only genesis-birth release",
        allow_abbrev=False,
    )
    _common(verify_birth_release)
    verify_birth_release.add_argument("--release", required=True)
    verify_birth_release.add_argument("--max-age-seconds", type=int, default=300)

    apply_birth = subparsers.add_parser(
        "apply-genesis-birth",
        help="install the internal proof guardian and prove A through Coolify without SSH",
        allow_abbrev=False,
    )
    _common(apply_birth)
    apply_birth.add_argument("--release", required=True)
    apply_birth.add_argument("--acknowledge-release-sha256", required=True)
    apply_birth.add_argument("--max-age-seconds", type=int, default=300)
    apply_birth.add_argument("--timeout", type=float, default=30.0)
    apply_birth.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    apply_birth.add_argument("--max-wait-seconds", type=float, default=180.0)
    apply_birth.add_argument("--poll-interval-seconds", type=float, default=5.0)
    apply_birth.add_argument("--execute", action="store_true")

    verify_birth = subparsers.add_parser(
        "verify-genesis-birth-evidence",
        help="verify persisted internal genesis-birth evidence",
        allow_abbrev=False,
    )
    _common(verify_birth)
    verify_birth.add_argument("--evidence", required=True)
    verify_birth.add_argument("--max-age-seconds", type=int, default=300)

    stage_replica = subparsers.add_parser(
        "stage-soft-replica",
        help="compile the exact C-side non-validator replica configuration without network access",
        allow_abbrev=False,
    )
    _common(stage_replica)
    stage_replica.add_argument("--birth-evidence", required=True)
    stage_replica.add_argument("--max-age-seconds", type=int, default=300)
    stage_replica.add_argument("--created-at")
    stage_replica.add_argument(
        "--write-transaction",
        action="store_true",
        help="persist the canonical soft-replica transaction beneath Mother actions",
    )

    verify_replica = subparsers.add_parser(
        "verify-soft-replica-transaction",
        help="verify a staged C-side replica configuration against fresh birth evidence",
        allow_abbrev=False,
    )
    _common(verify_replica)
    verify_replica.add_argument("--transaction", required=True)
    verify_replica.add_argument("--max-age-seconds", type=int, default=300)

    release_replica = subparsers.add_parser(
        "release-soft-replica",
        help="authorize one exact C-side soft-replica configuration for a short window",
        allow_abbrev=False,
    )
    _common(release_replica)
    release_replica.add_argument("--transaction", required=True)
    release_replica.add_argument("--acknowledge-soft-replica-transaction-sha256", required=True)
    release_replica.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    release_replica.add_argument("--expires-in-seconds", type=int, default=300)
    release_replica.add_argument("--created-at")
    release_replica.add_argument("--write-release", action="store_true")

    verify_replica_release = subparsers.add_parser(
        "verify-soft-replica-release",
        help="verify one expiring C-side soft-replica release",
        allow_abbrev=False,
    )
    _common(verify_replica_release)
    verify_replica_release.add_argument("--release", required=True)
    verify_replica_release.add_argument("--max-age-seconds", type=int, default=300)
    verify_replica_release.add_argument("--transaction-max-age-seconds", type=int, default=86400)

    apply_replica = subparsers.add_parser(
        "apply-soft-replica",
        help="inspect or consume one exact C-side soft-replica release",
        allow_abbrev=False,
    )
    _common(apply_replica)
    apply_replica.add_argument("--release", required=True)
    apply_replica.add_argument("--acknowledge-release-sha256", required=True)
    apply_replica.add_argument("--max-age-seconds", type=int, default=300)
    apply_replica.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    apply_replica.add_argument("--timeout", type=float, default=30.0)
    apply_replica.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    apply_replica.add_argument("--execute", action="store_true")

    release_replica_sync = subparsers.add_parser(
        "release-soft-replica-sync",
        help="release one internal-only C synchronization proof without validator admission",
        allow_abbrev=False,
    )
    _common(release_replica_sync)
    release_replica_sync.add_argument("--execution", required=True)
    release_replica_sync.add_argument("--acknowledge-soft-replica-execution-sha256", required=True)
    release_replica_sync.add_argument("--execution-max-age-seconds", type=int, default=86400)
    release_replica_sync.add_argument("--expires-in-seconds", type=int, default=300)
    release_replica_sync.add_argument("--created-at")
    release_replica_sync.add_argument("--write-release", action="store_true")

    verify_replica_sync_release = subparsers.add_parser(
        "verify-soft-replica-sync-release",
        help="verify one expiring internal-only C synchronization proof release",
        allow_abbrev=False,
    )
    _common(verify_replica_sync_release)
    verify_replica_sync_release.add_argument("--release", required=True)
    verify_replica_sync_release.add_argument("--max-age-seconds", type=int, default=300)
    verify_replica_sync_release.add_argument("--execution-max-age-seconds", type=int, default=86400)

    apply_replica_sync = subparsers.add_parser(
        "apply-soft-replica-sync",
        help="install C's internal synchronization guardian and prove synchronization without SSH",
        allow_abbrev=False,
    )
    _common(apply_replica_sync)
    apply_replica_sync.add_argument("--release", required=True)
    apply_replica_sync.add_argument("--acknowledge-release-sha256", required=True)
    apply_replica_sync.add_argument("--max-age-seconds", type=int, default=300)
    apply_replica_sync.add_argument("--execution-max-age-seconds", type=int, default=86400)
    apply_replica_sync.add_argument("--timeout", type=float, default=30.0)
    apply_replica_sync.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    apply_replica_sync.add_argument("--max-wait-seconds", type=float, default=240.0)
    apply_replica_sync.add_argument("--poll-interval-seconds", type=float, default=5.0)
    apply_replica_sync.add_argument("--execute", action="store_true")

    verify_replica_sync = subparsers.add_parser(
        "verify-soft-replica-sync-evidence",
        help="verify persisted internal C synchronization evidence",
        allow_abbrev=False,
    )
    _common(verify_replica_sync)
    verify_replica_sync.add_argument("--evidence", required=True)
    verify_replica_sync.add_argument("--max-age-seconds", type=int, default=300)

    stage_admission = subparsers.add_parser(
        "stage-validator-admission",
        help="compile one exact C validator-addition vote without casting it",
        allow_abbrev=False,
    )
    _common(stage_admission)
    stage_admission.add_argument("--sync-evidence", required=True)
    stage_admission.add_argument("--max-age-seconds", type=int, default=300)
    stage_admission.add_argument("--created-at")
    stage_admission.add_argument("--write-transaction", action="store_true")

    verify_admission = subparsers.add_parser(
        "verify-validator-admission-transaction",
        help="verify a staged validator-admission transaction",
        allow_abbrev=False,
    )
    _common(verify_admission)
    verify_admission.add_argument("--transaction", required=True)
    verify_admission.add_argument("--max-age-seconds", type=int, default=300)

    release_admission = subparsers.add_parser(
        "release-validator-admission",
        help="release one exact internal QBFT validator-addition vote",
        allow_abbrev=False,
    )
    _common(release_admission)
    release_admission.add_argument("--transaction", required=True)
    release_admission.add_argument("--acknowledge-validator-admission-transaction-sha256", required=True)
    release_admission.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    release_admission.add_argument("--expires-in-seconds", type=int, default=300)
    release_admission.add_argument("--created-at")
    release_admission.add_argument(
        "--failed-evidence",
        help="bind recovery to one exact failed post-mutation validator-admission evidence artifact",
    )
    release_admission.add_argument("--write-release", action="store_true")

    verify_admission_release = subparsers.add_parser(
        "verify-validator-admission-release",
        help="verify an expiring validator-admission release",
        allow_abbrev=False,
    )
    _common(verify_admission_release)
    verify_admission_release.add_argument("--release", required=True)
    verify_admission_release.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_admission_release.add_argument("--max-age-seconds", type=int, default=300)

    apply_admission = subparsers.add_parser(
        "apply-validator-admission",
        help="inspect or execute one released internal validator-admission vote",
        allow_abbrev=False,
    )
    _common(apply_admission)
    apply_admission.add_argument("--release", required=True)
    apply_admission.add_argument("--acknowledge-release-sha256", required=True)
    apply_admission.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    apply_admission.add_argument("--max-age-seconds", type=int, default=300)
    apply_admission.add_argument("--timeout", type=float, default=30.0)
    apply_admission.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    apply_admission.add_argument("--max-wait-seconds", type=float, default=300.0)
    apply_admission.add_argument("--poll-interval-seconds", type=float, default=5.0)
    apply_admission.add_argument("--execute", action="store_true")

    verify_admission_evidence = subparsers.add_parser(
        "verify-validator-admission-evidence",
        help="verify persisted validator-admission activation evidence",
        allow_abbrev=False,
    )
    _common(verify_admission_evidence)
    verify_admission_evidence.add_argument("--evidence", required=True)
    verify_admission_evidence.add_argument("--max-age-seconds", type=int, default=300)

    release_quorum = subparsers.add_parser(
        "release-validator-quorum-recovery",
        help="release one exact two-validator QBFT quorum reset without casting a vote",
        allow_abbrev=False,
    )
    _common(release_quorum)
    release_quorum.add_argument("--transaction", required=True)
    release_quorum.add_argument("--acknowledge-validator-admission-transaction-sha256", required=True)
    release_quorum.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    release_quorum.add_argument("--expires-in-seconds", type=int, default=300)
    release_quorum.add_argument("--created-at")
    release_quorum.add_argument("--write-release", action="store_true")

    verify_quorum_release = subparsers.add_parser(
        "verify-validator-quorum-recovery-release",
        help="verify an expiring two-validator quorum-recovery release",
        allow_abbrev=False,
    )
    _common(verify_quorum_release)
    verify_quorum_release.add_argument("--release", required=True)
    verify_quorum_release.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_quorum_release.add_argument("--max-age-seconds", type=int, default=300)

    apply_quorum = subparsers.add_parser(
        "apply-validator-quorum-recovery",
        help="inspect or execute the exact C-then-A QBFT quorum reset",
        allow_abbrev=False,
    )
    _common(apply_quorum)
    apply_quorum.add_argument("--release", required=True)
    apply_quorum.add_argument("--acknowledge-release-sha256", required=True)
    apply_quorum.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    apply_quorum.add_argument("--max-age-seconds", type=int, default=300)
    apply_quorum.add_argument("--timeout", type=float, default=30.0)
    apply_quorum.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    apply_quorum.add_argument("--max-wait-seconds", type=float, default=360.0)
    apply_quorum.add_argument("--poll-interval-seconds", type=float, default=5.0)
    apply_quorum.add_argument("--execute", action="store_true")

    reconcile_quorum = subparsers.add_parser(
        "reconcile-validator-quorum-recovery",
        help="reconcile a failed aggregate-health receipt from exact healthy quorum components",
        allow_abbrev=False,
    )
    _common(reconcile_quorum)
    reconcile_quorum.add_argument("--evidence", required=True)
    reconcile_quorum.add_argument("--max-age-seconds", type=int, default=86400)
    reconcile_quorum.add_argument("--timeout", type=float, default=30.0)
    reconcile_quorum.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)

    verify_quorum_reconciliation = subparsers.add_parser(
        "verify-validator-quorum-recovery-reconciliation",
        help="verify canonical component-scoped quorum recovery reconciliation evidence",
        allow_abbrev=False,
    )
    _common(verify_quorum_reconciliation)
    verify_quorum_reconciliation.add_argument("--reconciliation", required=True)
    verify_quorum_reconciliation.add_argument("--max-age-seconds", type=int, default=300)

    diagnose_quorum = subparsers.add_parser(
        "diagnose-validator-quorum-runtime",
        help="collect read-only redacted Coolify runtime diagnostics for failed quorum recovery",
        allow_abbrev=False,
    )
    _common(diagnose_quorum)
    diagnose_quorum.add_argument("--evidence", required=True)
    diagnose_quorum.add_argument("--timeout", type=float, default=30.0)
    diagnose_quorum.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)

    verify_quorum_evidence = subparsers.add_parser(
        "verify-validator-quorum-recovery-evidence",
        help="verify persisted two-validator quorum-recovery evidence",
        allow_abbrev=False,
    )
    _common(verify_quorum_evidence)
    verify_quorum_evidence.add_argument("--evidence", required=True)
    verify_quorum_evidence.add_argument("--max-age-seconds", type=int, default=300)

    stage_steady = subparsers.add_parser(
        "stage-post-admission-steady-state",
        help="compile exact A/C steady-state Compose documents from passing quorum reconciliation",
        allow_abbrev=False,
    )
    _common(stage_steady)
    stage_source = stage_steady.add_mutually_exclusive_group(required=True)
    stage_source.add_argument(
        "--reconciliation",
        help="passing validator-quorum-recovery reconciliation artifact",
    )
    stage_source.add_argument(
        "--quorum-evidence",
        help="passing validator-quorum-recovery execution evidence",
    )
    stage_steady.add_argument("--max-age-seconds", type=int, default=86400)
    stage_steady.add_argument("--created-at")
    stage_steady.add_argument("--write-transaction", action="store_true")
    stage_steady.add_argument(
        "--full-output",
        action="store_true",
        help="print embedded Compose and request bodies even after writing the transaction",
    )

    verify_steady_transaction = subparsers.add_parser(
        "verify-post-admission-steady-state-transaction",
        help="verify the offline post-admission steady-state cleanup transaction",
        allow_abbrev=False,
    )
    _common(verify_steady_transaction)
    verify_steady_transaction.add_argument("--transaction", required=True)
    verify_steady_transaction.add_argument("--max-age-seconds", type=int, default=86400)

    release_steady = subparsers.add_parser(
        "release-post-admission-steady-state",
        help="authorize one exact C-then-A steady-state cleanup for a short window",
        allow_abbrev=False,
    )
    _common(release_steady)
    release_steady.add_argument("--transaction", required=True)
    release_steady.add_argument(
        "--acknowledge-post-admission-steady-state-transaction-sha256",
        required=True,
    )
    release_steady.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    release_steady.add_argument("--expires-in-seconds", type=int, default=300)
    release_steady.add_argument("--created-at")
    release_steady.add_argument("--write-release", action="store_true")
    release_steady.add_argument(
        "--full-output",
        action="store_true",
        help="print embedded Compose and request bodies even after writing the release",
    )

    verify_steady_release = subparsers.add_parser(
        "verify-post-admission-steady-state-release",
        help="verify an expiring post-admission steady-state cleanup release",
        allow_abbrev=False,
    )
    _common(verify_steady_release)
    verify_steady_release.add_argument("--release", required=True)
    verify_steady_release.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_steady_release.add_argument("--max-age-seconds", type=int, default=300)

    apply_steady = subparsers.add_parser(
        "apply-post-admission-steady-state",
        help="inspect or execute the exact C-health-gate-then-A steady-state cleanup",
        allow_abbrev=False,
    )
    _common(apply_steady)
    apply_steady.add_argument("--release", required=True)
    apply_steady.add_argument("--acknowledge-release-sha256", required=True)
    apply_steady.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    apply_steady.add_argument("--max-age-seconds", type=int, default=300)
    apply_steady.add_argument("--timeout", type=float, default=30.0)
    apply_steady.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    apply_steady.add_argument("--max-wait-seconds", type=float, default=360.0)
    apply_steady.add_argument("--poll-interval-seconds", type=float, default=5.0)
    apply_steady.add_argument("--execute", action="store_true")
    apply_steady.add_argument(
        "--full-output",
        action="store_true",
        help="print complete execution receipts instead of the persisted-evidence summary",
    )

    verify_steady_evidence = subparsers.add_parser(
        "verify-post-admission-steady-state-evidence",
        help="verify persisted post-admission steady-state cleanup evidence",
        allow_abbrev=False,
    )
    _common(verify_steady_evidence)
    verify_steady_evidence.add_argument("--evidence", required=True)
    verify_steady_evidence.add_argument("--max-age-seconds", type=int, default=300)

    reconcile_steady = subparsers.add_parser(
        "reconcile-post-admission-steady-state",
        help="read-only reconcile the exact C-steady/A-recovered state after a consumed cleanup release",
        allow_abbrev=False,
    )
    _common(reconcile_steady)
    reconcile_steady.add_argument("--evidence", required=True)
    reconcile_steady.add_argument("--max-age-seconds", type=int, default=86400)
    reconcile_steady.add_argument("--timeout", type=float, default=30.0)
    reconcile_steady.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)

    verify_steady_reconciliation = subparsers.add_parser(
        "verify-post-admission-steady-state-reconciliation",
        help="verify canonical read-only C-steady/A-recovered reconciliation evidence",
        allow_abbrev=False,
    )
    _common(verify_steady_reconciliation)
    verify_steady_reconciliation.add_argument("--reconciliation", required=True)
    verify_steady_reconciliation.add_argument("--max-age-seconds", type=int, default=300)

    stage_steady_continuation = subparsers.add_parser(
        "stage-post-admission-steady-state-continuation",
        help="compile the exact A-only continuation from a passing mixed-state reconciliation",
        allow_abbrev=False,
    )
    _common(stage_steady_continuation)
    stage_steady_continuation.add_argument("--reconciliation", required=True)
    stage_steady_continuation.add_argument("--max-age-seconds", type=int, default=86400)
    stage_steady_continuation.add_argument("--created-at")
    stage_steady_continuation.add_argument("--write-transaction", action="store_true")
    stage_steady_continuation.add_argument("--full-output", action="store_true")

    verify_steady_continuation_transaction = subparsers.add_parser(
        "verify-post-admission-steady-state-continuation-transaction",
        help="verify the offline A-only steady-state continuation transaction",
        allow_abbrev=False,
    )
    _common(verify_steady_continuation_transaction)
    verify_steady_continuation_transaction.add_argument("--transaction", required=True)
    verify_steady_continuation_transaction.add_argument("--max-age-seconds", type=int, default=86400)

    release_steady_continuation = subparsers.add_parser(
        "release-post-admission-steady-state-continuation",
        help="authorize one exact A-only steady-state continuation",
        allow_abbrev=False,
    )
    _common(release_steady_continuation)
    release_steady_continuation.add_argument("--transaction", required=True)
    release_steady_continuation.add_argument(
        "--acknowledge-post-admission-steady-state-continuation-transaction-sha256",
        required=True,
    )
    release_steady_continuation.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    release_steady_continuation.add_argument("--expires-in-seconds", type=int, default=300)
    release_steady_continuation.add_argument("--created-at")
    release_steady_continuation.add_argument("--write-release", action="store_true")
    release_steady_continuation.add_argument("--full-output", action="store_true")

    verify_steady_continuation_release = subparsers.add_parser(
        "verify-post-admission-steady-state-continuation-release",
        help="verify an expiring A-only steady-state continuation release",
        allow_abbrev=False,
    )
    _common(verify_steady_continuation_release)
    verify_steady_continuation_release.add_argument("--release", required=True)
    verify_steady_continuation_release.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_steady_continuation_release.add_argument("--max-age-seconds", type=int, default=300)

    apply_steady_continuation = subparsers.add_parser(
        "apply-post-admission-steady-state-continuation",
        help="inspect or execute the exact C-refresh-gated A-only continuation",
        allow_abbrev=False,
    )
    _common(apply_steady_continuation)
    apply_steady_continuation.add_argument("--release", required=True)
    apply_steady_continuation.add_argument("--acknowledge-release-sha256", required=True)
    apply_steady_continuation.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    apply_steady_continuation.add_argument("--max-age-seconds", type=int, default=300)
    apply_steady_continuation.add_argument("--timeout", type=float, default=30.0)
    apply_steady_continuation.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    apply_steady_continuation.add_argument("--max-wait-seconds", type=float, default=360.0)
    apply_steady_continuation.add_argument("--poll-interval-seconds", type=float, default=5.0)
    apply_steady_continuation.add_argument("--execute", action="store_true")
    apply_steady_continuation.add_argument("--full-output", action="store_true")

    verify_steady_continuation_evidence = subparsers.add_parser(
        "verify-post-admission-steady-state-continuation-evidence",
        help="verify persisted A-only steady-state continuation evidence",
        allow_abbrev=False,
    )
    _common(verify_steady_continuation_evidence)
    verify_steady_continuation_evidence.add_argument("--evidence", required=True)
    verify_steady_continuation_evidence.add_argument("--max-age-seconds", type=int, default=300)
    verify_steady_continuation_evidence.add_argument("--transaction-max-age-seconds", type=int, default=86400)

    run_mainnet_soak = subparsers.add_parser(
        "run-mainnet-steady-state-soak",
        help="DEPRECATED legacy testing path: warns and refuses to define current acceptance",
        allow_abbrev=False,
    )
    _common(run_mainnet_soak)
    run_mainnet_soak.add_argument("--baseline-evidence", required=True)
    run_mainnet_soak.add_argument("--baseline-max-age-seconds", type=int, default=604800)
    run_mainnet_soak.add_argument("--duration-seconds", type=int, default=1800)
    run_mainnet_soak.add_argument("--observation-interval-seconds", type=int, default=60)
    run_mainnet_soak.add_argument("--timeout", type=float, default=30.0)
    run_mainnet_soak.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    run_mainnet_soak.add_argument("--full-output", action="store_true")

    verify_mainnet_soak = subparsers.add_parser(
        "verify-mainnet-steady-state-soak-evidence",
        help="DEPRECATED legacy testing path: warns before inspecting old soak evidence",
        allow_abbrev=False,
    )
    _common(verify_mainnet_soak)
    verify_mainnet_soak.add_argument("--evidence", required=True)
    verify_mainnet_soak.add_argument("--max-age-seconds", type=int, default=300)
    verify_mainnet_soak.add_argument("--baseline-max-age-seconds", type=int, default=604800)


    reserve_validator_rpc_canary_identity_parser = subparsers.add_parser(
        "reserve-validator-rpc-canary-identity",
        help="reserve a protected non-validator wallet for internal validator-RPC canaries",
        allow_abbrev=False,
    )
    _common(reserve_validator_rpc_canary_identity_parser)
    reserve_validator_rpc_canary_identity_parser.add_argument(
        "--canary-name",
        default="mainnet-canary1",
    )
    reserve_validator_rpc_canary_identity_parser.add_argument(
        "--write-identity",
        action="store_true",
    )

    verify_validator_rpc_canary_identity_parser = subparsers.add_parser(
        "verify-validator-rpc-canary-identity",
        help="verify a protected validator-RPC canary wallet artifact",
        allow_abbrev=False,
    )
    _common(verify_validator_rpc_canary_identity_parser)
    verify_validator_rpc_canary_identity_parser.add_argument("--identity", required=True)
    verify_validator_rpc_canary_identity_parser.add_argument("--canary-name")

    stage_validator_rpc_canary = subparsers.add_parser(
        "stage-validator-rpc-canary-transaction",
        help="compile an offline A-submit/C-verify internal validator-RPC canary",
        allow_abbrev=False,
    )
    _common(stage_validator_rpc_canary)
    stage_validator_rpc_canary.add_argument("--soak-evidence", required=True)
    stage_validator_rpc_canary.add_argument("--soak-max-age-seconds", type=int, default=86400)
    stage_validator_rpc_canary.add_argument("--identity", required=True)
    stage_validator_rpc_canary.add_argument("--canary-name", default="mainnet-canary1")
    stage_validator_rpc_canary.add_argument("--environment-name", default="mainnet")
    stage_validator_rpc_canary.add_argument(
        "--foundry-image",
        default="ghcr.io/foundry-rs/foundry:latest",
    )
    stage_validator_rpc_canary.add_argument("--write-transaction", action="store_true")
    stage_validator_rpc_canary.add_argument("--full-output", action="store_true")

    verify_validator_rpc_canary = subparsers.add_parser(
        "verify-validator-rpc-canary-transaction",
        help="verify an offline internal validator-RPC canary transaction",
        allow_abbrev=False,
    )
    _common(verify_validator_rpc_canary)
    verify_validator_rpc_canary.add_argument("--transaction", required=True)
    verify_validator_rpc_canary.add_argument("--max-age-seconds", type=int, default=86400)
    verify_validator_rpc_canary.add_argument("--soak-max-age-seconds", type=int, default=86400)

    release_validator_rpc_canary = subparsers.add_parser(
        "release-validator-rpc-canary",
        help="issue an expiring one-use release for the funded validator-RPC canary execution",
        allow_abbrev=False,
    )
    _common(release_validator_rpc_canary)
    release_validator_rpc_canary.add_argument("--transaction", required=True)
    release_validator_rpc_canary.add_argument("--funding-evidence", required=True)
    release_validator_rpc_canary.add_argument(
        "--acknowledge-validator-rpc-canary-transaction-sha256",
        required=True,
    )
    release_validator_rpc_canary.add_argument("--expires-in-seconds", type=int, default=300)
    release_validator_rpc_canary.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    release_validator_rpc_canary.add_argument("--funding-evidence-max-age-seconds", type=int, default=86400)
    release_validator_rpc_canary.add_argument("--funding-transaction-max-age-seconds", type=int, default=86400)
    release_validator_rpc_canary.add_argument("--soak-max-age-seconds", type=int, default=86400)
    release_validator_rpc_canary.add_argument("--write-release", action="store_true")
    release_validator_rpc_canary.add_argument("--full-output", action="store_true")

    verify_validator_rpc_canary_release_parser = subparsers.add_parser(
        "verify-validator-rpc-canary-release",
        help="verify a funded validator-RPC canary execution release",
        allow_abbrev=False,
    )
    _common(verify_validator_rpc_canary_release_parser)
    verify_validator_rpc_canary_release_parser.add_argument("--release", required=True)
    verify_validator_rpc_canary_release_parser.add_argument("--max-age-seconds", type=int, default=300)
    verify_validator_rpc_canary_release_parser.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_validator_rpc_canary_release_parser.add_argument("--funding-evidence-max-age-seconds", type=int, default=86400)
    verify_validator_rpc_canary_release_parser.add_argument("--funding-transaction-max-age-seconds", type=int, default=86400)
    verify_validator_rpc_canary_release_parser.add_argument("--soak-max-age-seconds", type=int, default=86400)

    apply_validator_rpc_canary = subparsers.add_parser(
        "apply-validator-rpc-canary",
        help="inspect or execute a funded validator-RPC canary release",
        allow_abbrev=False,
    )
    _common(apply_validator_rpc_canary)
    apply_validator_rpc_canary.add_argument("--release", required=True)
    apply_validator_rpc_canary.add_argument("--acknowledge-release-sha256", required=True)
    apply_validator_rpc_canary.add_argument("--execute", action="store_true")
    apply_validator_rpc_canary.add_argument("--max-age-seconds", type=int, default=300)
    apply_validator_rpc_canary.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    apply_validator_rpc_canary.add_argument("--funding-evidence-max-age-seconds", type=int, default=86400)
    apply_validator_rpc_canary.add_argument("--funding-transaction-max-age-seconds", type=int, default=86400)
    apply_validator_rpc_canary.add_argument("--soak-max-age-seconds", type=int, default=86400)
    apply_validator_rpc_canary.add_argument("--recovery-evidence")
    apply_validator_rpc_canary.add_argument("--recovery-evidence-max-age-seconds", type=int, default=86400)
    apply_validator_rpc_canary.add_argument("--timeout", type=float, default=30.0)
    apply_validator_rpc_canary.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    apply_validator_rpc_canary.add_argument("--max-wait-seconds", type=float, default=300.0)
    apply_validator_rpc_canary.add_argument("--poll-interval-seconds", type=float, default=5.0)
    apply_validator_rpc_canary.add_argument("--full-output", action="store_true")

    verify_validator_rpc_canary_evidence_parser = subparsers.add_parser(
        "verify-validator-rpc-canary-evidence",
        help="verify canonical validator-RPC canary execution evidence",
        allow_abbrev=False,
    )
    _common(verify_validator_rpc_canary_evidence_parser)
    verify_validator_rpc_canary_evidence_parser.add_argument("--evidence", required=True)
    verify_validator_rpc_canary_evidence_parser.add_argument("--max-age-seconds", type=int, default=86400)
    verify_validator_rpc_canary_evidence_parser.add_argument("--release-max-age-seconds", type=int, default=86400)
    verify_validator_rpc_canary_evidence_parser.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_validator_rpc_canary_evidence_parser.add_argument("--funding-evidence-max-age-seconds", type=int, default=86400)
    verify_validator_rpc_canary_evidence_parser.add_argument("--funding-transaction-max-age-seconds", type=int, default=86400)
    verify_validator_rpc_canary_evidence_parser.add_argument("--soak-max-age-seconds", type=int, default=86400)

    stage_validator_rpc_canary_funding = subparsers.add_parser(
        "stage-validator-rpc-canary-funding-transaction",
        help="compile an offline exact capped funding plan for the validator-RPC canary",
        allow_abbrev=False,
    )
    _common(stage_validator_rpc_canary_funding)
    stage_validator_rpc_canary_funding.add_argument("--canary-transaction", required=True)
    stage_validator_rpc_canary_funding.add_argument(
        "--canary-transaction-max-age-seconds", type=int, default=86400
    )
    stage_validator_rpc_canary_funding.add_argument(
        "--soak-max-age-seconds", type=int, default=86400
    )
    stage_validator_rpc_canary_funding.add_argument("--write-transaction", action="store_true")
    stage_validator_rpc_canary_funding.add_argument("--full-output", action="store_true")

    verify_validator_rpc_canary_funding = subparsers.add_parser(
        "verify-validator-rpc-canary-funding-transaction",
        help="verify an offline exact capped validator-RPC canary funding transaction",
        allow_abbrev=False,
    )
    _common(verify_validator_rpc_canary_funding)
    verify_validator_rpc_canary_funding.add_argument("--transaction", required=True)
    verify_validator_rpc_canary_funding.add_argument("--max-age-seconds", type=int, default=86400)
    verify_validator_rpc_canary_funding.add_argument(
        "--canary-transaction-max-age-seconds", type=int, default=86400
    )
    verify_validator_rpc_canary_funding.add_argument(
        "--soak-max-age-seconds", type=int, default=86400
    )

    release_validator_rpc_canary_funding = subparsers.add_parser(
        "release-validator-rpc-canary-funding",
        help="issue an expiring one-use release for the exact capped canary funding transaction",
        allow_abbrev=False,
    )
    _common(release_validator_rpc_canary_funding)
    release_validator_rpc_canary_funding.add_argument("--transaction", required=True)
    release_validator_rpc_canary_funding.add_argument(
        "--acknowledge-validator-rpc-canary-funding-transaction-sha256",
        required=True,
    )
    release_validator_rpc_canary_funding.add_argument("--expires-in-seconds", type=int, default=300)
    release_validator_rpc_canary_funding.add_argument(
        "--recovery-evidence",
        help="bind one exact prior safe funding failure for bounded idempotent retry",
    )
    release_validator_rpc_canary_funding.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    release_validator_rpc_canary_funding.add_argument("--canary-transaction-max-age-seconds", type=int, default=86400)
    release_validator_rpc_canary_funding.add_argument("--soak-max-age-seconds", type=int, default=86400)
    release_validator_rpc_canary_funding.add_argument("--write-release", action="store_true")
    release_validator_rpc_canary_funding.add_argument("--full-output", action="store_true")

    verify_validator_rpc_canary_funding_release_parser = subparsers.add_parser(
        "verify-validator-rpc-canary-funding-release",
        help="verify an expiring one-use validator-RPC canary funding release",
        allow_abbrev=False,
    )
    _common(verify_validator_rpc_canary_funding_release_parser)
    verify_validator_rpc_canary_funding_release_parser.add_argument("--release", required=True)
    verify_validator_rpc_canary_funding_release_parser.add_argument("--max-age-seconds", type=int, default=300)
    verify_validator_rpc_canary_funding_release_parser.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_validator_rpc_canary_funding_release_parser.add_argument("--canary-transaction-max-age-seconds", type=int, default=86400)
    verify_validator_rpc_canary_funding_release_parser.add_argument("--soak-max-age-seconds", type=int, default=86400)

    apply_validator_rpc_canary_funding = subparsers.add_parser(
        "apply-validator-rpc-canary-funding",
        help="inspect or execute exactly one capped canary funding release",
        allow_abbrev=False,
    )
    _common(apply_validator_rpc_canary_funding)
    apply_validator_rpc_canary_funding.add_argument("--release", required=True)
    apply_validator_rpc_canary_funding.add_argument("--acknowledge-release-sha256", required=True)
    apply_validator_rpc_canary_funding.add_argument("--execute", action="store_true")
    apply_validator_rpc_canary_funding.add_argument("--max-age-seconds", type=int, default=300)
    apply_validator_rpc_canary_funding.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    apply_validator_rpc_canary_funding.add_argument("--canary-transaction-max-age-seconds", type=int, default=86400)
    apply_validator_rpc_canary_funding.add_argument("--soak-max-age-seconds", type=int, default=86400)
    apply_validator_rpc_canary_funding.add_argument("--timeout", type=float, default=30.0)
    apply_validator_rpc_canary_funding.add_argument("--max-response-bytes", type=int, default=1048576)
    apply_validator_rpc_canary_funding.add_argument("--max-wait-seconds", type=float, default=300.0)
    apply_validator_rpc_canary_funding.add_argument("--poll-interval-seconds", type=float, default=5.0)
    apply_validator_rpc_canary_funding.add_argument("--full-output", action="store_true")

    verify_validator_rpc_canary_funding_evidence_parser = subparsers.add_parser(
        "verify-validator-rpc-canary-funding-evidence",
        help="verify persisted cross-validator canary funding evidence",
        allow_abbrev=False,
    )
    _common(verify_validator_rpc_canary_funding_evidence_parser)
    verify_validator_rpc_canary_funding_evidence_parser.add_argument("--evidence", required=True)
    verify_validator_rpc_canary_funding_evidence_parser.add_argument("--max-age-seconds", type=int, default=86400)
    verify_validator_rpc_canary_funding_evidence_parser.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_validator_rpc_canary_funding_evidence_parser.add_argument("--canary-transaction-max-age-seconds", type=int, default=86400)
    verify_validator_rpc_canary_funding_evidence_parser.add_argument("--soak-max-age-seconds", type=int, default=86400)

    cleanup_completed_helpers = subparsers.add_parser(
        "cleanup-completed-mother-helpers",
        help="inspect or delete completed one-shot Mother helper applications from a Coolify node stack",
        allow_abbrev=False,
    )
    _common(cleanup_completed_helpers)
    cleanup_completed_helpers.add_argument(
        "--controller-id",
        required=True,
        choices=("coolify-a", "coolify-c"),
    )
    cleanup_completed_helpers.add_argument("--service-uuid", required=True)
    cleanup_completed_helpers.add_argument(
        "--node-name",
        required=True,
        help="required core Besu application name in the service stack, for example mainneta-super1",
    )
    cleanup_completed_helpers.add_argument(
        "--required-component-name",
        action="append",
        default=[],
        help="extra required core application name; defaults to Mother FDB and Hub when omitted",
    )
    cleanup_completed_helpers.add_argument("--timeout", type=float, default=30.0)
    cleanup_completed_helpers.add_argument("--max-response-bytes", type=int, default=12 * 1024 * 1024)
    cleanup_completed_helpers.add_argument("--execute", action="store_true")
    cleanup_completed_helpers.add_argument(
        "--allow-compose-rewrite",
        action="store_true",
        help="when service-application DELETE returns 404, rewrite the service compose without completed helpers",
    )
    cleanup_completed_helpers.add_argument(
        "--instant-deploy-compose-rewrite",
        action="store_true",
        help="request an instant Coolify deploy with the rewritten service compose",
    )
    cleanup_completed_helpers.add_argument(
        "--allow-nested-application-delete",
        action="store_true",
        help="when top-level application DELETE returns 404, try service-scoped nested application DELETE endpoints",
    )
    cleanup_completed_helpers.add_argument(
        "--allow-compose-reconcile-refresh",
        action="store_true",
        help="when stale helper records remain but compose is already clean, PATCH the clean compose back to Coolify to refresh parsed service applications",
    )
    cleanup_completed_helpers.add_argument(
        "--instant-deploy-compose-reconcile-refresh",
        action="store_true",
        help="request an instant Coolify deploy with the no-op compose reconcile refresh",
    )
    cleanup_completed_helpers.add_argument(
        "--allow-service-redeploy-refresh",
        action="store_true",
        help="when stale helper records remain but compose is already clean, request a Coolify service redeploy refresh",
    )
    cleanup_completed_helpers.add_argument(
        "--no-force-service-redeploy-refresh",
        action="store_true",
        help="request the service redeploy refresh with force=false instead of the default force=true",
    )
    cleanup_completed_helpers.add_argument(
        "--allow-docker-orphan-container-cleanup",
        action="store_true",
        help="last-resort cleanup: create a temporary Docker CLI service to remove exited helper orphan containers for this exact service stack",
    )
    cleanup_completed_helpers.add_argument(
        "--allow-coolify-model-status-exclusion",
        action="store_true",
        help=(
            "after Docker orphan cleanup, use an ephemeral Docker-socket helper to "
            "mark exact stale terminal Coolify ServiceApplication rows exclude_from_status=true "
            "through Coolify's own Eloquent model layer"
        ),
    )
    cleanup_completed_helpers.add_argument("--max-wait-seconds", type=float, default=120.0)
    cleanup_completed_helpers.add_argument("--poll-interval-seconds", type=float, default=5.0)
    cleanup_completed_helpers.add_argument(
        "--acknowledge-service-uuid",
        default="",
        help="required only with --execute; must exactly match --service-uuid",
    )

    verify_completed_helpers_cleanup = subparsers.add_parser(
        "verify-completed-mother-helper-cleanup-evidence",
        help="verify persisted completed-helper cleanup evidence",
        allow_abbrev=False,
    )
    _common(verify_completed_helpers_cleanup)
    verify_completed_helpers_cleanup.add_argument("--evidence", required=True)
    verify_completed_helpers_cleanup.add_argument("--max-age-seconds", type=int, default=86400)

    coolify_service_lifecycle_probe = subparsers.add_parser(
        "probe-coolify-service-lifecycle",
        help="inspect or execute one temporary no-secret no-chain Coolify service lifecycle probe",
        allow_abbrev=False,
    )
    _common(coolify_service_lifecycle_probe)
    coolify_service_lifecycle_probe.add_argument(
        "--controller-id",
        required=True,
        choices=("coolify-a", "coolify-c"),
    )
    coolify_service_lifecycle_probe.add_argument("--environment-name", default="mainnet")
    coolify_service_lifecycle_probe.add_argument("--observe-seconds", type=float, default=60.0)
    coolify_service_lifecycle_probe.add_argument("--poll-interval-seconds", type=float, default=5.0)
    coolify_service_lifecycle_probe.add_argument("--timeout", type=float, default=30.0)
    coolify_service_lifecycle_probe.add_argument("--max-response-bytes", type=int, default=1048576)
    coolify_service_lifecycle_probe.add_argument("--execute", action="store_true")
    coolify_service_lifecycle_probe.add_argument(
        "--acknowledge-live-service-probe",
        default="",
        help="required only with --execute",
    )

    stage_c2_extension = subparsers.add_parser(
        "stage-c2-state-extension",
        help="stage a local-only post-T2 Mother generation that reserves canonical mainnetc-super2",
        allow_abbrev=False,
    )
    _common(stage_c2_extension)
    stage_c2_extension.add_argument("--canary-evidence", required=True)
    stage_c2_extension.add_argument("--canary-max-age-seconds", type=int, default=86400)
    stage_c2_extension.add_argument("--created-at")
    stage_c2_extension.add_argument(
        "--write-transaction",
        action="store_true",
        help="persist the secret-safe transaction and its private C2 reservation payload",
    )

    verify_c2_extension_transaction = subparsers.add_parser(
        "verify-c2-state-extension-transaction",
        help="verify a staged C2 private-state successor against current T2 state and canary evidence",
        allow_abbrev=False,
    )
    _common(verify_c2_extension_transaction)
    verify_c2_extension_transaction.add_argument("--transaction", required=True)
    verify_c2_extension_transaction.add_argument("--max-age-seconds", type=int, default=86400)
    verify_c2_extension_transaction.add_argument("--canary-max-age-seconds", type=int, default=86400)

    release_c2_extension = subparsers.add_parser(
        "release-c2-state-extension",
        help="authorize one exact local-only C2 private-state generation advance",
        allow_abbrev=False,
    )
    _common(release_c2_extension)
    release_c2_extension.add_argument("--transaction", required=True)
    release_c2_extension.add_argument("--acknowledge-c2-state-extension-transaction-sha256", required=True)
    release_c2_extension.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    release_c2_extension.add_argument("--canary-max-age-seconds", type=int, default=86400)
    release_c2_extension.add_argument("--expires-in-seconds", type=int, default=300)
    release_c2_extension.add_argument("--created-at")
    release_c2_extension.add_argument(
        "--write-release",
        action="store_true",
        help="persist the canonical one-use C2 state-extension release",
    )

    verify_c2_extension_release = subparsers.add_parser(
        "verify-c2-state-extension-release",
        help="verify an expiring C2 state-extension release before use",
        allow_abbrev=False,
    )
    _common(verify_c2_extension_release)
    verify_c2_extension_release.add_argument("--release", required=True)
    verify_c2_extension_release.add_argument("--max-age-seconds", type=int, default=300)
    verify_c2_extension_release.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_c2_extension_release.add_argument("--canary-max-age-seconds", type=int, default=86400)

    apply_c2_extension = subparsers.add_parser(
        "apply-c2-state-extension",
        help="inspect or consume one C2 release and advance Mother private state exactly one generation",
        allow_abbrev=False,
    )
    _common(apply_c2_extension)
    apply_c2_extension.add_argument("--release", required=True)
    apply_c2_extension.add_argument("--acknowledge-release-sha256", required=True)
    apply_c2_extension.add_argument("--max-age-seconds", type=int, default=300)
    apply_c2_extension.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    apply_c2_extension.add_argument("--canary-max-age-seconds", type=int, default=86400)
    apply_c2_extension.add_argument(
        "--execute",
        action="store_true",
        help="commit the released local private-state successor; without this flag inspect only",
    )

    verify_c2_extension_evidence = subparsers.add_parser(
        "verify-c2-state-extension-evidence",
        help="verify the committed C2 generation, predecessor recovery, and downstream soft-target plan",
        allow_abbrev=False,
    )
    _common(verify_c2_extension_evidence)
    verify_c2_extension_evidence.add_argument("--evidence", required=True)
    verify_c2_extension_evidence.add_argument("--max-age-seconds", type=int, default=86400)


    stage_c2_standby_service = subparsers.add_parser(
        "stage-c2-standby-service",
        help="stage the C2-only standby service creation transaction from clean preflight evidence",
        allow_abbrev=False,
    )
    _common(stage_c2_standby_service)
    stage_c2_standby_service.add_argument("--preflight-evidence", required=True)
    stage_c2_standby_service.add_argument("--c2-state-extension-evidence", required=True)
    stage_c2_standby_service.add_argument("--preflight-max-age-seconds", type=int, default=300)
    stage_c2_standby_service.add_argument("--c2-state-extension-max-age-seconds", type=int, default=86400)
    stage_c2_standby_service.add_argument("--created-at")

    verify_c2_standby_service_transaction = subparsers.add_parser(
        "verify-c2-standby-service-transaction",
        help="verify a staged C2 standby-service transaction",
        allow_abbrev=False,
    )
    _common(verify_c2_standby_service_transaction)
    verify_c2_standby_service_transaction.add_argument("--transaction", required=True)
    verify_c2_standby_service_transaction.add_argument("--c2-state-extension-evidence", required=True)
    verify_c2_standby_service_transaction.add_argument("--max-age-seconds", type=int, default=300)
    verify_c2_standby_service_transaction.add_argument("--c2-state-extension-max-age-seconds", type=int, default=86400)

    release_c2_standby_service = subparsers.add_parser(
        "release-c2-standby-service",
        help="authorize one exact C2 standby-service creation mutation",
        allow_abbrev=False,
    )
    _common(release_c2_standby_service)
    release_c2_standby_service.add_argument("--transaction", required=True)
    release_c2_standby_service.add_argument("--c2-state-extension-evidence", required=True)
    release_c2_standby_service.add_argument("--acknowledge-c2-standby-service-transaction-sha256", required=True)
    release_c2_standby_service.add_argument("--max-age-seconds", type=int, default=300)
    release_c2_standby_service.add_argument("--c2-state-extension-max-age-seconds", type=int, default=86400)
    release_c2_standby_service.add_argument("--expires-in-seconds", type=int, default=300)
    release_c2_standby_service.add_argument("--created-at")
    release_c2_standby_service.add_argument("--write-release", action="store_true")

    verify_c2_standby_service_release = subparsers.add_parser(
        "verify-c2-standby-service-release",
        help="verify an expiring C2 standby-service release before use",
        allow_abbrev=False,
    )
    _common(verify_c2_standby_service_release)
    verify_c2_standby_service_release.add_argument("--release", required=True)
    verify_c2_standby_service_release.add_argument("--c2-state-extension-evidence", required=True)
    verify_c2_standby_service_release.add_argument("--max-age-seconds", type=int, default=300)
    verify_c2_standby_service_release.add_argument("--c2-state-extension-max-age-seconds", type=int, default=86400)

    apply_c2_standby_service = subparsers.add_parser(
        "apply-c2-standby-service",
        help="inspect or execute the released C2 standby-service creation mutation",
        allow_abbrev=False,
    )
    _common(apply_c2_standby_service)
    apply_c2_standby_service.add_argument("--release", required=True)
    apply_c2_standby_service.add_argument("--c2-state-extension-evidence", required=True)
    apply_c2_standby_service.add_argument("--acknowledge-release-sha256", required=True)
    apply_c2_standby_service.add_argument("--max-age-seconds", type=int, default=300)
    apply_c2_standby_service.add_argument("--c2-state-extension-max-age-seconds", type=int, default=86400)
    apply_c2_standby_service.add_argument("--timeout", type=float, default=30.0)
    apply_c2_standby_service.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    apply_c2_standby_service.add_argument("--execute", action="store_true")

    verify_c2_standby_service_cmd = subparsers.add_parser(
        "verify-c2-standby-service",
        help="GET-verify the C2 standby service created by a successful service execution",
        allow_abbrev=False,
    )
    _common(verify_c2_standby_service_cmd)
    verify_c2_standby_service_cmd.add_argument("--execution", required=True)
    verify_c2_standby_service_cmd.add_argument("--timeout", type=float, default=30.0)
    verify_c2_standby_service_cmd.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    verify_c2_standby_service_cmd.add_argument("--observed-at")
    verify_c2_standby_service_cmd.add_argument("--write-evidence", action="store_true")
    verify_c2_standby_service_cmd.add_argument("--require-clean", action="store_true")

    stage_c2_standby_identity = subparsers.add_parser(
        "stage-c2-standby-identity",
        help="stage C2 reserved identity service-environment writes",
        allow_abbrev=False,
    )
    _common(stage_c2_standby_identity)
    stage_c2_standby_identity.add_argument("--standby-evidence", required=True)
    stage_c2_standby_identity.add_argument("--max-age-seconds", type=int, default=300)
    stage_c2_standby_identity.add_argument("--created-at")

    verify_c2_standby_identity_transaction_cmd = subparsers.add_parser(
        "verify-c2-standby-identity-transaction",
        help="verify a staged C2 standby identity transaction",
        allow_abbrev=False,
    )
    _common(verify_c2_standby_identity_transaction_cmd)
    verify_c2_standby_identity_transaction_cmd.add_argument("--transaction", required=True)
    verify_c2_standby_identity_transaction_cmd.add_argument("--max-age-seconds", type=int, default=300)

    release_c2_standby_identity = subparsers.add_parser(
        "release-c2-standby-identity",
        help="authorize one exact C2 standby identity installation mutation set",
        allow_abbrev=False,
    )
    _common(release_c2_standby_identity)
    release_c2_standby_identity.add_argument("--transaction", required=True)
    release_c2_standby_identity.add_argument("--acknowledge-c2-standby-identity-transaction-sha256", required=True)
    release_c2_standby_identity.add_argument("--max-age-seconds", type=int, default=300)
    release_c2_standby_identity.add_argument("--expires-in-seconds", type=int, default=300)
    release_c2_standby_identity.add_argument("--created-at")
    release_c2_standby_identity.add_argument("--write-release", action="store_true")

    verify_c2_standby_identity_release_cmd = subparsers.add_parser(
        "verify-c2-standby-identity-release",
        help="verify an expiring C2 standby identity release before use",
        allow_abbrev=False,
    )
    _common(verify_c2_standby_identity_release_cmd)
    verify_c2_standby_identity_release_cmd.add_argument("--release", required=True)
    verify_c2_standby_identity_release_cmd.add_argument("--max-age-seconds", type=int, default=300)

    apply_c2_standby_identity = subparsers.add_parser(
        "apply-c2-standby-identity",
        help="inspect or execute the released C2 standby identity env writes",
        allow_abbrev=False,
    )
    _common(apply_c2_standby_identity)
    apply_c2_standby_identity.add_argument("--release", required=True)
    apply_c2_standby_identity.add_argument("--acknowledge-release-sha256", required=True)
    apply_c2_standby_identity.add_argument("--max-age-seconds", type=int, default=300)
    apply_c2_standby_identity.add_argument("--timeout", type=float, default=30.0)
    apply_c2_standby_identity.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    apply_c2_standby_identity.add_argument("--execute", action="store_true")

    verify_c2_standby_identity_cmd = subparsers.add_parser(
        "verify-c2-standby-identity",
        help="GET-verify the C2 standby identity env keys installed by a successful identity execution",
        allow_abbrev=False,
    )
    _common(verify_c2_standby_identity_cmd)
    verify_c2_standby_identity_cmd.add_argument("--execution", required=True)
    verify_c2_standby_identity_cmd.add_argument("--timeout", type=float, default=30.0)
    verify_c2_standby_identity_cmd.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    verify_c2_standby_identity_cmd.add_argument("--observed-at")
    verify_c2_standby_identity_cmd.add_argument("--write-evidence", action="store_true")
    verify_c2_standby_identity_cmd.add_argument("--require-clean", action="store_true")

    stage_c2_replica_standby = subparsers.add_parser(
        "stage-c2-replica-standby",
        help="stage C2 genesis/replica standby Compose preparation without deploy, sync, or validator authority",
        allow_abbrev=False,
    )
    _common(stage_c2_replica_standby)
    stage_c2_replica_standby.add_argument("--c2-state-extension-evidence", required=True)
    stage_c2_replica_standby.add_argument("--identity-evidence", required=True)
    stage_c2_replica_standby.add_argument("--identity-rollback-evidence", required=True)
    stage_c2_replica_standby.add_argument("--genesis-birth-evidence", required=True)
    stage_c2_replica_standby.add_argument("--c2-state-extension-max-age-seconds", type=int, default=86400)
    stage_c2_replica_standby.add_argument("--identity-max-age-seconds", type=int, default=86400)
    stage_c2_replica_standby.add_argument("--genesis-birth-max-age-seconds", type=int, default=86400)
    stage_c2_replica_standby.add_argument("--created-at")
    stage_c2_replica_standby.add_argument("--write-transaction", action="store_true")

    verify_c2_replica_standby_transaction_cmd = subparsers.add_parser(
        "verify-c2-replica-standby-transaction",
        help="verify a staged C2 replica standby transaction",
        allow_abbrev=False,
    )
    _common(verify_c2_replica_standby_transaction_cmd)
    verify_c2_replica_standby_transaction_cmd.add_argument("--transaction", required=True)
    verify_c2_replica_standby_transaction_cmd.add_argument("--max-age-seconds", type=int, default=300)
    verify_c2_replica_standby_transaction_cmd.add_argument("--c2-state-extension-max-age-seconds", type=int, default=86400)
    verify_c2_replica_standby_transaction_cmd.add_argument("--identity-max-age-seconds", type=int, default=86400)
    verify_c2_replica_standby_transaction_cmd.add_argument("--genesis-birth-max-age-seconds", type=int, default=86400)

    release_c2_replica_standby = subparsers.add_parser(
        "release-c2-replica-standby",
        help="authorize one exact C2 replica standby Compose PATCH",
        allow_abbrev=False,
    )
    _common(release_c2_replica_standby)
    release_c2_replica_standby.add_argument("--transaction", required=True)
    release_c2_replica_standby.add_argument("--acknowledge-c2-replica-standby-transaction-sha256", required=True)
    release_c2_replica_standby.add_argument("--max-age-seconds", type=int, default=300)
    release_c2_replica_standby.add_argument("--c2-state-extension-max-age-seconds", type=int, default=86400)
    release_c2_replica_standby.add_argument("--identity-max-age-seconds", type=int, default=86400)
    release_c2_replica_standby.add_argument("--genesis-birth-max-age-seconds", type=int, default=86400)
    release_c2_replica_standby.add_argument("--expires-in-seconds", type=int, default=300)
    release_c2_replica_standby.add_argument("--created-at")
    release_c2_replica_standby.add_argument("--write-release", action="store_true")

    verify_c2_replica_standby_release_cmd = subparsers.add_parser(
        "verify-c2-replica-standby-release",
        help="verify an expiring C2 replica standby release",
        allow_abbrev=False,
    )
    _common(verify_c2_replica_standby_release_cmd)
    verify_c2_replica_standby_release_cmd.add_argument("--release", required=True)
    verify_c2_replica_standby_release_cmd.add_argument("--max-age-seconds", type=int, default=300)
    verify_c2_replica_standby_release_cmd.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    verify_c2_replica_standby_release_cmd.add_argument("--c2-state-extension-max-age-seconds", type=int, default=86400)
    verify_c2_replica_standby_release_cmd.add_argument("--identity-max-age-seconds", type=int, default=86400)
    verify_c2_replica_standby_release_cmd.add_argument("--genesis-birth-max-age-seconds", type=int, default=86400)

    apply_c2_replica_standby = subparsers.add_parser(
        "apply-c2-replica-standby",
        help="inspect or execute the released C2 replica standby Compose PATCH",
        allow_abbrev=False,
    )
    _common(apply_c2_replica_standby)
    apply_c2_replica_standby.add_argument("--release", required=True)
    apply_c2_replica_standby.add_argument("--acknowledge-release-sha256", required=True)
    apply_c2_replica_standby.add_argument("--max-age-seconds", type=int, default=300)
    apply_c2_replica_standby.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    apply_c2_replica_standby.add_argument("--c2-state-extension-max-age-seconds", type=int, default=86400)
    apply_c2_replica_standby.add_argument("--identity-max-age-seconds", type=int, default=86400)
    apply_c2_replica_standby.add_argument("--genesis-birth-max-age-seconds", type=int, default=86400)
    apply_c2_replica_standby.add_argument("--timeout", type=float, default=30.0)
    apply_c2_replica_standby.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    apply_c2_replica_standby.add_argument("--execute", action="store_true")

    verify_c2_replica_standby_cmd = subparsers.add_parser(
        "verify-c2-replica-standby",
        help="GET-verify the C2 replica standby Compose/genesis configuration",
        allow_abbrev=False,
    )
    _common(verify_c2_replica_standby_cmd)
    verify_c2_replica_standby_cmd.add_argument("--execution", required=True)
    verify_c2_replica_standby_cmd.add_argument("--timeout", type=float, default=30.0)
    verify_c2_replica_standby_cmd.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    verify_c2_replica_standby_cmd.add_argument("--observed-at")
    verify_c2_replica_standby_cmd.add_argument("--write-evidence", action="store_true")
    verify_c2_replica_standby_cmd.add_argument("--require-clean", action="store_true")

    release_c2_replica_sync = subparsers.add_parser(
        "release-c2-replica-sync",
        help="authorize one C2 non-validator replica synchronization proof",
        allow_abbrev=False,
    )
    _common(release_c2_replica_sync)
    release_c2_replica_sync.add_argument("--standby-evidence", required=True)
    release_c2_replica_sync.add_argument("--acknowledge-c2-replica-standby-evidence-sha256", required=True)
    release_c2_replica_sync.add_argument("--standby-max-age-seconds", type=int, default=86400)
    release_c2_replica_sync.add_argument("--expires-in-seconds", type=int, default=300)
    release_c2_replica_sync.add_argument("--created-at")
    release_c2_replica_sync.add_argument("--write-release", action="store_true")

    verify_c2_replica_sync_release_cmd = subparsers.add_parser(
        "verify-c2-replica-sync-release",
        help="verify one expiring C2 replica synchronization release",
        allow_abbrev=False,
    )
    _common(verify_c2_replica_sync_release_cmd)
    verify_c2_replica_sync_release_cmd.add_argument("--release", required=True)
    verify_c2_replica_sync_release_cmd.add_argument("--max-age-seconds", type=int, default=300)
    verify_c2_replica_sync_release_cmd.add_argument("--standby-max-age-seconds", type=int, default=86400)

    apply_c2_replica_sync = subparsers.add_parser(
        "apply-c2-replica-sync",
        help="inspect or execute one released C2 non-validator replica synchronization proof",
        allow_abbrev=False,
    )
    _common(apply_c2_replica_sync)
    apply_c2_replica_sync.add_argument("--release", required=True)
    apply_c2_replica_sync.add_argument("--acknowledge-release-sha256", required=True)
    apply_c2_replica_sync.add_argument("--max-age-seconds", type=int, default=300)
    apply_c2_replica_sync.add_argument("--standby-max-age-seconds", type=int, default=86400)
    apply_c2_replica_sync.add_argument("--timeout", type=float, default=30.0)
    apply_c2_replica_sync.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    apply_c2_replica_sync.add_argument("--max-wait-seconds", type=float, default=300.0)
    apply_c2_replica_sync.add_argument("--poll-interval-seconds", type=float, default=5.0)
    apply_c2_replica_sync.add_argument("--execute", action="store_true")

    verify_c2_replica_sync_cmd = subparsers.add_parser(
        "verify-c2-replica-sync-evidence",
        help="verify persisted C2 replica synchronization evidence",
        allow_abbrev=False,
    )
    _common(verify_c2_replica_sync_cmd)
    verify_c2_replica_sync_cmd.add_argument("--evidence", required=True)
    verify_c2_replica_sync_cmd.add_argument("--max-age-seconds", type=int, default=300)

    diagnose_c2_replica_sync_materialization_cmd = subparsers.add_parser(
        "diagnose-c2-replica-sync-materialization",
        help="GET-diagnose a failed C2 replica sync that did not materialize a healthy service",
        allow_abbrev=False,
    )
    _common(diagnose_c2_replica_sync_materialization_cmd)
    diagnose_c2_replica_sync_materialization_cmd.add_argument("--failed-evidence", required=True)
    diagnose_c2_replica_sync_materialization_cmd.add_argument("--failed-evidence-max-age-seconds", type=int, default=86400)
    diagnose_c2_replica_sync_materialization_cmd.add_argument("--timeout", type=float, default=30.0)
    diagnose_c2_replica_sync_materialization_cmd.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    diagnose_c2_replica_sync_materialization_cmd.add_argument("--operator-confirm-no-docker-materialization", action="store_true")
    diagnose_c2_replica_sync_materialization_cmd.add_argument("--operator-confirm-host-p2p-port-conflict", action="store_true")
    diagnose_c2_replica_sync_materialization_cmd.add_argument("--write-evidence", action="store_true")
    diagnose_c2_replica_sync_materialization_cmd.add_argument("--require-retry-authorized", action="store_true")
    diagnose_c2_replica_sync_materialization_cmd.add_argument("--require-retry-blocked", action="store_true")

    release_c2_replica_sync_resume = subparsers.add_parser(
        "release-c2-replica-sync-resume",
        help="authorize one deploy/start retry from an already-applied C2 sync-proof Compose",
        allow_abbrev=False,
    )
    _common(release_c2_replica_sync_resume)
    release_c2_replica_sync_resume.add_argument("--diagnostic-evidence", required=True)
    release_c2_replica_sync_resume.add_argument("--acknowledge-c2-replica-sync-diagnostic-sha256", required=True)
    release_c2_replica_sync_resume.add_argument("--diagnostic-max-age-seconds", type=int, default=86400)
    release_c2_replica_sync_resume.add_argument("--expires-in-seconds", type=int, default=300)
    release_c2_replica_sync_resume.add_argument("--created-at")
    release_c2_replica_sync_resume.add_argument("--write-release", action="store_true")

    verify_c2_replica_sync_resume_release_cmd = subparsers.add_parser(
        "verify-c2-replica-sync-resume-release",
        help="verify one expiring C2 replica sync resume release",
        allow_abbrev=False,
    )
    _common(verify_c2_replica_sync_resume_release_cmd)
    verify_c2_replica_sync_resume_release_cmd.add_argument("--release", required=True)
    verify_c2_replica_sync_resume_release_cmd.add_argument("--max-age-seconds", type=int, default=300)
    verify_c2_replica_sync_resume_release_cmd.add_argument("--diagnostic-max-age-seconds", type=int, default=86400)

    apply_c2_replica_sync_resume = subparsers.add_parser(
        "apply-c2-replica-sync-resume",
        help="inspect or execute one C2 replica sync resume release without re-patching Compose",
        allow_abbrev=False,
    )
    _common(apply_c2_replica_sync_resume)
    apply_c2_replica_sync_resume.add_argument("--release", required=True)
    apply_c2_replica_sync_resume.add_argument("--acknowledge-release-sha256", required=True)
    apply_c2_replica_sync_resume.add_argument("--max-age-seconds", type=int, default=300)
    apply_c2_replica_sync_resume.add_argument("--diagnostic-max-age-seconds", type=int, default=86400)
    apply_c2_replica_sync_resume.add_argument("--timeout", type=float, default=30.0)
    apply_c2_replica_sync_resume.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    apply_c2_replica_sync_resume.add_argument("--max-wait-seconds", type=float, default=300.0)
    apply_c2_replica_sync_resume.add_argument("--poll-interval-seconds", type=float, default=5.0)
    apply_c2_replica_sync_resume.add_argument("--execute", action="store_true")


    stage_c2_validator_admission = subparsers.add_parser(
        "stage-c2-validator-admission",
        help="compile the C2 two-voter QBFT validator admission transaction without casting votes",
        allow_abbrev=False,
    )
    _common(stage_c2_validator_admission)
    stage_c2_validator_admission.add_argument("--sync-evidence", required=True)
    stage_c2_validator_admission.add_argument("--max-age-seconds", type=int, default=300)
    stage_c2_validator_admission.add_argument("--created-at")
    stage_c2_validator_admission.add_argument("--write-transaction", action="store_true")

    verify_c2_validator_admission_tx = subparsers.add_parser(
        "verify-c2-validator-admission-transaction",
        help="verify a staged C2 validator-admission transaction",
        allow_abbrev=False,
    )
    _common(verify_c2_validator_admission_tx)
    verify_c2_validator_admission_tx.add_argument("--transaction", required=True)
    verify_c2_validator_admission_tx.add_argument("--max-age-seconds", type=int, default=300)

    release_c2_validator_admission = subparsers.add_parser(
        "release-c2-validator-admission",
        help="release the exact C2 two-voter validator-admission plan",
        allow_abbrev=False,
    )
    _common(release_c2_validator_admission)
    release_c2_validator_admission.add_argument("--transaction", required=True)
    release_c2_validator_admission.add_argument("--acknowledge-c2-validator-admission-transaction-sha256", required=True)
    release_c2_validator_admission.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    release_c2_validator_admission.add_argument("--expires-in-seconds", type=int, default=300)
    release_c2_validator_admission.add_argument("--created-at")
    release_c2_validator_admission.add_argument("--write-release", action="store_true")

    verify_c2_validator_admission_release_cmd = subparsers.add_parser(
        "verify-c2-validator-admission-release",
        help="verify an expiring C2 validator-admission release",
        allow_abbrev=False,
    )
    _common(verify_c2_validator_admission_release_cmd)
    verify_c2_validator_admission_release_cmd.add_argument("--release", required=True)
    verify_c2_validator_admission_release_cmd.add_argument("--max-age-seconds", type=int, default=300)
    verify_c2_validator_admission_release_cmd.add_argument("--transaction-max-age-seconds", type=int, default=86400)

    apply_c2_validator_admission = subparsers.add_parser(
        "apply-c2-validator-admission",
        help="inspect or execute the released C2 two-voter validator-admission plan",
        allow_abbrev=False,
    )
    _common(apply_c2_validator_admission)
    apply_c2_validator_admission.add_argument("--release", required=True)
    apply_c2_validator_admission.add_argument("--acknowledge-release-sha256", required=True)
    apply_c2_validator_admission.add_argument("--max-age-seconds", type=int, default=300)
    apply_c2_validator_admission.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    apply_c2_validator_admission.add_argument("--timeout", type=float, default=30.0)
    apply_c2_validator_admission.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    apply_c2_validator_admission.add_argument("--max-wait-seconds", type=float, default=300.0)
    apply_c2_validator_admission.add_argument("--poll-interval-seconds", type=float, default=5.0)
    apply_c2_validator_admission.add_argument("--execute", action="store_true")

    verify_c2_validator_admission_evidence_cmd = subparsers.add_parser(
        "verify-c2-validator-admission-evidence",
        help="verify persisted C2 validator-admission evidence",
        allow_abbrev=False,
    )
    _common(verify_c2_validator_admission_evidence_cmd)
    verify_c2_validator_admission_evidence_cmd.add_argument("--evidence", required=True)
    verify_c2_validator_admission_evidence_cmd.add_argument("--max-age-seconds", type=int, default=300)

    stage_t3_post_admission_steady_state = subparsers.add_parser(
        "stage-t3-post-admission-steady-state",
        help="compile a read-only T3 post-C2-admission steady-state observation transaction",
        allow_abbrev=False,
    )
    _common(stage_t3_post_admission_steady_state)
    stage_t3_post_admission_steady_state.add_argument("--admission-evidence", required=True)
    stage_t3_post_admission_steady_state.add_argument("--max-age-seconds", type=int, default=86400)
    stage_t3_post_admission_steady_state.add_argument("--created-at")
    stage_t3_post_admission_steady_state.add_argument("--write-transaction", action="store_true")

    verify_t3_post_admission_steady_state_tx = subparsers.add_parser(
        "verify-t3-post-admission-steady-state-transaction",
        help="verify a staged T3 post-admission steady-state transaction",
        allow_abbrev=False,
    )
    _common(verify_t3_post_admission_steady_state_tx)
    verify_t3_post_admission_steady_state_tx.add_argument("--transaction", required=True)
    verify_t3_post_admission_steady_state_tx.add_argument("--max-age-seconds", type=int, default=300)

    release_t3_post_admission_steady_state = subparsers.add_parser(
        "release-t3-post-admission-steady-state",
        help="release the exact read-only T3 post-admission steady-state observation plan",
        allow_abbrev=False,
    )
    _common(release_t3_post_admission_steady_state)
    release_t3_post_admission_steady_state.add_argument("--transaction", required=True)
    release_t3_post_admission_steady_state.add_argument("--acknowledge-t3-post-admission-steady-state-transaction-sha256", required=True)
    release_t3_post_admission_steady_state.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    release_t3_post_admission_steady_state.add_argument("--expires-in-seconds", type=int, default=300)
    release_t3_post_admission_steady_state.add_argument("--created-at")
    release_t3_post_admission_steady_state.add_argument("--write-release", action="store_true")

    verify_t3_post_admission_steady_state_release_cmd = subparsers.add_parser(
        "verify-t3-post-admission-steady-state-release",
        help="verify an expiring T3 post-admission steady-state release",
        allow_abbrev=False,
    )
    _common(verify_t3_post_admission_steady_state_release_cmd)
    verify_t3_post_admission_steady_state_release_cmd.add_argument("--release", required=True)
    verify_t3_post_admission_steady_state_release_cmd.add_argument("--max-age-seconds", type=int, default=300)
    verify_t3_post_admission_steady_state_release_cmd.add_argument("--transaction-max-age-seconds", type=int, default=86400)

    apply_t3_post_admission_steady_state = subparsers.add_parser(
        "apply-t3-post-admission-steady-state",
        help="inspect or execute read-only T3 post-admission steady-state observations",
        allow_abbrev=False,
    )
    _common(apply_t3_post_admission_steady_state)
    apply_t3_post_admission_steady_state.add_argument("--release", required=True)
    apply_t3_post_admission_steady_state.add_argument("--acknowledge-release-sha256", required=True)
    apply_t3_post_admission_steady_state.add_argument("--max-age-seconds", type=int, default=300)
    apply_t3_post_admission_steady_state.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    apply_t3_post_admission_steady_state.add_argument("--timeout", type=float, default=30.0)
    apply_t3_post_admission_steady_state.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    apply_t3_post_admission_steady_state.add_argument("--window-count", type=int, default=2)
    apply_t3_post_admission_steady_state.add_argument("--window-seconds", type=float, default=60.0)
    apply_t3_post_admission_steady_state.add_argument("--execute", action="store_true")

    verify_t3_post_admission_steady_state_evidence_cmd = subparsers.add_parser(
        "verify-t3-post-admission-steady-state-evidence",
        help="verify persisted T3 post-admission steady-state evidence",
        allow_abbrev=False,
    )
    _common(verify_t3_post_admission_steady_state_evidence_cmd)
    verify_t3_post_admission_steady_state_evidence_cmd.add_argument("--evidence", required=True)
    verify_t3_post_admission_steady_state_evidence_cmd.add_argument("--max-age-seconds", type=int, default=300)
    verify_t3_post_admission_steady_state_evidence_cmd.add_argument("--transaction-max-age-seconds", type=int, default=86400)

    verify_coolify_service_lifecycle_probe = subparsers.add_parser(
        "verify-coolify-service-lifecycle-probe-evidence",
        help="verify persisted no-secret no-chain Coolify service lifecycle probe evidence",
        allow_abbrev=False,
    )
    _common(verify_coolify_service_lifecycle_probe)
    verify_coolify_service_lifecycle_probe.add_argument("--evidence", required=True)
    verify_coolify_service_lifecycle_probe.add_argument("--max-age-seconds", type=int, default=86400)

    return parser


def _paths(args: argparse.Namespace):
    return MotherPaths(runtime_state_root=Path(args.runtime_state_root)).resolve_private_state_paths()


def _load(args: argparse.Namespace):
    operation = _operation(args.command, args.network, args.operation_id)
    paths = _paths(args)
    private_state = read_private_state(paths, operation=operation)
    return private_state


def _cmd_plan(args: argparse.Namespace, private_state) -> int:
    plan = build_starter_deployment_plan(
        private_state,
        network=args.network,
        selected_nodes=_selected_nodes(args.node),
    )
    print(json.dumps(plan, indent=2, sort_keys=True))
    if args.require_ready and not plan["summary"]["ready_for_execution"]:
        return 1
    return 0


def _cmd_preflight(args: argparse.Namespace, private_state) -> int:
    report = run_starter_deployment_preflight(
        private_state,
        network=args.network,
        selected_nodes=_selected_nodes(args.node),
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        max_items=args.max_items,
    )
    if args.write_evidence:
        path, digest = write_deployment_preflight_evidence(
            _paths(args), report, operation=_operation("preflight-evidence", args.network, args.operation_id)
        )
        report = {**report, "evidence": {"path": str(path), "sha256": digest}}
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.require_clean and not report["summary"]["clean"]:
        return 1
    return 0


def _cmd_verify_preflight(args: argparse.Namespace, private_state) -> int:
    try:
        result = verify_deployment_preflight_evidence(
            _paths(args),
            private_state,
            Path(args.evidence),
            max_age_seconds=args.max_age_seconds,
            selected_nodes=_selected_nodes(args.node),
        )
    except MotherDeploymentPreflightError:
        if args.require_clean:
            return 1
        raise
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_prepare_execution(args: argparse.Namespace, private_state) -> int:
    request = build_deployment_execution_request(
        _paths(args),
        private_state,
        Path(args.evidence),
        network=args.network,
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        created_at=args.created_at,
    )
    if args.write_request:
        path, digest = write_deployment_execution_request(
            _paths(args),
            request,
            operation=_operation("execution-request", args.network, args.operation_id),
        )
        request = {**request, "request_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(request, indent=2, sort_keys=True))
    return 0


def _cmd_verify_execution(args: argparse.Namespace, private_state) -> int:
    result = verify_deployment_execution_request(
        _paths(args),
        private_state,
        Path(args.request),
        max_age_seconds=args.max_age_seconds,
        selected_nodes=_selected_nodes(args.node),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_stage_mutation(args: argparse.Namespace, private_state) -> int:
    transaction = build_deployment_mutation_transaction(
        _paths(args),
        private_state,
        Path(args.request),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        created_at=args.created_at,
    )
    if args.write_transaction:
        path, digest = write_deployment_mutation_transaction(
            _paths(args),
            transaction,
            operation=_operation("deployment-transaction", args.network, args.operation_id),
        )
        transaction = {
            **transaction,
            "transaction_artifact": {"path": str(path), "sha256": digest},
        }
    print(json.dumps(transaction, indent=2, sort_keys=True))
    return 0


def _cmd_verify_mutation(args: argparse.Namespace, private_state) -> int:
    result = verify_deployment_mutation_transaction(
        _paths(args),
        private_state,
        Path(args.transaction),
        max_age_seconds=args.max_age_seconds,
        selected_nodes=_selected_nodes(args.node),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_mutation(args: argparse.Namespace, private_state) -> int:
    release = build_deployment_mutation_release(
        _paths(args),
        private_state,
        Path(args.transaction),
        acknowledged_transaction_sha256=args.acknowledge_transaction_sha256,
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_deployment_mutation_release(
            _paths(args),
            release,
            operation=_operation("deployment-release", args.network, args.operation_id),
        )
        release = {
            **release,
            "release_artifact": {"path": str(path), "sha256": digest},
        }
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_release(args: argparse.Namespace, private_state) -> int:
    result = verify_deployment_mutation_release(
        _paths(args),
        private_state,
        Path(args.release),
        max_age_seconds=args.max_age_seconds,
        selected_nodes=_selected_nodes(args.node),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_mutation(args: argparse.Namespace, private_state) -> int:
    common = {
        "acknowledged_release_sha256": args.acknowledge_release_sha256,
        "selected_nodes": _selected_nodes(args.node),
        "max_age_seconds": args.max_age_seconds,
    }
    if not args.execute:
        result = inspect_released_mutation(
            _paths(args),
            private_state,
            Path(args.release),
            **common,
        )
        result = {**result, "execute_requested": False, "live_mutation_performed": False}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    result = execute_released_mutation(
        _paths(args),
        private_state,
        Path(args.release),
        **common,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        operation=_operation("apply-mutation", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_rollback_mutation(args: argparse.Namespace, private_state) -> int:
    common = {
        "acknowledged_execution_sha256": args.acknowledge_execution_sha256,
    }
    if not args.execute:
        result = inspect_deployment_mutation_rollback(
            _paths(args),
            private_state,
            Path(args.execution),
            **common,
        )
        result = {**result, "execute_requested": False, "live_mutation_performed": False}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    result = execute_deployment_mutation_rollback(
        _paths(args),
        private_state,
        Path(args.execution),
        **common,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        operation=_operation("rollback-mutation", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_recover_mutation_rollback(args: argparse.Namespace, private_state) -> int:
    common = {
        "acknowledged_journal_sha256": args.acknowledge_journal_sha256,
    }
    if not args.execute:
        result = inspect_deployment_rollback_journal(
            _paths(args),
            private_state,
            Path(args.journal),
            **common,
        )
        result = {**result, "execute_requested": False, "live_mutation_performed": False}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    result = execute_deployment_journal_rollback(
        _paths(args),
        private_state,
        Path(args.journal),
        **common,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        operation=_operation("recover-mutation-rollback", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_verify_mutation_rollback(args: argparse.Namespace, private_state) -> int:
    result = verify_deployment_mutation_rollback(
        _paths(args),
        private_state,
        Path(args.rollback_result),
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("clean") is True else 1


def _cmd_verify_standby(args: argparse.Namespace, private_state) -> int:
    result = run_deployment_standby_verification(
        _paths(args),
        private_state,
        Path(args.execution),
        network=args.network,
        selected_nodes=_selected_nodes(args.node),
        observed_at=args.observed_at,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
    )
    if args.write_evidence:
        path, digest = write_deployment_standby_verification(
            _paths(args),
            result,
            operation=_operation("standby-evidence", args.network, args.operation_id),
        )
        result = {**result, "evidence": {"path": str(path), "sha256": digest}}
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.require_clean and result["summary"]["clean"] is not True:
        return 1
    return 0


def _cmd_verify_standby_evidence(args: argparse.Namespace, private_state) -> int:
    result = verify_deployment_standby_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_stage_identity(args: argparse.Namespace, private_state) -> int:
    transaction = build_deployment_identity_install_transaction(
        _paths(args),
        private_state,
        Path(args.standby_evidence),
        network=args.network,
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        created_at=args.created_at,
    )
    if args.write_transaction:
        path, digest = write_deployment_identity_install_transaction(
            _paths(args),
            transaction,
            operation=_operation("identity-transaction", args.network, args.operation_id),
        )
        transaction = {
            **transaction,
            "transaction_artifact": {"path": str(path), "sha256": digest},
        }
    print(json.dumps(transaction, indent=2, sort_keys=True))
    return 0


def _cmd_verify_identity_transaction(args: argparse.Namespace, private_state) -> int:
    result = verify_deployment_identity_install_transaction(
        _paths(args),
        private_state,
        Path(args.transaction),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_identity(args: argparse.Namespace, private_state) -> int:
    release = build_deployment_identity_release(
        _paths(args),
        private_state,
        Path(args.transaction),
        acknowledged_identity_transaction_sha256=args.acknowledge_identity_transaction_sha256,
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_deployment_identity_release(
            _paths(args),
            release,
            operation=_operation("identity-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_identity_release(args: argparse.Namespace, private_state) -> int:
    result = verify_deployment_identity_release(
        _paths(args),
        private_state,
        Path(args.release),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_identity(args: argparse.Namespace, private_state) -> int:
    common = {
        "acknowledged_release_sha256": args.acknowledge_release_sha256,
        "selected_nodes": _selected_nodes(args.node),
        "max_age_seconds": args.max_age_seconds,
    }
    if not args.execute:
        result = inspect_released_identity(
            _paths(args),
            private_state,
            Path(args.release),
            **common,
        )
        result = {**result, "execute_requested": False, "live_mutation_performed": False}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_released_identity(
        _paths(args),
        private_state,
        Path(args.release),
        **common,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        operation=_operation("apply-identity", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_rollback_identity(args: argparse.Namespace, private_state) -> int:
    common = {
        "acknowledged_execution_sha256": args.acknowledge_execution_sha256,
        "timeout": args.timeout,
        "max_response_bytes": args.max_response_bytes,
        "operation": _operation("rollback-identity", args.network, args.operation_id),
    }
    if not args.execute:
        result = inspect_identity_mutation_rollback(
            _paths(args),
            private_state,
            Path(args.execution),
            **common,
        )
        result = {
            **result,
            "execute_requested": False,
            "live_mutation_performed": False,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_identity_mutation_rollback(
        _paths(args),
        private_state,
        Path(args.execution),
        **common,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_recover_identity_rollback(args: argparse.Namespace, private_state) -> int:
    common = {
        "acknowledged_journal_sha256": args.acknowledge_journal_sha256,
    }
    if not args.execute:
        result = inspect_identity_rollback_journal(
            _paths(args),
            private_state,
            Path(args.journal),
            **common,
        )
        result = {
            **result,
            "execute_requested": False,
            "live_mutation_performed": False,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_identity_journal_rollback(
        _paths(args),
        private_state,
        Path(args.journal),
        **common,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        operation=_operation("recover-identity-rollback", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_verify_identity_rollback(args: argparse.Namespace, private_state) -> int:
    result = verify_identity_mutation_rollback(
        _paths(args),
        private_state,
        Path(args.rollback_result),
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        observed_at=args.observed_at,
    )
    if args.write_evidence:
        path, digest = write_identity_mutation_rollback_verification(
            _paths(args),
            result,
            operation=_operation("verify-identity-rollback", args.network, args.operation_id),
        )
        result = {
            **result,
            "evidence_artifact": {"path": str(path), "sha256": digest},
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("clean") is True else 1


def _cmd_stage_genesis(args: argparse.Namespace, private_state) -> int:
    transaction = build_deployment_genesis_transaction(
        _paths(args),
        private_state,
        Path(args.identity_execution),
        identity_rollback_verification_path=Path(args.identity_rollback_verification),
        network=args.network,
        selected_nodes=_selected_nodes(args.node),
        created_at=args.created_at,
    )
    if args.write_transaction:
        path, digest = write_deployment_genesis_transaction(
            _paths(args),
            transaction,
            operation=_operation("genesis-transaction", args.network, args.operation_id),
        )
        transaction = {
            **transaction,
            "transaction_artifact": {"path": str(path), "sha256": digest},
        }
    print(json.dumps(transaction, indent=2, sort_keys=True))
    return 0


def _cmd_verify_genesis_transaction(args: argparse.Namespace, private_state) -> int:
    result = verify_deployment_genesis_transaction(
        _paths(args),
        private_state,
        Path(args.transaction),
        selected_nodes=_selected_nodes(args.node),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_genesis(args: argparse.Namespace, private_state) -> int:
    release = build_deployment_genesis_release(
        _paths(args),
        private_state,
        Path(args.transaction),
        acknowledged_genesis_transaction_sha256=args.acknowledge_genesis_transaction_sha256,
        hub_git_repository=args.hub_git_repository,
        hub_git_ref=(args.hub_git_commit_sha or args.hub_git_ref),
        selected_nodes=_selected_nodes(args.node),
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_deployment_genesis_release(
            _paths(args),
            release,
            operation=_operation("genesis-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_genesis_release(args: argparse.Namespace, private_state) -> int:
    result = verify_deployment_genesis_release(
        _paths(args),
        private_state,
        Path(args.release),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_genesis(args: argparse.Namespace, private_state) -> int:
    common = {
        "acknowledged_release_sha256": args.acknowledge_release_sha256,
        "selected_nodes": _selected_nodes(args.node),
        "max_age_seconds": args.max_age_seconds,
    }
    if not args.execute:
        result = inspect_released_genesis(
            _paths(args), private_state, Path(args.release), **common
        )
        result = {**result, "execute_requested": False, "live_mutation_performed": False}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_released_genesis(
        _paths(args),
        private_state,
        Path(args.release),
        **common,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        operation=_operation("apply-genesis", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_rollback_genesis(args: argparse.Namespace, private_state) -> int:
    common = {
        "acknowledged_execution_sha256": args.acknowledge_execution_sha256,
    }
    if not args.execute:
        result = inspect_genesis_mutation_rollback(
            _paths(args), private_state, Path(args.execution), **common
        )
        result = {
            **result,
            "execute_requested": False,
            "live_mutation_performed": False,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_genesis_mutation_rollback(
        _paths(args),
        private_state,
        Path(args.execution),
        **common,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        max_wait_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        operation=_operation("rollback-genesis", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_recover_genesis_rollback(args: argparse.Namespace, private_state) -> int:
    if not args.execute:
        result = inspect_genesis_rollback_journal(
            _paths(args),
            private_state,
            Path(args.journal),
            acknowledged_journal_sha256=args.acknowledge_journal_sha256,
        )
        result = {
            **result,
            "execute_requested": False,
            "live_mutation_performed": False,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_genesis_journal_rollback(
        _paths(args),
        private_state,
        Path(args.journal),
        acknowledged_journal_sha256=args.acknowledge_journal_sha256,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        max_wait_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        operation=_operation("recover-genesis-rollback", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_verify_genesis_rollback(args: argparse.Namespace, private_state) -> int:
    result = verify_genesis_mutation_rollback(
        _paths(args),
        private_state,
        Path(args.rollback_result),
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        observed_at=args.observed_at,
    )
    if args.write_evidence:
        path, digest = write_genesis_mutation_rollback_verification(
            _paths(args),
            result,
            operation=_operation("verify-genesis-rollback", args.network, args.operation_id),
        )
        result = {**result, "evidence_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_genesis_birth(args: argparse.Namespace, private_state) -> int:
    release = build_genesis_birth_release(
        _paths(args),
        private_state,
        Path(args.execution),
        acknowledged_genesis_execution_sha256=args.acknowledge_genesis_execution_sha256,
        genesis_rollback_verification_path=Path(args.genesis_rollback_verification),
        selected_nodes=_selected_nodes(args.node),
        superseded_service_uuid=args.superseded_service_uuid,
        acknowledged_superseded_service_removal=(
            args.acknowledge_superseded_service_removal
        ),
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_genesis_birth_release(
            _paths(args), release,
            operation=_operation("genesis-birth-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_genesis_birth_release(args: argparse.Namespace, private_state) -> int:
    result = verify_genesis_birth_release(
        _paths(args), private_state, Path(args.release),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_genesis_birth(args: argparse.Namespace, private_state) -> int:
    common = {
        "acknowledged_release_sha256": args.acknowledge_release_sha256,
        "selected_nodes": _selected_nodes(args.node),
        "max_age_seconds": args.max_age_seconds,
    }
    if not args.execute:
        result = inspect_genesis_birth_release(
            _paths(args), private_state, Path(args.release), **common
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_genesis_birth_release(
        _paths(args), private_state, Path(args.release), **common,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        max_wait_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        operation=_operation("apply-genesis-birth", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_verify_genesis_birth_evidence(args: argparse.Namespace, private_state) -> int:
    result = verify_genesis_birth_evidence(
        _paths(args), private_state, Path(args.evidence),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_stage_soft_replica(args: argparse.Namespace, private_state) -> int:
    transaction = build_soft_replica_transaction(
        _paths(args),
        private_state,
        Path(args.birth_evidence),
        network=args.network,
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        created_at=args.created_at,
    )
    if args.write_transaction:
        path, digest = write_soft_replica_transaction(
            _paths(args),
            transaction,
            operation=_operation("soft-replica-transaction", args.network, args.operation_id),
        )
        transaction = {**transaction, "transaction_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(transaction, indent=2, sort_keys=True))
    return 0


def _cmd_verify_soft_replica_transaction(args: argparse.Namespace, private_state) -> int:
    result = verify_soft_replica_transaction(
        _paths(args),
        private_state,
        Path(args.transaction),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_soft_replica(args: argparse.Namespace, private_state) -> int:
    release = build_soft_replica_release(
        _paths(args), private_state, Path(args.transaction),
        acknowledged_transaction_sha256=args.acknowledge_soft_replica_transaction_sha256,
        selected_nodes=_selected_nodes(args.node),
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_soft_replica_release(
            _paths(args), release,
            operation=_operation("soft-replica-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_soft_replica_release(args: argparse.Namespace, private_state) -> int:
    result = verify_soft_replica_release(
        _paths(args), private_state, Path(args.release),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_soft_replica(args: argparse.Namespace, private_state) -> int:
    common = dict(
        acknowledged_release_sha256=args.acknowledge_release_sha256,
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
    )
    if not args.execute:
        result = inspect_released_soft_replica(_paths(args), private_state, Path(args.release), **common)
        result["execute_requested"] = False
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_released_soft_replica(
        _paths(args), private_state, Path(args.release), **common,
        timeout=args.timeout, max_response_bytes=args.max_response_bytes,
        operation=_operation("apply-soft-replica", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_release_soft_replica_sync(args: argparse.Namespace, private_state) -> int:
    release = build_soft_replica_sync_release(
        _paths(args), private_state, Path(args.execution),
        acknowledged_soft_replica_execution_sha256=args.acknowledge_soft_replica_execution_sha256,
        selected_nodes=_selected_nodes(args.node),
        execution_max_age_seconds=args.execution_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_soft_replica_sync_release(
            _paths(args), release,
            operation=_operation("soft-replica-sync-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_soft_replica_sync_release(args: argparse.Namespace, private_state) -> int:
    result = verify_soft_replica_sync_release(
        _paths(args), private_state, Path(args.release),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        execution_max_age_seconds=args.execution_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_soft_replica_sync(args: argparse.Namespace, private_state) -> int:
    common = dict(
        acknowledged_release_sha256=args.acknowledge_release_sha256,
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        execution_max_age_seconds=args.execution_max_age_seconds,
    )
    if not args.execute:
        result = inspect_soft_replica_sync_release(
            _paths(args), private_state, Path(args.release), **common
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_soft_replica_sync_release(
        _paths(args), private_state, Path(args.release), **common,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        max_wait_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        operation=_operation("apply-soft-replica-sync", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_verify_soft_replica_sync_evidence(args: argparse.Namespace, private_state) -> int:
    result = verify_soft_replica_sync_evidence(
        _paths(args), private_state, Path(args.evidence),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_stage_validator_admission(args: argparse.Namespace, private_state) -> int:
    transaction = build_validator_admission_transaction(
        _paths(args),
        private_state,
        Path(args.sync_evidence),
        network=args.network,
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        created_at=args.created_at,
    )
    if args.write_transaction:
        path, digest = write_validator_admission_transaction(
            _paths(args),
            transaction,
            operation=_operation("validator-admission-transaction", args.network, args.operation_id),
        )
        transaction = {**transaction, "transaction_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(transaction, indent=2, sort_keys=True))
    return 0


def _cmd_verify_validator_admission_transaction(args: argparse.Namespace, private_state) -> int:
    result = verify_validator_admission_transaction(
        _paths(args),
        private_state,
        Path(args.transaction),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_validator_admission(args: argparse.Namespace, private_state) -> int:
    release = build_validator_admission_release(
        _paths(args),
        private_state,
        Path(args.transaction),
        acknowledged_transaction_sha256=args.acknowledge_validator_admission_transaction_sha256,
        selected_nodes=_selected_nodes(args.node),
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
        failed_evidence_path=Path(args.failed_evidence) if args.failed_evidence else None,
    )
    if args.write_release:
        path, digest = write_validator_admission_release(
            _paths(args),
            release,
            operation=_operation("validator-admission-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_validator_admission_release(args: argparse.Namespace, private_state) -> int:
    result = verify_validator_admission_release(
        _paths(args),
        private_state,
        Path(args.release),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_validator_admission(args: argparse.Namespace, private_state) -> int:
    common = dict(
        acknowledged_release_sha256=args.acknowledge_release_sha256,
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
    )
    if not args.execute:
        result = inspect_validator_admission_release(
            _paths(args), private_state, Path(args.release), **common
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_validator_admission_release(
        _paths(args),
        private_state,
        Path(args.release),
        **common,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        max_wait_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        operation=_operation("apply-validator-admission", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_verify_validator_admission_evidence(args: argparse.Namespace, private_state) -> int:
    result = verify_validator_admission_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_validator_quorum_recovery(args: argparse.Namespace, private_state) -> int:
    release = build_validator_quorum_recovery_release(
        _paths(args), private_state, Path(args.transaction),
        acknowledged_transaction_sha256=args.acknowledge_validator_admission_transaction_sha256,
        selected_nodes=_selected_nodes(args.node),
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_validator_quorum_recovery_release(
            _paths(args), release,
            operation=_operation("validator-quorum-recovery-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_validator_quorum_recovery_release(args: argparse.Namespace, private_state) -> int:
    result = verify_validator_quorum_recovery_release(
        _paths(args), private_state, Path(args.release),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_validator_quorum_recovery(args: argparse.Namespace, private_state) -> int:
    common = dict(
        acknowledged_release_sha256=args.acknowledge_release_sha256,
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
    )
    if not args.execute:
        result = inspect_validator_quorum_recovery_release(
            _paths(args), private_state, Path(args.release), **common
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_validator_quorum_recovery_release(
        _paths(args), private_state, Path(args.release), **common,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        max_wait_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        operation=_operation("apply-validator-quorum-recovery", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_reconcile_validator_quorum_recovery(args: argparse.Namespace, private_state) -> int:
    result = reconcile_validator_quorum_recovery(
        _paths(args),
        private_state,
        Path(args.evidence),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        operation=_operation("reconcile-validator-quorum-recovery", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_verify_validator_quorum_recovery_reconciliation(args: argparse.Namespace, private_state) -> int:
    result = verify_validator_quorum_recovery_reconciliation(
        _paths(args),
        private_state,
        Path(args.reconciliation),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_diagnose_validator_quorum_runtime(args: argparse.Namespace, private_state) -> int:
    result = diagnose_validator_quorum_runtime(
        _paths(args),
        private_state,
        Path(args.evidence),
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        operation=_operation("diagnose-validator-quorum-runtime", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_verify_validator_quorum_recovery_evidence(args: argparse.Namespace, private_state) -> int:
    result = verify_validator_quorum_recovery_evidence(
        _paths(args), private_state, Path(args.evidence),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_stage_post_admission_steady_state(args: argparse.Namespace, private_state) -> int:
    transaction = build_post_admission_steady_state_transaction(
        _paths(args),
        private_state,
        Path(args.reconciliation) if args.reconciliation else None,
        quorum_evidence_path=Path(args.quorum_evidence) if args.quorum_evidence else None,
        network=args.network,
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        created_at=args.created_at,
    )
    if args.write_transaction:
        path, digest = write_post_admission_steady_state_transaction(
            _paths(args),
            transaction,
            operation=_operation("post-admission-steady-state-transaction", args.network, args.operation_id),
        )
        transaction = {**transaction, "transaction_artifact": {"path": str(path), "sha256": digest}}
    output = (
        transaction
        if args.full_output or not args.write_transaction
        else _compact_post_admission_artifact(transaction)
    )
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _cmd_verify_post_admission_steady_state_transaction(args: argparse.Namespace, private_state) -> int:
    result = verify_post_admission_steady_state_transaction(
        _paths(args),
        private_state,
        Path(args.transaction),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_post_admission_steady_state(args: argparse.Namespace, private_state) -> int:
    release = build_post_admission_steady_state_release(
        _paths(args),
        private_state,
        Path(args.transaction),
        acknowledged_transaction_sha256=args.acknowledge_post_admission_steady_state_transaction_sha256,
        selected_nodes=_selected_nodes(args.node),
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_post_admission_steady_state_release(
            _paths(args),
            release,
            operation=_operation("post-admission-steady-state-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    output = (
        release
        if args.full_output or not args.write_release
        else _compact_post_admission_artifact(release)
    )
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _cmd_verify_post_admission_steady_state_release(args: argparse.Namespace, private_state) -> int:
    result = verify_post_admission_steady_state_release(
        _paths(args),
        private_state,
        Path(args.release),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_post_admission_steady_state(args: argparse.Namespace, private_state) -> int:
    common = dict(
        acknowledged_release_sha256=args.acknowledge_release_sha256,
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
    )
    if not args.execute:
        result = inspect_post_admission_steady_state_release(
            _paths(args), private_state, Path(args.release), **common
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_post_admission_steady_state_release(
        _paths(args),
        private_state,
        Path(args.release),
        **common,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        max_wait_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        operation=_operation("apply-post-admission-steady-state", args.network, args.operation_id),
    )
    output = result if args.full_output else _compact_post_admission_execution(result)
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_verify_post_admission_steady_state_evidence(args: argparse.Namespace, private_state) -> int:
    result = verify_post_admission_steady_state_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_reconcile_post_admission_steady_state(args: argparse.Namespace, private_state) -> int:
    result = reconcile_post_admission_steady_state(
        _paths(args),
        private_state,
        Path(args.evidence),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        operation=_operation(
            "reconcile-post-admission-steady-state",
            args.network,
            args.operation_id,
        ),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_verify_post_admission_steady_state_reconciliation(
    args: argparse.Namespace,
    private_state,
) -> int:
    result = verify_post_admission_steady_state_reconciliation(
        _paths(args),
        private_state,
        Path(args.reconciliation),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0



def _cmd_stage_post_admission_steady_state_continuation(
    args: argparse.Namespace,
    private_state,
) -> int:
    transaction = build_post_admission_steady_state_continuation_transaction(
        _paths(args),
        private_state,
        Path(args.reconciliation),
        network=args.network,
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        created_at=args.created_at,
    )
    if args.write_transaction:
        path, digest = (
            write_post_admission_steady_state_continuation_transaction(
                _paths(args),
                transaction,
                operation=_operation(
                    "post-admission-steady-state-continuation-transaction",
                    args.network,
                    args.operation_id,
                ),
            )
        )
        transaction = {
            **transaction,
            "transaction_artifact": {
                "path": str(path),
                "sha256": digest,
            },
        }
    output = (
        transaction
        if args.full_output or not args.write_transaction
        else _compact_post_admission_artifact(transaction)
    )
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _cmd_verify_post_admission_steady_state_continuation_transaction(
    args: argparse.Namespace,
    private_state,
) -> int:
    result = verify_post_admission_steady_state_continuation_transaction(
        _paths(args),
        private_state,
        Path(args.transaction),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_post_admission_steady_state_continuation(
    args: argparse.Namespace,
    private_state,
) -> int:
    release = build_post_admission_steady_state_continuation_release(
        _paths(args),
        private_state,
        Path(args.transaction),
        acknowledged_transaction_sha256=(
            args.acknowledge_post_admission_steady_state_continuation_transaction_sha256
        ),
        selected_nodes=_selected_nodes(args.node),
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_post_admission_steady_state_continuation_release(
            _paths(args),
            release,
            operation=_operation(
                "post-admission-steady-state-continuation-release",
                args.network,
                args.operation_id,
            ),
        )
        release = {
            **release,
            "release_artifact": {
                "path": str(path),
                "sha256": digest,
            },
        }
    output = (
        release
        if args.full_output or not args.write_release
        else _compact_post_admission_artifact(release)
    )
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _cmd_verify_post_admission_steady_state_continuation_release(
    args: argparse.Namespace,
    private_state,
) -> int:
    result = verify_post_admission_steady_state_continuation_release(
        _paths(args),
        private_state,
        Path(args.release),
        selected_nodes=_selected_nodes(args.node),
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_post_admission_steady_state_continuation(
    args: argparse.Namespace,
    private_state,
) -> int:
    common = dict(
        acknowledged_release_sha256=args.acknowledge_release_sha256,
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
    )
    if not args.execute:
        result = inspect_post_admission_steady_state_continuation_release(
            _paths(args),
            private_state,
            Path(args.release),
            **common,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_post_admission_steady_state_continuation_release(
        _paths(args),
        private_state,
        Path(args.release),
        **common,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        max_wait_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        operation=_operation(
            "apply-post-admission-steady-state-continuation",
            args.network,
            args.operation_id,
        ),
    )
    output = (
        result
        if args.full_output
        else _compact_post_admission_execution(result)
    )
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_verify_post_admission_steady_state_continuation_evidence(
    args: argparse.Namespace,
    private_state,
) -> int:
    result = verify_post_admission_steady_state_continuation_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_run_mainnet_steady_state_soak(
    args: argparse.Namespace,
    private_state,
) -> int:
    _warn_mainnet_soak_out_of_date("run-mainnet-steady-state-soak")
    result = run_mainnet_steady_state_soak(
        _paths(args),
        private_state,
        Path(args.baseline_evidence),
        selected_nodes=_selected_nodes(args.node),
        baseline_max_age_seconds=args.baseline_max_age_seconds,
        duration_seconds=args.duration_seconds,
        observation_interval_seconds=args.observation_interval_seconds,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        operation=_operation(
            "run-mainnet-steady-state-soak",
            args.network,
            args.operation_id,
        ),
    )
    output = result if args.full_output else {
        "status": result["status"],
        "network": result["network"],
        "nodes": result["nodes"],
        "chain_id": result["chain_id"],
        "genesis_sha256": result["genesis_sha256"],
        "validator_set": result["validator_set"],
        "summary": result["summary"],
        "timing": result["timing"],
        "evidence": result["evidence"],
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_verify_mainnet_steady_state_soak_evidence(
    args: argparse.Namespace,
    private_state,
) -> int:
    _warn_mainnet_soak_out_of_date("verify-mainnet-steady-state-soak-evidence")
    result = verify_mainnet_steady_state_soak_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0



def _cmd_reserve_validator_rpc_canary_identity(
    args: argparse.Namespace,
    private_state,
) -> int:
    operation = _operation(
        "reserve-validator-rpc-canary-identity",
        args.network,
        args.operation_id,
    )
    if args.write_identity:
        result = reserve_validator_rpc_canary_identity(
            _paths(args),
            private_state,
            canary_name=args.canary_name,
            network=args.network,
            operation=operation,
        )
    else:
        result = inspect_validator_rpc_canary_identity_reservation(
            _paths(args),
            private_state,
            canary_name=args.canary_name,
            network=args.network,
            operation=operation,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_verify_validator_rpc_canary_identity(
    args: argparse.Namespace,
    private_state,
) -> int:
    result = verify_validator_rpc_canary_identity(
        _paths(args),
        private_state,
        Path(args.identity),
        network=args.network,
        canary_name=args.canary_name,
        operation=_operation(
            "verify-validator-rpc-canary-identity",
            args.network,
            args.operation_id,
        ),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_stage_validator_rpc_canary_transaction(
    args: argparse.Namespace,
    private_state,
) -> int:
    operation = _operation(
        "stage-validator-rpc-canary-transaction",
        args.network,
        args.operation_id,
    )
    transaction = build_validator_rpc_canary_transaction(
        _paths(args),
        private_state,
        Path(args.soak_evidence),
        Path(args.identity),
        canary_name=args.canary_name,
        environment_name=args.environment_name,
        foundry_image=args.foundry_image,
        network=args.network,
        selected_nodes=_selected_nodes(args.node),
        soak_max_age_seconds=args.soak_max_age_seconds,
        operation=operation,
    )
    result = dict(transaction)
    if args.write_transaction:
        path, digest = write_validator_rpc_canary_transaction(
            _paths(args),
            transaction,
            operation=operation,
        )
        result["transaction_artifact"] = {
            "path": str(path),
            "sha256": digest,
        }
    if args.full_output:
        output = result
    else:
        output = {
            "status": "pass",
            "network": result["network"],
            "staged_scope": result["staged_scope"],
            "chain": result["chain"],
            "identity": result["identity"],
            "validator_services": result["validator_services"],
            "canary_contract": result["canary_contract"],
            "fee_policy": result["fee_policy"],
            "required_secret_bindings": result["required_secret_bindings"],
            "authority": result["authority"],
            "summary": result["summary"],
            "transaction_sha256": result["validator_rpc_canary_transaction_sha256"],
            **(
                {"transaction_artifact": result["transaction_artifact"]}
                if "transaction_artifact" in result
                else {}
            ),
        }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _cmd_verify_validator_rpc_canary_transaction(
    args: argparse.Namespace,
    private_state,
) -> int:
    result = verify_validator_rpc_canary_transaction(
        _paths(args),
        private_state,
        Path(args.transaction),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        soak_max_age_seconds=args.soak_max_age_seconds,
        operation=_operation(
            "verify-validator-rpc-canary-transaction",
            args.network,
            args.operation_id,
        ),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_validator_rpc_canary(
    args: argparse.Namespace,
    private_state,
) -> int:
    operation = _operation(
        "release-validator-rpc-canary",
        args.network,
        args.operation_id,
    )
    release = build_validator_rpc_canary_release(
        _paths(args),
        private_state,
        Path(args.transaction),
        Path(args.funding_evidence),
        acknowledged_transaction_sha256=(
            args.acknowledge_validator_rpc_canary_transaction_sha256
        ),
        selected_nodes=_selected_nodes(args.node),
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        funding_evidence_max_age_seconds=args.funding_evidence_max_age_seconds,
        funding_transaction_max_age_seconds=args.funding_transaction_max_age_seconds,
        soak_max_age_seconds=args.soak_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        operation=operation,
    )
    result = dict(release)
    if args.write_release:
        path, digest = write_validator_rpc_canary_release(
            _paths(args),
            release,
            operation=operation,
        )
        result["release_artifact"] = {"path": str(path), "sha256": digest}
    if args.full_output:
        output = result
    else:
        output = {
            "status": "pass",
            "network": result["network"],
            "expires_at": result["expires_at"],
            "chain": result["chain"],
            "identity": result["identity"],
            "funding_evidence": result["funding_evidence"],
            "execution": result["execution"],
            "authority": result["authority"],
            "policy": result["policy"],
            "release_sha256": result["validator_rpc_canary_release_sha256"],
            **(
                {"release_artifact": result["release_artifact"]}
                if "release_artifact" in result
                else {}
            ),
        }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _cmd_verify_validator_rpc_canary_release(
    args: argparse.Namespace,
    private_state,
) -> int:
    result = verify_validator_rpc_canary_release(
        _paths(args),
        private_state,
        Path(args.release),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        funding_evidence_max_age_seconds=args.funding_evidence_max_age_seconds,
        funding_transaction_max_age_seconds=args.funding_transaction_max_age_seconds,
        soak_max_age_seconds=args.soak_max_age_seconds,
        operation=_operation(
            "verify-validator-rpc-canary-release",
            args.network,
            args.operation_id,
        ),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_validator_rpc_canary(
    args: argparse.Namespace,
    private_state,
) -> int:
    operation = _operation(
        "apply-validator-rpc-canary",
        args.network,
        args.operation_id,
    )
    if args.execute:
        result = execute_validator_rpc_canary_release(
            _paths(args),
            private_state,
            Path(args.release),
            acknowledged_release_sha256=args.acknowledge_release_sha256,
            selected_nodes=_selected_nodes(args.node),
            max_age_seconds=args.max_age_seconds,
            transaction_max_age_seconds=args.transaction_max_age_seconds,
            funding_evidence_max_age_seconds=args.funding_evidence_max_age_seconds,
            funding_transaction_max_age_seconds=args.funding_transaction_max_age_seconds,
            soak_max_age_seconds=args.soak_max_age_seconds,
            recovery_evidence_path=Path(args.recovery_evidence) if args.recovery_evidence else None,
            recovery_evidence_max_age_seconds=args.recovery_evidence_max_age_seconds,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            max_wait_seconds=args.max_wait_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            operation=operation,
        )
        output = result if args.full_output else {
            "status": result["status"],
            "network": result["network"],
            "chain_id": result["chain_id"],
            "canary_address": result["canary_address"],
            "chain_state": result["chain_state"],
            "summary": result["summary"],
            "evidence": result["evidence"],
        }
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0 if result["status"] == "pass" else 1
    result = inspect_validator_rpc_canary_release(
        _paths(args),
        private_state,
        Path(args.release),
        acknowledged_release_sha256=args.acknowledge_release_sha256,
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        funding_evidence_max_age_seconds=args.funding_evidence_max_age_seconds,
        funding_transaction_max_age_seconds=args.funding_transaction_max_age_seconds,
        soak_max_age_seconds=args.soak_max_age_seconds,
        operation=operation,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_verify_validator_rpc_canary_evidence(
    args: argparse.Namespace,
    private_state,
) -> int:
    result = verify_validator_rpc_canary_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        release_max_age_seconds=args.release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        funding_evidence_max_age_seconds=args.funding_evidence_max_age_seconds,
        funding_transaction_max_age_seconds=args.funding_transaction_max_age_seconds,
        soak_max_age_seconds=args.soak_max_age_seconds,
        operation=_operation(
            "verify-validator-rpc-canary-evidence",
            args.network,
            args.operation_id,
        ),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_stage_validator_rpc_canary_funding_transaction(
    args: argparse.Namespace,
    private_state,
) -> int:
    operation = _operation(
        "stage-validator-rpc-canary-funding-transaction",
        args.network,
        args.operation_id,
    )
    transaction = build_validator_rpc_canary_funding_transaction(
        _paths(args),
        private_state,
        Path(args.canary_transaction),
        selected_nodes=_selected_nodes(args.node),
        transaction_max_age_seconds=args.canary_transaction_max_age_seconds,
        soak_max_age_seconds=args.soak_max_age_seconds,
        operation=operation,
    )
    result = dict(transaction)
    if args.write_transaction:
        path, digest = write_validator_rpc_canary_funding_transaction(
            _paths(args),
            transaction,
            operation=operation,
        )
        result["transaction_artifact"] = {"path": str(path), "sha256": digest}
    if args.full_output:
        output = result
    else:
        output = {
            "status": "pass",
            "network": result["network"],
            "staged_scope": result["staged_scope"],
            "chain": result["chain"],
            "funding_source": result["funding_source"],
            "destination": result["destination"],
            "funding_policy": result["funding_policy"],
            "required_secret_bindings": result["required_secret_bindings"],
            "authority": result["authority"],
            "summary": result["summary"],
            "transaction_sha256": result[
                "validator_rpc_canary_funding_transaction_sha256"
            ],
            **(
                {"transaction_artifact": result["transaction_artifact"]}
                if "transaction_artifact" in result
                else {}
            ),
        }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _cmd_verify_validator_rpc_canary_funding_transaction(
    args: argparse.Namespace,
    private_state,
) -> int:
    result = verify_validator_rpc_canary_funding_transaction(
        _paths(args),
        private_state,
        Path(args.transaction),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        canary_transaction_max_age_seconds=args.canary_transaction_max_age_seconds,
        soak_max_age_seconds=args.soak_max_age_seconds,
        operation=_operation(
            "verify-validator-rpc-canary-funding-transaction",
            args.network,
            args.operation_id,
        ),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_validator_rpc_canary_funding(
    args: argparse.Namespace,
    private_state,
) -> int:
    operation = _operation(
        "release-validator-rpc-canary-funding",
        args.network,
        args.operation_id,
    )
    release = build_validator_rpc_canary_funding_release(
        _paths(args),
        private_state,
        Path(args.transaction),
        acknowledged_transaction_sha256=(
            args.acknowledge_validator_rpc_canary_funding_transaction_sha256
        ),
        selected_nodes=_selected_nodes(args.node),
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        canary_transaction_max_age_seconds=args.canary_transaction_max_age_seconds,
        soak_max_age_seconds=args.soak_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        recovery_evidence_path=(
            Path(args.recovery_evidence) if args.recovery_evidence else None
        ),
        operation=operation,
    )
    result = dict(release)
    if args.write_release:
        path, digest = write_validator_rpc_canary_funding_release(
            _paths(args),
            release,
            operation=operation,
        )
        result["release_artifact"] = {"path": str(path), "sha256": digest}
    if args.full_output:
        output = result
    else:
        output = {
            "status": "pass",
            "network": result["network"],
            "expires_at": result["expires_at"],
            "chain": result["chain"],
            "funding_source": result["funding_source"],
            "destination": result["destination"],
            "funding_policy": result["funding_policy"],
            "authority": result["authority"],
            "policy": result["policy"],
            "recovery": result.get("recovery"),
            "release_sha256": result["validator_rpc_canary_funding_release_sha256"],
            **(
                {"release_artifact": result["release_artifact"]}
                if "release_artifact" in result
                else {}
            ),
        }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _cmd_verify_validator_rpc_canary_funding_release(
    args: argparse.Namespace,
    private_state,
) -> int:
    result = verify_validator_rpc_canary_funding_release(
        _paths(args),
        private_state,
        Path(args.release),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        canary_transaction_max_age_seconds=args.canary_transaction_max_age_seconds,
        soak_max_age_seconds=args.soak_max_age_seconds,
        operation=_operation(
            "verify-validator-rpc-canary-funding-release",
            args.network,
            args.operation_id,
        ),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_validator_rpc_canary_funding(
    args: argparse.Namespace,
    private_state,
) -> int:
    operation = _operation(
        "apply-validator-rpc-canary-funding",
        args.network,
        args.operation_id,
    )
    if args.execute:
        result = execute_validator_rpc_canary_funding_release(
            _paths(args),
            private_state,
            Path(args.release),
            acknowledged_release_sha256=args.acknowledge_release_sha256,
            selected_nodes=_selected_nodes(args.node),
            max_age_seconds=args.max_age_seconds,
            transaction_max_age_seconds=args.transaction_max_age_seconds,
            canary_transaction_max_age_seconds=args.canary_transaction_max_age_seconds,
            soak_max_age_seconds=args.soak_max_age_seconds,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            max_wait_seconds=args.max_wait_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            operation=operation,
        )
        output = result if args.full_output else {
            "status": result["status"],
            "network": result["network"],
            "chain_id": result["chain_id"],
            "canary_address": result["canary_address"],
            "transfer_value_wei": result["transfer_value_wei"],
            "funding_transaction_hash": result["funding_transaction_hash"],
            "summary": result["summary"],
            "evidence": result["evidence"],
        }
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0 if result["status"] == "pass" else 1
    result = inspect_validator_rpc_canary_funding_release(
        _paths(args),
        private_state,
        Path(args.release),
        acknowledged_release_sha256=args.acknowledge_release_sha256,
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        canary_transaction_max_age_seconds=args.canary_transaction_max_age_seconds,
        soak_max_age_seconds=args.soak_max_age_seconds,
        operation=operation,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_verify_validator_rpc_canary_funding_evidence(
    args: argparse.Namespace,
    private_state,
) -> int:
    result = verify_validator_rpc_canary_funding_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        canary_transaction_max_age_seconds=args.canary_transaction_max_age_seconds,
        soak_max_age_seconds=args.soak_max_age_seconds,
        operation=_operation(
            "verify-validator-rpc-canary-funding-evidence",
            args.network,
            args.operation_id,
        ),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _completed_helper_required_components(args: argparse.Namespace) -> tuple[str, ...]:
    values = tuple(item.strip() for item in args.required_component_name if item and item.strip())
    return values if values else ("mother-super-node-fdb", "mother-super-node-hub")


def _cmd_cleanup_completed_mother_helpers(
    args: argparse.Namespace,
    private_state,
) -> int:
    operation = _operation(
        "cleanup-completed-mother-helpers",
        args.network,
        args.operation_id,
    )
    if args.execute:
        result = execute_completed_mother_helper_cleanup(
            _paths(args),
            private_state,
            network=args.network,
            controller_id=args.controller_id,
            service_uuid=args.service_uuid,
            node=args.node_name,
            acknowledged_service_uuid=args.acknowledge_service_uuid,
            required_component_names=_completed_helper_required_components(args),
            max_wait_seconds=args.max_wait_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            allow_compose_rewrite=args.allow_compose_rewrite,
            instant_deploy_compose_rewrite=args.instant_deploy_compose_rewrite,
            allow_nested_application_delete=args.allow_nested_application_delete,
            allow_compose_reconcile_refresh=args.allow_compose_reconcile_refresh,
            instant_deploy_compose_reconcile_refresh=args.instant_deploy_compose_reconcile_refresh,
            allow_service_redeploy_refresh=args.allow_service_redeploy_refresh,
            force_service_redeploy_refresh=not args.no_force_service_redeploy_refresh,
            allow_docker_orphan_container_cleanup=args.allow_docker_orphan_container_cleanup,
            allow_coolify_model_status_exclusion=args.allow_coolify_model_status_exclusion,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            operation=operation,
        )
        output = {
            "status": result["status"],
            "network": result["network"],
            "controller_id": result["controller_id"],
            "service_uuid": result["service_uuid"],
            "node": result["node"],
            "summary": result["summary"],
            "final_parent": result["final_parent"],
            "initial_completed_helper_candidates": result["initial_completed_helper_candidates"],
            "deleted_applications": result["deleted_applications"],
            "nested_deleted_applications": result["nested_deleted_applications"],
            "service_compose_rewrite": result["service_compose_rewrite"],
            "service_compose_reconcile": result["service_compose_reconcile"],
            "service_redeploy_refresh": result["service_redeploy_refresh"],
            "docker_orphan_container_cleanup": result["docker_orphan_container_cleanup"],
            "coolify_model_status_exclusion": result["coolify_model_status_exclusion"],
            "evidence": result["evidence"],
        }
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0 if result["status"] == "pass" else 1

    result = inspect_completed_mother_helper_cleanup(
        private_state,
        network=args.network,
        controller_id=args.controller_id,
        service_uuid=args.service_uuid,
        node=args.node_name,
        required_component_names=_completed_helper_required_components(args),
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        operation=operation,
    )
    output = {
        "status": result["status"],
        "network": result["network"],
        "controller_id": result["controller_id"],
        "service_uuid": result["service_uuid"],
        "node": result["node"],
        "summary": result["summary"],
        "parent": result["parent"],
        "required_components": result["required_components"],
        "completed_helper_candidates": result["completed_helper_candidates"],
        "excluded_completed_helper_records": result["excluded_completed_helper_records"],
        "unexpected_terminal_components": result["unexpected_terminal_components"],
        "unclassified_unhealthy_components": result["unclassified_unhealthy_components"],
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


def _cmd_verify_completed_mother_helper_cleanup_evidence(
    args: argparse.Namespace,
    private_state,
) -> int:
    result = verify_completed_mother_helper_cleanup_evidence(
        _paths(args),
        Path(args.evidence),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["clean"] else 1


def _cmd_probe_coolify_service_lifecycle(
    args: argparse.Namespace,
    private_state,
) -> int:
    operation = _operation(
        "probe-coolify-service-lifecycle",
        args.network,
        args.operation_id,
    )
    if args.execute:
        result = execute_coolify_service_lifecycle_probe(
            _paths(args),
            private_state,
            network=args.network,
            controller_id=args.controller_id,
            environment_name=args.environment_name,
            acknowledged_probe=args.acknowledge_live_service_probe,
            observe_seconds=args.observe_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            operation=operation,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("status") == "pass" else 1
    result = inspect_coolify_service_lifecycle_probe(
        private_state,
        network=args.network,
        controller_id=args.controller_id,
        environment_name=args.environment_name,
        observe_seconds=args.observe_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        operation=operation,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_verify_coolify_service_lifecycle_probe_evidence(
    args: argparse.Namespace,
    private_state,
) -> int:
    result = verify_coolify_service_lifecycle_probe_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0



def _c2_selection(args: argparse.Namespace) -> None:
    _warn_legacy_c2_testing_path_deprecated(getattr(args, "command", "stage-c2/apply-c2"))
    selected = _selected_nodes(args.node)
    if selected and selected != ("mainnetc-super2",):
        raise MotherDeploymentC2StateExtensionError(
            "MOTHER_DEPLOY_C2_STATE_EXTENSION_TARGET_INVALID",
            "C2 state extension may target only canonical mainnetc-super2",
        )


def _cmd_stage_c2_state_extension(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    operation = _operation("stage-c2-state-extension", args.network, args.operation_id)
    staging = stage_c2_state_extension(
        _paths(args),
        private_state,
        Path(args.canary_evidence),
        network=args.network,
        canary_max_age_seconds=args.canary_max_age_seconds,
        created_at=args.created_at,
        operation=operation,
    )
    result = dict(staging.transaction)
    if args.write_transaction:
        path, digest = write_c2_state_extension_transaction(
            _paths(args),
            staging,
            operation=_operation("write-c2-state-extension-transaction", args.network, args.operation_id),
        )
        result = {
            **result,
            "transaction_artifact": {"path": str(path), "sha256": digest},
            "secret_payload_persisted": True,
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_verify_c2_state_extension_transaction(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_c2_state_extension_transaction(
        _paths(args),
        private_state,
        Path(args.transaction),
        max_age_seconds=args.max_age_seconds,
        canary_max_age_seconds=args.canary_max_age_seconds,
        operation=_operation("verify-c2-state-extension-transaction", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_c2_state_extension(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    operation = _operation("release-c2-state-extension", args.network, args.operation_id)
    release = build_c2_state_extension_release(
        _paths(args),
        private_state,
        Path(args.transaction),
        acknowledge_transaction_sha256=args.acknowledge_c2_state_extension_transaction_sha256,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        canary_max_age_seconds=args.canary_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
        operation=operation,
    )
    if args.write_release:
        path, digest = write_c2_state_extension_release(
            _paths(args),
            release,
            operation=_operation("write-c2-state-extension-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_c2_state_extension_release(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_c2_state_extension_release(
        _paths(args),
        private_state,
        Path(args.release),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        canary_max_age_seconds=args.canary_max_age_seconds,
        operation=_operation("verify-c2-state-extension-release", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_c2_state_extension(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    common = {
        "acknowledge_release_sha256": args.acknowledge_release_sha256,
        "max_age_seconds": args.max_age_seconds,
        "transaction_max_age_seconds": args.transaction_max_age_seconds,
        "canary_max_age_seconds": args.canary_max_age_seconds,
        "operation": _operation("apply-c2-state-extension", args.network, args.operation_id),
    }
    if not args.execute:
        result = inspect_c2_state_extension_release(
            _paths(args),
            private_state,
            Path(args.release),
            **common,
        )
        result = {**result, "execute_requested": False}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_c2_state_extension_release(
        _paths(args),
        private_state,
        Path(args.release),
        **common,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_verify_c2_state_extension_evidence(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_c2_state_extension_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        max_age_seconds=args.max_age_seconds,
        operation=_operation("verify-c2-state-extension-evidence", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_stage_c2_standby_service(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = stage_c2_standby_service_transaction(
        _paths(args),
        private_state,
        Path(args.preflight_evidence),
        Path(args.c2_state_extension_evidence),
        network=args.network,
        preflight_max_age_seconds=args.preflight_max_age_seconds,
        c2_state_extension_max_age_seconds=args.c2_state_extension_max_age_seconds,
        created_at=args.created_at,
        operation=_operation("stage-c2-standby-service", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_verify_c2_standby_service_transaction(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_c2_standby_service_transaction(
        _paths(args),
        private_state,
        Path(args.transaction),
        Path(args.c2_state_extension_evidence),
        max_age_seconds=args.max_age_seconds,
        c2_state_extension_max_age_seconds=args.c2_state_extension_max_age_seconds,
        operation=_operation("verify-c2-standby-service-transaction", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_c2_standby_service(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    release = build_c2_standby_service_release(
        _paths(args),
        private_state,
        Path(args.transaction),
        Path(args.c2_state_extension_evidence),
        acknowledged_transaction_sha256=args.acknowledge_c2_standby_service_transaction_sha256,
        max_age_seconds=args.max_age_seconds,
        c2_state_extension_max_age_seconds=args.c2_state_extension_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
        operation=_operation("release-c2-standby-service", args.network, args.operation_id),
    )
    if args.write_release:
        path, digest = write_c2_standby_service_release(
            _paths(args),
            release,
            operation=_operation("write-c2-standby-service-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_c2_standby_service_release(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_c2_standby_service_release(
        _paths(args),
        private_state,
        Path(args.release),
        Path(args.c2_state_extension_evidence),
        max_age_seconds=args.max_age_seconds,
        c2_state_extension_max_age_seconds=args.c2_state_extension_max_age_seconds,
        operation=_operation("verify-c2-standby-service-release", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_c2_standby_service(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    common = {
        "acknowledged_release_sha256": args.acknowledge_release_sha256,
        "max_age_seconds": args.max_age_seconds,
        "c2_state_extension_max_age_seconds": args.c2_state_extension_max_age_seconds,
        "operation": _operation("apply-c2-standby-service", args.network, args.operation_id),
    }
    if not args.execute:
        result = inspect_c2_standby_service_release(
            _paths(args),
            private_state,
            Path(args.release),
            Path(args.c2_state_extension_evidence),
            **common,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_c2_standby_service_release(
        _paths(args),
        private_state,
        Path(args.release),
        Path(args.c2_state_extension_evidence),
        **common,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        opener=_DEFAULT_OPENER,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_verify_c2_standby_service(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_c2_standby_service(
        _paths(args),
        private_state,
        Path(args.execution),
        network=args.network,
        observed_at=args.observed_at,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        opener=_DEFAULT_OPENER,
        write_evidence=args.write_evidence,
        operation=_operation("verify-c2-standby-service", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.require_clean and result["summary"]["clean"] is not True:
        return 1
    return 0


def _cmd_stage_c2_standby_identity(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = stage_c2_standby_identity_transaction(
        _paths(args),
        private_state,
        Path(args.standby_evidence),
        max_age_seconds=args.max_age_seconds,
        created_at=args.created_at,
        operation=_operation("stage-c2-standby-identity", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_verify_c2_standby_identity_transaction(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_c2_standby_identity_transaction(
        _paths(args),
        private_state,
        Path(args.transaction),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_c2_standby_identity(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    release = build_c2_standby_identity_release(
        _paths(args),
        private_state,
        Path(args.transaction),
        acknowledged_identity_transaction_sha256=args.acknowledge_c2_standby_identity_transaction_sha256,
        max_age_seconds=args.max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_c2_standby_identity_release(
            _paths(args),
            release,
            operation=_operation("write-c2-standby-identity-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_c2_standby_identity_release(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_c2_standby_identity_release(
        _paths(args),
        private_state,
        Path(args.release),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_c2_standby_identity(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    common = {
        "acknowledged_release_sha256": args.acknowledge_release_sha256,
        "max_age_seconds": args.max_age_seconds,
    }
    if not args.execute:
        result = inspect_c2_standby_identity_release(
            _paths(args),
            private_state,
            Path(args.release),
            **common,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_c2_standby_identity_release(
        _paths(args),
        private_state,
        Path(args.release),
        **common,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        opener=_DEFAULT_OPENER,
        operation=_operation("apply-c2-standby-identity", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_verify_c2_standby_identity(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_c2_standby_identity(
        _paths(args),
        private_state,
        Path(args.execution),
        network=args.network,
        observed_at=args.observed_at,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        opener=_DEFAULT_OPENER,
        write_evidence=args.write_evidence,
        operation=_operation("verify-c2-standby-identity", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.require_clean and result["summary"]["clean"] is not True:
        return 1
    return 0



def _cmd_stage_c2_replica_standby(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    transaction = stage_c2_replica_standby_transaction(
        _paths(args),
        private_state,
        c2_state_extension_evidence=Path(args.c2_state_extension_evidence),
        identity_evidence=Path(args.identity_evidence),
        identity_rollback_evidence=Path(args.identity_rollback_evidence),
        genesis_birth_evidence=Path(args.genesis_birth_evidence),
        c2_state_extension_max_age_seconds=args.c2_state_extension_max_age_seconds,
        identity_max_age_seconds=args.identity_max_age_seconds,
        genesis_birth_max_age_seconds=args.genesis_birth_max_age_seconds,
        created_at=args.created_at,
        operation=_operation("stage-c2-replica-standby", args.network, args.operation_id),
    )
    if args.write_transaction:
        path, digest = write_c2_replica_standby_transaction(
            _paths(args),
            transaction,
            operation=_operation("write-c2-replica-standby-transaction", args.network, args.operation_id),
        )
        transaction = {**transaction, "transaction_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(transaction, indent=2, sort_keys=True))
    return 0


def _cmd_verify_c2_replica_standby_transaction(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_c2_replica_standby_transaction(
        _paths(args),
        private_state,
        Path(args.transaction),
        max_age_seconds=args.max_age_seconds,
        c2_state_extension_max_age_seconds=args.c2_state_extension_max_age_seconds,
        identity_max_age_seconds=args.identity_max_age_seconds,
        genesis_birth_max_age_seconds=args.genesis_birth_max_age_seconds,
        operation=_operation("verify-c2-replica-standby-transaction", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_c2_replica_standby(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    release = build_c2_replica_standby_release(
        _paths(args),
        private_state,
        Path(args.transaction),
        acknowledged_transaction_sha256=args.acknowledge_c2_replica_standby_transaction_sha256,
        max_age_seconds=args.max_age_seconds,
        c2_state_extension_max_age_seconds=args.c2_state_extension_max_age_seconds,
        identity_max_age_seconds=args.identity_max_age_seconds,
        genesis_birth_max_age_seconds=args.genesis_birth_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
        operation=_operation("release-c2-replica-standby", args.network, args.operation_id),
    )
    if args.write_release:
        path, digest = write_c2_replica_standby_release(
            _paths(args),
            release,
            operation=_operation("write-c2-replica-standby-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_c2_replica_standby_release(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_c2_replica_standby_release(
        _paths(args),
        private_state,
        Path(args.release),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        c2_state_extension_max_age_seconds=args.c2_state_extension_max_age_seconds,
        identity_max_age_seconds=args.identity_max_age_seconds,
        genesis_birth_max_age_seconds=args.genesis_birth_max_age_seconds,
        operation=_operation("verify-c2-replica-standby-release", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_c2_replica_standby(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    common = {
        "acknowledged_release_sha256": args.acknowledge_release_sha256,
        "max_age_seconds": args.max_age_seconds,
        "transaction_max_age_seconds": args.transaction_max_age_seconds,
        "c2_state_extension_max_age_seconds": args.c2_state_extension_max_age_seconds,
        "identity_max_age_seconds": args.identity_max_age_seconds,
        "genesis_birth_max_age_seconds": args.genesis_birth_max_age_seconds,
        "operation": _operation("apply-c2-replica-standby", args.network, args.operation_id),
    }
    if not args.execute:
        result = inspect_c2_replica_standby_release(
            _paths(args),
            private_state,
            Path(args.release),
            **common,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_c2_replica_standby_release(
        _paths(args),
        private_state,
        Path(args.release),
        **common,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_verify_c2_replica_standby(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_c2_replica_standby(
        _paths(args),
        private_state,
        Path(args.execution),
        observed_at=args.observed_at,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        write_evidence=args.write_evidence,
        operation=_operation("verify-c2-replica-standby", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.require_clean and result["summary"]["clean"] is not True:
        return 1
    return 0


def _cmd_release_c2_replica_sync(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    release = build_c2_replica_sync_release(
        _paths(args),
        private_state,
        Path(args.standby_evidence),
        acknowledged_c2_replica_standby_evidence_sha256=args.acknowledge_c2_replica_standby_evidence_sha256,
        selected_nodes=_selected_nodes(args.node),
        standby_max_age_seconds=args.standby_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_c2_replica_sync_release(
            _paths(args),
            release,
            operation=_operation("write-c2-replica-sync-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_c2_replica_sync_release(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_c2_replica_sync_release(
        _paths(args),
        private_state,
        Path(args.release),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        standby_max_age_seconds=args.standby_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_c2_replica_sync(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    common = {
        "acknowledged_release_sha256": args.acknowledge_release_sha256,
        "selected_nodes": _selected_nodes(args.node),
        "max_age_seconds": args.max_age_seconds,
        "standby_max_age_seconds": args.standby_max_age_seconds,
    }
    if not args.execute:
        result = inspect_c2_replica_sync_release(
            _paths(args),
            private_state,
            Path(args.release),
            **common,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_c2_replica_sync_release(
        _paths(args),
        private_state,
        Path(args.release),
        **common,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        max_wait_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        opener=_DEFAULT_OPENER,
        operation=_operation("apply-c2-replica-sync", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_verify_c2_replica_sync_evidence(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_c2_replica_sync_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_diagnose_c2_replica_sync_materialization(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = diagnose_c2_replica_sync_materialization(
        _paths(args),
        private_state,
        Path(args.failed_evidence),
        selected_nodes=_selected_nodes(args.node),
        failed_evidence_max_age_seconds=args.failed_evidence_max_age_seconds,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        operator_confirm_no_docker_materialization=args.operator_confirm_no_docker_materialization,
        operator_confirm_host_p2p_port_conflict=args.operator_confirm_host_p2p_port_conflict,
        opener=_DEFAULT_OPENER,
        now=None,
        write_evidence=args.write_evidence,
        operation=_operation("diagnose-c2-replica-sync-materialization", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.require_retry_authorized and result.get("summary", {}).get("retry_authorized") is not True:
        return 1
    if args.require_retry_blocked and result.get("summary", {}).get("retry_authorized") is not False:
        return 1
    return 0


def _cmd_release_c2_replica_sync_resume(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    release = build_c2_replica_sync_resume_release(
        _paths(args),
        private_state,
        Path(args.diagnostic_evidence),
        acknowledged_c2_replica_sync_diagnostic_sha256=args.acknowledge_c2_replica_sync_diagnostic_sha256,
        selected_nodes=_selected_nodes(args.node),
        diagnostic_max_age_seconds=args.diagnostic_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_c2_replica_sync_resume_release(
            _paths(args),
            release,
            operation=_operation("write-c2-replica-sync-resume-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_c2_replica_sync_resume_release(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_c2_replica_sync_resume_release(
        _paths(args),
        private_state,
        Path(args.release),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        diagnostic_max_age_seconds=args.diagnostic_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_c2_replica_sync_resume(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    common = {
        "acknowledged_release_sha256": args.acknowledge_release_sha256,
        "selected_nodes": _selected_nodes(args.node),
        "max_age_seconds": args.max_age_seconds,
        "diagnostic_max_age_seconds": args.diagnostic_max_age_seconds,
    }
    if not args.execute:
        result = inspect_c2_replica_sync_resume_release(
            _paths(args),
            private_state,
            Path(args.release),
            **common,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_c2_replica_sync_resume_release(
        _paths(args),
        private_state,
        Path(args.release),
        **common,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        max_wait_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        opener=_DEFAULT_OPENER,
        operation=_operation("apply-c2-replica-sync-resume", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_stage_c2_validator_admission(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    transaction = build_c2_validator_admission_transaction(
        _paths(args),
        private_state,
        Path(args.sync_evidence),
        network=args.network,
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        created_at=args.created_at,
    )
    if args.write_transaction:
        path, digest = write_c2_validator_admission_transaction(
            _paths(args),
            transaction,
            operation=_operation("write-c2-validator-admission-transaction", args.network, args.operation_id),
        )
        transaction = {**transaction, "transaction_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(transaction, indent=2, sort_keys=True))
    return 0


def _cmd_verify_c2_validator_admission_transaction(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_c2_validator_admission_transaction(
        _paths(args),
        private_state,
        Path(args.transaction),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_c2_validator_admission(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    release = build_c2_validator_admission_release(
        _paths(args),
        private_state,
        Path(args.transaction),
        acknowledged_transaction_sha256=args.acknowledge_c2_validator_admission_transaction_sha256,
        selected_nodes=_selected_nodes(args.node),
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_c2_validator_admission_release(
            _paths(args),
            release,
            operation=_operation("write-c2-validator-admission-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_c2_validator_admission_release(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_c2_validator_admission_release(
        _paths(args),
        private_state,
        Path(args.release),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_c2_validator_admission(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    if args.execute:
        result = execute_c2_validator_admission_release(
            _paths(args),
            private_state,
            Path(args.release),
            acknowledged_release_sha256=args.acknowledge_release_sha256,
            selected_nodes=_selected_nodes(args.node),
            max_age_seconds=args.max_age_seconds,
            transaction_max_age_seconds=args.transaction_max_age_seconds,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            max_wait_seconds=args.max_wait_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            operation=_operation("execute-c2-validator-admission", args.network, args.operation_id),
        )
    else:
        result = inspect_c2_validator_admission_release(
            _paths(args),
            private_state,
            Path(args.release),
            acknowledged_release_sha256=args.acknowledge_release_sha256,
            selected_nodes=_selected_nodes(args.node),
            max_age_seconds=args.max_age_seconds,
            transaction_max_age_seconds=args.transaction_max_age_seconds,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_verify_c2_validator_admission_evidence(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_c2_validator_admission_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_stage_t3_post_admission_steady_state(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    transaction = build_t3_post_admission_steady_state_transaction(
        _paths(args),
        private_state,
        Path(args.admission_evidence),
        network=args.network,
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        created_at=args.created_at,
    )
    if args.write_transaction:
        path, digest = write_t3_post_admission_steady_state_transaction(
            _paths(args),
            transaction,
            operation=_operation("write-t3-post-admission-steady-state-transaction", args.network, args.operation_id),
        )
        transaction = {**transaction, "transaction_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(transaction, indent=2, sort_keys=True))
    return 0


def _cmd_verify_t3_post_admission_steady_state_transaction(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_t3_post_admission_steady_state_transaction(
        _paths(args),
        private_state,
        Path(args.transaction),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_t3_post_admission_steady_state(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    release = build_t3_post_admission_steady_state_release(
        _paths(args),
        private_state,
        Path(args.transaction),
        acknowledged_transaction_sha256=args.acknowledge_t3_post_admission_steady_state_transaction_sha256,
        selected_nodes=_selected_nodes(args.node),
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_t3_post_admission_steady_state_release(
            _paths(args),
            release,
            operation=_operation("write-t3-post-admission-steady-state-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_t3_post_admission_steady_state_release(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_t3_post_admission_steady_state_release(
        _paths(args),
        private_state,
        Path(args.release),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_apply_t3_post_admission_steady_state(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    if args.execute:
        result = execute_t3_post_admission_steady_state_release(
            _paths(args),
            private_state,
            Path(args.release),
            acknowledged_release_sha256=args.acknowledge_release_sha256,
            selected_nodes=_selected_nodes(args.node),
            max_age_seconds=args.max_age_seconds,
            transaction_max_age_seconds=args.transaction_max_age_seconds,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            window_count=args.window_count,
            window_seconds=args.window_seconds,
            operation=_operation("execute-t3-post-admission-steady-state", args.network, args.operation_id),
        )
    else:
        result = inspect_t3_post_admission_steady_state_release(
            _paths(args),
            private_state,
            Path(args.release),
            acknowledged_release_sha256=args.acknowledge_release_sha256,
            selected_nodes=_selected_nodes(args.node),
            max_age_seconds=args.max_age_seconds,
            transaction_max_age_seconds=args.transaction_max_age_seconds,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_verify_t3_post_admission_steady_state_evidence(args: argparse.Namespace, private_state) -> int:
    _c2_selection(args)
    result = verify_t3_post_admission_steady_state_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        selected_nodes=_selected_nodes(args.node),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0




def _cmd_add_node_prep(args: argparse.Namespace, private_state) -> int:
    transaction = build_node_add_prep_transaction(
        _paths(args),
        private_state,
        Path(args.baseline_evidence),
        network=args.network,
        target_node=args.node,
        target_host=args.host,
        mode=args.mode,
        baseline_evidence_sha256=args.baseline_evidence_sha256,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
        created_at=args.created_at,
    )
    if args.write_transaction:
        path, digest = write_node_add_prep_transaction(
            _paths(args),
            transaction,
            operation=_operation("write-node-add-prep-transaction", args.network, args.operation_id),
        )
        transaction = {**transaction, "transaction_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(transaction, indent=2, sort_keys=True))
    return 0


def _cmd_verify_node_add_prep_transaction(args: argparse.Namespace, private_state) -> int:
    result = verify_node_add_prep_transaction(
        _paths(args),
        private_state,
        Path(args.transaction),
        max_age_seconds=args.max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_node_add_do(args: argparse.Namespace, private_state) -> int:
    release = build_node_add_do_release(
        _paths(args),
        private_state,
        Path(args.transaction),
        acknowledged_prep_transaction_sha256=args.acknowledge_node_add_prep_transaction_sha256,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_node_add_do_release(
            _paths(args),
            release,
            operation=_operation("write-node-add-do-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_node_add_do_release(args: argparse.Namespace, private_state) -> int:
    result = verify_node_add_do_release(
        _paths(args),
        private_state,
        Path(args.release),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_add_node_do(args: argparse.Namespace, private_state) -> int:
    if not args.execute:
        raise RuntimeError("--execute is required for add-node do")
    result = execute_node_add_do_release(
        _paths(args),
        private_state,
        Path(args.release),
        acknowledged_release_sha256=args.acknowledge_release_sha256,
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        operation=_operation("execute-node-add-do", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_verify_node_add_do_evidence(args: argparse.Namespace, private_state) -> int:
    result = verify_node_add_do_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        max_age_seconds=args.max_age_seconds,
        release_max_age_seconds=args.release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_node_add_identity(args: argparse.Namespace, private_state) -> int:
    release = build_node_add_identity_release(
        _paths(args),
        private_state,
        Path(args.add_do_evidence),
        acknowledged_add_do_evidence_sha256=args.acknowledge_add_node_do_evidence_sha256,
        max_age_seconds=args.max_age_seconds,
        add_do_release_max_age_seconds=args.add_do_release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_node_add_identity_release(
            _paths(args),
            release,
            operation=_operation("write-node-add-identity-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_node_add_identity_release(args: argparse.Namespace, private_state) -> int:
    result = verify_node_add_identity_release(
        _paths(args),
        private_state,
        Path(args.release),
        max_age_seconds=args.max_age_seconds,
        add_do_max_age_seconds=args.add_do_max_age_seconds,
        add_do_release_max_age_seconds=args.add_do_release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_add_node_identity(args: argparse.Namespace, private_state) -> int:
    if not args.execute:
        raise RuntimeError("--execute is required for add-node identity")
    result = execute_node_add_identity_release(
        _paths(args),
        private_state,
        Path(args.release),
        acknowledged_release_sha256=args.acknowledge_release_sha256,
        max_age_seconds=args.max_age_seconds,
        add_do_max_age_seconds=args.add_do_max_age_seconds,
        add_do_release_max_age_seconds=args.add_do_release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        operation=_operation("execute-node-add-identity", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_verify_node_add_identity_evidence(args: argparse.Namespace, private_state) -> int:
    result = verify_node_add_identity_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        max_age_seconds=args.max_age_seconds,
        release_max_age_seconds=args.release_max_age_seconds,
        add_do_max_age_seconds=args.add_do_max_age_seconds,
        add_do_release_max_age_seconds=args.add_do_release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_node_add_single_node_bootstrap(args: argparse.Namespace, private_state) -> int:
    release = build_node_add_single_node_bootstrap_release(
        _paths(args),
        private_state,
        Path(args.identity_evidence),
        acknowledged_add_node_identity_evidence_sha256=args.acknowledge_add_node_identity_evidence_sha256,
        max_age_seconds=args.max_age_seconds,
        identity_release_max_age_seconds=args.identity_release_max_age_seconds,
        add_do_max_age_seconds=args.add_do_max_age_seconds,
        add_do_release_max_age_seconds=args.add_do_release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        hub_git_repository=args.hub_git_repository,
        hub_git_ref=args.hub_git_ref,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_node_add_single_node_bootstrap_release(
            _paths(args),
            release,
            operation=_operation("write-node-add-single-node-bootstrap-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_node_add_single_node_bootstrap_release(args: argparse.Namespace, private_state) -> int:
    result = verify_node_add_single_node_bootstrap_release(
        _paths(args),
        private_state,
        Path(args.release),
        max_age_seconds=args.max_age_seconds,
        identity_max_age_seconds=args.identity_max_age_seconds,
        identity_release_max_age_seconds=args.identity_release_max_age_seconds,
        add_do_max_age_seconds=args.add_do_max_age_seconds,
        add_do_release_max_age_seconds=args.add_do_release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_add_node_single_node_bootstrap(args: argparse.Namespace, private_state) -> int:
    if not args.execute:
        raise RuntimeError("--execute is required for add-node single-node-bootstrap")
    result = execute_node_add_single_node_bootstrap_release(
        _paths(args),
        private_state,
        Path(args.release),
        acknowledged_release_sha256=args.acknowledge_release_sha256,
        max_age_seconds=args.max_age_seconds,
        identity_max_age_seconds=args.identity_max_age_seconds,
        identity_release_max_age_seconds=args.identity_release_max_age_seconds,
        add_do_max_age_seconds=args.add_do_max_age_seconds,
        add_do_release_max_age_seconds=args.add_do_release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        max_wait_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        operation=_operation("execute-node-add-single-node-bootstrap", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_verify_node_add_single_node_bootstrap_evidence(args: argparse.Namespace, private_state) -> int:
    result = verify_node_add_single_node_bootstrap_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        max_age_seconds=args.max_age_seconds,
        release_max_age_seconds=args.release_max_age_seconds,
        identity_max_age_seconds=args.identity_max_age_seconds,
        identity_release_max_age_seconds=args.identity_release_max_age_seconds,
        add_do_max_age_seconds=args.add_do_max_age_seconds,
        add_do_release_max_age_seconds=args.add_do_release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0



def _cmd_adopt_node_add_single_node_bootstrap_live_proof(args: argparse.Namespace, private_state) -> int:
    result = adopt_node_add_single_node_bootstrap_live_proof(
        _paths(args),
        private_state,
        Path(args.evidence),
        max_age_seconds=args.max_age_seconds,
        release_max_age_seconds=args.release_max_age_seconds,
        identity_max_age_seconds=args.identity_max_age_seconds,
        identity_release_max_age_seconds=args.identity_release_max_age_seconds,
        add_do_max_age_seconds=args.add_do_max_age_seconds,
        add_do_release_max_age_seconds=args.add_do_release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        operation=_operation("adopt-node-add-single-node-bootstrap-live-proof", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_add_node_single_node_chain_and_hub_proof(args: argparse.Namespace, private_state) -> int:
    result = finalize_node_add_single_node_chain_and_hub_proof(
        _paths(args),
        private_state,
        Path(args.bootstrap_evidence),
        acknowledged_bootstrap_evidence_sha256=args.acknowledge_bootstrap_evidence_sha256,
        max_age_seconds=args.max_age_seconds,
        release_max_age_seconds=args.release_max_age_seconds,
        identity_max_age_seconds=args.identity_max_age_seconds,
        identity_release_max_age_seconds=args.identity_release_max_age_seconds,
        add_do_max_age_seconds=args.add_do_max_age_seconds,
        add_do_release_max_age_seconds=args.add_do_release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
        write_evidence=args.write_evidence,
        operation=_operation("add-node-single-node-chain-and-hub-proof", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_verify_node_add_single_node_chain_and_hub_proof_evidence(args: argparse.Namespace, private_state) -> int:
    result = verify_node_add_single_node_chain_and_hub_proof_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        max_age_seconds=args.max_age_seconds,
        bootstrap_max_age_seconds=args.bootstrap_max_age_seconds,
        release_max_age_seconds=args.release_max_age_seconds,
        identity_max_age_seconds=args.identity_max_age_seconds,
        identity_release_max_age_seconds=args.identity_release_max_age_seconds,
        add_do_max_age_seconds=args.add_do_max_age_seconds,
        add_do_release_max_age_seconds=args.add_do_release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0




def _cmd_detect_mother_topology_staleness(args: argparse.Namespace, private_state) -> int:
    result = detect_topology_staleness(
        _paths(args),
        private_state,
        Path(args.topology_evidence),
        network=args.network,
        acknowledged_topology_evidence_sha256=args.acknowledge_topology_evidence_sha256,
        max_age_seconds=args.max_age_seconds,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_adopt_empty_current_topology(args: argparse.Namespace, private_state) -> int:
    result = adopt_empty_current_topology(
        _paths(args),
        private_state,
        Path(args.topology_evidence),
        network=args.network,
        acknowledged_topology_evidence_sha256=args.acknowledge_topology_evidence_sha256,
        actual_nodes=args.actual_node,
        max_age_seconds=args.max_age_seconds,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        write_evidence=args.write_evidence,
        operation=_operation("adopt-empty-current-topology", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_verify_empty_current_topology_evidence(args: argparse.Namespace, private_state) -> int:
    result = verify_empty_topology_rectification_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_node_add_replica_sync(args: argparse.Namespace, private_state) -> int:
    release = build_node_add_replica_sync_release(
        _paths(args),
        private_state,
        Path(args.identity_evidence),
        acknowledged_add_node_identity_evidence_sha256=args.acknowledge_add_node_identity_evidence_sha256,
        max_age_seconds=args.max_age_seconds,
        identity_release_max_age_seconds=args.identity_release_max_age_seconds,
        add_do_max_age_seconds=args.add_do_max_age_seconds,
        add_do_release_max_age_seconds=args.add_do_release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_node_add_replica_sync_release(
            _paths(args),
            release,
            operation=_operation("write-node-add-replica-sync-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_node_add_replica_sync_release(args: argparse.Namespace, private_state) -> int:
    result = verify_node_add_replica_sync_release(
        _paths(args),
        private_state,
        Path(args.release),
        max_age_seconds=args.max_age_seconds,
        identity_max_age_seconds=args.identity_max_age_seconds,
        identity_release_max_age_seconds=args.identity_release_max_age_seconds,
        add_do_max_age_seconds=args.add_do_max_age_seconds,
        add_do_release_max_age_seconds=args.add_do_release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_add_node_replica_sync(args: argparse.Namespace, private_state) -> int:
    if not args.execute:
        raise RuntimeError("--execute is required for add-node replica-sync")
    result = execute_node_add_replica_sync_release(
        _paths(args),
        private_state,
        Path(args.release),
        acknowledged_release_sha256=args.acknowledge_release_sha256,
        max_age_seconds=args.max_age_seconds,
        identity_max_age_seconds=args.identity_max_age_seconds,
        identity_release_max_age_seconds=args.identity_release_max_age_seconds,
        add_do_max_age_seconds=args.add_do_max_age_seconds,
        add_do_release_max_age_seconds=args.add_do_release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        max_wait_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        operation=_operation("execute-node-add-replica-sync", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_verify_node_add_replica_sync_evidence(args: argparse.Namespace, private_state) -> int:
    result = verify_node_add_replica_sync_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        max_age_seconds=args.max_age_seconds,
        release_max_age_seconds=args.release_max_age_seconds,
        identity_max_age_seconds=args.identity_max_age_seconds,
        identity_release_max_age_seconds=args.identity_release_max_age_seconds,
        add_do_max_age_seconds=args.add_do_max_age_seconds,
        add_do_release_max_age_seconds=args.add_do_release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0



def _cmd_release_node_add_validator_admission(args: argparse.Namespace, private_state) -> int:
    release = build_node_add_validator_admission_release(
        _paths(args),
        private_state,
        Path(args.replica_sync_evidence),
        acknowledged_replica_sync_evidence_sha256=args.acknowledge_add_node_replica_sync_evidence_sha256,
        network=args.network,
        replica_sync_max_age_seconds=args.replica_sync_max_age_seconds,
        replica_sync_release_max_age_seconds=args.replica_sync_release_max_age_seconds,
        identity_max_age_seconds=args.identity_max_age_seconds,
        identity_release_max_age_seconds=args.identity_release_max_age_seconds,
        add_do_max_age_seconds=args.add_do_max_age_seconds,
        add_do_release_max_age_seconds=args.add_do_release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_node_add_validator_admission_release(
            _paths(args),
            release,
            operation=_operation("node-add-validator-admission-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_node_add_validator_admission_release(args: argparse.Namespace, private_state) -> int:
    result = verify_node_add_validator_admission_release(
        _paths(args),
        private_state,
        Path(args.release),
        max_age_seconds=args.max_age_seconds,
        replica_sync_max_age_seconds=args.replica_sync_max_age_seconds,
        replica_sync_release_max_age_seconds=args.replica_sync_release_max_age_seconds,
        identity_max_age_seconds=args.identity_max_age_seconds,
        identity_release_max_age_seconds=args.identity_release_max_age_seconds,
        add_do_max_age_seconds=args.add_do_max_age_seconds,
        add_do_release_max_age_seconds=args.add_do_release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_add_node_validator_admission(args: argparse.Namespace, private_state) -> int:
    if not args.execute:
        raise RuntimeError("--execute is required for add-node validator-admission")
    result = execute_node_add_validator_admission_release(
        _paths(args),
        private_state,
        Path(args.release),
        acknowledged_release_sha256=args.acknowledge_release_sha256,
        max_age_seconds=args.max_age_seconds,
        replica_sync_max_age_seconds=args.replica_sync_max_age_seconds,
        replica_sync_release_max_age_seconds=args.replica_sync_release_max_age_seconds,
        identity_max_age_seconds=args.identity_max_age_seconds,
        identity_release_max_age_seconds=args.identity_release_max_age_seconds,
        add_do_max_age_seconds=args.add_do_max_age_seconds,
        add_do_release_max_age_seconds=args.add_do_release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        max_wait_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        operation=_operation("execute-node-add-validator-admission", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_verify_node_add_validator_admission_evidence(args: argparse.Namespace, private_state) -> int:
    result = verify_node_add_validator_admission_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        max_age_seconds=args.max_age_seconds,
        release_max_age_seconds=args.release_max_age_seconds,
        replica_sync_max_age_seconds=args.replica_sync_max_age_seconds,
        replica_sync_release_max_age_seconds=args.replica_sync_release_max_age_seconds,
        identity_max_age_seconds=args.identity_max_age_seconds,
        identity_release_max_age_seconds=args.identity_release_max_age_seconds,
        add_do_max_age_seconds=args.add_do_max_age_seconds,
        add_do_release_max_age_seconds=args.add_do_release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_node_add_rollback(args: argparse.Namespace, private_state) -> int:
    release = build_node_add_rollback_release(
        _paths(args),
        private_state,
        Path(args.failed_evidence),
        acknowledged_failed_evidence_sha256=args.acknowledge_failed_evidence_sha256,
        max_age_seconds=args.max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_node_add_rollback_release(
            _paths(args),
            release,
            operation=_operation("write-node-add-rollback-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_node_add_rollback_release(args: argparse.Namespace, private_state) -> int:
    result = verify_node_add_rollback_release(
        _paths(args),
        private_state,
        Path(args.release),
        max_age_seconds=args.max_age_seconds,
        failed_evidence_max_age_seconds=args.failed_evidence_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_add_node_rollback(args: argparse.Namespace, private_state) -> int:
    if not args.execute:
        result = verify_node_add_rollback_release(
            _paths(args),
            private_state,
            Path(args.release),
            max_age_seconds=args.max_age_seconds,
            failed_evidence_max_age_seconds=args.failed_evidence_max_age_seconds,
        )
        result = {
            **result,
            "execute_requested": False,
            "live_mutation_performed": False,
            "next_phase": f"add-node-rollback-{args.network}",
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = execute_node_add_rollback_release(
        _paths(args),
        private_state,
        Path(args.release),
        acknowledged_release_sha256=args.acknowledge_release_sha256,
        max_age_seconds=args.max_age_seconds,
        failed_evidence_max_age_seconds=args.failed_evidence_max_age_seconds,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        max_wait_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        operation=_operation("execute-node-add-rollback", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


def _cmd_verify_node_add_rollback_evidence(args: argparse.Namespace, private_state) -> int:
    result = verify_node_add_rollback_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        max_age_seconds=args.max_age_seconds,
        release_max_age_seconds=args.release_max_age_seconds,
        failed_evidence_max_age_seconds=args.failed_evidence_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_remove_node_prep(args: argparse.Namespace, private_state) -> int:
    transaction = build_node_remove_prep_transaction(
        _paths(args),
        private_state,
        Path(args.baseline_evidence),
        network=args.network,
        target_node=args.node,
        mode=args.mode,
        baseline_evidence_sha256=args.baseline_evidence_sha256,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
        created_at=args.created_at,
    )
    if args.write_transaction:
        path, digest = write_node_remove_prep_transaction(
            _paths(args),
            transaction,
            operation=_operation("write-node-remove-prep-transaction", args.network, args.operation_id),
        )
        transaction = {**transaction, "transaction_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(transaction, indent=2, sort_keys=True))
    return 0


def _cmd_verify_node_remove_prep_transaction(args: argparse.Namespace, private_state) -> int:
    result = verify_node_remove_prep_transaction(
        _paths(args),
        private_state,
        Path(args.transaction),
        max_age_seconds=args.max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_release_node_remove_do(args: argparse.Namespace, private_state) -> int:
    release = build_node_remove_do_release(
        _paths(args),
        private_state,
        Path(args.transaction),
        acknowledged_prep_transaction_sha256=args.acknowledge_node_remove_prep_transaction_sha256,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
        expires_in_seconds=args.expires_in_seconds,
        created_at=args.created_at,
    )
    if args.write_release:
        path, digest = write_node_remove_do_release(
            _paths(args),
            release,
            operation=_operation("write-node-remove-do-release", args.network, args.operation_id),
        )
        release = {**release, "release_artifact": {"path": str(path), "sha256": digest}}
    print(json.dumps(release, indent=2, sort_keys=True))
    return 0


def _cmd_verify_node_remove_do_release(args: argparse.Namespace, private_state) -> int:
    result = verify_node_remove_do_release(
        _paths(args),
        private_state,
        Path(args.release),
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_remove_node_do(args: argparse.Namespace, private_state) -> int:
    if not args.execute:
        raise RuntimeError("--execute is required for remove-node do")
    result = execute_node_remove_do_release(
        _paths(args),
        private_state,
        Path(args.release),
        acknowledged_release_sha256=args.acknowledge_release_sha256,
        max_age_seconds=args.max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
        timeout=args.timeout,
        max_wait_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        allow_missing_service=args.allow_missing_service,
        operation=_operation("execute-node-remove-do", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_verify_node_remove_do_evidence(args: argparse.Namespace, private_state) -> int:
    result = verify_node_remove_do_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        max_age_seconds=args.max_age_seconds,
        release_max_age_seconds=args.release_max_age_seconds,
        transaction_max_age_seconds=args.transaction_max_age_seconds,
        baseline_max_age_seconds=args.baseline_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_remove_node_finalize(args: argparse.Namespace, private_state) -> int:
    result = finalize_node_remove(
        _paths(args),
        private_state,
        Path(args.do_evidence),
        network=args.network,
        max_age_seconds=args.max_age_seconds,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        write_evidence=args.write_evidence,
        operation=_operation("finalize-node-remove", args.network, args.operation_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_verify_node_remove_finalize_evidence(args: argparse.Namespace, private_state) -> int:
    result = verify_node_remove_finalize_evidence(
        _paths(args),
        private_state,
        Path(args.evidence),
        max_age_seconds=args.max_age_seconds,
        do_max_age_seconds=args.do_max_age_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        private_state = _load(args)
        if args.command == "add-node":
            if args.add_node_phase == "prep":
                return _cmd_add_node_prep(args, private_state)
            if args.add_node_phase == "do":
                return _cmd_add_node_do(args, private_state)
            if args.add_node_phase == "identity":
                return _cmd_add_node_identity(args, private_state)
            if args.add_node_phase == "single-node-bootstrap":
                return _cmd_add_node_single_node_bootstrap(args, private_state)
            if args.add_node_phase == "single-node-chain-and-hub-proof":
                return _cmd_add_node_single_node_chain_and_hub_proof(args, private_state)
            if args.add_node_phase == "replica-sync":
                return _cmd_add_node_replica_sync(args, private_state)
            if args.add_node_phase == "validator-admission":
                return _cmd_add_node_validator_admission(args, private_state)
            if args.add_node_phase == "rollback":
                return _cmd_add_node_rollback(args, private_state)
            raise RuntimeError(f"unsupported add-node phase: {args.add_node_phase}")
        if args.command == "verify-add-node-prep-transaction":
            return _cmd_verify_node_add_prep_transaction(args, private_state)
        if args.command == "release-add-node-do":
            return _cmd_release_node_add_do(args, private_state)
        if args.command == "verify-add-node-do-release":
            return _cmd_verify_node_add_do_release(args, private_state)
        if args.command == "verify-add-node-do-evidence":
            return _cmd_verify_node_add_do_evidence(args, private_state)
        if args.command == "release-add-node-identity":
            return _cmd_release_node_add_identity(args, private_state)
        if args.command == "verify-add-node-identity-release":
            return _cmd_verify_node_add_identity_release(args, private_state)
        if args.command == "verify-add-node-identity-evidence":
            return _cmd_verify_node_add_identity_evidence(args, private_state)
        if args.command == "release-add-node-single-node-bootstrap":
            return _cmd_release_node_add_single_node_bootstrap(args, private_state)
        if args.command == "verify-add-node-single-node-bootstrap-release":
            return _cmd_verify_node_add_single_node_bootstrap_release(args, private_state)
        if args.command == "verify-add-node-single-node-bootstrap-evidence":
            return _cmd_verify_node_add_single_node_bootstrap_evidence(args, private_state)
        if args.command == "adopt-add-node-single-node-bootstrap-live-proof":
            return _cmd_adopt_node_add_single_node_bootstrap_live_proof(args, private_state)
        if args.command == "verify-add-node-single-node-chain-and-hub-proof-evidence":
            return _cmd_verify_node_add_single_node_chain_and_hub_proof_evidence(args, private_state)
        if args.command == "detect-mother-topology-staleness":
            return _cmd_detect_mother_topology_staleness(args, private_state)
        if args.command == "adopt-empty-current-topology":
            return _cmd_adopt_empty_current_topology(args, private_state)
        if args.command == "verify-empty-current-topology-evidence":
            return _cmd_verify_empty_current_topology_evidence(args, private_state)
        if args.command == "release-add-node-replica-sync":
            return _cmd_release_node_add_replica_sync(args, private_state)
        if args.command == "verify-add-node-replica-sync-release":
            return _cmd_verify_node_add_replica_sync_release(args, private_state)
        if args.command == "verify-add-node-replica-sync-evidence":
            return _cmd_verify_node_add_replica_sync_evidence(args, private_state)
        if args.command == "release-add-node-validator-admission":
            return _cmd_release_node_add_validator_admission(args, private_state)
        if args.command == "verify-add-node-validator-admission-release":
            return _cmd_verify_node_add_validator_admission_release(args, private_state)
        if args.command == "verify-add-node-validator-admission-evidence":
            return _cmd_verify_node_add_validator_admission_evidence(args, private_state)
        if args.command == "release-add-node-rollback":
            return _cmd_release_node_add_rollback(args, private_state)
        if args.command == "verify-add-node-rollback-release":
            return _cmd_verify_node_add_rollback_release(args, private_state)
        if args.command == "verify-add-node-rollback-evidence":
            return _cmd_verify_node_add_rollback_evidence(args, private_state)
        if args.command == "remove-node":
            if args.remove_node_phase == "prep":
                return _cmd_remove_node_prep(args, private_state)
            if args.remove_node_phase == "do":
                return _cmd_remove_node_do(args, private_state)
            if args.remove_node_phase == "finalize":
                return _cmd_remove_node_finalize(args, private_state)
            raise RuntimeError(f"unsupported remove-node phase: {args.remove_node_phase}")
        if args.command == "verify-remove-node-prep-transaction":
            return _cmd_verify_node_remove_prep_transaction(args, private_state)
        if args.command == "release-remove-node-do":
            return _cmd_release_node_remove_do(args, private_state)
        if args.command == "verify-remove-node-do-release":
            return _cmd_verify_node_remove_do_release(args, private_state)
        if args.command == "verify-remove-node-do-evidence":
            return _cmd_verify_node_remove_do_evidence(args, private_state)
        if args.command == "verify-remove-node-finalize-evidence":
            return _cmd_verify_node_remove_finalize_evidence(args, private_state)
        if args.command == "plan":
            return _cmd_plan(args, private_state)
        if args.command == "preflight":
            return _cmd_preflight(args, private_state)
        if args.command == "verify-preflight":
            return _cmd_verify_preflight(args, private_state)
        if args.command == "prepare-execution":
            return _cmd_prepare_execution(args, private_state)
        if args.command == "verify-execution":
            return _cmd_verify_execution(args, private_state)
        if args.command == "stage-mutation":
            return _cmd_stage_mutation(args, private_state)
        if args.command == "verify-mutation":
            return _cmd_verify_mutation(args, private_state)
        if args.command == "release-mutation":
            return _cmd_release_mutation(args, private_state)
        if args.command == "verify-release":
            return _cmd_verify_release(args, private_state)
        if args.command == "apply-mutation":
            return _cmd_apply_mutation(args, private_state)
        if args.command == "rollback-mutation":
            return _cmd_rollback_mutation(args, private_state)
        if args.command == "recover-mutation-rollback":
            return _cmd_recover_mutation_rollback(args, private_state)
        if args.command == "verify-mutation-rollback":
            return _cmd_verify_mutation_rollback(args, private_state)
        if args.command == "verify-standby":
            return _cmd_verify_standby(args, private_state)
        if args.command == "verify-standby-evidence":
            return _cmd_verify_standby_evidence(args, private_state)
        if args.command == "stage-identity":
            return _cmd_stage_identity(args, private_state)
        if args.command == "verify-identity-transaction":
            return _cmd_verify_identity_transaction(args, private_state)
        if args.command == "release-identity":
            return _cmd_release_identity(args, private_state)
        if args.command == "verify-identity-release":
            return _cmd_verify_identity_release(args, private_state)
        if args.command == "apply-identity":
            return _cmd_apply_identity(args, private_state)
        if args.command == "rollback-identity":
            return _cmd_rollback_identity(args, private_state)
        if args.command == "recover-identity-rollback":
            return _cmd_recover_identity_rollback(args, private_state)
        if args.command == "verify-identity-rollback":
            return _cmd_verify_identity_rollback(args, private_state)
        if args.command == "stage-genesis":
            return _cmd_stage_genesis(args, private_state)
        if args.command == "verify-genesis-transaction":
            return _cmd_verify_genesis_transaction(args, private_state)
        if args.command == "release-genesis":
            return _cmd_release_genesis(args, private_state)
        if args.command == "verify-genesis-release":
            return _cmd_verify_genesis_release(args, private_state)
        if args.command == "apply-genesis":
            return _cmd_apply_genesis(args, private_state)
        if args.command == "rollback-genesis":
            return _cmd_rollback_genesis(args, private_state)
        if args.command == "recover-genesis-rollback":
            return _cmd_recover_genesis_rollback(args, private_state)
        if args.command == "verify-genesis-rollback":
            return _cmd_verify_genesis_rollback(args, private_state)
        if args.command == "release-genesis-birth":
            return _cmd_release_genesis_birth(args, private_state)
        if args.command == "verify-genesis-birth-release":
            return _cmd_verify_genesis_birth_release(args, private_state)
        if args.command == "apply-genesis-birth":
            return _cmd_apply_genesis_birth(args, private_state)
        if args.command == "verify-genesis-birth-evidence":
            return _cmd_verify_genesis_birth_evidence(args, private_state)
        if args.command == "stage-soft-replica":
            return _cmd_stage_soft_replica(args, private_state)
        if args.command == "verify-soft-replica-transaction":
            return _cmd_verify_soft_replica_transaction(args, private_state)
        if args.command == "release-soft-replica":
            return _cmd_release_soft_replica(args, private_state)
        if args.command == "verify-soft-replica-release":
            return _cmd_verify_soft_replica_release(args, private_state)
        if args.command == "apply-soft-replica":
            return _cmd_apply_soft_replica(args, private_state)
        if args.command == "release-soft-replica-sync":
            return _cmd_release_soft_replica_sync(args, private_state)
        if args.command == "verify-soft-replica-sync-release":
            return _cmd_verify_soft_replica_sync_release(args, private_state)
        if args.command == "apply-soft-replica-sync":
            return _cmd_apply_soft_replica_sync(args, private_state)
        if args.command == "verify-soft-replica-sync-evidence":
            return _cmd_verify_soft_replica_sync_evidence(args, private_state)
        if args.command == "stage-validator-admission":
            return _cmd_stage_validator_admission(args, private_state)
        if args.command == "verify-validator-admission-transaction":
            return _cmd_verify_validator_admission_transaction(args, private_state)
        if args.command == "release-validator-admission":
            return _cmd_release_validator_admission(args, private_state)
        if args.command == "verify-validator-admission-release":
            return _cmd_verify_validator_admission_release(args, private_state)
        if args.command == "apply-validator-admission":
            return _cmd_apply_validator_admission(args, private_state)
        if args.command == "verify-validator-admission-evidence":
            return _cmd_verify_validator_admission_evidence(args, private_state)
        if args.command == "release-validator-quorum-recovery":
            return _cmd_release_validator_quorum_recovery(args, private_state)
        if args.command == "verify-validator-quorum-recovery-release":
            return _cmd_verify_validator_quorum_recovery_release(args, private_state)
        if args.command == "apply-validator-quorum-recovery":
            return _cmd_apply_validator_quorum_recovery(args, private_state)
        if args.command == "reconcile-validator-quorum-recovery":
            return _cmd_reconcile_validator_quorum_recovery(args, private_state)
        if args.command == "verify-validator-quorum-recovery-reconciliation":
            return _cmd_verify_validator_quorum_recovery_reconciliation(args, private_state)
        if args.command == "diagnose-validator-quorum-runtime":
            return _cmd_diagnose_validator_quorum_runtime(args, private_state)
        if args.command == "verify-validator-quorum-recovery-evidence":
            return _cmd_verify_validator_quorum_recovery_evidence(args, private_state)
        if args.command == "stage-post-admission-steady-state":
            return _cmd_stage_post_admission_steady_state(args, private_state)
        if args.command == "verify-post-admission-steady-state-transaction":
            return _cmd_verify_post_admission_steady_state_transaction(args, private_state)
        if args.command == "release-post-admission-steady-state":
            return _cmd_release_post_admission_steady_state(args, private_state)
        if args.command == "verify-post-admission-steady-state-release":
            return _cmd_verify_post_admission_steady_state_release(args, private_state)
        if args.command == "apply-post-admission-steady-state":
            return _cmd_apply_post_admission_steady_state(args, private_state)
        if args.command == "verify-post-admission-steady-state-evidence":
            return _cmd_verify_post_admission_steady_state_evidence(args, private_state)
        if args.command == "reconcile-post-admission-steady-state":
            return _cmd_reconcile_post_admission_steady_state(args, private_state)
        if args.command == "verify-post-admission-steady-state-reconciliation":
            return _cmd_verify_post_admission_steady_state_reconciliation(args, private_state)
        if args.command == "stage-post-admission-steady-state-continuation":
            return _cmd_stage_post_admission_steady_state_continuation(args, private_state)
        if args.command == "verify-post-admission-steady-state-continuation-transaction":
            return _cmd_verify_post_admission_steady_state_continuation_transaction(args, private_state)
        if args.command == "release-post-admission-steady-state-continuation":
            return _cmd_release_post_admission_steady_state_continuation(args, private_state)
        if args.command == "verify-post-admission-steady-state-continuation-release":
            return _cmd_verify_post_admission_steady_state_continuation_release(args, private_state)
        if args.command == "apply-post-admission-steady-state-continuation":
            return _cmd_apply_post_admission_steady_state_continuation(args, private_state)
        if args.command == "verify-post-admission-steady-state-continuation-evidence":
            return _cmd_verify_post_admission_steady_state_continuation_evidence(args, private_state)
        if args.command == "run-mainnet-steady-state-soak":
            return _cmd_run_mainnet_steady_state_soak(args, private_state)
        if args.command == "verify-mainnet-steady-state-soak-evidence":
            return _cmd_verify_mainnet_steady_state_soak_evidence(args, private_state)
        if args.command == "reserve-validator-rpc-canary-identity":
            return _cmd_reserve_validator_rpc_canary_identity(args, private_state)
        if args.command == "verify-validator-rpc-canary-identity":
            return _cmd_verify_validator_rpc_canary_identity(args, private_state)
        if args.command == "stage-validator-rpc-canary-transaction":
            return _cmd_stage_validator_rpc_canary_transaction(args, private_state)
        if args.command == "verify-validator-rpc-canary-transaction":
            return _cmd_verify_validator_rpc_canary_transaction(args, private_state)
        if args.command == "release-validator-rpc-canary":
            return _cmd_release_validator_rpc_canary(args, private_state)
        if args.command == "verify-validator-rpc-canary-release":
            return _cmd_verify_validator_rpc_canary_release(args, private_state)
        if args.command == "apply-validator-rpc-canary":
            return _cmd_apply_validator_rpc_canary(args, private_state)
        if args.command == "verify-validator-rpc-canary-evidence":
            return _cmd_verify_validator_rpc_canary_evidence(args, private_state)
        if args.command == "stage-validator-rpc-canary-funding-transaction":
            return _cmd_stage_validator_rpc_canary_funding_transaction(args, private_state)
        if args.command == "verify-validator-rpc-canary-funding-transaction":
            return _cmd_verify_validator_rpc_canary_funding_transaction(args, private_state)
        if args.command == "release-validator-rpc-canary-funding":
            return _cmd_release_validator_rpc_canary_funding(args, private_state)
        if args.command == "verify-validator-rpc-canary-funding-release":
            return _cmd_verify_validator_rpc_canary_funding_release(args, private_state)
        if args.command == "apply-validator-rpc-canary-funding":
            return _cmd_apply_validator_rpc_canary_funding(args, private_state)
        if args.command == "verify-validator-rpc-canary-funding-evidence":
            return _cmd_verify_validator_rpc_canary_funding_evidence(args, private_state)
        if args.command == "cleanup-completed-mother-helpers":
            return _cmd_cleanup_completed_mother_helpers(args, private_state)
        if args.command == "verify-completed-mother-helper-cleanup-evidence":
            return _cmd_verify_completed_mother_helper_cleanup_evidence(args, private_state)
        if args.command == "stage-c2-state-extension":
            return _cmd_stage_c2_state_extension(args, private_state)
        if args.command == "verify-c2-state-extension-transaction":
            return _cmd_verify_c2_state_extension_transaction(args, private_state)
        if args.command == "release-c2-state-extension":
            return _cmd_release_c2_state_extension(args, private_state)
        if args.command == "verify-c2-state-extension-release":
            return _cmd_verify_c2_state_extension_release(args, private_state)
        if args.command == "apply-c2-state-extension":
            return _cmd_apply_c2_state_extension(args, private_state)
        if args.command == "verify-c2-state-extension-evidence":
            return _cmd_verify_c2_state_extension_evidence(args, private_state)
        if args.command == "stage-c2-standby-service":
            return _cmd_stage_c2_standby_service(args, private_state)
        if args.command == "verify-c2-standby-service-transaction":
            return _cmd_verify_c2_standby_service_transaction(args, private_state)
        if args.command == "release-c2-standby-service":
            return _cmd_release_c2_standby_service(args, private_state)
        if args.command == "verify-c2-standby-service-release":
            return _cmd_verify_c2_standby_service_release(args, private_state)
        if args.command == "apply-c2-standby-service":
            return _cmd_apply_c2_standby_service(args, private_state)
        if args.command == "verify-c2-standby-service":
            return _cmd_verify_c2_standby_service(args, private_state)
        if args.command == "stage-c2-standby-identity":
            return _cmd_stage_c2_standby_identity(args, private_state)
        if args.command == "verify-c2-standby-identity-transaction":
            return _cmd_verify_c2_standby_identity_transaction(args, private_state)
        if args.command == "release-c2-standby-identity":
            return _cmd_release_c2_standby_identity(args, private_state)
        if args.command == "verify-c2-standby-identity-release":
            return _cmd_verify_c2_standby_identity_release(args, private_state)
        if args.command == "apply-c2-standby-identity":
            return _cmd_apply_c2_standby_identity(args, private_state)
        if args.command == "verify-c2-standby-identity":
            return _cmd_verify_c2_standby_identity(args, private_state)
        if args.command == "stage-c2-replica-standby":
            return _cmd_stage_c2_replica_standby(args, private_state)
        if args.command == "verify-c2-replica-standby-transaction":
            return _cmd_verify_c2_replica_standby_transaction(args, private_state)
        if args.command == "release-c2-replica-standby":
            return _cmd_release_c2_replica_standby(args, private_state)
        if args.command == "verify-c2-replica-standby-release":
            return _cmd_verify_c2_replica_standby_release(args, private_state)
        if args.command == "apply-c2-replica-standby":
            return _cmd_apply_c2_replica_standby(args, private_state)
        if args.command == "verify-c2-replica-standby":
            return _cmd_verify_c2_replica_standby(args, private_state)
        if args.command == "release-c2-replica-sync":
            return _cmd_release_c2_replica_sync(args, private_state)
        if args.command == "verify-c2-replica-sync-release":
            return _cmd_verify_c2_replica_sync_release(args, private_state)
        if args.command == "apply-c2-replica-sync":
            return _cmd_apply_c2_replica_sync(args, private_state)
        if args.command == "verify-c2-replica-sync-evidence":
            return _cmd_verify_c2_replica_sync_evidence(args, private_state)
        if args.command == "diagnose-c2-replica-sync-materialization":
            return _cmd_diagnose_c2_replica_sync_materialization(args, private_state)
        if args.command == "release-c2-replica-sync-resume":
            return _cmd_release_c2_replica_sync_resume(args, private_state)
        if args.command == "verify-c2-replica-sync-resume-release":
            return _cmd_verify_c2_replica_sync_resume_release(args, private_state)
        if args.command == "apply-c2-replica-sync-resume":
            return _cmd_apply_c2_replica_sync_resume(args, private_state)
        if args.command == "stage-c2-validator-admission":
            return _cmd_stage_c2_validator_admission(args, private_state)
        if args.command == "verify-c2-validator-admission-transaction":
            return _cmd_verify_c2_validator_admission_transaction(args, private_state)
        if args.command == "release-c2-validator-admission":
            return _cmd_release_c2_validator_admission(args, private_state)
        if args.command == "verify-c2-validator-admission-release":
            return _cmd_verify_c2_validator_admission_release(args, private_state)
        if args.command == "apply-c2-validator-admission":
            return _cmd_apply_c2_validator_admission(args, private_state)
        if args.command == "verify-c2-validator-admission-evidence":
            return _cmd_verify_c2_validator_admission_evidence(args, private_state)
        if args.command == "stage-t3-post-admission-steady-state":
            return _cmd_stage_t3_post_admission_steady_state(args, private_state)
        if args.command == "verify-t3-post-admission-steady-state-transaction":
            return _cmd_verify_t3_post_admission_steady_state_transaction(args, private_state)
        if args.command == "release-t3-post-admission-steady-state":
            return _cmd_release_t3_post_admission_steady_state(args, private_state)
        if args.command == "verify-t3-post-admission-steady-state-release":
            return _cmd_verify_t3_post_admission_steady_state_release(args, private_state)
        if args.command == "apply-t3-post-admission-steady-state":
            return _cmd_apply_t3_post_admission_steady_state(args, private_state)
        if args.command == "verify-t3-post-admission-steady-state-evidence":
            return _cmd_verify_t3_post_admission_steady_state_evidence(args, private_state)
        if args.command == "probe-coolify-service-lifecycle":
            return _cmd_probe_coolify_service_lifecycle(args, private_state)
        if args.command == "verify-coolify-service-lifecycle-probe-evidence":
            return _cmd_verify_coolify_service_lifecycle_probe_evidence(args, private_state)
        raise RuntimeError(f"unsupported command: {args.command}")
    except (
        CoolifyObservationError,
        MotherDeploymentC2StateExtensionError,
        MotherDeploymentC2ReplicaStandbyError,
        MotherDeploymentC2ReplicaSyncError,
        MotherDeploymentC2ValidatorAdmissionError,
        MotherDeploymentT3PostAdmissionSteadyStateError,
        MotherDeploymentNodeAddPrepError,
        MotherDeploymentNodeAddDoError,
        MotherDeploymentNodeAddIdentityError,
        MotherDeploymentNodeAddSingleNodeBootstrapError,
        MotherDeploymentNodeAddReplicaSyncError,
        MotherDeploymentNodeAddValidatorAdmissionError,
        MotherDeploymentNodeAddRollbackError,
        MotherDeploymentTopologyRectificationError,
        MotherDeploymentNodeRemovePrepError,
        MotherDeploymentNodeRemoveDoError,
        MotherDeploymentNodeRemoveFinalizeError,
        MotherDeploymentExecutorError,
        MotherDeploymentCoolifyServiceLifecycleProbeError,
        MotherDeploymentCompletedHelperCleanupError,
        MotherDeploymentExecutionError,
        MotherDeploymentGenesisBirthError,
        MotherDeploymentGenesisError,
        MotherDeploymentGenesisExecutorError,
        MotherDeploymentGenesisReleaseError,
        MotherDeploymentGenesisRollbackError,
        MotherDeploymentIdentityExecutorError,
        MotherDeploymentIdentityInstallError,
        MotherDeploymentIdentityReleaseError,
        MotherDeploymentIdentityRollbackError,
        MotherDeploymentPlanError,
        MotherDeploymentPreflightError,
        MotherDeploymentReleaseError,
        MotherDeploymentRollbackError,
        MotherDeploymentSoftReplicaError,
        MotherDeploymentSoftReplicaReleaseError,
        MotherDeploymentSoftReplicaExecutorError,
        MotherDeploymentSoftReplicaSyncError,
        MotherDeploymentValidatorAdmissionError,
        MotherDeploymentValidatorAdmissionReleaseError,
        MotherDeploymentValidatorAdmissionExecutorError,
        MotherDeploymentValidatorQuorumRecoveryError,
        MotherDeploymentPostAdmissionSteadyStateContinuationError,
        MotherDeploymentMainnetSoakError,
        MotherDeploymentValidatorRpcCanaryError,
        MotherDeploymentValidatorRpcCanaryExecutionError,
        MotherDeploymentValidatorRpcCanaryFundingError,
        MotherDeploymentPostAdmissionSteadyStateError,
        MotherDeploymentStandbyError,
        MotherDeploymentTransactionError,
    ) as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    except MotherError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return exit_code_for(exc)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
