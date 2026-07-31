#!/usr/bin/env python3
"""Plan, preflight, stage, and explicitly release starter deployment artifacts."""

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
        raise RuntimeError(f"unsupported command: {args.command}")
    except (
        CoolifyObservationError,
        MotherDeploymentExecutionError,
        MotherDeploymentPlanError,
        MotherDeploymentPreflightError,
        MotherDeploymentReleaseError,
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
