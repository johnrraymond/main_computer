from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from main_computer.credit_units import (
    credit_count_to_wei,
    credit_decimal_text_to_wei,
    credit_wei_to_decimal_text,
)
from main_computer.hub_credit_ledger import HubCreditLedger
from main_computer.protected_mode_pretest import (
    DEFAULT_PROTECTED_NETWORK,
    ProtectedModePretestError,
    deterministic_bytes32,
    find_repo_root,
    load_protected_network_profile,
    normalize_network_name,
    protected_amount_probe,
)
from tools.temporal_lab.activities import FakeTokenActivities
from tools.temporal_lab.event_log import DEFAULT_EVENT_LOG_PATH
from tools.temporal_lab.local_temporal import DEFAULT_NAMESPACE
from tools.temporal_lab.models import FakeTokenRequest, RingDecision, decide_ring


ExecutionMode = Literal["live-temporal", "direct-activity"]


DEFAULT_PROTECTED_TEMPORAL_REPORT_PATH = Path("runtime") / "temporal_lab" / "protected_temporal_flow_report.json"
DEFAULT_PROTECTED_TEMPORAL_LEDGER_ROOT = Path("runtime") / "temporal_lab" / "protected_temporal_flow_hub_credit_ledger"


@dataclass(frozen=True)
class ProtectedTemporalFlowConfig:
    repo_root: Path
    network: str = DEFAULT_PROTECTED_NETWORK
    deployment_path: Path | None = None
    ledger_root: Path | None = DEFAULT_PROTECTED_TEMPORAL_LEDGER_ROOT
    report_path: Path | None = DEFAULT_PROTECTED_TEMPORAL_REPORT_PATH
    reset_ledger: bool = True
    execution_mode: ExecutionMode = "live-temporal"
    temporal_address: str = "localhost:7233"
    namespace: str = DEFAULT_NAMESPACE
    event_log_path: Path = DEFAULT_EVENT_LOG_PATH
    deposit_credits: str = "25"
    success_credits_offered: int = 3
    failure_credits_offered: int = 3
    token_count: int = 3
    token_interval_seconds: float = 0.1
    account_id: str = "protected-temporal-smoke-client"
    worker_id: str = "protected-temporal-worker-01"

    def resolved_deployment_path(self) -> Path | None:
        if self.deployment_path is None:
            return None
        return self.deployment_path if self.deployment_path.is_absolute() else self.repo_root / self.deployment_path

    def resolved_ledger_root(self) -> Path | None:
        if self.ledger_root is None:
            return None
        return self.ledger_root if self.ledger_root.is_absolute() else self.repo_root / self.ledger_root

    def resolved_report_path(self) -> Path | None:
        if self.report_path is None:
            return None
        return self.report_path if self.report_path.is_absolute() else self.repo_root / self.report_path

    def resolved_event_log_path(self) -> Path:
        return self.event_log_path if self.event_log_path.is_absolute() else self.repo_root / self.event_log_path


def _require_positive_int(value: object, *, field_name: str) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ProtectedModePretestError(f"{field_name} must be an integer") from exc
    if parsed <= 0:
        raise ProtectedModePretestError(f"{field_name} must be positive")
    return parsed


def _prepare_ledger_root(config: ProtectedTemporalFlowConfig) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    ledger_root = config.resolved_ledger_root()
    tempdir: tempfile.TemporaryDirectory[str] | None = None
    if ledger_root is None:
        tempdir = tempfile.TemporaryDirectory(prefix="protected-temporal-flow-ledger-")
        ledger_root = Path(tempdir.name)

    if config.reset_ledger and ledger_root.exists():
        shutil.rmtree(ledger_root)
    ledger_root.mkdir(parents=True, exist_ok=True)
    return ledger_root, tempdir


def _make_request(
    *,
    request_id: str,
    account_id: str,
    credits_offered: int,
    token_count: int,
    token_interval_seconds: float,
    force_failure: bool,
) -> FakeTokenRequest:
    payload: dict[str, Any] = {
        "source": "protected_temporal_flow_smoke",
        "protected_mode": True,
    }
    if force_failure:
        payload["force_failure"] = True
    return FakeTokenRequest(
        request_id=request_id,
        account_id=account_id,
        credits_offered=credits_offered,
        token_count=token_count,
        token_interval_seconds=token_interval_seconds,
        payload=payload,
        idempotency_key=f"idem-{request_id}",
    )


