from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from main_computer.dev_chain_bridge import DevChainBridgeAdapter, DevChainBridgeError
from main_computer.dev_chain_smoke_support import bring_up_dev_chain_for_smoke, snapshot_balances_wei
from main_computer.temporal_fdb_hub_multi_hub_smoke import (
    DEFAULT_MULTI_HUB_A_URL,
    DEFAULT_MULTI_HUB_B_URL,
    HubMultiHubSmokeConfig,
    _execute_and_settle_multi_hub,
    _exercise_cross_hub_payout_lock,
    _quote_and_submit_requests_multi,
    _register_workers_multi,
    _wait_for_hub_health,
)
from main_computer.temporal_fdb_hub_node_market_smoke import (
    DEFAULT_AUTO_HUB_CLUSTER_FILE,
    DEFAULT_AUTO_HUB_NAMESPACE_PREFIX,
    DEFAULT_AUTO_HUB_ROOT,
    DEFAULT_HTTP_RETRY_ATTEMPTS,
    HubNodeMarketSmokeConfig,
    NodeMarketSmokeConfig,
    NodeMarketSmokeError,
    _ProgressReporter,
    _auto_hub_namespace,
    _bridge_audit_events,
    _bridge_fund_requester,
    _get_json,
    _heartbeat_loop,
    _local_lab_startup_help,
    _post_json,
    _verify_backends,
    _verify_expected_audit_types,
)
from main_computer.temporal_fdb_node_market_smoke import (
    NODE_MARKET_TASK_QUEUE_PREFIX,
    _positive_float as _validate_positive_float,
    _positive_int as _validate_positive_int,
    build_worker_nodes,
)
from tools.temporal_lab.event_log import DEFAULT_EVENT_LOG_PATH, read_jsonl_events
from tools.temporal_lab.local_temporal import DEFAULT_NAMESPACE


DEFAULT_STRESS_REPORT_PATH = Path("runtime") / "temporal_lab" / "temporal_fdb_hub_stress_report.json"
DEFAULT_STRESS_A_URL = DEFAULT_MULTI_HUB_A_URL
DEFAULT_STRESS_B_URL = DEFAULT_MULTI_HUB_B_URL


def _setup_status_fields(**fields: Any) -> str:
    return " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)


def _print_setup_status(event: str, **fields: Any) -> None:
    """Emit non-optional setup progress so long dev-chain bring-up never looks hung."""

    if event == "dev_chain_reset_output":
        line = str(fields.get("line") or "").strip()
        if line.startswith("SETUP:"):
            line = line.removeprefix("SETUP:").strip()
        if line:
            print(f"[stress setup] {line}", flush=True)
        return

    messages = {
        "dev_chain_setup_start": "preparing run-scoped dev-chain wallets and local chain",
        "dev_chain_reset_process_start": "launching tools/dev-chain-reset.py; Docker, Anvil, funding, and contract deployment may take a while",
        "dev_chain_reset_process_done": "dev-chain reset/deploy process exited",
        "dev_chain_deployment_load": "loading published dev-chain deployment metadata",
        "dev_chain_balance_snapshot_start": "reading initial dev-chain balances for the run rollup",
        "dev_chain_setup_ready": "dev-chain setup is ready; continuing into Hub/FDB/Temporal stress path",
        "dev_chain_bridge_adapter_ready": "dev-chain bridge adapter ready; HubCreditBridgeEscrow movements will be recorded",
        "dev_chain_bridge_deposit_start": "recording requester deposit on HubCreditBridgeEscrow",
        "dev_chain_bridge_deposit_done": "requester deposit recorded on HubCreditBridgeEscrow",
        "dev_chain_bridge_payout_start": "recording worker payout release on HubCreditBridgeEscrow",
        "dev_chain_bridge_payout_done": "worker payout release recorded on HubCreditBridgeEscrow",
        "dev_chain_bridge_tx_start": "submitting dev-chain bridge transaction",
        "dev_chain_bridge_tx_done": "dev-chain bridge transaction mined",
    }
    message = messages.get(event, event)
    details = _setup_status_fields(**fields)
    suffix = f" {details}" if details else ""
    print(f"[stress setup] {message}{suffix}", flush=True)


def _positive_int(value: object) -> int:
    try:
        return _validate_positive_int(value, field_name="value")
    except NodeMarketSmokeError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _positive_float(value: object) -> float:
    try:
        return _validate_positive_float(value, field_name="value")
    except NodeMarketSmokeError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


