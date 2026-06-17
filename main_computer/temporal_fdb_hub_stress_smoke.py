from __future__ import annotations

import argparse
import asyncio
import json
import random
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from main_computer.dev_chain_smoke_support import bring_up_dev_chain_for_smoke, snapshot_balances_wei
from main_computer.node_behavior_chaos import (
    NodeReconnectScenarioConfig,
    RequesterDisconnectResultRetentionScenarioConfig,
    WorkerConnectionReliabilityScenarioConfig,
    compact_node_behavior_summary,
    compact_requester_disconnect_result_retention_summary,
    compact_worker_connection_reliability_summary,
    exercise_node_reconnect_scenario,
    exercise_requester_disconnect_result_retention_scenario,
    exercise_worker_connection_reliability_scenario,
)
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
    _post_json_expect_http_error,
    _verify_backends,
    _verify_expected_audit_types,
    _worker_payload,
    _worker_wallet_address_for_config,
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
DEFAULT_AGENT_FEEDBACK_FAIL_MARKER = "FAILFAILFAIL"
DEFAULT_AGENT_FEEDBACK_NOISY_FAIL_CLAIM_RATE = 0.10
DEFAULT_AGENT_FEEDBACK_RANDOM_FALSE_CLAIM_RATE = 0.05


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


def _stress_requester_audit_readback_limit(request_count: object) -> int:
    """Fetch enough requester audit rows to include initial bridge deposit events.

    High-volume runs can append two requester hold audit rows per paid request
    after the initial bridge deposit.  A fixed limit of 400 is too shallow for
    request_count=250, so size the readback window with extra headroom.
    """

    try:
        count = max(0, int(request_count or 0))
    except (TypeError, ValueError):
        count = 0
    return max(400, count * 3 + 100)


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


def _ring_admission_config_path(config: "HubStressSmokeConfig", *, run_id: str) -> Path:
    return config.repo_root / "runtime" / "temporal_lab" / run_id / "ring.config.json"


def _stress_pick_ring0_allowlisted_nodes(nodes: list[Any]) -> list[Any]:
    preferred_indexes = [1, 2, 11, 12]
    selected: list[Any] = []
    for index in preferred_indexes:
        if 0 <= index < len(nodes):
            selected.append(nodes[index])
    if len(selected) < min(4, len(nodes)):
        selected_ids = {node.node_id for node in selected}
        for node in nodes:
            if node.node_id in selected_ids:
                continue
            selected.append(node)
            selected_ids.add(node.node_id)
            if len(selected) >= min(4, len(nodes)):
                break
    return selected[: min(4, len(nodes))]


def _node_with_ring(node: Any, ring: int) -> Any:
    try:
        return replace(node, ring=int(ring))
    except TypeError:
        data = dict(getattr(node, "__dict__", {}))
        data["ring"] = int(ring)
        return type(node)(**data)