async def _execute_direct_activity(
    *,
    request: FakeTokenRequest,
    event_log_path: Path,
    worker_id: str,
) -> dict[str, Any]:
    return await FakeTokenActivities(event_log_path=event_log_path, worker_id=worker_id).emit_fake_tokens(
        request.to_dict()
    )


async def _execute_live_temporal_workflow(
    *,
    temporal_address: str,
    namespace: str,
    request: FakeTokenRequest,
    task_queue: str,
    event_log_path: Path,
    worker_id: str,
) -> dict[str, Any]:
    try:
        from temporalio.client import Client
        from temporalio.worker import Worker
    except ImportError as exc:
        raise ProtectedModePretestError(
            "temporalio is required for live protected Temporal flow. "
            "Install with: python -m pip install -r tools/temporal_lab/requirements-temporal.txt"
        ) from exc

    from tools.temporal_lab.workflows import FakeTokenWorkflow

    client = await Client.connect(temporal_address, namespace=namespace)
    activities = FakeTokenActivities(event_log_path=event_log_path, worker_id=worker_id)
    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[FakeTokenWorkflow],
        activities=[activities.emit_fake_tokens],
    ):
        return await client.execute_workflow(
            FakeTokenWorkflow.run,
            request.to_dict(),
            id=request.idempotency_key or request.request_id,
            task_queue=task_queue,
        )


async def _execute_work(
    *,
    config: ProtectedTemporalFlowConfig,
    request: FakeTokenRequest,
    decision: RingDecision,
) -> dict[str, Any]:
    if decision.task_queue is None:
        raise ProtectedModePretestError("accepted request must include a task queue")

    if config.execution_mode == "direct-activity":
        return await _execute_direct_activity(
            request=request,
            event_log_path=config.resolved_event_log_path(),
            worker_id=config.worker_id,
        )
    if config.execution_mode == "live-temporal":
        return await _execute_live_temporal_workflow(
            temporal_address=config.temporal_address,
            namespace=config.namespace,
            request=request,
            task_queue=decision.task_queue,
            event_log_path=config.resolved_event_log_path(),
            worker_id=config.worker_id,
        )
    raise ProtectedModePretestError(f"unknown execution mode: {config.execution_mode}")


def _decision_required_wei(decision: RingDecision) -> int:
    if not decision.accepted or decision.ring is None or decision.task_queue is None:
        raise ProtectedModePretestError(f"ring decision rejected unexpectedly: {decision.to_dict()}")
    required_credits = _require_positive_int(decision.required_credits, field_name="decision.required_credits")
    return credit_count_to_wei(required_credits)


