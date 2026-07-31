#!/usr/bin/env python3
"""Plan, preflight, stage, release, and execute starter deployment artifacts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.coolify_state import CoolifyObservationError
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
from tools.mother.common.deployment_genesis_release import (
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
from tools.mother.common.deployment_soft_replica import (
    MotherDeploymentSoftReplicaError,
    build_soft_replica_transaction,
    verify_soft_replica_transaction,
    write_soft_replica_transaction,
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

    stage_genesis = subparsers.add_parser(
        "stage-genesis",
        help="compile one Mother-owned first genesis and the later soft-admission specification",
        allow_abbrev=False,
    )
    _common(stage_genesis)
    stage_genesis.add_argument("--identity-execution", required=True)
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
    release_birth = subparsers.add_parser(
        "release-genesis-birth",
        help="release an internal-only proof guardian for the exact successful first-genesis execution",
        allow_abbrev=False,
    )
    _common(release_birth)
    release_birth.add_argument("--execution", required=True)
    release_birth.add_argument("--acknowledge-genesis-execution-sha256", required=True)
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


def _cmd_stage_genesis(args: argparse.Namespace, private_state) -> int:
    transaction = build_deployment_genesis_transaction(
        _paths(args),
        private_state,
        Path(args.identity_execution),
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


def _cmd_release_genesis_birth(args: argparse.Namespace, private_state) -> int:
    release = build_genesis_birth_release(
        _paths(args),
        private_state,
        Path(args.execution),
        acknowledged_genesis_execution_sha256=args.acknowledge_genesis_execution_sha256,
        selected_nodes=_selected_nodes(args.node),
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        private_state = _load(args)
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
        raise RuntimeError(f"unsupported command: {args.command}")
    except (
        CoolifyObservationError,
        MotherDeploymentExecutorError,
        MotherDeploymentExecutionError,
        MotherDeploymentGenesisBirthError,
        MotherDeploymentGenesisError,
        MotherDeploymentGenesisExecutorError,
        MotherDeploymentGenesisReleaseError,
        MotherDeploymentIdentityExecutorError,
        MotherDeploymentIdentityInstallError,
        MotherDeploymentIdentityReleaseError,
        MotherDeploymentPlanError,
        MotherDeploymentPreflightError,
        MotherDeploymentReleaseError,
        MotherDeploymentSoftReplicaError,
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