class _FreezeTracker:
    """Tracks last meaningful market/chatter progress and fails a run that stalls."""

    def __init__(self, *, timeout_seconds: float) -> None:
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.started_at = time.perf_counter()
        self._last_progress_at = self.started_at
        self._last_label = "start"
        self._lock = threading.Lock()

    def touch(self, label: str) -> None:
        with self._lock:
            self._last_progress_at = time.perf_counter()
            self._last_label = str(label)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.perf_counter()
            return {
                "elapsed_seconds": now - self.started_at,
                "idle_seconds": now - self._last_progress_at,
                "last_progress_label": self._last_label,
                "freeze_timeout_seconds": self.timeout_seconds,
            }

    async def watch(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await asyncio.sleep(min(1.0, max(0.1, self.timeout_seconds / 10.0)))
            snap = self.snapshot()
            if float(snap["idle_seconds"]) > self.timeout_seconds:
                raise NodeMarketSmokeError(
                    "Stress smoke freeze detector fired: "
                    f"idle_seconds={snap['idle_seconds']:.2f} "
                    f"timeout_seconds={self.timeout_seconds:.2f} "
                    f"last_progress_label={snap['last_progress_label']!r}"
                )


class _StressProgress:
    """Progress proxy that feeds the freeze detector and delegates human output."""

    def __init__(self, *, delegate: _ProgressReporter, tracker: _FreezeTracker) -> None:
        self.delegate = delegate
        self.tracker = tracker

    @property
    def interval_seconds(self) -> float:
        return self.delegate.interval_seconds

    def emit(self, event: str, **fields: Any) -> None:
        # Treat the events below as proof that the system is not frozen.  The
        # background chatter also calls touch directly when read-only probes work.
        if event in {
            "stress_chatter_progress",
            "stress_chatter_done",
            "multi_hub_request_submitted",
            "multi_hub_lease_received",
            "workflow_completed",
            "multi_hub_result_completed",
            "multi_hub_payout_lock_visible",
            "multi_hub_payout_confirmed",
            "multi_hub_audit_readback_ok",
            "report_write_ok",
            "done",
        } or event.endswith("_ok"):
            self.tracker.touch(event)
        self.delegate.emit(event, **fields)

    def heartbeat(self, key: str, event: str, **fields: Any) -> None:
        self.tracker.touch(event)
        self.delegate.heartbeat(key, event, **fields)


async def _await_or_freeze(awaitable: Any, *, watchdog_task: asyncio.Task[Any] | None) -> Any:
    """Await a long operation but fail immediately when the freeze watchdog fires."""

    task = awaitable if isinstance(awaitable, asyncio.Task) else asyncio.create_task(awaitable)
    if watchdog_task is None:
        return await task
    if watchdog_task.done():
        # Propagate an earlier freeze before starting more work.
        watchdog_task.result()
    done, pending = await asyncio.wait({task, watchdog_task}, return_when=asyncio.FIRST_COMPLETED)
    if watchdog_task in done:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        watchdog_task.result()
    return await task


@dataclass(frozen=True)
class HubStressSmokeConfig:
    repo_root: Path
    hub_a_url: str = DEFAULT_STRESS_A_URL
    hub_b_url: str = DEFAULT_STRESS_B_URL
    execution_mode: str = "live-temporal"
    temporal_address: str = "localhost:7233"
    namespace: str = DEFAULT_NAMESPACE
    report_path: Path | None = DEFAULT_STRESS_REPORT_PATH
    event_log_path: Path = DEFAULT_EVENT_LOG_PATH
    node_count: int = 80
    request_count: int = 30
    requested_ring: int = 2
    max_price_credits: int = 2
    deposit_credits: int = 200
    token_count: int = 3
    token_interval_seconds: float = 0.02
    keepalive_interval_seconds: float = 1.0
    account_id: str = "temporal-fdb-hub-stress-client"
    requester_wallet_address: str = "0x0000000000000000000000000000000000000cc1"
    worker_wallet_addresses: tuple[str, ...] = ()
    mockchain: bool = False
    dev_chain_run_id: str | None = None
    dev_chain_payout_admin_wallet_count: int = 4
    dev_chain_port_strategy: str = "auto"
    dev_chain_wait_timeout_seconds: float = 0.0
    dev_chain_deploy_timeout_seconds: float = 0.0
    model: str = "temporal-fdb-hub-stress-model"
    task_queue_prefix: str = NODE_MARKET_TASK_QUEUE_PREFIX + "-stress"
    run_id: str | None = None
    require_foundationdb_backends: bool = True
    emit_progress: bool = False
    progress_interval_seconds: float = 2.0
    http_timeout_seconds: float = 10.0
    http_retry_attempts: int = DEFAULT_HTTP_RETRY_ATTEMPTS
    hub_start_mode: str = "auto"
    hub_start_timeout_seconds: float = 60.0
    hub_namespace_prefix: str = DEFAULT_AUTO_HUB_NAMESPACE_PREFIX + "-stress"
    hub_root: Path = DEFAULT_AUTO_HUB_ROOT / "stress"
    cluster_file: Path = DEFAULT_AUTO_HUB_CLUSTER_FILE
    chatter_clients: int = 8
    chatter_rounds: int = 30
    chatter_interval_seconds: float = 0.02
    freeze_timeout_seconds: float = 30.0
    failover_hub_a: bool = True

    def resolved_report_path(self) -> Path | None:
        if self.report_path is None:
            return None
        return self.report_path if self.report_path.is_absolute() else self.repo_root / self.report_path

    def resolved_event_log_path(self) -> Path:
        return self.event_log_path if self.event_log_path.is_absolute() else self.repo_root / self.event_log_path

    def resolved_hub_root(self) -> Path:
        return self.hub_root if self.hub_root.is_absolute() else self.repo_root / self.hub_root

    def resolved_cluster_file(self) -> Path:
        return self.cluster_file if self.cluster_file.is_absolute() else self.repo_root / self.cluster_file

    def to_hub_config(self, hub_url: str, *, hub_name: str) -> HubNodeMarketSmokeConfig:
        return HubNodeMarketSmokeConfig(
            repo_root=self.repo_root,
            hub_url=hub_url,
            execution_mode=self.execution_mode,
            temporal_address=self.temporal_address,
            namespace=self.namespace,
            report_path=None,
            event_log_path=self.event_log_path,
            node_count=self.node_count,
            request_count=self.request_count,
            requested_ring=self.requested_ring,
            max_price_credits=self.max_price_credits,
            deposit_credits=self.deposit_credits,
            token_count=self.token_count,
            token_interval_seconds=self.token_interval_seconds,
            keepalive_interval_seconds=self.keepalive_interval_seconds,
            account_id=self.account_id,
            requester_wallet_address=self.requester_wallet_address,
            worker_wallet_addresses=self.worker_wallet_addresses,
            model=self.model,
            task_queue_prefix=self.task_queue_prefix,
            run_id=self.run_id,
            require_foundationdb_backends=self.require_foundationdb_backends,
            emit_progress=self.emit_progress,
            progress_interval_seconds=self.progress_interval_seconds,
            http_timeout_seconds=self.http_timeout_seconds,
            http_retry_attempts=self.http_retry_attempts,
            hub_start_mode=self.hub_start_mode,
            hub_start_timeout_seconds=self.hub_start_timeout_seconds,
            hub_namespace_prefix=self.hub_namespace_prefix,
            hub_root=self.resolved_hub_root() / hub_name,
            cluster_file=self.cluster_file,
        )

    def to_node_config(self) -> NodeMarketSmokeConfig:
        return NodeMarketSmokeConfig(
            repo_root=self.repo_root,
            execution_mode=self.execution_mode,  # type: ignore[arg-type]
            ledger_backend="foundationdb",
            report_path=None,
            event_log_path=self.event_log_path,
            temporal_address=self.temporal_address,
            namespace=self.namespace,
            node_count=self.node_count,
            request_count=self.request_count,
            requested_ring=self.requested_ring,
            max_price_credits=self.max_price_credits,
            deposit_credits=self.deposit_credits,
            token_count=self.token_count,
            token_interval_seconds=self.token_interval_seconds,
            task_queue_prefix=self.task_queue_prefix,
            run_id=self.run_id,
            emit_progress=self.emit_progress,
            progress_interval_seconds=self.progress_interval_seconds,
        )


async def _frontend_chatter_worker(
    *,
    worker_index: int,
    config: HubStressSmokeConfig,
    configs: dict[str, HubNodeMarketSmokeConfig],
    stop_event: asyncio.Event,
    stats: dict[str, int],
    stats_lock: asyncio.Lock,
    tracker: _FreezeTracker,
    progress: _StressProgress,
) -> None:
    labels = ("hub_a", "hub_b")
    for round_index in range(config.chatter_rounds):
        if stop_event.is_set():
            break
        label = labels[(worker_index + round_index) % len(labels)]
        cfg = configs[label]
        operation = (worker_index + round_index) % 6
        try:
            if operation == 0:
                _get_json(cfg.hub_url, "/api/hub/v1/health", timeout=cfg.http_timeout_seconds)
                key = "health_checks"
            elif operation == 1:
                _get_json(cfg.hub_url, "/api/hub/v1/status", timeout=cfg.http_timeout_seconds)
                key = "status_checks"
            elif operation == 2:
                _get_json(cfg.hub_url, "/api/hub/v1/credits", timeout=cfg.http_timeout_seconds)
                key = "credit_checks"
            elif operation == 3:
                _get_json(
                    cfg.hub_url,
                    f"/api/hub/v1/bridge/wallet-locks?{urlencode({'wallet_address': config.requester_wallet_address})}",
                    timeout=cfg.http_timeout_seconds,
                )
                key = "wallet_lock_checks"
            elif operation == 4:
                _bridge_audit_events(cfg, account_id=config.account_id, limit=10)
                key = "audit_readbacks"
            else:
                quote_payload = {
                    "account_id": config.account_id,
                    "client_node_id": config.account_id,
                    "model": config.model,
                    "prompt": f"Temporal FDB Hub stress quote probe {worker_index}-{round_index}",
                    "max_price_credits": config.max_price_credits,
                    "requested_ring": config.requested_ring,
                    "execution_mode": "worker_pull_v0",
                    "pricing_mode": "market_offer_fixed_per_call_v0",
                    "idempotency_key": f"{config.run_id}-stress-probe-{worker_index:02d}-{round_index:04d}",
                    "metadata": {"stress_probe": True, "worker_index": worker_index, "round_index": round_index},
                }
                _post_json(
                    cfg.hub_url,
                    "/api/hub/v1/requests/quote",
                    quote_payload,
                    timeout=cfg.http_timeout_seconds,
                    retry_attempts=cfg.http_retry_attempts,
                )
                key = "quote_probes"
            tracker.touch(f"chatter:{key}")
            async with stats_lock:
                stats[key] = stats.get(key, 0) + 1
                stats[f"{label}_requests"] = stats.get(f"{label}_requests", 0) + 1
                total = sum(stats.get(name, 0) for name in (
                    "health_checks",
                    "status_checks",
                    "credit_checks",
                    "wallet_lock_checks",
                    "audit_readbacks",
                    "quote_probes",
                ))
            if total and total % 50 == 0:
                progress.emit("stress_chatter_progress", total_operations=total)
        except Exception as exc:
            async with stats_lock:
                stats["transient_errors"] = stats.get("transient_errors", 0) + 1
            # Stress chatter is meant to apply load.  The market path and final
            # reconciliation decide pass/fail; chatter errors are reported and
            # bounded at the end.
            if config.emit_progress:
                progress.emit("stress_chatter_error", worker_index=worker_index, round_index=round_index, error=type(exc).__name__)
        await asyncio.sleep(max(0.0, float(config.chatter_interval_seconds)))


async def run_temporal_fdb_hub_stress_smoke(config: HubStressSmokeConfig) -> dict[str, Any]:
    run_id = config.run_id or f"{time.time_ns():x}"[-10:]
    object.__setattr__(config, "run_id", run_id)

    tracker = _FreezeTracker(timeout_seconds=config.freeze_timeout_seconds)
    base_progress = _ProgressReporter(
        enabled=config.emit_progress,
        interval_seconds=config.progress_interval_seconds,
    )
    progress = _StressProgress(delegate=base_progress, tracker=tracker)

    dev_chain_context = None
    dev_chain_bridge: DevChainBridgeAdapter | None = None
    dev_chain_movements: dict[str, Any] = {}
    bridge_backend = "mock-chain-lite" if config.mockchain else "dev-chain"
    if not config.mockchain:
        dev_chain_run_token = config.dev_chain_run_id or run_id
        progress.emit(
            "dev_chain_bring_up_start",
            bridge_backend=bridge_backend,
            dev_chain_run_id=dev_chain_run_token,
            node_wallet_count=config.node_count,
            payout_admin_wallet_count=config.dev_chain_payout_admin_wallet_count,
        )
        try:
            dev_chain_context = await asyncio.to_thread(
                bring_up_dev_chain_for_smoke,
                repo_root=config.repo_root,
                smoke_name="temporal-fdb-hub-stress",
                run_id=dev_chain_run_token,
                node_wallet_count=config.node_count,
                payout_admin_wallet_count=config.dev_chain_payout_admin_wallet_count,
                port_strategy=config.dev_chain_port_strategy,
                wait_timeout_s=config.dev_chain_wait_timeout_seconds,
                deploy_timeout_s=config.dev_chain_deploy_timeout_seconds,
                status=_print_setup_status,
            )
        except Exception as exc:
            raise NodeMarketSmokeError(f"Dev-chain bring-up failed before stress smoke: {exc}") from exc
        if dev_chain_context.requester_wallet_address:
            object.__setattr__(config, "requester_wallet_address", dev_chain_context.requester_wallet_address)
        object.__setattr__(config, "worker_wallet_addresses", dev_chain_context.node_wallet_addresses)
        try:
            dev_chain_bridge = DevChainBridgeAdapter.from_deployment(
                repo_root=config.repo_root,
                deployment_path=dev_chain_context.deployment_path,
                status=_print_setup_status,
            )
        except DevChainBridgeError as exc:
            raise NodeMarketSmokeError(f"Dev-chain bridge adapter setup failed before stress smoke: {exc}") from exc
        progress.emit(
            "dev_chain_ready",
            bridge_backend=bridge_backend,
            dev_chain_run_id=dev_chain_context.run_id,
            chain_id=dev_chain_context.chain_id,
            rpc_url=dev_chain_context.rpc_url,
            requester_wallet_address=dev_chain_context.requester_wallet_address,
            node_wallet_count=len(dev_chain_context.node_wallet_addresses),
            payout_admin_wallet_count=len(dev_chain_context.payout_admin_wallet_addresses),
            escrow_address=dev_chain_bridge.escrow_address,
        )

    config_a = config.to_hub_config(config.hub_a_url, hub_name="hub-a")
    config_b = config.to_hub_config(config.hub_b_url, hub_name="hub-b")
    configs = {"hub_a": config_a, "hub_b": config_b}

    event_log_path = config.resolved_event_log_path()
    event_log_path.parent.mkdir(parents=True, exist_ok=True)
    event_log_path.write_text("", encoding="utf-8")

    progress.emit(
        "stress_start",
        hub_a_url=config.hub_a_url,
        hub_b_url=config.hub_b_url,
        shared_namespace=_auto_hub_namespace(config_a),
        execution_mode=config.execution_mode,
        bridge_backend=bridge_backend,
        dev_chain_run_id=dev_chain_context.run_id if dev_chain_context is not None else None,
        nodes=config.node_count,
        requests=config.request_count,
        chatter_clients=config.chatter_clients,
        chatter_rounds=config.chatter_rounds,
        freeze_timeout_seconds=config.freeze_timeout_seconds,
    )

    started_a = None
    started_b = None
    hub_a_stopped_for_failover = False
    stop_event = asyncio.Event()
    chatter_stop_event = asyncio.Event()
    watchdog_stop_event = asyncio.Event()
    chatter_stats: dict[str, int] = {}
    chatter_stats_lock = asyncio.Lock()
    heartbeat_tasks: list[asyncio.Task[Any]] = []
    chatter_tasks: list[asyncio.Task[Any]] = []
    watchdog_task: asyncio.Task[Any] | None = None

    try:
        started_a = _wait_for_hub_health(config_a, progress=progress, label="stress_hub_a")
        started_b = _wait_for_hub_health(config_b, progress=progress, label="stress_hub_b")
        _verify_backends(config_a, progress=progress)
        _verify_backends(config_b, progress=progress)
        def _record_dev_chain_deposit(deposit_info: dict[str, Any]) -> dict[str, Any] | None:
            if dev_chain_bridge is None:
                return None
            deposit_id = str(deposit_info.get("deposit_id") or "")
            wallet_address = str(deposit_info.get("wallet_address") or config.requester_wallet_address)
            credits = int(deposit_info.get("credits") or config.deposit_credits)
            movement = dev_chain_bridge.record_requester_deposit(
                account_wallet_address=wallet_address,
                amount_units=credits,
                deposit_id=deposit_id,
                memo=f"hub stress requester deposit {config.run_id}",
            )
            payload = movement.to_dict()
            dev_chain_movements["requester_deposit"] = payload
            return payload

        bridge_funding = _bridge_fund_requester(
            config_a,
            progress=progress,
            before_confirm_callback=_record_dev_chain_deposit if dev_chain_bridge is not None else None,
        )

        nodes = build_worker_nodes(node_count=config.node_count, run_id=run_id, task_queue_prefix=config.task_queue_prefix)
        registration_counts = _register_workers_multi(configs, nodes, progress=progress)
        nodes_by_id = {node.node_id: node for node in nodes}

        for index, node in enumerate(nodes, start=1):
            heartbeat_cfg = config_a if index % 2 else config_b
            heartbeat_tasks.append(asyncio.create_task(_heartbeat_loop(config=heartbeat_cfg, node=node, stop_event=stop_event)))

        for worker_index in range(config.chatter_clients):
            chatter_tasks.append(
                asyncio.create_task(
                    _frontend_chatter_worker(
                        worker_index=worker_index,
                        config=config,
                        configs=configs,
                        stop_event=chatter_stop_event,
                        stats=chatter_stats,
                        stats_lock=chatter_stats_lock,
                        tracker=tracker,
                        progress=progress,
                    )
                )
            )
        watchdog_task = asyncio.create_task(tracker.watch(watchdog_stop_event))

        request_jobs, submitted, route_submit_counts = await _await_or_freeze(
            asyncio.to_thread(
                _quote_and_submit_requests_multi,
                configs,
                nodes_by_id,
                progress=progress,
            ),
            watchdog_task=watchdog_task,
        )
        tracker.touch("quote_submit_complete")

        leases, completed, surprise_payout_rejection, route_execution_counts = await _await_or_freeze(
            _execute_and_settle_multi_hub(
                configs,
                node_config=config.to_node_config(),
                nodes_by_id=nodes_by_id,
                request_jobs=request_jobs,
                event_log_path=event_log_path,
                progress=progress,
            ),
            watchdog_task=watchdog_task,
        )
        tracker.touch("execution_settlement_complete")

        selected_worker_ids = sorted({str(record.get("selected_worker_node_id", "")) for record in completed})
        def _record_dev_chain_payout(payout_info: dict[str, Any]) -> dict[str, Any] | None:
            if dev_chain_bridge is None:
                return None
            source_wallet = str(dev_chain_context.requester_wallet_address if dev_chain_context is not None else config.requester_wallet_address)
            payout_id = str(payout_info.get("payout_id") or "")
            wallet_address = str(payout_info.get("wallet_address") or "")
            credits = int(payout_info.get("credits") or max(1, int(config.max_price_credits or 1)))
            movement = dev_chain_bridge.record_worker_payout(
                source_account_wallet_address=source_wallet,
                worker_wallet_address=wallet_address,
                amount_units=credits,
                payout_id=payout_id,
                memo=f"hub stress worker payout {config.run_id}",
            )
            payload = movement.to_dict()
            dev_chain_movements["worker_payout"] = payload
            return payload

        payout = await _await_or_freeze(
            asyncio.to_thread(
                _exercise_cross_hub_payout_lock,
                configs,
                nodes_by_id=nodes_by_id,
                selected_worker_ids=selected_worker_ids,
                progress=progress,
                before_confirm_callback=_record_dev_chain_payout if dev_chain_bridge is not None else None,
            ),
            watchdog_task=watchdog_task,
        )
        tracker.touch("payout_complete")

        chatter_stop_event.set()
        await asyncio.gather(*chatter_tasks, return_exceptions=True)
        progress.emit("stress_chatter_done", **chatter_stats)

        if config.failover_hub_a and started_a is not None:
            progress.emit("stress_failover_stop_hub_a", hub_url=config_a.hub_url)
            started_a.stop()
            started_a = None
            hub_a_stopped_for_failover = True
            tracker.touch("hub_a_stopped")

        status_b = _get_json(config_b.hub_url, "/api/hub/v1/status", timeout=config_b.http_timeout_seconds)
        credit_status_b = _get_json(config_b.hub_url, "/api/hub/v1/credits", timeout=config_b.http_timeout_seconds)
        mock_chain_status = credit_status_b.get("mock_chain", {}) if isinstance(credit_status_b.get("mock_chain"), dict) else {}
        bridge_reconciliation_ok = (
            int(mock_chain_status.get("active_wallet_lock_count", 0) or 0) == 0
            and int(mock_chain_status.get("pending_payout_credit_wei", 0) or 0) == 0
            and int(mock_chain_status.get("pending_deposit_credit_wei", 0) or 0) == 0
        )
        if mock_chain_status and not bridge_reconciliation_ok:
            raise NodeMarketSmokeError(f"Mock chain bridge did not reconcile after stress run: {mock_chain_status}")

        requester_events = _bridge_audit_events(config_b, account_id=config.account_id, limit=400)
        worker_events = _bridge_audit_events(config_b, worker_node_id=str(payout.get("worker_node_id", "")), limit=400)
        _verify_expected_audit_types(
            label="stress requester",
            events=requester_events,
            expected_types={
                "bridge.deposit.requested",
                "bridge.deposit.confirmed",
                "hub.hold.created",
                "hub.hold.charged",
            },
        )
        _verify_expected_audit_types(
            label="stress worker",
            events=worker_events,
            expected_types={
                "hub.worker.earning.recorded",
                "bridge.payout.requested",
                "bridge.payout.confirmed",
            },
        )
        bridge_audit_readback_ok = True
        progress.emit(
            "stress_audit_readback_ok",
            requester_event_count=len(requester_events),
            worker_event_count=len(worker_events),
            worker_node_id=str(payout.get("worker_node_id", "")),
        )

        token_events = [event for event in read_jsonl_events(event_log_path) if event.get("event") == "token"]
        expected_token_events = config.request_count * config.token_count
        expected_spend = config.request_count * config.max_price_credits
        final_spent = int(credit_status_b.get("totals", {}).get("spent_credits", 0) or 0)

        selected_nodes = [node for node in nodes if node.node_id in selected_worker_ids]
        eligible_worker_count = sum(
            1 for node in nodes if node.ring <= config.requested_ring and node.price_credits <= config.max_price_credits
        )
        if eligible_worker_count > 1 and len(selected_worker_ids) < 2:
            raise NodeMarketSmokeError(
                "Stress run selected only one worker even though multiple eligible workers were registered. "
                f"selected_worker_ids={selected_worker_ids} eligible_worker_count={eligible_worker_count}"
            )
        if len(token_events) != expected_token_events:
            raise NodeMarketSmokeError(f"Expected {expected_token_events} fake token events, got {len(token_events)}.")
        if final_spent < expected_spend:
            raise NodeMarketSmokeError(f"Final spent credits {final_spent} is below expected stress spend {expected_spend}.")

        total_chatter_operations = sum(
            chatter_stats.get(name, 0)
            for name in (
                "health_checks",
                "status_checks",
                "credit_checks",
                "wallet_lock_checks",
                "audit_readbacks",
                "quote_probes",
            )
        )
        expected_min_chatter = max(1, config.chatter_clients * config.chatter_rounds // 3)
        if total_chatter_operations < expected_min_chatter:
            raise NodeMarketSmokeError(
                f"Stress chatter did too little work: total={total_chatter_operations} expected_min={expected_min_chatter} "
                f"stats={chatter_stats}"
            )

        transient_errors = int(chatter_stats.get("transient_errors", 0) or 0)
        if transient_errors > max(10, total_chatter_operations):
            raise NodeMarketSmokeError(f"Stress chatter had too many transient errors: stats={chatter_stats}")

        route_counts = {
            **registration_counts,
            **route_submit_counts,
            **route_execution_counts,
        }
        hub_b_after_failover_ok = bool(hub_a_stopped_for_failover and status_b.get("backend") == "foundationdb")
        if not config.failover_hub_a:
            hub_b_after_failover_ok = True

        dev_chain_rollup: dict[str, Any] | None = None
        if dev_chain_context is not None:
            rollup_addresses = (
                ([dev_chain_context.requester_wallet_address] if dev_chain_context.requester_wallet_address else [])
                + list(dev_chain_context.node_wallet_addresses)
                + list(dev_chain_context.payout_admin_wallet_addresses)
            )
            if dev_chain_bridge is not None:
                rollup_addresses.extend(
                    [
                        dev_chain_bridge.escrow_address,
                        dev_chain_bridge.bridge_controller_wallet.address,
                    ]
                )
            after_balances = snapshot_balances_wei(dev_chain_context.rpc_url, rollup_addresses)
            before_balances = dict(dev_chain_context.before_balances_wei)
            dev_chain_rollup = dev_chain_context.rollup()
            dev_chain_rollup["after_balances_wei"] = after_balances
            balance_deltas = {
                address: int(after_balances.get(address, 0)) - int(before_balances.get(address, 0))
                for address in sorted(set(before_balances) | set(after_balances))
            }
            dev_chain_rollup["balance_deltas_wei"] = balance_deltas
            dev_chain_rollup["balance_deltas_nonzero_wei"] = {
                address: delta for address, delta in balance_deltas.items() if int(delta) != 0
            }
            dev_chain_rollup["bridge_movements"] = dict(dev_chain_movements)
            dev_chain_rollup["escrow_address"] = dev_chain_bridge.escrow_address if dev_chain_bridge is not None else None
            dev_chain_rollup["bridge_controller_address"] = (
                dev_chain_bridge.bridge_controller_wallet.address if dev_chain_bridge is not None else None
            )
            dev_chain_rollup["bridge_lifecycle_note"] = (
                "This smoke brought up the dev chain, used fresh dev-chain requester/node identities, "
                "recorded deterministic HubCreditBridgeEscrow deposit and payout-release transactions, "
                "and then confirmed the existing Hub/FDB bridge lifecycle with those tx hashes in metadata. "
                "The Hub API seam still uses the current mock-chain endpoints until the server-side backend swap lands."
            )

            required_movements = {"requester_deposit", "worker_payout"}
            missing_movements = sorted(required_movements - set(dev_chain_movements))
            if missing_movements:
                raise NodeMarketSmokeError(f"Dev-chain bridge movement did not run: missing={missing_movements}")
            if not dev_chain_rollup["balance_deltas_nonzero_wei"]:
                raise NodeMarketSmokeError("Dev-chain bridge movement produced no non-zero balance deltas.")

        report = {
            "ok": True,
            "run_id": run_id,
            "hub_a_url": config.hub_a_url,
            "hub_b_url": config.hub_b_url,
            "shared_namespace": _auto_hub_namespace(config_a),
            "execution_mode": config.execution_mode,
            "bridge_backend": bridge_backend,
            "mockchain": bool(config.mockchain),
            "dev_chain_rollup": dev_chain_rollup,
            "dev_chain_run_id": dev_chain_context.run_id if dev_chain_context is not None else None,
            "dev_chain_requester_wallet_address": (
                dev_chain_context.requester_wallet_address if dev_chain_context is not None else None
            ),
            "dev_chain_node_wallet_count": (
                len(dev_chain_context.node_wallet_addresses) if dev_chain_context is not None else 0
            ),
            "dev_chain_payout_admin_wallet_count": (
                len(dev_chain_context.payout_admin_wallet_addresses) if dev_chain_context is not None else 0
            ),
            "dev_chain_escrow_address": dev_chain_bridge.escrow_address if dev_chain_bridge is not None else None,
            "dev_chain_bridge_movements": dict(dev_chain_movements),
            "dev_chain_balance_delta_nonzero_count": (
                len(dev_chain_rollup.get("balance_deltas_nonzero_wei", {})) if dev_chain_rollup is not None else 0
            ),
            "nodes_registered": len(nodes),
            "requests_submitted": len(submitted),
            "requests_completed": len(completed),
            "selected_worker_count": len(selected_worker_ids),
            "selected_worker_ids": selected_worker_ids,
            "eligible_worker_count": eligible_worker_count,
            "selected_worker_rings": sorted({node.ring for node in selected_nodes}),
            "selected_worker_prices": sorted({node.price_credits for node in selected_nodes}),
            "token_events": len(token_events),
            "expected_token_events": expected_token_events,
            "expected_spend_credits": expected_spend,
            "final_spent_credits_total": final_spent,
            "route_counts": route_counts,
            "stress_chatter_stats": dict(sorted(chatter_stats.items())),
            "stress_chatter_total_operations": total_chatter_operations,
            "freeze_detection_ok": True,
            "freeze_detector": tracker.snapshot(),
            "quote_submit_cross_hub": route_submit_counts.get("submit_hub_a", 0) > 0 and route_submit_counts.get("submit_hub_b", 0) > 0,
            "workers_registered_via_both_hubs": registration_counts.get("hub_a", 0) > 0 and registration_counts.get("hub_b", 0) > 0,
            "leases_polled_via_both_hubs": route_execution_counts.get("poll_hub_a", 0) > 0 and route_execution_counts.get("poll_hub_b", 0) > 0,
            "results_completed_via_both_hubs": route_execution_counts.get("completion_hub_a", 0) > 0 and route_execution_counts.get("completion_hub_b", 0) > 0,
            "cross_hub_result_replay_idempotent": bool(route_execution_counts.get("cross_hub_replay_idempotent")),
            "surprise_payout_rejected_active_work_cross_hub": bool(surprise_payout_rejection.get("rejected")),
            "payout_lock_visible_cross_hub": bool(payout.get("lock_visible_cross_hub")),
            "locked_wallet_excluded_cross_hub": bool(payout.get("locked_wallet_excluded_cross_hub")),
            "hub_a_failover_completed_via_hub_b": hub_b_after_failover_ok,
            "bridge_audit_readback_ok": bridge_audit_readback_ok,
            "bridge_reconciliation_ok": bridge_reconciliation_ok,
            "bridge_audit_event_count": int(mock_chain_status.get("audit_event_count", 0) or 0),
            "bridge_deposit_id": str(bridge_funding.get("deposit", {}).get("deposit_id", "")),
            "worker_payout_id": str(payout.get("payout", {}).get("payout_id", "")),
            "worker_payout_wallet_address": str(payout.get("wallet_address", "")),
            "event_log_path": str(event_log_path),
        }

        required = {
            "quote_submit_cross_hub": report["quote_submit_cross_hub"],
            "workers_registered_via_both_hubs": report["workers_registered_via_both_hubs"],
            "leases_polled_via_both_hubs": report["leases_polled_via_both_hubs"],
            "results_completed_via_both_hubs": report["results_completed_via_both_hubs"],
            "cross_hub_result_replay_idempotent": report["cross_hub_result_replay_idempotent"],
            "surprise_payout_rejected_active_work_cross_hub": report["surprise_payout_rejected_active_work_cross_hub"],
            "payout_lock_visible_cross_hub": report["payout_lock_visible_cross_hub"],
            "locked_wallet_excluded_cross_hub": report["locked_wallet_excluded_cross_hub"],
            "bridge_audit_readback_ok": report["bridge_audit_readback_ok"],
            "bridge_reconciliation_ok": report["bridge_reconciliation_ok"],
            "freeze_detection_ok": report["freeze_detection_ok"],
        }
        if config.failover_hub_a:
            required["hub_a_failover_completed_via_hub_b"] = report["hub_a_failover_completed_via_hub_b"]
        failed = sorted(key for key, value in required.items() if not value)
        if failed:
            raise NodeMarketSmokeError(f"Stress smoke failed required checks: {failed} report={report}")

        report_path = config.resolved_report_path()
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            progress.emit("report_write_ok", report_path=report_path)
        progress.emit("done", ok=True)
        return report
    finally:
        watchdog_stop_event.set()
        chatter_stop_event.set()
        stop_event.set()
        if watchdog_task is not None:
            if watchdog_task.done():
                try:
                    watchdog_task.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    # The main body propagates watchdog failures through
                    # _await_or_freeze.  Suppress duplicate task-finalizer noise.
                    pass
            else:
                watchdog_task.cancel()
        if chatter_tasks:
            await asyncio.gather(*chatter_tasks, return_exceptions=True)
        if heartbeat_tasks:
            await asyncio.gather(*heartbeat_tasks, return_exceptions=True)
        if started_a is not None:
            progress.emit("hub_autostart_stop", hub="stress_hub_a", hub_url=config_a.hub_url)
            started_a.stop()
        if started_b is not None:
            progress.emit("hub_autostart_stop", hub="stress_hub_b", hub_url=config_b.hub_url)
            started_b.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stress two FDB-backed Hubs with frontend chatter while the Temporal node market runs."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="Repository root containing exp-fdb-hub.py.")
    parser.add_argument("--hub-a-url", default=DEFAULT_STRESS_A_URL, help="Hub A URL.")
    parser.add_argument("--hub-b-url", default=DEFAULT_STRESS_B_URL, help="Hub B URL.")
    parser.add_argument("--execution-mode", choices=["live-temporal", "local"], default="live-temporal")
    parser.add_argument("--temporal-address", default="localhost:7233")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_STRESS_REPORT_PATH)
    parser.add_argument("--event-log-path", type=Path, default=DEFAULT_EVENT_LOG_PATH)
    parser.add_argument("--node-count", type=_positive_int, default=80)
    parser.add_argument("--request-count", type=_positive_int, default=30)
    parser.add_argument("--requested-ring", type=int, default=2)
    parser.add_argument("--max-price-credits", type=_positive_int, default=2)
    parser.add_argument("--deposit-credits", type=_positive_int, default=200)
    parser.add_argument("--token-count", type=_positive_int, default=3)
    parser.add_argument("--token-interval-seconds", type=_positive_float, default=0.02)
    parser.add_argument("--keepalive-interval-seconds", type=_positive_float, default=1.0)
    parser.add_argument("--account-id", default="temporal-fdb-hub-stress-client")
    parser.add_argument("--requester-wallet-address", default="0x0000000000000000000000000000000000000cc1")
    parser.add_argument("--mockchain", action="store_true", help="Use the legacy mocked chain-lite bridge path instead of bringing up a dev chain.")
    parser.add_argument("--dev-chain-run-id", default=None, help="Optional run id token for the run-scoped dev-chain wallet/deployment set.")
    parser.add_argument("--dev-chain-payout-admin-wallet-count", type=_positive_int, default=4)
    parser.add_argument(
        "--dev-chain-port-strategy",
        choices=["replace-project", "replace-any", "auto", "fail"],
        default="auto",
    )
    parser.add_argument("--dev-chain-wait-timeout-seconds", type=float, default=0.0)
    parser.add_argument("--dev-chain-deploy-timeout-seconds", type=float, default=0.0)
    parser.add_argument("--model", default="temporal-fdb-hub-stress-model")
    parser.add_argument("--task-queue-prefix", default=NODE_MARKET_TASK_QUEUE_PREFIX + "-stress")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--allow-non-fdb-backends", action="store_true")
    parser.add_argument("--progress", action="store_true", help="Emit detailed progress lines.")
    parser.add_argument("--progress-interval-seconds", type=_positive_float, default=2.0)
    parser.add_argument("--http-timeout-seconds", type=_positive_float, default=10.0)
    parser.add_argument("--http-retry-attempts", type=_positive_int, default=DEFAULT_HTTP_RETRY_ATTEMPTS)
    parser.add_argument("--hub-start-mode", choices=["auto", "never"], default="auto")
    parser.add_argument("--hub-start-timeout-seconds", type=_positive_float, default=60.0)
    parser.add_argument("--hub-namespace-prefix", default=DEFAULT_AUTO_HUB_NAMESPACE_PREFIX + "-stress")
    parser.add_argument("--hub-root", type=Path, default=DEFAULT_AUTO_HUB_ROOT / "stress")
    parser.add_argument("--cluster-file", type=Path, default=DEFAULT_AUTO_HUB_CLUSTER_FILE)
    parser.add_argument("--chatter-clients", type=_positive_int, default=8)
    parser.add_argument("--chatter-rounds", type=_positive_int, default=30)
    parser.add_argument("--chatter-interval-seconds", type=float, default=0.02)
    parser.add_argument("--freeze-timeout-seconds", type=_positive_float, default=30.0)
    parser.add_argument("--no-failover-hub-a", action="store_true", help="Do not stop Hub A before final readback.")
    return parser


def _config_from_args(args: argparse.Namespace) -> HubStressSmokeConfig:
    return HubStressSmokeConfig(
        repo_root=args.repo_root,
        hub_a_url=args.hub_a_url,
        hub_b_url=args.hub_b_url,
        execution_mode=args.execution_mode,
        temporal_address=args.temporal_address,
        namespace=args.namespace,
        report_path=args.report_path,
        event_log_path=args.event_log_path,
        node_count=args.node_count,
        request_count=args.request_count,
        requested_ring=args.requested_ring,
        max_price_credits=args.max_price_credits,
        deposit_credits=args.deposit_credits,
        token_count=args.token_count,
        token_interval_seconds=args.token_interval_seconds,
        keepalive_interval_seconds=args.keepalive_interval_seconds,
        account_id=args.account_id,
        requester_wallet_address=args.requester_wallet_address,
        mockchain=bool(args.mockchain),
        dev_chain_run_id=args.dev_chain_run_id,
        dev_chain_payout_admin_wallet_count=args.dev_chain_payout_admin_wallet_count,
        dev_chain_port_strategy=args.dev_chain_port_strategy,
        dev_chain_wait_timeout_seconds=args.dev_chain_wait_timeout_seconds,
        dev_chain_deploy_timeout_seconds=args.dev_chain_deploy_timeout_seconds,
        model=args.model,
        task_queue_prefix=args.task_queue_prefix,
        run_id=args.run_id,
        require_foundationdb_backends=not args.allow_non_fdb_backends,
        emit_progress=args.progress,
        progress_interval_seconds=args.progress_interval_seconds,
        http_timeout_seconds=args.http_timeout_seconds,
        http_retry_attempts=args.http_retry_attempts,
        hub_start_mode=args.hub_start_mode,
        hub_start_timeout_seconds=args.hub_start_timeout_seconds,
        hub_namespace_prefix=args.hub_namespace_prefix,
        hub_root=args.hub_root,
        cluster_file=args.cluster_file,
        chatter_clients=args.chatter_clients,
        chatter_rounds=args.chatter_rounds,
        chatter_interval_seconds=args.chatter_interval_seconds,
        freeze_timeout_seconds=args.freeze_timeout_seconds,
        failover_hub_a=not args.no_failover_hub_a,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = _config_from_args(args)
    try:
        report = asyncio.run(run_temporal_fdb_hub_stress_smoke(config))
    except NodeMarketSmokeError as exc:
        print(f"FAIL: Temporal FDB Hub stress smoke failed: {exc}")
        print(_local_lab_startup_help(config.temporal_address, config.namespace, config.resolved_cluster_file()))
        return 1

    print("PASS: Temporal FDB Hub stress smoke succeeded")
    for key in (
        "hub_a_url",
        "hub_b_url",
        "shared_namespace",
        "execution_mode",
        "bridge_backend",
        "dev_chain_run_id",
        "dev_chain_requester_wallet_address",
        "dev_chain_node_wallet_count",
        "dev_chain_payout_admin_wallet_count",
        "dev_chain_escrow_address",
        "dev_chain_balance_delta_nonzero_count",
        "nodes_registered",
        "requests_completed",
        "selected_worker_count",
        "selected_worker_ids",
        "stress_chatter_total_operations",
        "freeze_detection_ok",
        "quote_submit_cross_hub",
        "leases_polled_via_both_hubs",
        "results_completed_via_both_hubs",
        "cross_hub_result_replay_idempotent",
        "surprise_payout_rejected_active_work_cross_hub",
        "payout_lock_visible_cross_hub",
        "locked_wallet_excluded_cross_hub",
        "hub_a_failover_completed_via_hub_b",
        "bridge_audit_readback_ok",
        "bridge_reconciliation_ok",
    ):
        print(f"{key}: {report.get(key)}")
    if report.get("dev_chain_rollup"):
        rollup = report["dev_chain_rollup"]
        nonzero_deltas = rollup.get("balance_deltas_nonzero_wei", {})
        print("dev_chain_balance_deltas_nonzero_wei: " + json.dumps(nonzero_deltas, sort_keys=True))
        movement_summary = {
            name: {
                "amount_units": movement.get("amount_units"),
                "contract_id": movement.get("contract_id"),
                "transaction_hashes": movement.get("transaction_hashes", []),
            }
            for name, movement in dict(rollup.get("bridge_movements", {})).items()
            if isinstance(movement, dict)
        }
        print("dev_chain_bridge_movement_summary: " + json.dumps(movement_summary, sort_keys=True))
        print("dev_chain_bridge_lifecycle_note: " + str(rollup.get("bridge_lifecycle_note", "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