async def _run_request_flow(
    *,
    config: ProtectedTemporalFlowConfig,
    ledger: HubCreditLedger,
    request: FakeTokenRequest,
    expect_failure: bool,
) -> dict[str, Any]:
    decision = decide_ring(request)
    required_wei = _decision_required_wei(decision)
    routed = request.with_ring(decision.ring)

    hold = ledger.create_hold_credit_wei(
        account_id=request.account_id,
        request_id=request.request_id,
        credit_wei=str(required_wei),
        memo=f"protected Temporal flow hold for {request.request_id}",
        metadata={
            "protected_temporal_flow": True,
            "ring": decision.ring,
            "task_queue": decision.task_queue,
            "required_credits": decision.required_credits,
            "credits_offered": request.credits_offered,
            "idempotency_key": request.idempotency_key,
        },
    )
    duplicate_hold = ledger.create_hold_credit_wei(
        account_id=request.account_id,
        request_id=request.request_id,
        credit_wei=str(required_wei),
    )
    if not duplicate_hold["idempotent"]:
        raise ProtectedModePretestError(f"duplicate protected hold did not replay for {request.request_id}")

    workflow_result: dict[str, Any] | None = None
    workflow_error: str | None = None
    settlement: dict[str, Any] | None = None
    release: dict[str, Any] | None = None

    try:
        workflow_result = await _execute_work(config=config, request=routed, decision=decision)
    except Exception as exc:
        workflow_error = f"{type(exc).__name__}: {exc}"
        if not expect_failure:
            release = ledger.release_hold(
                hold_id=hold["hold"]["hold_id"],
                reason="unexpected protected Temporal workflow failure",
                metadata={"protected_temporal_flow": True, "unexpected_failure": True},
            )
            raise ProtectedModePretestError(f"protected Temporal workflow failed unexpectedly: {workflow_error}") from exc

    if workflow_error is None:
        if expect_failure:
            settlement = ledger.charge_hold_credit_wei(
                hold_id=hold["hold"]["hold_id"],
                charged_credit_wei=str(required_wei),
                worker_node_id=config.worker_id,
                memo="unexpected protected Temporal success charged before failure assertion",
                metadata={"protected_temporal_flow": True, "unexpected_success": True},
            )
            raise ProtectedModePretestError(f"protected Temporal failure request unexpectedly succeeded: {workflow_result}")

        settlement = ledger.charge_hold_credit_wei(
            hold_id=hold["hold"]["hold_id"],
            charged_credit_wei=str(required_wei),
            worker_node_id=config.worker_id,
            memo="protected Temporal workflow settlement",
            metadata={
                "protected_temporal_flow": True,
                "workflow_result": workflow_result,
                "task_queue": decision.task_queue,
            },
        )
        duplicate_settlement = ledger.charge_hold_credit_wei(
            hold_id=hold["hold"]["hold_id"],
            charged_credit_wei=str(required_wei),
            worker_node_id=config.worker_id,
        )
        if not duplicate_settlement["idempotent"]:
            raise ProtectedModePretestError(f"duplicate protected settlement did not replay for {request.request_id}")
    else:
        if not expect_failure:
            raise ProtectedModePretestError(f"unexpected workflow error for success request: {workflow_error}")
        release = ledger.release_hold(
            hold_id=hold["hold"]["hold_id"],
            reason="protected Temporal workflow failure release",
            metadata={
                "protected_temporal_flow": True,
                "workflow_error": workflow_error,
                "task_queue": decision.task_queue,
            },
        )
        duplicate_release = ledger.release_hold(
            hold_id=hold["hold"]["hold_id"],
            reason="protected Temporal duplicate failure release",
        )
        if not duplicate_release["idempotent"]:
            raise ProtectedModePretestError(f"duplicate protected release did not replay for {request.request_id}")

    return {
        "request": routed.to_dict(),
        "decision": decision.to_dict(),
        "required_credit_wei": str(required_wei),
        "required_credits_display": credit_wei_to_decimal_text(required_wei),
        "hold": hold,
        "duplicate_hold": duplicate_hold,
        "workflow_result": workflow_result,
        "workflow_error": workflow_error,
        "settlement": settlement,
        "release": release,
        "final_hold": ledger.list_holds(request_id=request.request_id, limit=1)[0].as_dict(),
    }