def _prepare_ring_admission_world(
    *,
    config: "HubStressSmokeConfig",
    configs: dict[str, HubNodeMarketSmokeConfig],
    nodes: list[Any],
    run_id: str,
) -> tuple[list[Any], dict[str, Any]]:
    allowlisted_original = _stress_pick_ring0_allowlisted_nodes(nodes)
    allowlisted_ids = {node.node_id for node in allowlisted_original}
    non_allowlisted = [node for node in nodes if node.node_id not in allowlisted_ids]
    ring2_probe = non_allowlisted[0] if len(non_allowlisted) >= 1 else None
    ring1_probe = non_allowlisted[1] if len(non_allowlisted) >= 2 else None
    probe_ids = {node.node_id for node in (ring2_probe, ring1_probe) if node is not None}

    final_nodes: list[Any] = []
    for node in nodes:
        final_ring = 0 if node.node_id in allowlisted_ids else 3
        final_nodes.append(_node_with_ring(node, final_ring))

    final_nodes_by_id = {node.node_id: node for node in final_nodes}
    ring_config_path = _ring_admission_config_path(config, run_id=run_id)
    ring_config_path.parent.mkdir(parents=True, exist_ok=True)
    wallet_min_ring: dict[str, int] = {}
    config_a = configs["hub_a"]
    for node in allowlisted_original:
        final_node = final_nodes_by_id[node.node_id]
        wallet_min_ring[_worker_wallet_address_for_config(config_a, final_node).lower()] = 0
    ring_config_payload = {
        "default_min_ring": 3,
        "wallet_min_ring": dict(sorted(wallet_min_ring.items())),
    }
    ring_config_path.write_text(json.dumps(ring_config_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    readout = {
        "ring_config_enabled": True,
        "ring_config_path": str(ring_config_path),
        "ring_config_default_min_ring": 3,
        "ring_config_allowlisted_ring0_wallet_count": len(wallet_min_ring),
        "ring0_registration_requested_count": len(allowlisted_original),
        "ring3_default_wallet_count": max(0, len(nodes) - len(allowlisted_original)),
        "ring2_probe_node_id": ring2_probe.node_id if ring2_probe is not None else "",
        "ring1_probe_node_id": ring1_probe.node_id if ring1_probe is not None else "",
        "ring_admission_probe_ids": sorted(probe_ids),
    }
    return final_nodes, readout


def _register_workers_with_ring_admission_multi(
    configs: dict[str, HubNodeMarketSmokeConfig],
    nodes: list[Any],
    *,
    progress: _StressProgress,
    ring_readout: dict[str, Any],
) -> dict[str, int]:
    counts: dict[str, int] = {
        "hub_a": 0,
        "hub_b": 0,
        "ring0_allowed": 0,
        "ring0_rejected": 0,
        "ring3_default": 0,
        "ring2_probe_requested": 0,
        "ring2_probe_rejected": 0,
        "ring2_probe_fallback_ring3_succeeded": 0,
        "ring1_probe_requested": 0,
        "ring1_probe_rejected": 0,
        "ring1_probe_ring2_retry_rejected": 0,
        "ring1_probe_fallback_ring3_succeeded": 0,
        "unauthorized_high_ring_allowed": 0,
    }
    ring2_probe_id = str(ring_readout.get("ring2_probe_node_id") or "")
    ring1_probe_id = str(ring_readout.get("ring1_probe_node_id") or "")

    def _cfg_for_index(index: int) -> tuple[str, HubNodeMarketSmokeConfig]:
        label = "hub_a" if index % 2 else "hub_b"
        return label, configs[label]

    def _register(index: int, node: Any, *, ring: int, expect_fallback_bucket: str = "") -> dict[str, Any]:
        label, cfg = _cfg_for_index(index)
        ring_node = _node_with_ring(node, ring)
        result = _post_json(
            cfg.hub_url,
            "/api/hub/v1/workers/register",
            _worker_payload(ring_node, model=cfg.model, wallet_address=_worker_wallet_address_for_config(cfg, ring_node)),
            timeout=cfg.http_timeout_seconds,
            retry_attempts=cfg.http_retry_attempts,
        )
        worker = result.get("worker", {}) if isinstance(result.get("worker"), dict) else {}
        capabilities = worker.get("capabilities", {}) if isinstance(worker.get("capabilities"), dict) else {}
        effective_ring = capabilities.get("effective_ring", capabilities.get("assigned_ring"))
        if int(effective_ring) != int(ring):
            raise NodeMarketSmokeError(
                f"{label} did not preserve accepted effective ring for {node.node_id}: expected {ring}, got {effective_ring!r}"
            )
        counts[label] += 1
        if ring == 0:
            counts["ring0_allowed"] += 1
        elif ring == 3:
            if expect_fallback_bucket:
                counts[expect_fallback_bucket] += 1
            else:
                counts["ring3_default"] += 1
        return result

    def _expect_rejected(index: int, node: Any, *, ring: int, counter_key: str) -> dict[str, Any]:
        label, cfg = _cfg_for_index(index)
        probe_node = _node_with_ring(node, ring)
        payload = _worker_payload(probe_node, model=cfg.model, wallet_address=_worker_wallet_address_for_config(cfg, probe_node))
        result = _post_json_expect_http_error(
            cfg.hub_url,
            "/api/hub/v1/workers/register",
            payload,
            timeout=cfg.http_timeout_seconds,
            expected_status=403,
        )
        if result.get("error") != "ring_not_allowed":
            raise NodeMarketSmokeError(f"{label} rejected {node.node_id} with wrong ring admission error: {result}")
        counts[counter_key] += 1
        return result

    for index, node in enumerate(nodes, start=1):
        if node.node_id == ring2_probe_id:
            counts["ring2_probe_requested"] += 1
            rejected = _expect_rejected(index, node, ring=2, counter_key="ring2_probe_rejected")
            if rejected.get("fallback_ring") != 3:
                raise NodeMarketSmokeError(f"ring2 probe fallback was not ring 3: {rejected}")
            _register(index, node, ring=3, expect_fallback_bucket="ring2_probe_fallback_ring3_succeeded")
        elif node.node_id == ring1_probe_id:
            counts["ring1_probe_requested"] += 1
            rejected_ring1 = _expect_rejected(index, node, ring=1, counter_key="ring1_probe_rejected")
            rejected_ring2 = _expect_rejected(index, node, ring=2, counter_key="ring1_probe_ring2_retry_rejected")
            if rejected_ring1.get("fallback_ring") != 3 or rejected_ring2.get("fallback_ring") != 3:
                raise NodeMarketSmokeError(f"ring1 probe fallback was not ring 3: {rejected_ring1}, {rejected_ring2}")
            _register(index, node, ring=3, expect_fallback_bucket="ring1_probe_fallback_ring3_succeeded")
        else:
            _register(index, node, ring=int(node.ring))
        if index == 1 or index == len(nodes) or index % 10 == 0:
            progress.emit(
                "stress_ring_admission_worker_registered",
                worker_index=index,
                workers_total=len(nodes),
                node_id=node.node_id,
                ring=node.ring,
            )

    unauthorized_allowed = counts["unauthorized_high_ring_allowed"]
    if unauthorized_allowed:
        raise NodeMarketSmokeError(f"Unauthorized high-trust ring registrations were allowed: {unauthorized_allowed}")
    return counts


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
    hub_bridge_backend: str = "mock-chain"
    dev_chain_deployment_path: Path | None = None
    ring_config_path: Path | None = None
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
    node_reconnect_events: int = 4
    worker_connection_recover_events: int = 2
    worker_connection_lost_events: int = 2
    worker_connection_lease_seconds: float = 1.0
    worker_connection_lost_after_seconds: float = 1.25
    requester_disconnect_events: int = 2
    requester_disconnect_pickup_after_seconds: float = 0.1
    requester_result_retention_window_seconds: int = 3600
    random_bridge_funding_events: int = 3
    random_bridge_payout_events: int = 3
    random_bridge_failed_payout_events: int = 1
    agent_feedback_reviews: bool = True
    agent_feedback_fail_marker: str = DEFAULT_AGENT_FEEDBACK_FAIL_MARKER
    agent_feedback_noisy_fail_claim_rate: float = DEFAULT_AGENT_FEEDBACK_NOISY_FAIL_CLAIM_RATE
    agent_feedback_random_false_claim_rate: float = DEFAULT_AGENT_FEEDBACK_RANDOM_FALSE_CLAIM_RATE

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
            hub_bridge_backend=self.hub_bridge_backend,
            dev_chain_deployment_path=self.dev_chain_deployment_path,
            ring_config_path=self.ring_config_path,
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


def _dev_chain_movement_from_bridge_confirm(confirmed: dict[str, Any], *, payload_key: str) -> dict[str, Any]:
    payload = confirmed.get(payload_key, {}) if isinstance(confirmed.get(payload_key), dict) else {}
    metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
    dev_chain = metadata.get("dev_chain", {}) if isinstance(metadata.get("dev_chain"), dict) else {}
    movement = dev_chain.get("movement", {}) if isinstance(dev_chain.get("movement"), dict) else {}
    return movement


def _bridge_random_funding_event(
    config: HubNodeMarketSmokeConfig,
    *,
    event_index: int,
    credits: int,
    hub_label: str,
    progress: _StressProgress,
) -> dict[str, Any]:
    """Create and confirm one requester top-up through the Hub-owned bridge backend."""

    wallet_address = str(config.requester_wallet_address or "").strip()
    if not wallet_address:
        raise NodeMarketSmokeError("Cannot run bridge funding event without requester wallet address.")
    event_id = f"funding-{event_index:02d}"
    metadata = {
        "run_id": config.run_id,
        "smoke": "temporal_fdb_hub_stress",
        "bridge_random_event": True,
        "bridge_random_event_kind": "requester_funding",
        "bridge_random_event_id": event_id,
        "hub_label": hub_label,
    }

    mint = _post_json(
        config.hub_url,
        "/api/hub/v1/bridge/mock-chain/mint",
        {
            "wallet_address": wallet_address,
            "credits": credits,
            "idempotency_key": f"{config.run_id}-{event_id}-mint",
            "memo": "temporal fdb hub stress randomized requester funding",
            "metadata": metadata,
        },
        timeout=config.http_timeout_seconds,
        retry_attempts=config.http_retry_attempts,
    )
    deposit = _post_json(
        config.hub_url,
        "/api/hub/v1/bridge/deposits",
        {
            "wallet_address": wallet_address,
            "account_id": config.account_id,
            "credits": credits,
            "idempotency_key": f"{config.run_id}-{event_id}-deposit",
            "memo": "temporal fdb hub stress randomized requester bridge deposit",
            "metadata": metadata,
        },
        timeout=config.http_timeout_seconds,
        retry_attempts=config.http_retry_attempts,
    )
    deposit_payload = deposit.get("deposit", {}) if isinstance(deposit.get("deposit"), dict) else {}
    deposit_id = str(deposit_payload.get("deposit_id") or "")
    if not deposit_id:
        raise NodeMarketSmokeError(f"Randomized bridge funding event did not return deposit_id: {deposit}")
    confirmed = _post_json(
        config.hub_url,
        "/api/hub/v1/bridge/deposits/confirm",
        {
            "deposit_id": deposit_id,
            "metadata": metadata,
        },
        timeout=config.http_timeout_seconds,
        retry_attempts=config.http_retry_attempts,
    )
    movement = _dev_chain_movement_from_bridge_confirm(confirmed, payload_key="deposit")
    progress.emit(
        "stress_bridge_random_funding_confirmed",
        event_id=event_id,
        hub_label=hub_label,
        account_id=config.account_id,
        wallet_address=wallet_address,
        credits=credits,
        deposit_id=deposit_id,
        dev_chain_tx_count=len(movement.get("transaction_hashes", [])) if movement else 0,
    )
    return {
        "event_id": event_id,
        "event_kind": "requester_funding",
        "hub_label": hub_label,
        "credits": credits,
        "wallet_address": wallet_address,
        "deposit_id": deposit_id,
        "mint": mint,
        "deposit": deposit_payload,
        "confirmed": confirmed,
        "movement": movement,
    }


def _exercise_random_bridge_funding_events(
    configs: dict[str, HubNodeMarketSmokeConfig],
    *,
    event_count: int,
    seed: str,
    progress: _StressProgress,
) -> dict[str, Any]:
    """Run seeded requester top-up events while chatter is active."""

    count = max(0, int(event_count or 0))
    rng = random.Random(f"{seed}:funding")
    labels = ["hub_a", "hub_b"]
    events: list[dict[str, Any]] = []
    for event_index in range(1, count + 1):
        hub_label = labels[rng.randrange(len(labels))]
        cfg = configs[hub_label]
        credits = rng.randint(2, max(2, int(cfg.max_price_credits or 1) * 4))
        events.append(
            _bridge_random_funding_event(
                cfg,
                event_index=event_index,
                credits=credits,
                hub_label=hub_label,
                progress=progress,
            )
        )
    total_credits = sum(int(event.get("credits", 0) or 0) for event in events)
    return {
        "requested_count": count,
        "confirmed_count": len(events),
        "total_credits": total_credits,
        "events": events,
    }


def _bridge_random_confirmed_payout_event(
    request_config: HubNodeMarketSmokeConfig,
    confirm_config: HubNodeMarketSmokeConfig,
    *,
    event_index: int,
    worker: Any,
    wallet_address: str,
    credits: int,
    request_hub_label: str,
    confirm_hub_label: str,
    progress: _StressProgress,
) -> dict[str, Any]:
    event_id = f"payout-{event_index:02d}"
    metadata = {
        "run_id": request_config.run_id,
        "smoke": "temporal_fdb_hub_stress",
        "bridge_random_event": True,
        "bridge_random_event_kind": "worker_payout_confirmed",
        "bridge_random_event_id": event_id,
        "request_hub": request_hub_label,
        "confirm_hub": confirm_hub_label,
    }
    payout = _post_json(
        request_config.hub_url,
        "/api/hub/v1/bridge/payouts",
        {
            "wallet_address": wallet_address,
            "worker_node_id": worker.node_id,
            "credits": credits,
            "idempotency_key": f"{request_config.run_id}-{worker.node_id}-{event_id}",
            "memo": "temporal fdb hub stress randomized worker payout",
            "metadata": metadata,
        },
        timeout=request_config.http_timeout_seconds,
        retry_attempts=request_config.http_retry_attempts,
    )
    payout_payload = payout.get("payout", {}) if isinstance(payout.get("payout"), dict) else {}
    payout_id = str(payout_payload.get("payout_id") or "")
    if not payout_id:
        raise NodeMarketSmokeError(f"Randomized payout event did not return payout_id: {payout}")

    lock_status = _get_json(
        confirm_config.hub_url,
        f"/api/hub/v1/bridge/wallet-locks?{urlencode({'wallet_address': wallet_address})}",
        timeout=confirm_config.http_timeout_seconds,
    )
    if lock_status.get("locked") is not True:
        raise NodeMarketSmokeError(f"Randomized payout did not lock wallet {wallet_address}: {lock_status}")

    confirmed = _post_json(
        confirm_config.hub_url,
        "/api/hub/v1/bridge/payouts/confirm",
        {
            "payout_id": payout_id,
            "metadata": metadata,
        },
        timeout=confirm_config.http_timeout_seconds,
        retry_attempts=confirm_config.http_retry_attempts,
    )
    final_lock = _get_json(
        request_config.hub_url,
        f"/api/hub/v1/bridge/wallet-locks?{urlencode({'wallet_address': wallet_address})}",
        timeout=request_config.http_timeout_seconds,
    )
    if final_lock.get("locked") is True:
        raise NodeMarketSmokeError(f"Randomized payout confirmation did not unlock wallet {wallet_address}: {final_lock}")

    movement = _dev_chain_movement_from_bridge_confirm(confirmed, payload_key="payout")
    progress.emit(
        "stress_bridge_random_payout_confirmed",
        event_id=event_id,
        worker_node_id=worker.node_id,
        wallet_address=wallet_address,
        credits=credits,
        request_hub=request_hub_label,
        confirm_hub=confirm_hub_label,
        payout_id=payout_id,
        dev_chain_tx_count=len(movement.get("transaction_hashes", [])) if movement else 0,
    )
    return {
        "event_id": event_id,
        "event_kind": "worker_payout_confirmed",
        "worker_node_id": worker.node_id,
        "wallet_address": wallet_address,
        "credits": credits,
        "request_hub": request_hub_label,
        "confirm_hub": confirm_hub_label,
        "payout_id": payout_id,
        "payout": payout_payload,
        "confirmed": confirmed,
        "movement": movement,
    }


def _bridge_random_failed_payout_event(
    request_config: HubNodeMarketSmokeConfig,
    fail_config: HubNodeMarketSmokeConfig,
    *,
    event_index: int,
    worker: Any,
    wallet_address: str,
    credits: int,
    request_hub_label: str,
    fail_hub_label: str,
    progress: _StressProgress,
) -> dict[str, Any]:
    event_id = f"failed-payout-{event_index:02d}"
    metadata = {
        "run_id": request_config.run_id,
        "smoke": "temporal_fdb_hub_stress",
        "bridge_random_event": True,
        "bridge_random_event_kind": "worker_payout_failed",
        "bridge_random_event_id": event_id,
        "request_hub": request_hub_label,
        "fail_hub": fail_hub_label,
    }
    payout = _post_json(
        request_config.hub_url,
        "/api/hub/v1/bridge/payouts",
        {
            "wallet_address": wallet_address,
            "worker_node_id": worker.node_id,
            "credits": credits,
            "idempotency_key": f"{request_config.run_id}-{worker.node_id}-{event_id}",
            "memo": "temporal fdb hub stress randomized failed payout recovery",
            "metadata": metadata,
        },
        timeout=request_config.http_timeout_seconds,
        retry_attempts=request_config.http_retry_attempts,
    )
    payout_payload = payout.get("payout", {}) if isinstance(payout.get("payout"), dict) else {}
    payout_id = str(payout_payload.get("payout_id") or "")
    if not payout_id:
        raise NodeMarketSmokeError(f"Randomized failed-payout event did not return payout_id: {payout}")

    lock_status = _get_json(
        fail_config.hub_url,
        f"/api/hub/v1/bridge/wallet-locks?{urlencode({'wallet_address': wallet_address})}",
        timeout=fail_config.http_timeout_seconds,
    )
    if lock_status.get("locked") is not True:
        raise NodeMarketSmokeError(f"Randomized failed payout did not lock wallet {wallet_address}: {lock_status}")

    failed = _post_json(
        fail_config.hub_url,
        "/api/hub/v1/bridge/payouts/fail",
        {
            "payout_id": payout_id,
            "reason": "stress_randomized_failure_recovery",
            "metadata": metadata,
        },
        timeout=fail_config.http_timeout_seconds,
        retry_attempts=fail_config.http_retry_attempts,
    )
    final_lock = _get_json(
        request_config.hub_url,
        f"/api/hub/v1/bridge/wallet-locks?{urlencode({'wallet_address': wallet_address})}",
        timeout=request_config.http_timeout_seconds,
    )
    if final_lock.get("locked") is True:
        raise NodeMarketSmokeError(f"Randomized failed payout did not unlock wallet {wallet_address}: {final_lock}")

    progress.emit(
        "stress_bridge_random_payout_failed_recovered",
        event_id=event_id,
        worker_node_id=worker.node_id,
        wallet_address=wallet_address,
        credits=credits,
        request_hub=request_hub_label,
        fail_hub=fail_hub_label,
        payout_id=payout_id,
    )
    return {
        "event_id": event_id,
        "event_kind": "worker_payout_failed",
        "worker_node_id": worker.node_id,
        "wallet_address": wallet_address,
        "credits": credits,
        "request_hub": request_hub_label,
        "fail_hub": fail_hub_label,
        "payout_id": payout_id,
        "payout": payout_payload,
        "failed": failed,
        "movement": {},
    }


def _exercise_random_bridge_payout_events(
    configs: dict[str, HubNodeMarketSmokeConfig],
    *,
    nodes_by_id: dict[str, Any],
    selected_worker_ids: list[str],
    excluded_worker_ids: set[str],
    confirmed_count: int,
    failed_count: int,
    seed: str,
    progress: _StressProgress,
) -> dict[str, Any]:
    """Run seeded post-settlement payout confirmations/failures through both Hubs."""

    rng = random.Random(f"{seed}:payouts")
    labels = ["hub_a", "hub_b"]
    candidates = [worker_id for worker_id in selected_worker_ids if worker_id not in excluded_worker_ids]
    rng.shuffle(candidates)
    confirmed_target = max(0, int(confirmed_count or 0))
    failed_target = max(0, int(failed_count or 0))
    confirmed_events: list[dict[str, Any]] = []
    failed_events: list[dict[str, Any]] = []

    for event_index in range(1, confirmed_target + 1):
        if not candidates:
            break
        worker_id = candidates.pop(0)
        worker = nodes_by_id[worker_id]
        request_hub_label = labels[(event_index + rng.randrange(2)) % 2]
        confirm_hub_label = "hub_b" if request_hub_label == "hub_a" else "hub_a"
        request_config = configs[request_hub_label]
        confirm_config = configs[confirm_hub_label]
        wallet_address = _worker_wallet_address_for_config(request_config, worker)
        confirmed_events.append(
            _bridge_random_confirmed_payout_event(
                request_config,
                confirm_config,
                event_index=event_index,
                worker=worker,
                wallet_address=wallet_address,
                # Request the worker's full currently-claimable earning set.
                # The Hub ledger intentionally rejects partial worker-earning
                # payouts when the requested amount cuts through an earning
                # record boundary.  A zero-credit request means "all available"
                # for that worker, which keeps the randomized payout path inside
                # the currently supported bridge lifecycle.
                credits=0,
                request_hub_label=request_hub_label,
                confirm_hub_label=confirm_hub_label,
                progress=progress,
            )
        )

    for event_index in range(1, failed_target + 1):
        if not candidates:
            break
        worker_id = candidates.pop(0)
        worker = nodes_by_id[worker_id]
        request_hub_label = labels[(event_index + rng.randrange(2)) % 2]
        fail_hub_label = "hub_b" if request_hub_label == "hub_a" else "hub_a"
        request_config = configs[request_hub_label]
        fail_config = configs[fail_hub_label]
        wallet_address = _worker_wallet_address_for_config(request_config, worker)
        failed_events.append(
            _bridge_random_failed_payout_event(
                request_config,
                fail_config,
                event_index=event_index,
                worker=worker,
                wallet_address=wallet_address,
                # Request the worker's full currently-claimable earning set.
                # Failed payouts exercise recovery, not partial-earning slicing.
                credits=0,
                request_hub_label=request_hub_label,
                fail_hub_label=fail_hub_label,
                progress=progress,
            )
        )

    return {
        "confirmed_requested_count": confirmed_target,
        "confirmed_count": len(confirmed_events),
        "failed_requested_count": failed_target,
        "failed_count": len(failed_events),
        "confirmed_events": confirmed_events,
        "failed_events": failed_events,
    }



def _extract_result_content(payload: dict[str, Any]) -> str:
    """Return requester-visible content from the Hub pickup/result payload."""

    for key in ("response", "result"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            if "content" in candidate:
                return str(candidate.get("content") or "")
            nested = candidate.get("response")
            if isinstance(nested, dict) and "content" in nested:
                return str(nested.get("content") or "")
    request = payload.get("request")
    if isinstance(request, dict):
        response = request.get("response")
        if isinstance(response, dict):
            if "content" in response:
                return str(response.get("content") or "")
            nested = response.get("response")
            if isinstance(nested, dict) and "content" in nested:
                return str(nested.get("content") or "")
    return ""


def _extract_report_token(payload: dict[str, Any], *, fallback: dict[str, Any] | None = None) -> str:
    """Return the opaque requester report token from public pickup/readback payloads."""

    sources: list[dict[str, Any]] = []
    for key in ("request", "response", "result"):
        value = payload.get(key)
        if isinstance(value, dict):
            sources.append(value)
    if isinstance(fallback, dict):
        sources.append(fallback)
    for source in sources:
        receipt = source.get("receipt") if isinstance(source, dict) else None
        if isinstance(receipt, dict):
            token = str(receipt.get("report_token") or "").strip()
            if token:
                return token
        metadata = source.get("metadata") if isinstance(source, dict) else None
        hub_metadata = metadata.get("hub") if isinstance(metadata, dict) else None
        payment = hub_metadata.get("payment") if isinstance(hub_metadata, dict) else None
        if isinstance(payment, dict):
            token = str(payment.get("report_token") or "").strip()
            if token:
                return token
    return ""


def _count_requester_worker_identity_leaks(value: Any) -> int:
    """Count raw worker identifiers leaked in requester-facing result pickup payloads."""

    sensitive_keys = {
        "selected_worker_node_id",
        "selected_worker_instance_id",
        "worker_node_id",
        "worker_instance_id",
        "worker_wallet_address",
    }

    def _present(item: Any) -> bool:
        if item is None:
            return False
        if item == "":
            return False
        if item == [] or item == {}:
            return False
        return True

    if isinstance(value, dict):
        total = 0
        for key, nested in value.items():
            if str(key) in sensitive_keys and _present(nested):
                total += 1
            total += _count_requester_worker_identity_leaks(nested)
        return total
    if isinstance(value, list):
        return sum(_count_requester_worker_identity_leaks(item) for item in value)
    return 0


def _compact_worker_review_readout(
    *,
    selected_worker_ids: list[str],
    ring_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    workers = ring_summary.get("workers", []) if isinstance(ring_summary.get("workers"), list) else []
    by_id = {
        str(item.get("worker_node_id", "") or ""): item
        for item in workers
        if isinstance(item, dict) and str(item.get("worker_node_id", "") or "")
    }
    readout: list[dict[str, Any]] = []
    for worker_id in selected_worker_ids:
        summary = by_id.get(worker_id, {})
        tag_counts = summary.get("feedback_tag_counts", {}) if isinstance(summary.get("feedback_tag_counts"), dict) else {}
        readout.append(
            {
                "worker_node_id": worker_id,
                "completed_request_count": int(summary.get("completed_request_count", 0) or 0),
                "feedback_count": int(summary.get("feedback_count", 0) or 0),
                "accepted_count": int(summary.get("accepted_count", 0) or 0),
                "rejected_count": int(summary.get("rejected_count", 0) or 0),
                "needs_revision_count": int(summary.get("needs_revision_count", 0) or 0),
                "average_score": float(summary.get("average_score", 0.0) or 0.0),
                "fail_signal_observed_count": int(summary.get("fail_signal_observed_count", 0) or 0),
                "agent_complaint_count": int(summary.get("agent_complaint_count", 0) or 0),
                "noisy_requester_complaint_count": int(summary.get("noisy_requester_complaint_count", 0) or 0),
                "bounded_negative_feedback_count": int(summary.get("bounded_negative_feedback_count", 0) or 0),
                "feedback_tag_counts": dict(sorted(tag_counts.items())),
            }
        )
    return readout


def _exercise_agent_feedback_review_scenario(
    configs: dict[str, HubNodeMarketSmokeConfig],
    *,
    config: HubStressSmokeConfig,
    completed: list[dict[str, Any]],
    selected_worker_ids: list[str],
    bad_worker_node_id: str,
    fail_marker: str,
    progress: _StressProgress,
) -> dict[str, Any]:
    """Have the agent pick up results, review them, and read ring-control summaries.

    The requester-facing side only uses request_id plus the opaque report token.
    The private ring-control readback maps the resulting feedback to workers.
    """

    if not config.agent_feedback_reviews:
        return {
            "enabled": False,
            "seeded_bad_worker_count": 0,
            "seeded_bad_worker_id": "",
            "fail_marker": fail_marker,
            "worker_review_readout": [],
        }

    if not bad_worker_node_id:
        raise NodeMarketSmokeError("Agent feedback reviews are enabled but no seeded bad worker was selected.")

    config_a = configs["hub_a"]
    config_b = configs["hub_b"]
    labels = ("hub_a", "hub_b")
    pickup_count = 0
    fail_result_count = 0
    agent_review_submitted_count = 0
    agent_complaint_count = 0
    noisy_fail_seen_count = 0
    noisy_fail_complaint_count = 0
    noisy_random_complaint_count = 0
    random_false_complaint_count = 0
    feedback_submission_count = 0
    feedback_cross_hub_visible_count = 0
    requester_visible_worker_id_leak_count = 0
    feedback_errors: list[str] = []
    rng = random.Random(f"{config.run_id}:agent-feedback:noisy-reviewers")
    agent_run_id = f"agent-feedback-{config.run_id}"
    completed_by_request = {str(item.get("request_id", "") or ""): item for item in completed if isinstance(item, dict)}

    for index, request_id in enumerate(sorted(completed_by_request), start=1):
        pickup_label = labels[index % 2]
        submit_label = labels[(index + 1) % 2]
        readback_label = pickup_label
        pickup_cfg = configs[pickup_label]
        submit_cfg = configs[submit_label]
        readback_cfg = configs[readback_label]
        query = urlencode({"account_id": config.account_id, "client_node_id": config.account_id})
        pickup = _get_json(
            pickup_cfg.hub_url,
            f"/api/hub/v1/requests/{request_id}/result?{query}",
            timeout=pickup_cfg.http_timeout_seconds,
        )
        pickup_count += 1
        requester_visible_worker_id_leak_count += _count_requester_worker_identity_leaks(pickup)
        content = _extract_result_content(pickup)
        fail_seen = bool(fail_marker and fail_marker in content)
        if fail_seen:
            fail_result_count += 1
            noisy_fail_seen_count += 1
        fallback_record = completed_by_request.get(request_id, {})
        report_token = _extract_report_token(pickup, fallback=fallback_record)
        if not report_token:
            feedback_errors.append(f"{request_id}:missing_report_token")
            continue

        score = 1 if fail_seen else 5
        verdict = "rejected" if fail_seen else "accepted"
        tags = ["low_quality", "fail_signal"] if fail_seen else ["correct", "useful"]
        agent_payload = {
            "account_id": config.account_id,
            "requester_wallet_address": config.requester_wallet_address,
            "report_token": report_token,
            "score": score,
            "verdict": verdict,
            "feedback_tags": tags,
            "note": "Agent saw FAILFAILFAIL in the result." if fail_seen else "Agent accepted the result.",
            "source": "agent",
            "feedback_channel": f"agent-{index:04d}",
            "agent_run_id": agent_run_id,
            "agent_step_id": f"step-{index:04d}",
            "agent_label": "temporal-fdb-hub-stress-review",
        }
        submitted = _post_json(
            submit_cfg.hub_url,
            f"/api/hub/v1/requests/{request_id}/feedback",
            agent_payload,
            timeout=submit_cfg.http_timeout_seconds,
            retry_attempts=submit_cfg.http_retry_attempts,
        )
        if submitted.get("ok") is not True:
            feedback_errors.append(f"{request_id}:agent_submit_not_ok")
        else:
            agent_review_submitted_count += 1
            feedback_submission_count += 1
            if fail_seen:
                agent_complaint_count += 1

        if fail_seen and rng.random() < max(0.0, min(1.0, float(config.agent_feedback_noisy_fail_claim_rate))):
            noisy_payload = {
                "account_id": config.account_id,
                "requester_wallet_address": config.requester_wallet_address,
                "report_token": report_token,
                "score": 1,
                "verdict": "rejected",
                "feedback_tags": ["low_quality", "fail_signal", "noisy_detected_fail"],
                "note": "Noisy requester claimed the visible FAIL marker.",
                "source": "noisy_requester",
                "feedback_channel": f"noisy-fail-{index:04d}",
                "agent_run_id": agent_run_id,
                "agent_step_id": f"noisy-fail-{index:04d}",
                "agent_label": "temporal-fdb-hub-stress-noisy-review",
            }
            noisy_submitted = _post_json(
                submit_cfg.hub_url,
                f"/api/hub/v1/requests/{request_id}/feedback",
                noisy_payload,
                timeout=submit_cfg.http_timeout_seconds,
                retry_attempts=submit_cfg.http_retry_attempts,
            )
            if noisy_submitted.get("ok") is True:
                noisy_fail_complaint_count += 1
                feedback_submission_count += 1
            else:
                feedback_errors.append(f"{request_id}:noisy_fail_submit_not_ok")

        random_roll = rng.random()
        if random_roll < max(0.0, min(1.0, float(config.agent_feedback_random_false_claim_rate))):
            random_payload = {
                "account_id": config.account_id,
                "requester_wallet_address": config.requester_wallet_address,
                "report_token": report_token,
                "score": 1,
                "verdict": "rejected",
                "feedback_tags": ["low_quality", "random_complaint"],
                "note": "Noisy requester randomly complained regardless of result quality.",
                "source": "noisy_requester",
                "feedback_channel": f"noisy-random-{index:04d}",
                "agent_run_id": agent_run_id,
                "agent_step_id": f"noisy-random-{index:04d}",
                "agent_label": "temporal-fdb-hub-stress-random-review",
            }
            random_submitted = _post_json(
                submit_cfg.hub_url,
                f"/api/hub/v1/requests/{request_id}/feedback",
                random_payload,
                timeout=submit_cfg.http_timeout_seconds,
                retry_attempts=submit_cfg.http_retry_attempts,
            )
            if random_submitted.get("ok") is True:
                noisy_random_complaint_count += 1
                feedback_submission_count += 1
                if not fail_seen:
                    random_false_complaint_count += 1
            else:
                feedback_errors.append(f"{request_id}:noisy_random_submit_not_ok")

        readback = _get_json(
            readback_cfg.hub_url,
            f"/api/hub/v1/requests/{request_id}/feedback?{urlencode({'account_id': config.account_id})}",
            timeout=readback_cfg.http_timeout_seconds,
        )
        feedback_cross_hub_visible_count += int(readback.get("feedback_count", 0) or 0)

    summary_limit = max(500, len(completed) * 4 + 100)
    ring_summary = _get_json(
        config_b.hub_url,
        f"/api/hub/v1/ring-control/feedback-summary?{urlencode({'limit': str(summary_limit)})}",
        timeout=config_b.http_timeout_seconds,
    )
    worker_review_readout = _compact_worker_review_readout(
        selected_worker_ids=selected_worker_ids,
        ring_summary=ring_summary if isinstance(ring_summary, dict) else {},
    )
    bad_worker_rows = [row for row in worker_review_readout if row.get("worker_node_id") == bad_worker_node_id]
    ring_control_bad_worker_identified_count = sum(
        1
        for row in bad_worker_rows
        if int(row.get("fail_signal_observed_count", 0) or 0) > 0 and int(row.get("rejected_count", 0) or 0) > 0
    )
    ring_control_false_positive_worker_count = sum(
        1
        for row in worker_review_readout
        if row.get("worker_node_id") != bad_worker_node_id and int(row.get("rejected_count", 0) or 0) > 0
    )
    feedback_recorded_count = sum(int(row.get("feedback_count", 0) or 0) for row in worker_review_readout)
    money_movement_count = int(ring_summary.get("feedback_money_movement_count", 0) or 0) if isinstance(ring_summary, dict) else 0

    progress.emit(
        "stress_agent_feedback_reviews_done",
        bad_worker_node_id=bad_worker_node_id,
        fail_result_count=fail_result_count,
        agent_review_submitted_count=agent_review_submitted_count,
        feedback_recorded_count=feedback_recorded_count,
        requester_visible_worker_id_leak_count=requester_visible_worker_id_leak_count,
        ring_control_bad_worker_identified_count=ring_control_bad_worker_identified_count,
    )

    return {
        "enabled": True,
        "seeded_bad_worker_count": 1,
        "seeded_bad_worker_id": bad_worker_node_id,
        "fail_marker": fail_marker,
        "result_pickup_count": pickup_count,
        "failfailfail_result_count": fail_result_count,
        "agent_review_submitted_count": agent_review_submitted_count,
        "agent_complaint_count": agent_complaint_count,
        "noisy_fail_seen_count": noisy_fail_seen_count,
        "noisy_fail_complaint_count": noisy_fail_complaint_count,
        "noisy_random_complaint_count": noisy_random_complaint_count,
        "random_false_complaint_count": random_false_complaint_count,
        "feedback_submission_count": feedback_submission_count,
        "feedback_recorded_count": feedback_recorded_count,
        "feedback_cross_hub_visible_count": feedback_cross_hub_visible_count,
        "requester_visible_worker_id_leak_count": requester_visible_worker_id_leak_count,
        "ring_control_worker_review_summary_count": len(worker_review_readout),
        "ring_control_bad_worker_identified_count": ring_control_bad_worker_identified_count,
        "ring_control_false_positive_worker_count": ring_control_false_positive_worker_count,
        "feedback_money_movement_count": money_movement_count,
        "worker_review_readout": worker_review_readout,
        "feedback_errors": feedback_errors,
        "feedback_error_count": len(feedback_errors),
        "ring_control_feedback_summary": ring_summary if isinstance(ring_summary, dict) else {},
    }


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
    dev_chain_movements: dict[str, Any] = {}
    random_bridge_funding: dict[str, Any] = {"requested_count": 0, "confirmed_count": 0, "total_credits": 0, "events": []}
    random_bridge_payouts: dict[str, Any] = {"confirmed_requested_count": 0, "confirmed_count": 0, "failed_requested_count": 0, "failed_count": 0, "confirmed_events": [], "failed_events": []}
    agent_feedback_reviews: dict[str, Any] = {
        "enabled": bool(config.agent_feedback_reviews),
        "seeded_bad_worker_count": 0,
        "seeded_bad_worker_id": "",
        "fail_marker": config.agent_feedback_fail_marker,
        "worker_review_readout": [],
    }
    node_behavior: dict[str, Any] = {"requested_count": 0, "planned_count": 0, "completed_count": 0, "offline_seen_count": 0, "available_after_reconnect_count": 0, "duplicate_registration_count": 0, "cross_hub_status_ok": True, "events": []}
    worker_connection_reliability: dict[str, Any] = {
        "recover_requested_count": 0,
        "lost_requested_count": 0,
        "planned_count": 0,
        "completed_count": 0,
        "recover_before_timeout_completed_count": 0,
        "lost_timeout_failed_count": 0,
        "late_result_rejected_count": 0,
        "no_charge_after_lost_count": 0,
        "duplicate_result_replay_count": 0,
        "payout_integrity_ok": True,
        "events": [],
    }
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
        object.__setattr__(config, "hub_bridge_backend", "dev-chain")
        object.__setattr__(config, "dev_chain_deployment_path", dev_chain_context.deployment_path)
        progress.emit(
            "dev_chain_ready",
            bridge_backend=bridge_backend,
            dev_chain_run_id=dev_chain_context.run_id,
            chain_id=dev_chain_context.chain_id,
            rpc_url=dev_chain_context.rpc_url,
            requester_wallet_address=dev_chain_context.requester_wallet_address,
            node_wallet_count=len(dev_chain_context.node_wallet_addresses),
            payout_admin_wallet_count=len(dev_chain_context.payout_admin_wallet_addresses),
            escrow_address=dev_chain_context.bridge_escrow_address,
            hub_bridge_backend="dev-chain",
        )
    else:
        object.__setattr__(config, "hub_bridge_backend", "mock-chain")
        object.__setattr__(config, "dev_chain_deployment_path", None)

    base_nodes = build_worker_nodes(node_count=config.node_count, run_id=run_id, task_queue_prefix=config.task_queue_prefix)
    object.__setattr__(config, "ring_config_path", _ring_admission_config_path(config, run_id=run_id))
    config_a = config.to_hub_config(config.hub_a_url, hub_name="hub-a")
    config_b = config.to_hub_config(config.hub_b_url, hub_name="hub-b")
    configs = {"hub_a": config_a, "hub_b": config_b}
    nodes, ring_admission_readout = _prepare_ring_admission_world(
        config=config,
        configs=configs,
        nodes=base_nodes,
        run_id=run_id,
    )

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
        ring_status_a = _get_json(config_a.hub_url, "/api/hub/v1/status", timeout=config_a.http_timeout_seconds)
        ring_status_b = _get_json(config_b.hub_url, "/api/hub/v1/status", timeout=config_b.http_timeout_seconds)
        ring_hash_a = str(ring_status_a.get("ring_config_hash") or "")
        ring_hash_b = str(ring_status_b.get("ring_config_hash") or "")
        ring_admission_readout.update(
            {
                "ring_config_loaded_by_hub_a": bool(ring_status_a.get("ring_config_load_ok")),
                "ring_config_loaded_by_hub_b": bool(ring_status_b.get("ring_config_load_ok")),
                "ring_config_hash": ring_hash_a,
                "ring_config_hash_match": bool(ring_hash_a and ring_hash_a == ring_hash_b),
                "ring_admission_config_hash_match": bool(ring_hash_a and ring_hash_a == ring_hash_b),
                "hub_a_ring_config_hash": ring_hash_a,
                "hub_b_ring_config_hash": ring_hash_b,
            }
        )
        if not ring_admission_readout["ring_admission_config_hash_match"]:
            raise NodeMarketSmokeError(f"Hub ring admission config hashes do not match: A={ring_hash_a!r} B={ring_hash_b!r}")
        bridge_funding = _bridge_fund_requester(
            config_a,
            progress=progress,
        )
        deposit_confirmed = bridge_funding.get("confirmed", {}) if isinstance(bridge_funding.get("confirmed"), dict) else {}
        deposit_payload = deposit_confirmed.get("deposit", {}) if isinstance(deposit_confirmed.get("deposit"), dict) else {}
        deposit_metadata = deposit_payload.get("metadata", {}) if isinstance(deposit_payload.get("metadata"), dict) else {}
        deposit_dev_chain = deposit_metadata.get("dev_chain", {}) if isinstance(deposit_metadata.get("dev_chain"), dict) else {}
        deposit_movement = deposit_dev_chain.get("movement", {}) if isinstance(deposit_dev_chain.get("movement"), dict) else {}
        if deposit_movement:
            dev_chain_movements["requester_deposit"] = deposit_movement

        registration_counts = _register_workers_with_ring_admission_multi(
            configs,
            nodes,
            progress=progress,
            ring_readout=ring_admission_readout,
        )
        nodes_by_id = {node.node_id: node for node in nodes}

        node_behavior = await _await_or_freeze(
            asyncio.to_thread(
                exercise_node_reconnect_scenario,
                configs=configs,
                nodes=nodes,
                scenario=NodeReconnectScenarioConfig(
                    event_count=config.node_reconnect_events,
                    seed=run_id,
                    verify_cross_hub=True,
                ),
                post_json=_post_json,
                get_json=_get_json,
                worker_payload=_worker_payload,
                worker_wallet_address=_worker_wallet_address_for_config,
                progress=progress,
            ),
            watchdog_task=watchdog_task,
        )
        tracker.touch("node_behavior_reconnect_complete")

        worker_connection_reliability = await _await_or_freeze(
            asyncio.to_thread(
                exercise_worker_connection_reliability_scenario,
                configs=configs,
                nodes=nodes,
                scenario=WorkerConnectionReliabilityScenarioConfig(
                    recover_before_timeout_events=config.worker_connection_recover_events,
                    lost_timeout_events=config.worker_connection_lost_events,
                    lease_seconds=config.worker_connection_lease_seconds,
                    lost_after_seconds=config.worker_connection_lost_after_seconds,
                    seed=run_id,
                    verify_cross_hub=True,
                ),
                post_json=_post_json,
                get_json=_get_json,
                post_json_expect_http_error=_post_json_expect_http_error,
                worker_payload=_worker_payload,
                worker_wallet_address=_worker_wallet_address_for_config,
                progress=progress,
            ),
            watchdog_task=watchdog_task,
        )
        tracker.touch("worker_connection_reliability_complete")

        requester_disconnect_result_retention = await _await_or_freeze(
            asyncio.to_thread(
                exercise_requester_disconnect_result_retention_scenario,
                configs=configs,
                nodes=nodes,
                scenario=RequesterDisconnectResultRetentionScenarioConfig(
                    event_count=config.requester_disconnect_events,
                    pickup_after_seconds=config.requester_disconnect_pickup_after_seconds,
                    result_retention_window_seconds=config.requester_result_retention_window_seconds,
                    seed=run_id,
                    verify_cross_hub=True,
                ),
                post_json=_post_json,
                get_json=_get_json,
                progress=progress,
            ),
            watchdog_task=watchdog_task,
        )
        tracker.touch("requester_disconnect_result_retention_complete")

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

        random_bridge_funding = await _await_or_freeze(
            asyncio.to_thread(
                _exercise_random_bridge_funding_events,
                configs,
                event_count=config.random_bridge_funding_events,
                seed=run_id,
                progress=progress,
            ),
            watchdog_task=watchdog_task,
        )
        for event in random_bridge_funding.get("events", []):
            if isinstance(event, dict) and event.get("movement"):
                dev_chain_movements[f"requester_random_funding_{event.get('event_id', len(dev_chain_movements))}"] = event["movement"]
        tracker.touch("random_bridge_funding_complete")

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

        result_content_by_worker_node_id: dict[str, str] = {}
        agent_feedback_bad_worker_id = ""
        if config.agent_feedback_reviews and request_jobs:
            selected_for_review = sorted({match.worker.node_id for match, _request in request_jobs})
            if selected_for_review:
                agent_feedback_bad_worker_id = random.Random(f"{run_id}:agent-feedback:bad-worker").choice(selected_for_review)
                result_content_by_worker_node_id[agent_feedback_bad_worker_id] = str(
                    config.agent_feedback_fail_marker or DEFAULT_AGENT_FEEDBACK_FAIL_MARKER
                )
                progress.emit(
                    "stress_agent_feedback_bad_worker_seeded",
                    worker_node_id=agent_feedback_bad_worker_id,
                    selected_worker_candidate_count=len(selected_for_review),
                    fail_marker=config.agent_feedback_fail_marker,
                )

        leases, completed, surprise_payout_rejection, route_execution_counts = await _await_or_freeze(
            _execute_and_settle_multi_hub(
                configs,
                node_config=config.to_node_config(),
                nodes_by_id=nodes_by_id,
                request_jobs=request_jobs,
                event_log_path=event_log_path,
                progress=progress,
                result_content_by_worker_node_id=result_content_by_worker_node_id,
            ),
            watchdog_task=watchdog_task,
        )
        tracker.touch("execution_settlement_complete")

        selected_worker_ids = sorted({str(record.get("selected_worker_node_id", "")) for record in completed})
        if config.agent_feedback_reviews:
            agent_feedback_reviews = await _await_or_freeze(
                asyncio.to_thread(
                    _exercise_agent_feedback_review_scenario,
                    configs,
                    config=config,
                    completed=completed,
                    selected_worker_ids=selected_worker_ids,
                    bad_worker_node_id=agent_feedback_bad_worker_id,
                    fail_marker=str(config.agent_feedback_fail_marker or DEFAULT_AGENT_FEEDBACK_FAIL_MARKER),
                    progress=progress,
                ),
                watchdog_task=watchdog_task,
            )
            tracker.touch("agent_feedback_reviews_complete")
        payout = await _await_or_freeze(
            asyncio.to_thread(
                _exercise_cross_hub_payout_lock,
                configs,
                nodes_by_id=nodes_by_id,
                selected_worker_ids=selected_worker_ids,
                progress=progress,
            ),
            watchdog_task=watchdog_task,
        )
        payout_confirmed = payout.get("confirmed", {}) if isinstance(payout.get("confirmed"), dict) else {}
        payout_payload = payout_confirmed.get("payout", {}) if isinstance(payout_confirmed.get("payout"), dict) else {}
        payout_metadata = payout_payload.get("metadata", {}) if isinstance(payout_payload.get("metadata"), dict) else {}
        payout_dev_chain = payout_metadata.get("dev_chain", {}) if isinstance(payout_metadata.get("dev_chain"), dict) else {}
        payout_movement = payout_dev_chain.get("movement", {}) if isinstance(payout_dev_chain.get("movement"), dict) else {}
        if payout_movement:
            dev_chain_movements["worker_payout"] = payout_movement
        tracker.touch("payout_complete")

        random_bridge_payouts = await _await_or_freeze(
            asyncio.to_thread(
                _exercise_random_bridge_payout_events,
                configs,
                nodes_by_id=nodes_by_id,
                selected_worker_ids=selected_worker_ids,
                excluded_worker_ids={str(payout.get("worker_node_id", ""))},
                confirmed_count=config.random_bridge_payout_events,
                failed_count=config.random_bridge_failed_payout_events,
                seed=run_id,
                progress=progress,
            ),
            watchdog_task=watchdog_task,
        )
        for event in random_bridge_payouts.get("confirmed_events", []):
            if isinstance(event, dict) and event.get("movement"):
                dev_chain_movements[f"worker_random_payout_{event.get('event_id', len(dev_chain_movements))}"] = event["movement"]
        tracker.touch("random_bridge_payouts_complete")

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

        requester_audit_limit = _stress_requester_audit_readback_limit(config.request_count)
        requester_events = _bridge_audit_events(config_b, account_id=config.account_id, limit=requester_audit_limit)
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
            requester_audit_limit=requester_audit_limit,
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
            if dev_chain_context.bridge_escrow_address:
                rollup_addresses.append(dev_chain_context.bridge_escrow_address)
            if dev_chain_context.hub_admin_wallet_address:
                rollup_addresses.append(dev_chain_context.hub_admin_wallet_address)
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
            dev_chain_rollup["random_bridge_event_summary"] = {
                "funding_requested_count": int(random_bridge_funding.get("requested_count", 0) or 0),
                "funding_confirmed_count": int(random_bridge_funding.get("confirmed_count", 0) or 0),
                "funding_total_credits": int(random_bridge_funding.get("total_credits", 0) or 0),
                "payout_confirmed_requested_count": int(random_bridge_payouts.get("confirmed_requested_count", 0) or 0),
                "payout_confirmed_count": int(random_bridge_payouts.get("confirmed_count", 0) or 0),
                "payout_failed_requested_count": int(random_bridge_payouts.get("failed_requested_count", 0) or 0),
                "payout_failed_count": int(random_bridge_payouts.get("failed_count", 0) or 0),
            }
            dev_chain_rollup["escrow_address"] = dev_chain_context.bridge_escrow_address
            dev_chain_rollup["bridge_controller_address"] = dev_chain_context.hub_admin_wallet_address
            dev_chain_rollup["bridge_lifecycle_note"] = (
                "This smoke brought up the dev chain, used fresh dev-chain requester/node identities, "
                "used Hub-owned dev-chain bridge backend execution for seeded funding top-ups, "
                "payout failures, and payout-release transactions, and confirmed "
                "the existing Hub/FDB bridge lifecycle with those tx hashes in metadata."
            )

            required_movements = {"requester_deposit", "worker_payout"}
            missing_movements = sorted(required_movements - set(dev_chain_movements))
            if missing_movements:
                raise NodeMarketSmokeError(f"Dev-chain bridge movement did not run: missing={missing_movements}")
            if int(random_bridge_funding.get("confirmed_count", 0) or 0) < min(config.random_bridge_funding_events, 1):
                raise NodeMarketSmokeError(f"Random bridge funding did not run: {random_bridge_funding}")
            expected_random_payout_min = min(config.random_bridge_payout_events, max(0, len(selected_worker_ids) - 1), 1)
            if int(random_bridge_payouts.get("confirmed_count", 0) or 0) < expected_random_payout_min:
                raise NodeMarketSmokeError(f"Random bridge payout confirmations did not run: {random_bridge_payouts}")
            if not dev_chain_rollup["balance_deltas_nonzero_wei"]:
                raise NodeMarketSmokeError("Dev-chain bridge movement produced no non-zero balance deltas.")

        final_ring_status_a = _get_json(config_a.hub_url, "/api/hub/v1/status", timeout=config_a.http_timeout_seconds)
        final_ring_status_b = _get_json(config_b.hub_url, "/api/hub/v1/status", timeout=config_b.http_timeout_seconds)
        ring_rejection_audit_count = max(
            int(final_ring_status_a.get("ring_admission_rejection_audit_count", 0) or 0),
            int(final_ring_status_b.get("ring_admission_rejection_audit_count", 0) or 0),
        )
        ring_admission_readout.update(
            {
                "ring0_registration_allowed_count": int(registration_counts.get("ring0_allowed", 0) or 0),
                "ring0_registration_rejected_count": int(registration_counts.get("ring0_rejected", 0) or 0),
                "ring3_default_registration_count": (
                    int(registration_counts.get("ring3_default", 0) or 0)
                    + int(registration_counts.get("ring2_probe_fallback_ring3_succeeded", 0) or 0)
                    + int(registration_counts.get("ring1_probe_fallback_ring3_succeeded", 0) or 0)
                ),
                "ring2_probe_requested_count": int(registration_counts.get("ring2_probe_requested", 0) or 0),
                "ring2_probe_rejected_count": int(registration_counts.get("ring2_probe_rejected", 0) or 0),
                "ring2_probe_fallback_ring3_succeeded_count": int(registration_counts.get("ring2_probe_fallback_ring3_succeeded", 0) or 0),
                "ring1_probe_requested_count": int(registration_counts.get("ring1_probe_requested", 0) or 0),
                "ring1_probe_rejected_count": int(registration_counts.get("ring1_probe_rejected", 0) or 0),
                "ring1_probe_ring2_retry_rejected_count": int(registration_counts.get("ring1_probe_ring2_retry_rejected", 0) or 0),
                "ring1_probe_fallback_ring3_succeeded_count": int(registration_counts.get("ring1_probe_fallback_ring3_succeeded", 0) or 0),
                "unauthorized_high_ring_registration_allowed_count": int(registration_counts.get("unauthorized_high_ring_allowed", 0) or 0),
                "ring_admission_rejection_audit_count": ring_rejection_audit_count,
                "ring_admission_cross_hub_consistent": bool(ring_admission_readout.get("ring_admission_config_hash_match")),
            }
        )
        if ring_admission_readout["unauthorized_high_ring_registration_allowed_count"] != 0:
            raise NodeMarketSmokeError(f"Unauthorized high-trust ring registration was allowed: {ring_admission_readout}")
        if ring_admission_readout["ring_admission_rejection_audit_count"] < 3:
            raise NodeMarketSmokeError(f"Ring admission rejection audit count is too low: {ring_admission_readout}")

        report = {
            "ok": True,
            "run_id": run_id,
            "hub_a_url": config.hub_a_url,
            "hub_b_url": config.hub_b_url,
            "shared_namespace": _auto_hub_namespace(config_a),
            "execution_mode": config.execution_mode,
            "bridge_backend": bridge_backend,
            "mockchain": bool(config.mockchain),
            **ring_admission_readout,
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
            "dev_chain_escrow_address": dev_chain_context.bridge_escrow_address if dev_chain_context is not None else None,
            "dev_chain_bridge_movements": dict(dev_chain_movements),
            "bridge_random_funding_event_count": int(random_bridge_funding.get("confirmed_count", 0) or 0),
            "bridge_random_funding_total_credits": int(random_bridge_funding.get("total_credits", 0) or 0),
            "bridge_random_payout_confirmed_count": int(random_bridge_payouts.get("confirmed_count", 0) or 0),
            "bridge_random_payout_failed_count": int(random_bridge_payouts.get("failed_count", 0) or 0),
            "bridge_active_work_payout_rejection_count": 1 if bool(surprise_payout_rejection.get("rejected")) else 0,
            "node_reconnect_requested_count": int(node_behavior.get("requested_count", 0) or 0),
            "node_reconnect_completed_count": int(node_behavior.get("completed_count", 0) or 0),
            "node_reconnect_offline_seen_count": int(node_behavior.get("offline_seen_count", 0) or 0),
            "node_reconnect_available_after_reconnect_count": int(node_behavior.get("available_after_reconnect_count", 0) or 0),
            "node_reconnect_duplicate_registration_count": int(node_behavior.get("duplicate_registration_count", 0) or 0),
            "node_reconnect_cross_hub_status_ok": bool(node_behavior.get("cross_hub_status_ok", True)),
            "node_behavior_summary": compact_node_behavior_summary(node_behavior),
            "worker_connection_recover_requested_count": int(worker_connection_reliability.get("recover_requested_count", 0) or 0),
            "worker_connection_lost_requested_count": int(worker_connection_reliability.get("lost_requested_count", 0) or 0),
            "worker_connection_recover_completed_count": int(worker_connection_reliability.get("recover_before_timeout_completed_count", 0) or 0),
            "worker_connection_lost_failed_count": int(worker_connection_reliability.get("lost_timeout_failed_count", 0) or 0),
            "worker_connection_late_result_rejected_count": int(worker_connection_reliability.get("late_result_rejected_count", 0) or 0),
            "worker_connection_no_charge_after_lost_count": int(worker_connection_reliability.get("no_charge_after_lost_count", 0) or 0),
            "worker_connection_payout_integrity_ok": bool(worker_connection_reliability.get("payout_integrity_ok", True)),
            "worker_connection_reliability_summary": compact_worker_connection_reliability_summary(worker_connection_reliability),
            "requester_disconnect_result_requested_count": int(requester_disconnect_result_retention.get("requested_count", 0) or 0),
            "requester_disconnect_result_retained_count": int(requester_disconnect_result_retention.get("result_retained_count", 0) or 0),
            "requester_reconnect_result_pickup_count": int(requester_disconnect_result_retention.get("result_pickup_count", 0) or 0),
            "requester_result_retention_window_seconds": int(requester_disconnect_result_retention.get("retention_window_seconds", 0) or 0),
            "requester_result_expired_count": int(requester_disconnect_result_retention.get("expired_count", 0) or 0),
            "requester_disconnect_worker_payout_integrity_ok": bool(requester_disconnect_result_retention.get("worker_payout_integrity_ok", True)),
            "requester_disconnect_result_retention_summary": compact_requester_disconnect_result_retention_summary(requester_disconnect_result_retention),
            "dev_chain_balance_delta_nonzero_count": (
                len(dev_chain_rollup.get("balance_deltas_nonzero_wei", {})) if dev_chain_rollup is not None else 0
            ),
            "nodes_registered": len(nodes),
            "requests_submitted": len(submitted),
            "requests_completed": len(completed),
            "selected_worker_count": len(selected_worker_ids),
            "selected_worker_ids": selected_worker_ids,
            "agent_feedback_scenario_enabled": bool(agent_feedback_reviews.get("enabled", False)),
            "agent_feedback_seeded_bad_worker_count": int(agent_feedback_reviews.get("seeded_bad_worker_count", 0) or 0),
            "agent_feedback_seeded_bad_worker_id": str(agent_feedback_reviews.get("seeded_bad_worker_id", "") or ""),
            "agent_feedback_fail_marker": str(agent_feedback_reviews.get("fail_marker", "") or ""),
            "agent_feedback_result_pickup_count": int(agent_feedback_reviews.get("result_pickup_count", 0) or 0),
            "agent_feedback_failfailfail_result_count": int(agent_feedback_reviews.get("failfailfail_result_count", 0) or 0),
            "agent_feedback_agent_review_submitted_count": int(agent_feedback_reviews.get("agent_review_submitted_count", 0) or 0),
            "agent_feedback_agent_complaint_count": int(agent_feedback_reviews.get("agent_complaint_count", 0) or 0),
            "agent_feedback_noisy_fail_seen_count": int(agent_feedback_reviews.get("noisy_fail_seen_count", 0) or 0),
            "agent_feedback_noisy_fail_complaint_count": int(agent_feedback_reviews.get("noisy_fail_complaint_count", 0) or 0),
            "agent_feedback_noisy_random_complaint_count": int(agent_feedback_reviews.get("noisy_random_complaint_count", 0) or 0),
            "agent_feedback_random_false_complaint_count": int(agent_feedback_reviews.get("random_false_complaint_count", 0) or 0),
            "agent_feedback_submitted_count": int(agent_feedback_reviews.get("feedback_submission_count", 0) or 0),
            "agent_feedback_recorded_count": int(agent_feedback_reviews.get("feedback_recorded_count", 0) or 0),
            "agent_feedback_cross_hub_visible_count": int(agent_feedback_reviews.get("feedback_cross_hub_visible_count", 0) or 0),
            "agent_feedback_feedback_error_count": int(agent_feedback_reviews.get("feedback_error_count", 0) or 0),
            "agent_feedback_feedback_errors": list(agent_feedback_reviews.get("feedback_errors", []) or []),
            "agent_feedback_money_movement_count": int(agent_feedback_reviews.get("feedback_money_movement_count", 0) or 0),
            "requester_visible_worker_id_leak_count": int(agent_feedback_reviews.get("requester_visible_worker_id_leak_count", 0) or 0),
            "ring_control_worker_review_summary_count": int(agent_feedback_reviews.get("ring_control_worker_review_summary_count", 0) or 0),
            "ring_control_bad_worker_identified_count": int(agent_feedback_reviews.get("ring_control_bad_worker_identified_count", 0) or 0),
            "ring_control_false_positive_worker_count": int(agent_feedback_reviews.get("ring_control_false_positive_worker_count", 0) or 0),
            "worker_review_readout": list(agent_feedback_reviews.get("worker_review_readout", []) or []),
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
            "node_reconnect_cross_hub_status_ok": report["node_reconnect_cross_hub_status_ok"],
            "requester_disconnect_worker_payout_integrity_ok": report["requester_disconnect_worker_payout_integrity_ok"],
        }
        if config.requester_disconnect_events > 0:
            required["requester_disconnect_result_retained"] = (
                report["requester_disconnect_result_retained_count"] == report["requester_disconnect_result_requested_count"]
            )
            required["requester_reconnect_result_pickup"] = (
                report["requester_reconnect_result_pickup_count"] == report["requester_disconnect_result_requested_count"]
            )
            required["requester_result_not_expired"] = report["requester_result_expired_count"] == 0
        if config.agent_feedback_reviews:
            required["agent_feedback_no_worker_identity_leak"] = report["requester_visible_worker_id_leak_count"] == 0
            required["agent_feedback_no_money_movement"] = report["agent_feedback_money_movement_count"] == 0
            required["agent_feedback_no_submit_errors"] = report["agent_feedback_feedback_error_count"] == 0
            required["agent_feedback_bad_worker_seeded"] = report["agent_feedback_seeded_bad_worker_count"] == 1
            required["agent_feedback_fail_marker_seen"] = report["agent_feedback_failfailfail_result_count"] > 0
            required["agent_feedback_agent_complained_on_failures"] = (
                report["agent_feedback_agent_complaint_count"] == report["agent_feedback_failfailfail_result_count"]
            )
            required["ring_control_bad_worker_identified"] = report["ring_control_bad_worker_identified_count"] == 1
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
    parser.add_argument("--node-reconnect-events", type=int, default=4, help="Seeded offline/reconnect node behavior events before work starts; use 0 to disable.")
    parser.add_argument("--worker-connection-recover-events", type=int, default=2, help="Seeded active-lease worker disconnects that reconnect before timeout; use 0 to disable.")
    parser.add_argument("--worker-connection-lost-events", type=int, default=2, help="Seeded active-lease worker disconnects that exceed timeout and must fail/no-charge; use 0 to disable.")
    parser.add_argument("--worker-connection-lease-seconds", type=_positive_float, default=1.0, help="Short lease duration used for worker connection reliability probes.")
    parser.add_argument("--worker-connection-lost-after-seconds", type=_positive_float, default=1.25, help="How long to wait before submitting the late result in worker-lost probes.")
    parser.add_argument("--requester-disconnect-events", type=int, default=2, help="Seeded requester disconnect/result-pickup probes; use 0 to disable.")
    parser.add_argument("--requester-disconnect-pickup-after-seconds", type=float, default=0.1, help="How long the requester stays silent after worker completion before result pickup.")
    parser.add_argument("--requester-result-retention-window-seconds", type=_positive_int, default=3600, help="Durable completed-result pickup window advertised by requester-disconnect probes.")
    parser.add_argument("--random-bridge-funding-events", type=_positive_int, default=3)
    parser.add_argument("--random-bridge-payout-events", type=_positive_int, default=3)
    parser.add_argument("--random-bridge-failed-payout-events", type=_positive_int, default=1)
    parser.add_argument("--no-agent-feedback-reviews", action="store_true", help="Disable seeded FAILFAILFAIL agent review/readout scenario.")
    parser.add_argument("--agent-feedback-fail-marker", default=DEFAULT_AGENT_FEEDBACK_FAIL_MARKER)
    parser.add_argument(
        "--agent-feedback-noisy-fail-claim-rate",
        type=float,
        default=DEFAULT_AGENT_FEEDBACK_NOISY_FAIL_CLAIM_RATE,
        help="Probability that a noisy requester complains after seeing the fail marker.",
    )
    parser.add_argument(
        "--agent-feedback-random-false-claim-rate",
        type=float,
        default=DEFAULT_AGENT_FEEDBACK_RANDOM_FALSE_CLAIM_RATE,
        help="Probability that a noisy requester complains regardless of result quality.",
    )
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
        node_reconnect_events=max(0, int(args.node_reconnect_events or 0)),
        worker_connection_recover_events=max(0, int(args.worker_connection_recover_events or 0)),
        worker_connection_lost_events=max(0, int(args.worker_connection_lost_events or 0)),
        worker_connection_lease_seconds=args.worker_connection_lease_seconds,
        worker_connection_lost_after_seconds=args.worker_connection_lost_after_seconds,
        requester_disconnect_events=max(0, int(args.requester_disconnect_events or 0)),
        requester_disconnect_pickup_after_seconds=max(0.0, float(args.requester_disconnect_pickup_after_seconds or 0.0)),
        requester_result_retention_window_seconds=max(0, int(args.requester_result_retention_window_seconds or 0)),
        random_bridge_funding_events=args.random_bridge_funding_events,
        random_bridge_payout_events=args.random_bridge_payout_events,
        random_bridge_failed_payout_events=args.random_bridge_failed_payout_events,
        agent_feedback_reviews=not args.no_agent_feedback_reviews,
        agent_feedback_fail_marker=str(args.agent_feedback_fail_marker or DEFAULT_AGENT_FEEDBACK_FAIL_MARKER),
        agent_feedback_noisy_fail_claim_rate=max(0.0, min(1.0, float(args.agent_feedback_noisy_fail_claim_rate))),
        agent_feedback_random_false_claim_rate=max(0.0, min(1.0, float(args.agent_feedback_random_false_claim_rate))),
    )


def _print_worker_review_readout(readout: Any) -> None:
    if not isinstance(readout, list) or not readout:
        return
    print("worker_review_readout_count: " + str(len(readout)))
    for row in readout:
        if not isinstance(row, dict):
            continue
        worker_id = str(row.get("worker_node_id", "") or "unknown")
        printable = {
            "completed": int(row.get("completed_request_count", 0) or 0),
            "feedback": int(row.get("feedback_count", 0) or 0),
            "accepted": int(row.get("accepted_count", 0) or 0),
            "rejected": int(row.get("rejected_count", 0) or 0),
            "avg_score": row.get("average_score", 0.0),
            "fail_signal": int(row.get("fail_signal_observed_count", 0) or 0),
            "agent_complaints": int(row.get("agent_complaint_count", 0) or 0),
            "noisy_complaints": int(row.get("noisy_requester_complaint_count", 0) or 0),
            "bounded_negative": int(row.get("bounded_negative_feedback_count", 0) or 0),
            "tags": row.get("feedback_tag_counts", {}),
        }
        print(f"worker_review[{worker_id}]: " + json.dumps(printable, sort_keys=True))


def _print_dev_chain_rollup(rollup: dict[str, Any]) -> None:
    balance_deltas = rollup.get("balance_deltas_wei", {})
    if not isinstance(balance_deltas, dict):
        balance_deltas = {}
    nonzero_deltas = {
        str(address): delta
        for address, delta in balance_deltas.items()
        if int(delta or 0) != 0
    }
    print(f"dev_chain_balance_delta_wallet_count: {len(balance_deltas)}")
    print(f"dev_chain_balance_delta_nonzero_count: {len(nonzero_deltas)}")
    for address, delta in sorted(nonzero_deltas.items()):
        print(f"dev_chain_balance_delta_wei[{address}]: {delta}")

    movement_summary = {
        name: {
            "amount_units": movement.get("amount_units"),
            "contract_id": movement.get("contract_id"),
            "transaction_hashes": movement.get("transaction_hashes", []),
        }
        for name, movement in dict(rollup.get("bridge_movements", {})).items()
        if isinstance(movement, dict)
    }
    if movement_summary:
        print("dev_chain_bridge_movement_summary: " + json.dumps(movement_summary, sort_keys=True))
    random_summary = rollup.get("random_bridge_event_summary", {})
    if isinstance(random_summary, dict) and random_summary:
        print("bridge_random_event_summary: " + json.dumps(random_summary, sort_keys=True))
    if rollup.get("bridge_lifecycle_note"):
        print("dev_chain_bridge_lifecycle_note: " + str(rollup.get("bridge_lifecycle_note", "")))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = _config_from_args(args)
    try:
        report = asyncio.run(run_temporal_fdb_hub_stress_smoke(config))
    except NodeMarketSmokeError as exc:
        print(f"FAIL: Temporal FDB Hub stress smoke failed: {exc}")
        print(_local_lab_startup_help(config.to_hub_config(config.hub_a_url, hub_name="hub-a")))
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
        "bridge_random_funding_event_count",
        "bridge_random_funding_total_credits",
        "bridge_random_payout_confirmed_count",
        "bridge_random_payout_failed_count",
        "bridge_active_work_payout_rejection_count",
        "ring_config_enabled",
        "ring_config_path",
        "ring_config_loaded_by_hub_a",
        "ring_config_loaded_by_hub_b",
        "ring_config_hash_match",
        "ring_admission_config_hash_match",
        "ring_config_default_min_ring",
        "ring_config_allowlisted_ring0_wallet_count",
        "ring0_registration_requested_count",
        "ring0_registration_allowed_count",
        "ring0_registration_rejected_count",
        "ring3_default_wallet_count",
        "ring3_default_registration_count",
        "ring2_probe_requested_count",
        "ring2_probe_rejected_count",
        "ring2_probe_fallback_ring3_succeeded_count",
        "ring1_probe_requested_count",
        "ring1_probe_rejected_count",
        "ring1_probe_ring2_retry_rejected_count",
        "ring1_probe_fallback_ring3_succeeded_count",
        "unauthorized_high_ring_registration_allowed_count",
        "ring_admission_rejection_audit_count",
        "ring_admission_cross_hub_consistent",
        "node_reconnect_requested_count",
        "node_reconnect_completed_count",
        "node_reconnect_offline_seen_count",
        "node_reconnect_available_after_reconnect_count",
        "node_reconnect_duplicate_registration_count",
        "node_reconnect_cross_hub_status_ok",
        "worker_connection_recover_requested_count",
        "worker_connection_lost_requested_count",
        "worker_connection_recover_completed_count",
        "worker_connection_lost_failed_count",
        "worker_connection_late_result_rejected_count",
        "worker_connection_no_charge_after_lost_count",
        "worker_connection_payout_integrity_ok",
        "requester_disconnect_result_requested_count",
        "requester_disconnect_result_retained_count",
        "requester_reconnect_result_pickup_count",
        "requester_result_retention_window_seconds",
        "requester_result_expired_count",
        "requester_disconnect_worker_payout_integrity_ok",
        "nodes_registered",
        "requests_completed",
        "selected_worker_count",
        "selected_worker_ids",
        "agent_feedback_scenario_enabled",
        "agent_feedback_seeded_bad_worker_count",
        "agent_feedback_seeded_bad_worker_id",
        "agent_feedback_failfailfail_result_count",
        "agent_feedback_agent_review_submitted_count",
        "agent_feedback_agent_complaint_count",
        "agent_feedback_noisy_fail_seen_count",
        "agent_feedback_noisy_fail_complaint_count",
        "agent_feedback_noisy_random_complaint_count",
        "agent_feedback_random_false_complaint_count",
        "agent_feedback_submitted_count",
        "agent_feedback_recorded_count",
        "agent_feedback_cross_hub_visible_count",
        "agent_feedback_money_movement_count",
        "requester_visible_worker_id_leak_count",
        "ring_control_worker_review_summary_count",
        "ring_control_bad_worker_identified_count",
        "ring_control_false_positive_worker_count",
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
    _print_worker_review_readout(report.get("worker_review_readout"))
    if isinstance(report.get("dev_chain_rollup"), dict):
        _print_dev_chain_rollup(report["dev_chain_rollup"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