async def run_protected_temporal_flow_smoke(config: ProtectedTemporalFlowConfig) -> dict[str, Any]:
    network = normalize_network_name(config.network)
    profile = load_protected_network_profile(
        repo_root=config.repo_root,
        network=network,
        deployment_path=config.resolved_deployment_path(),
        live_chain=False,
    )
    ledger_root, tempdir = _prepare_ledger_root(config)
    event_log_path = config.resolved_event_log_path()
    event_log_path.parent.mkdir(parents=True, exist_ok=True)
    if config.reset_ledger:
        event_log_path.unlink(missing_ok=True)

    try:
        token_count = _require_positive_int(config.token_count, field_name="token_count")
        success_credits_offered = _require_positive_int(config.success_credits_offered, field_name="success_credits_offered")
        failure_credits_offered = _require_positive_int(config.failure_credits_offered, field_name="failure_credits_offered")
        deposit_wei = credit_decimal_text_to_wei(config.deposit_credits, round_up=False)
        if deposit_wei <= 0:
            raise ProtectedModePretestError("deposit_credits must be positive")

        ledger = HubCreditLedger(ledger_root)
        deposit_id = deterministic_bytes32(
            "protected-temporal-flow-deposit",
            profile.network,
            profile.smoke_client_address,
            config.account_id,
        )
        completion_tx_hash = deterministic_bytes32("protected-temporal-flow-complete", deposit_id)
        deposit_result = ledger.record_completed_bridge_deposit(
            account_id=config.account_id,
            owner_address=profile.smoke_client_address,
            chain_completed_credit_wei=str(deposit_wei),
            deposit_id=deposit_id,
            completion_tx_hash=completion_tx_hash,
            chain_id=profile.chain_id,
            contract_address=profile.hub_credit_bridge_escrow_address,
            completed_units=deposit_wei,
            deposit_amount_units=deposit_wei,
            memo="protected Temporal flow bridge completion",
            metadata={
                "protected_temporal_flow": True,
                "network": profile.network,
                "bridge_controller_address": profile.bridge_controller_address,
            },
        )
        duplicate_deposit = ledger.record_completed_bridge_deposit(
            account_id=config.account_id,
            owner_address=profile.smoke_client_address,
            chain_completed_credit_wei=str(deposit_wei),
            deposit_id=deposit_id,
            completion_tx_hash=completion_tx_hash,
            chain_id=profile.chain_id,
            contract_address=profile.hub_credit_bridge_escrow_address,
            completed_units=deposit_wei,
            deposit_amount_units=deposit_wei,
        )
        if not duplicate_deposit["idempotent"]:
            raise ProtectedModePretestError("protected Temporal bridge completion did not replay idempotently")

        suffix = uuid.uuid4().hex[:10]
        success_request = _make_request(
            request_id=f"protected-temporal-success-{suffix}",
            account_id=config.account_id,
            credits_offered=success_credits_offered,
            token_count=token_count,
            token_interval_seconds=config.token_interval_seconds,
            force_failure=False,
        )
        failure_request = _make_request(
            request_id=f"protected-temporal-failure-{suffix}",
            account_id=config.account_id,
            credits_offered=failure_credits_offered,
            token_count=token_count,
            token_interval_seconds=config.token_interval_seconds,
            force_failure=True,
        )

        success_flow = await _run_request_flow(
            config=config,
            ledger=ledger,
            request=success_request,
            expect_failure=False,
        )
        failure_flow = await _run_request_flow(
            config=config,
            ledger=ledger,
            request=failure_request,
            expect_failure=True,
        )

        final_status = ledger.status()
        final_totals = final_status["totals"]
        spent_wei = int(final_totals["spent_credit_wei"])
        available_wei = int(final_totals["available_credit_wei"])
        held_wei = int(final_totals["held_credit_wei"])
        success_required_wei = int(success_flow["required_credit_wei"])
        failure_required_wei = int(failure_flow["required_credit_wei"])

        invariants = {
            "bridge_deposit_completion_idempotent": bool(duplicate_deposit["idempotent"]),
            "success_decision_selected_ring": success_flow["decision"]["ring"] is not None,
            "success_task_queue_matches_ring": (
                str(success_flow["decision"]["task_queue"]).endswith(f"ring-{success_flow['decision']['ring']}")
            ),
            "success_hold_charged": success_flow["final_hold"]["status"] == "charged",
            "success_charge_matches_required_wei": (
                str(success_flow["settlement"]["charge"]["charged_credit_wei"]) == str(success_required_wei)
                if success_flow["settlement"]
                else False
            ),
            "failure_decision_selected_ring": failure_flow["decision"]["ring"] is not None,
            "failure_workflow_failed": bool(failure_flow["workflow_error"]),
            "failure_hold_released": failure_flow["final_hold"]["status"] == "released",
            "failure_release_matches_required_wei": (
                str(failure_flow["release"]["hold"]["credit_wei"]) == str(failure_required_wei)
                if failure_flow["release"]
                else False
            ),
            "final_held_zero": held_wei == 0,
            "final_spent_equals_success_required": spent_wei == success_required_wei,
            "final_available_plus_spent_equals_deposit": available_wei + spent_wei == deposit_wei,
            "failure_release_preserved_available_balance": available_wei == deposit_wei - success_required_wei,
        }
        ok = all(invariants.values())

        report = {
            "ok": ok,
            "mode": "protected-temporal-flow-smoke-v1",
            "execution_mode": config.execution_mode,
            "network_profile": profile.as_dict(),
            "ledger_root": str(ledger_root),
            "event_log_path": str(event_log_path),
            "account_id": config.account_id,
            "worker_id": config.worker_id,
            "amount_probes": {
                "deposit": protected_amount_probe(config.deposit_credits).as_dict(),
            },
            "steps": {
                "bridge_deposit_completed": deposit_result,
                "bridge_deposit_duplicate": duplicate_deposit,
                "success_flow": success_flow,
                "failure_flow": failure_flow,
                "final_status": final_status,
            },
            "invariants": invariants,
        }
        if not ok:
            failed = [name for name, value in invariants.items() if not value]
            raise ProtectedModePretestError(f"protected Temporal flow invariants failed: {failed}")

        report_path = config.resolved_report_path()
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            report["report_path"] = str(report_path)
        return report
    finally:
        if tempdir is not None:
            tempdir.cleanup()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="End-to-end protected Temporal bridge-credit smoke.")
    parser.add_argument("--network", default=DEFAULT_PROTECTED_NETWORK, choices=("dev", "test", "testnet", "mainnet"))
    parser.add_argument("--deployment", type=Path, default=None)
    parser.add_argument("--ledger-root", type=Path, default=DEFAULT_PROTECTED_TEMPORAL_LEDGER_ROOT)
    parser.add_argument("--keep-ledger", action="store_true", help="Do not delete/recreate the protected Temporal flow ledger root.")
    parser.add_argument("--report", type=Path, default=DEFAULT_PROTECTED_TEMPORAL_REPORT_PATH)
    parser.add_argument("--execution-mode", choices=("live-temporal", "direct-activity"), default="live-temporal")
    parser.add_argument("--address", default="localhost:7233")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--event-log", type=Path, default=DEFAULT_EVENT_LOG_PATH)
    parser.add_argument("--deposit-credits", default="25")
    parser.add_argument("--success-credits-offered", type=int, default=3)
    parser.add_argument("--failure-credits-offered", type=int, default=3)
    parser.add_argument("--token-count", type=int, default=3)
    parser.add_argument("--token-interval-seconds", type=float, default=0.1)
    parser.add_argument("--account-id", default="protected-temporal-smoke-client")
    parser.add_argument("--worker-id", default="protected-temporal-worker-01")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = find_repo_root(Path.cwd())

    try:
        report = asyncio.run(
            run_protected_temporal_flow_smoke(
                ProtectedTemporalFlowConfig(
                    repo_root=repo_root,
                    network=args.network,
                    deployment_path=args.deployment,
                    ledger_root=args.ledger_root,
                    report_path=args.report,
                    reset_ledger=not args.keep_ledger,
                    execution_mode=args.execution_mode,
                    temporal_address=args.address,
                    namespace=args.namespace,
                    event_log_path=args.event_log,
                    deposit_credits=args.deposit_credits,
                    success_credits_offered=args.success_credits_offered,
                    failure_credits_offered=args.failure_credits_offered,
                    token_count=args.token_count,
                    token_interval_seconds=args.token_interval_seconds,
                    account_id=args.account_id,
                    worker_id=args.worker_id,
                )
            )
        )
    except ProtectedModePretestError as exc:
        print(f"FAIL: {exc}")
        return 2
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1

    print("PASS: protected Temporal bridge-credit flow smoke succeeded")
    if report.get("report_path"):
        print(f"report: {report['report_path']}")
    print(f"execution_mode: {report['execution_mode']}")
    print(f"network: {report['network_profile']['network']}")
    print(f"chain_id: {report['network_profile']['chain_id']}")
    print(f"account_id: {report['account_id']}")
    print(f"success_ring: {report['steps']['success_flow']['decision']['ring']}")
    print(f"success_task_queue: {report['steps']['success_flow']['decision']['task_queue']}")
    print(f"success_hold_status: {report['steps']['success_flow']['final_hold']['status']}")
    print(f"failure_hold_status: {report['steps']['failure_flow']['final_hold']['status']}")
    print(f"final_available_credit_wei: {report['steps']['final_status']['totals']['available_credit_wei']}")
    print(f"final_spent_credit_wei: {report['steps']['final_status']['totals']['spent_credit_wei']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
