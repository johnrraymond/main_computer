from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from main_computer.temporal_fdb_hub_node_market_smoke import (
    DEFAULT_AUTO_HUB_CLUSTER_FILE,
    DEFAULT_AUTO_HUB_NAMESPACE_PREFIX,
    DEFAULT_AUTO_HUB_ROOT,
    DEFAULT_HUB_URL,
    DEFAULT_HTTP_RETRY_ATTEMPTS,
    HubNodeMarketSmokeConfig,
    NodeMarketSmokeConfig,
    NodeMarketSmokeError,
    RequestSpec,
    WorkerMatch,
    _ProgressReporter,
    _StartedHubProcess,
    _auto_hub_namespace,
    _bridge_audit_events,
    _bridge_fund_requester,
    _get_json,
    _heartbeat_loop,
    _hub_host_port,
    _local_lab_startup_help,
    _mock_chain_wallet_available_wei,
    _post_json,
    _post_json_expect_http_error,
    _response_content,
    _tcp_accepts_connections,
    _validate_lease,
    _verify_backends,
    _verify_expected_audit_types,
    _split_wallet_addresses,
    _worker_payload,
    _worker_wallet_address_for_config,
)
from main_computer.temporal_fdb_node_market_smoke import (
    NODE_MARKET_TASK_QUEUE_PREFIX,
    build_worker_nodes,
    _execute_requests,
    _make_request_payload,
    _positive_float as _validate_positive_float,
    _positive_int as _validate_positive_int,
)
from tools.temporal_lab.event_log import DEFAULT_EVENT_LOG_PATH, read_jsonl_events
from tools.temporal_lab.local_temporal import DEFAULT_NAMESPACE


DEFAULT_MULTI_HUB_REPORT_PATH = Path("runtime") / "temporal_lab" / "temporal_fdb_hub_multi_hub_report.json"
DEFAULT_MULTI_HUB_A_URL = DEFAULT_HUB_URL
DEFAULT_MULTI_HUB_B_URL = "http://127.0.0.1:8871"


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


@dataclass(frozen=True)
class HubMultiHubSmokeConfig:
    repo_root: Path
    hub_a_url: str = DEFAULT_MULTI_HUB_A_URL
    hub_b_url: str = DEFAULT_MULTI_HUB_B_URL
    execution_mode: str = "live-temporal"
    temporal_address: str = "localhost:7233"
    namespace: str = DEFAULT_NAMESPACE
    report_path: Path | None = DEFAULT_MULTI_HUB_REPORT_PATH
    event_log_path: Path = DEFAULT_EVENT_LOG_PATH
    node_count: int = 50
    request_count: int = 20
    requested_ring: int = 2
    max_price_credits: int = 2
    deposit_credits: int = 100
    token_count: int = 5
    token_interval_seconds: float = 0.02
    keepalive_interval_seconds: float = 2.0
    account_id: str = "temporal-fdb-hub-multi-hub-client"
    requester_wallet_address: str = "0x0000000000000000000000000000000000000bb1"
    worker_wallet_addresses: tuple[str, ...] = ()
    model: str = "temporal-fdb-hub-multi-hub-model"
    task_queue_prefix: str = NODE_MARKET_TASK_QUEUE_PREFIX + "-multi-hub"
    run_id: str | None = None
    require_foundationdb_backends: bool = True
    emit_progress: bool = False
    progress_interval_seconds: float = 2.0
    http_timeout_seconds: float = 10.0
    http_retry_attempts: int = DEFAULT_HTTP_RETRY_ATTEMPTS
    hub_start_mode: str = "auto"
    hub_start_timeout_seconds: float = 60.0
    hub_namespace_prefix: str = DEFAULT_AUTO_HUB_NAMESPACE_PREFIX + "-multi-hub"
    hub_root: Path = DEFAULT_AUTO_HUB_ROOT / "multi-hub"
    cluster_file: Path = DEFAULT_AUTO_HUB_CLUSTER_FILE
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


def _wait_for_hub_health(config: HubNodeMarketSmokeConfig, *, progress: _ProgressReporter, label: str) -> _StartedHubProcess | None:
    """Start or probe a Hub for the multi-Hub smoke.

    In auto mode this smoke intentionally wants fresh, known Hub processes.  Reusing
    an already-listening port can silently point at a different namespace, which is
    exactly the kind of state split this smoke is designed to avoid.
    """

    host, port = _hub_host_port(config.hub_url)
    mode = str(config.hub_start_mode or "auto").strip().lower()
    if mode not in {"auto", "never"}:
        raise NodeMarketSmokeError("--hub-start-mode must be 'auto' or 'never'.")
    if mode == "auto" and _tcp_accepts_connections(host, port, timeout=0.5):
        raise NodeMarketSmokeError(
            f"Multi-Hub smoke needs a fresh {label} on {config.hub_url}, but that port is already listening. "
            "Stop the existing Hub or pass different --hub-a-url/--hub-b-url ports.\n"
            "After reboot, also make sure only the lab dependencies are running, not stale Hubs:\n"
            + _local_lab_startup_help(config)
        )

    from main_computer.temporal_fdb_hub_node_market_smoke import _ensure_hub_running

    started = _ensure_hub_running(config, progress=progress)
    if mode == "never":
        health = _get_json(config.hub_url, "/api/hub/v1/health", timeout=config.http_timeout_seconds)
        if health.get("ok") is not True:
            raise NodeMarketSmokeError(f"{label} health check failed: {health}\n" + _local_lab_startup_help(config))
    progress.emit("multi_hub_ready", hub=label, hub_url=config.hub_url, namespace=_auto_hub_namespace(config))
    return started


def _register_workers_multi(
    configs: dict[str, HubNodeMarketSmokeConfig],
    nodes: list[Any],
    *,
    progress: _ProgressReporter,
) -> dict[str, int]:
    counts = {"hub_a": 0, "hub_b": 0}
    for index, node in enumerate(nodes, start=1):
        label = "hub_a" if index % 2 else "hub_b"
        cfg = configs[label]
        result = _post_json(
            cfg.hub_url,
            "/api/hub/v1/workers/register",
            _worker_payload(node, model=cfg.model, wallet_address=_worker_wallet_address_for_config(cfg, node)),
            timeout=cfg.http_timeout_seconds,
        )
        worker = result.get("worker", {}) if isinstance(result.get("worker"), dict) else {}
        offer = worker.get("offer", {}) if isinstance(worker.get("offer"), dict) else {}
        if offer.get("assigned_ring") != node.ring:
            raise NodeMarketSmokeError(
                f"{label} did not preserve assigned_ring for {node.node_id}: "
                f"expected {node.ring}, got {offer.get('assigned_ring')!r}"
            )
        counts[label] += 1
        if index == 1 or index == len(nodes) or index % 10 == 0:
            progress.emit(
                "multi_hub_worker_registered",
                hub=label,
                worker_index=index,
                workers_total=len(nodes),
                node_id=node.node_id,
                ring=node.ring,
                price_credits=node.price_credits,
                task_queue=node.task_queue,
            )
    return counts


def _quote_and_submit_requests_multi(
    configs: dict[str, HubNodeMarketSmokeConfig],
    nodes_by_id: dict[str, Any],
    *,
    progress: _ProgressReporter,
) -> tuple[list[tuple[WorkerMatch, Any]], list[dict[str, Any]], dict[str, int]]:
    config_a = configs["hub_a"]
    request_jobs: list[tuple[WorkerMatch, Any]] = []
    submitted_records: list[dict[str, Any]] = []
    counts = {"quote_hub_a": 0, "quote_hub_b": 0, "submit_hub_a": 0, "submit_hub_b": 0}
    for offset in range(config_a.request_count):
        quote_label = "hub_a" if offset % 2 == 0 else "hub_b"
        submit_label = "hub_b" if quote_label == "hub_a" else "hub_a"
        quote_cfg = configs[quote_label]
        submit_cfg = configs[submit_label]
        logical_id = f"multi-hub-node-market-{config_a.run_id}-{offset + 1:04d}"
        quote_payload = {
            "account_id": config_a.account_id,
            "client_node_id": config_a.account_id,
            "model": config_a.model,
            "prompt": f"Temporal multi-Hub FDB node-market request {offset + 1}",
            "max_price_credits": config_a.max_price_credits,
            "requested_ring": config_a.requested_ring,
            "execution_mode": "worker_pull_v0",
            "pricing_mode": "market_offer_fixed_per_call_v0",
            "idempotency_key": f"{logical_id}-quote",
        }
        quote = _post_json(
            quote_cfg.hub_url,
            "/api/hub/v1/requests/quote",
            quote_payload,
            timeout=quote_cfg.http_timeout_seconds,
        )["quote"]
        counts[f"quote_{quote_label}"] += 1
        selected_offer = quote.get("selected_offer", {}) if isinstance(quote.get("selected_offer"), dict) else {}
        selected_worker_id = str(selected_offer.get("worker_node_id") or quote.get("selected_worker_node_id") or "")
        selected = nodes_by_id.get(selected_worker_id)
        if selected is None:
            raise NodeMarketSmokeError(f"{quote_label} selected unknown worker {selected_worker_id!r} for {logical_id}.")
        if selected.ring > config_a.requested_ring or selected.price_credits > config_a.max_price_credits:
            raise NodeMarketSmokeError(
                f"{quote_label} selected ineligible worker {selected.node_id}: "
                f"ring={selected.ring} requested_ring={config_a.requested_ring} "
                f"price={selected.price_credits} max_price={config_a.max_price_credits}"
            )
        submit_payload = {
            **quote_payload,
            "quote_id": quote["quote_id"],
            "metadata": {
                "worker_pull_v0": True,
                "requested_ring": config_a.requested_ring,
                "expected_token_count": config_a.token_count,
                "multi_hub": True,
                "quote_hub": quote_label,
                "submit_hub": submit_label,
            },
            "idempotency_key": f"{logical_id}-submit",
        }
        submitted = _post_json(
            submit_cfg.hub_url,
            "/api/hub/v1/requests",
            submit_payload,
            timeout=submit_cfg.http_timeout_seconds,
        )["request"]
        counts[f"submit_{submit_label}"] += 1
        request = RequestSpec(
            request_id=str(submitted["request_id"]),
            account_id=config_a.account_id,
            requested_ring=config_a.requested_ring,
            max_price_credits=config_a.max_price_credits,
            token_count=config_a.token_count,
            token_interval_seconds=config_a.token_interval_seconds,
        )
        match = WorkerMatch(
            request=request,
            worker=selected,
            partition_size=0,
            candidate_node_ids=(),
        )
        request_jobs.append((match, _make_request_payload(match)))
        submitted_records.append(submitted)
        if (offset + 1) == 1 or (offset + 1) == config_a.request_count or (offset + 1) % 5 == 0:
            progress.emit(
                "multi_hub_request_submitted",
                submitted=offset + 1,
                total=config_a.request_count,
                request_id=submitted["request_id"],
                selected_node=selected.node_id,
                selected_ring=selected.ring,
                selected_price_credits=selected.price_credits,
                quote_hub=quote_label,
                submit_hub=submit_label,
            )
    return request_jobs, submitted_records, counts


def _submit_results_and_verify_multi(
    configs: dict[str, HubNodeMarketSmokeConfig],
    execution_results: list[dict[str, Any]],
    leases: dict[str, dict[str, Any]],
    *,
    progress: _ProgressReporter,
    hub_a_available: bool,
    result_content_by_worker_node_id: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config_a = configs["hub_a"]
    completed: list[dict[str, Any]] = []
    route_counts = {
        "completion_hub_a": 0,
        "completion_hub_b": 0,
        "replay_hub_a": 0,
        "replay_hub_b": 0,
        "cross_hub_replay_idempotent": True,
    }
    content_by_worker = dict(result_content_by_worker_node_id or {})
    for index, result in enumerate(sorted(execution_results, key=lambda item: str(item.get("request_id", ""))), start=1):
        request_id = str(result["request_id"])
        worker_node_id = str(result["worker_node_id"])
        lease = leases[request_id]
        workflow_result = result.get("workflow_result", {}) if isinstance(result.get("workflow_result"), dict) else {}
        token_count = int(workflow_result.get("token_count", config_a.token_count) or config_a.token_count)
        injected_content = content_by_worker.get(worker_node_id)
        result_content = injected_content if injected_content is not None else _response_content(token_count)
        response = {
            "status": "success",
            "response": {
                "content": result_content,
                "provider": "temporal-lab-fake-token",
                "model": config_a.model,
                "metadata": {
                    "temporal_lab": True,
                    "token_count": token_count,
                    "workflow_result": workflow_result,
                    "requester_visible_token_events": token_count,
                    "multi_hub": True,
                    "agent_feedback_injected_failure": injected_content is not None,
                },
            },
        }
        completion_label = "hub_b" if (not hub_a_available or index % 2 == 0) else "hub_a"
        replay_label = "hub_a" if completion_label == "hub_b" and hub_a_available else "hub_b"
        completion_cfg = configs[completion_label]
        replay_cfg = configs[replay_label]
        completion = _post_json(
            completion_cfg.hub_url,
            "/api/hub/v1/workers/results",
            {
                "worker_node_id": worker_node_id,
                "request_id": request_id,
                "lease_id": lease["lease_id"],
                "result": response,
            },
            timeout=completion_cfg.http_timeout_seconds,
        )["request"]
        route_counts[f"completion_{completion_label}"] += 1
        if completion.get("state") != "completed":
            raise NodeMarketSmokeError(f"{completion_label} did not complete {request_id}: {completion}")

        replay = _post_json(
            replay_cfg.hub_url,
            "/api/hub/v1/workers/results",
            {
                "worker_node_id": worker_node_id,
                "request_id": request_id,
                "lease_id": lease["lease_id"],
                "result": response,
            },
            timeout=replay_cfg.http_timeout_seconds,
        )
        route_counts[f"replay_{replay_label}"] += 1
        if replay.get("duplicate_completion_additional_charge") != 0:
            route_counts["cross_hub_replay_idempotent"] = False
            raise NodeMarketSmokeError(
                f"Duplicate completion replay through {replay_label} charged again for {request_id}: {replay}"
            )

        verify_cfg = configs["hub_b"]
        charges = _get_json(verify_cfg.hub_url, f"/api/hub/v1/requests/{request_id}/charges", timeout=verify_cfg.http_timeout_seconds)
        if int(charges.get("charge_count", 0) or 0) != 1:
            raise NodeMarketSmokeError(f"Expected one charge for {request_id}, got: {charges}")
        earnings_query = urlencode({"worker_node_id": worker_node_id, "request_id": request_id})
        earnings = _get_json(verify_cfg.hub_url, f"/api/hub/v1/credits/worker-earnings?{earnings_query}", timeout=verify_cfg.http_timeout_seconds)
        if int(earnings.get("worker_earning_count", 0) or 0) < 1:
            raise NodeMarketSmokeError(f"Expected worker earning for {request_id}, got: {earnings}")
        events = _get_json(verify_cfg.hub_url, f"/api/hub/v1/requests/{request_id}/events", timeout=verify_cfg.http_timeout_seconds)
        event_types = [str(event.get("event_type") or event.get("type") or "") for event in events.get("events", []) if isinstance(event, dict)]
        if not any("completed" in event_type for event_type in event_types):
            raise NodeMarketSmokeError(f"Hub request events did not expose completion for {request_id}: {events}")
        completed.append(completion)
        if index == 1 or index == len(execution_results) or index % 5 == 0:
            progress.emit(
                "multi_hub_result_completed",
                completed=index,
                total=len(execution_results),
                request_id=request_id,
                completion_hub=completion_label,
                replay_hub=replay_label,
            )
    return completed, route_counts


def _verify_surprise_payout_rejected_cross_hub(
    configs: dict[str, HubNodeMarketSmokeConfig],
    *,
    worker: Any,
    progress: _ProgressReporter,
) -> dict[str, Any]:
    request_cfg = configs["hub_a"]
    wallet_address = _worker_wallet_address_for_config(request_cfg, worker)
    error = _post_json_expect_http_error(
        request_cfg.hub_url,
        "/api/hub/v1/bridge/payouts",
        {
            "wallet_address": wallet_address,
            "credits": max(1, int(request_cfg.max_price_credits or 1)),
            "idempotency_key": f"{request_cfg.run_id}-{worker.node_id}-cross-hub-surprise-payout-while-active",
            "memo": "multi-Hub surprise payout while worker has an active lease should be rejected",
            "metadata": {
                "run_id": request_cfg.run_id,
                "smoke": "temporal_fdb_hub_multi_hub",
                "expected_rejection": "active_worker_lease",
                "request_hub": "hub_a",
            },
        },
        timeout=request_cfg.http_timeout_seconds,
        expected_status=409,
    )
    if error.get("error_type") != "wallet_active_worker_leases":
        raise NodeMarketSmokeError(
            f"Cross-Hub surprise payout for active worker {worker.node_id} was rejected for wrong reason: {error}"
        )
    active_ids = {str(item) for item in error.get("active_worker_node_ids", [])}
    if worker.node_id not in active_ids:
        raise NodeMarketSmokeError(
            f"Cross-Hub surprise payout rejection did not name active worker {worker.node_id}: {error}"
        )
    progress.emit(
        "multi_hub_payout_rejected_active_work",
        payout_hub="hub_a",
        active_work_hub="hub_b",
        wallet_address=wallet_address,
        worker_node_id=worker.node_id,
        error_type=error.get("error_type"),
    )
    return {
        "wallet_address": wallet_address,
        "worker_node_id": worker.node_id,
        "error": error,
        "rejected": True,
    }


async def _execute_and_settle_multi_hub(
    configs: dict[str, HubNodeMarketSmokeConfig],
    *,
    node_config: NodeMarketSmokeConfig,
    nodes_by_id: dict[str, Any],
    request_jobs: list[tuple[WorkerMatch, Any]],
    event_log_path: Path,
    progress: _ProgressReporter,
    result_content_by_worker_node_id: dict[str, str] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    config_a = configs["hub_a"]
    config_b = configs["hub_b"]
    pending: dict[str, tuple[WorkerMatch, Any]] = {match.request.request_id: (match, request) for match, request in request_jobs}
    leases: dict[str, dict[str, Any]] = {}
    completed: list[dict[str, Any]] = []
    surprise_payout_rejection: dict[str, Any] = {}
    route_counts: dict[str, Any] = {
        "poll_hub_a": 0,
        "poll_hub_b": 0,
        "completion_hub_a": 0,
        "completion_hub_b": 0,
        "replay_hub_a": 0,
        "replay_hub_b": 0,
        "cross_hub_replay_idempotent": True,
    }
    deadline = time.perf_counter() + max(45.0, float(config_a.request_count) * max(2.0, config_a.http_timeout_seconds))
    idle_rounds = 0
    hub_a_available = True

    while pending:
        if time.perf_counter() > deadline:
            raise NodeMarketSmokeError(
                "Timed out waiting for multi-Hub worker-pull leases/results. "
                f"completed={len(completed)} pending={len(pending)} pending_request_ids={list(pending)[:10]}"
            )

        batch: list[tuple[WorkerMatch, Any]] = []
        batch_leases: dict[str, dict[str, Any]] = {}
        polled_workers_this_round: set[str] = set()

        for quoted_match, _quoted_request in list(pending.values()):
            worker = quoted_match.worker
            if worker.node_id in polled_workers_this_round:
                continue
            polled_workers_this_round.add(worker.node_id)
            poll_label = "hub_b" if not hub_a_available or len(leases) % 2 == 0 else "hub_a"
            poll_cfg = configs[poll_label]
            result = _post_json(
                poll_cfg.hub_url,
                "/api/hub/v1/workers/poll",
                {"worker_node_id": worker.node_id},
                timeout=poll_cfg.http_timeout_seconds,
            )
            route_counts[f"poll_{poll_label}"] += 1
            lease = result.get("lease")
            if not isinstance(lease, dict):
                continue
            request_id = _validate_lease(
                config_a,
                lease=lease,
                worker_node_id=worker.node_id,
                pending_request_ids=set(pending),
            )
            original_match, _original_request = pending.pop(request_id)
            actual_worker = nodes_by_id.get(worker.node_id, worker)
            actual_match = WorkerMatch(
                request=original_match.request,
                worker=actual_worker,
                partition_size=original_match.partition_size,
                candidate_node_ids=original_match.candidate_node_ids,
            )
            batch.append((actual_match, _make_request_payload(actual_match)))
            batch_leases[request_id] = lease
            leases[request_id] = lease
            leased_count = len(leases)
            if leased_count == 1 or leased_count == len(request_jobs) or leased_count % 5 == 0:
                progress.emit(
                    "multi_hub_lease_received",
                    leased=leased_count,
                    total=len(request_jobs),
                    poll_hub=poll_label,
                    worker_node_id=worker.node_id,
                    request_id=request_id,
                )

        if not batch:
            idle_rounds += 1
            if idle_rounds == 1 or idle_rounds % 10 == 0:
                progress.emit(
                    "multi_hub_lease_waiting",
                    completed=len(completed),
                    pending=len(pending),
                    polled_workers=len(polled_workers_this_round),
                )
            await asyncio.sleep(0.2)
            continue

        idle_rounds = 0
        if not surprise_payout_rejection and batch:
            surprise_payout_rejection = _verify_surprise_payout_rejected_cross_hub(
                configs,
                worker=batch[0][0].worker,
                progress=progress,
            )

        unique_batch_nodes: dict[str, Any] = {}
        for match, _request in batch:
            unique_batch_nodes[match.worker.node_id] = match.worker
        batch_nodes = sorted(unique_batch_nodes.values(), key=lambda node: node.node_id)
        execution_results = await _execute_requests(
            config=node_config,
            nodes=batch_nodes,
            requests=batch,
            event_log_path=event_log_path,
            progress=progress,
        )
        batch_completed, batch_routes = _submit_results_and_verify_multi(
            configs,
            execution_results,
            batch_leases,
            progress=progress,
            hub_a_available=hub_a_available,
            result_content_by_worker_node_id=result_content_by_worker_node_id,
        )
        completed.extend(batch_completed)
        for key, value in batch_routes.items():
            if isinstance(value, bool):
                route_counts[key] = bool(route_counts.get(key, True)) and value
            else:
                route_counts[key] = int(route_counts.get(key, 0) or 0) + int(value or 0)

    return leases, completed, surprise_payout_rejection, route_counts


def _exercise_cross_hub_payout_lock(
    configs: dict[str, HubNodeMarketSmokeConfig],
    *,
    nodes_by_id: dict[str, Any],
    selected_worker_ids: list[str],
    progress: _ProgressReporter,
    before_confirm_callback: Any | None = None,
) -> dict[str, Any]:
    if not selected_worker_ids:
        raise NodeMarketSmokeError("Cannot exercise cross-Hub payout lock without a selected worker.")
    config_a = configs["hub_a"]
    config_b = configs["hub_b"]
    payout_worker_id = selected_worker_ids[0]
    worker = nodes_by_id[payout_worker_id]
    wallet_address = _worker_wallet_address_for_config(config_a, worker)
    payout_credits = max(1, int(config_a.max_price_credits or 1))

    payout = _post_json(
        config_a.hub_url,
        "/api/hub/v1/bridge/payouts",
        {
            "wallet_address": wallet_address,
            "worker_node_id": payout_worker_id,
            "credits": payout_credits,
            "idempotency_key": f"{config_a.run_id}-{payout_worker_id}-cross-hub-payout",
            "memo": "temporal fdb multi-Hub worker payout",
            "metadata": {"run_id": config_a.run_id, "smoke": "temporal_fdb_hub_multi_hub", "request_hub": "hub_a"},
        },
        timeout=config_a.http_timeout_seconds,
    )
    payout_payload = payout.get("payout", {}) if isinstance(payout.get("payout"), dict) else {}
    payout_id = str(payout_payload.get("payout_id", ""))
    if not payout_id:
        raise NodeMarketSmokeError(f"Cross-Hub worker payout did not return payout_id: {payout}")

    lock_status_b = _get_json(
        config_b.hub_url,
        f"/api/hub/v1/bridge/wallet-locks?{urlencode({'wallet_address': wallet_address})}",
        timeout=config_b.http_timeout_seconds,
    )
    if lock_status_b.get("locked") is not True:
        raise NodeMarketSmokeError(f"Hub B did not see payout wallet lock created by Hub A: {lock_status_b}")

    probe_quote = _post_json(
        config_b.hub_url,
        "/api/hub/v1/requests/quote",
        {
            "account_id": config_a.account_id,
            "client_node_id": config_a.account_id,
            "model": config_a.model,
            "prompt": "Temporal multi-Hub locked-wallet quote probe",
            "max_price_credits": config_a.max_price_credits,
            "requested_ring": config_a.requested_ring,
            "execution_mode": "worker_pull_v0",
            "pricing_mode": "market_offer_fixed_per_call_v0",
            "idempotency_key": f"{config_a.run_id}-cross-hub-locked-wallet-quote-probe",
        },
        timeout=config_b.http_timeout_seconds,
    )["quote"]
    selected_offer = probe_quote.get("selected_offer", {}) if isinstance(probe_quote.get("selected_offer"), dict) else {}
    probe_worker_id = str(selected_offer.get("worker_node_id") or probe_quote.get("selected_worker_node_id") or "")
    if probe_worker_id == payout_worker_id:
        raise NodeMarketSmokeError(f"Hub B selected locked payout worker {payout_worker_id}: {probe_quote}")

    confirm_metadata: dict[str, Any] = {
        "run_id": config_a.run_id,
        "smoke": "temporal_fdb_hub_multi_hub",
        "confirm_hub": "hub_b",
    }
    dev_chain_confirmation = None
    if before_confirm_callback is not None:
        dev_chain_confirmation = before_confirm_callback(
            {
                "payout_id": payout_id,
                "wallet_address": wallet_address,
                "worker_node_id": payout_worker_id,
                "credits": payout_credits,
                "payout": payout_payload,
            }
        )
        if dev_chain_confirmation is not None:
            confirm_metadata["dev_chain"] = dev_chain_confirmation

    confirmed = _post_json(
        config_b.hub_url,
        "/api/hub/v1/bridge/payouts/confirm",
        {
            "payout_id": payout_id,
            "metadata": confirm_metadata,
        },
        timeout=config_b.http_timeout_seconds,
    )
    final_lock_a = _get_json(
        config_a.hub_url,
        f"/api/hub/v1/bridge/wallet-locks?{urlencode({'wallet_address': wallet_address})}",
        timeout=config_a.http_timeout_seconds,
    )
    if final_lock_a.get("locked") is True:
        raise NodeMarketSmokeError(f"Hub A still sees wallet locked after Hub B confirmed payout: {final_lock_a}")
    chain_available = _mock_chain_wallet_available_wei(config_b, wallet_address)
    if chain_available <= 0:
        raise NodeMarketSmokeError(f"Cross-Hub payout confirmation did not credit mock-chain wallet: {chain_available}")

    events = _bridge_audit_events(config_b, worker_node_id=payout_worker_id, limit=100)
    audit_types = _verify_expected_audit_types(
        label=f"cross-Hub worker payout {payout_worker_id}",
        events=events,
        expected_types={
            "hub.worker.earning.recorded",
            "bridge.wallet.locked",
            "bridge.payout.requested",
            "bridge.wallet.unlocked",
            "bridge.payout.confirmed",
        },
    )
    progress.emit(
        "multi_hub_payout_lock_confirmed",
        request_hub="hub_a",
        confirm_hub="hub_b",
        wallet_address=wallet_address,
        worker_node_id=payout_worker_id,
        payout_id=payout_id,
        quote_probe_selected_worker_id=probe_worker_id,
    )
    return {
        "wallet_address": wallet_address,
        "worker_node_id": payout_worker_id,
        "payout": payout_payload,
        "confirmed": confirmed,
        "dev_chain_confirmation": dev_chain_confirmation,
        "lock_visible_cross_hub": True,
        "locked_wallet_excluded_cross_hub": probe_worker_id != payout_worker_id,
        "quote_probe_selected_worker_id": probe_worker_id,
        "audit_event_types": sorted(audit_types),
    }


async def run_temporal_fdb_hub_multi_hub_smoke(config: HubMultiHubSmokeConfig) -> dict[str, Any]:
    progress = _ProgressReporter(
        enabled=config.emit_progress,
        interval_seconds=config.progress_interval_seconds,
    )
    run_id = config.run_id or f"{time.time_ns():x}"[-10:]
    object.__setattr__(config, "run_id", run_id)
    config_a = config.to_hub_config(config.hub_a_url, hub_name="hub-a")
    config_b = config.to_hub_config(config.hub_b_url, hub_name="hub-b")
    configs = {"hub_a": config_a, "hub_b": config_b}

    event_log_path = config.resolved_event_log_path()
    event_log_path.parent.mkdir(parents=True, exist_ok=True)
    event_log_path.write_text("", encoding="utf-8")

    progress.emit(
        "start",
        hub_a_url=config.hub_a_url,
        hub_b_url=config.hub_b_url,
        execution_mode=config.execution_mode,
        nodes=config.node_count,
        requests=config.request_count,
        requested_ring=config.requested_ring,
        max_price_credits=config.max_price_credits,
        shared_namespace=_auto_hub_namespace(config_a),
    )

    started_a: _StartedHubProcess | None = None
    started_b: _StartedHubProcess | None = None
    hub_a_stopped_for_failover = False
    try:
        started_a = _wait_for_hub_health(config_a, progress=progress, label="hub_a")
        started_b = _wait_for_hub_health(config_b, progress=progress, label="hub_b")

        _verify_backends(config_a, progress=progress)
        _verify_backends(config_b, progress=progress)

        bridge_funding = _bridge_fund_requester(config_a, progress=progress)

        nodes = build_worker_nodes(node_count=config.node_count, run_id=run_id, task_queue_prefix=config.task_queue_prefix)
        registration_counts = _register_workers_multi(configs, nodes, progress=progress)

        stop_event = asyncio.Event()
        heartbeat_tasks = []
        for index, node in enumerate(nodes, start=1):
            heartbeat_cfg = config_a if index % 2 else config_b
            heartbeat_tasks.append(asyncio.create_task(_heartbeat_loop(config=heartbeat_cfg, node=node, stop_event=stop_event)))

        try:
            request_jobs, submitted, route_submit_counts = _quote_and_submit_requests_multi(
                configs,
                {node.node_id: node for node in nodes},
                progress=progress,
            )
            node_config = NodeMarketSmokeConfig(
                repo_root=config.repo_root,
                execution_mode=config.execution_mode,  # type: ignore[arg-type]
                ledger_backend="foundationdb",
                report_path=None,
                event_log_path=event_log_path,
                temporal_address=config.temporal_address,
                namespace=config.namespace,
                node_count=config.node_count,
                request_count=config.request_count,
                requested_ring=config.requested_ring,
                max_price_credits=config.max_price_credits,
                deposit_credits=config.deposit_credits,
                token_count=config.token_count,
                token_interval_seconds=config.token_interval_seconds,
                task_queue_prefix=config.task_queue_prefix,
                run_id=run_id,
                emit_progress=config.emit_progress,
                progress_interval_seconds=config.progress_interval_seconds,
            )
            leases, completed, surprise_payout_rejection, route_execution_counts = await _execute_and_settle_multi_hub(
                configs,
                node_config=node_config,
                nodes_by_id={node.node_id: node for node in nodes},
                request_jobs=request_jobs,
                event_log_path=event_log_path,
                progress=progress,
            )
        finally:
            stop_event.set()
            await asyncio.gather(*heartbeat_tasks, return_exceptions=True)

        selected_worker_ids = sorted({str(record.get("selected_worker_node_id", "")) for record in completed})
        payout = _exercise_cross_hub_payout_lock(
            configs,
            nodes_by_id={node.node_id: node for node in nodes},
            selected_worker_ids=selected_worker_ids,
            progress=progress,
        )

        if config.failover_hub_a and started_a is not None:
            progress.emit("multi_hub_failover_stop_hub_a", hub_url=config_a.hub_url)
            started_a.stop()
            started_a = None
            hub_a_stopped_for_failover = True

        credit_status_b = _get_json(config_b.hub_url, "/api/hub/v1/credits", timeout=config_b.http_timeout_seconds)
        status_b = _get_json(config_b.hub_url, "/api/hub/v1/status", timeout=config_b.http_timeout_seconds)
        mock_chain_status = credit_status_b.get("mock_chain", {}) if isinstance(credit_status_b.get("mock_chain"), dict) else {}
        bridge_reconciliation_ok = (
            int(mock_chain_status.get("active_wallet_lock_count", 0) or 0) == 0
            and int(mock_chain_status.get("pending_payout_credit_wei", 0) or 0) == 0
            and int(mock_chain_status.get("pending_deposit_credit_wei", 0) or 0) == 0
        )
        if mock_chain_status and not bridge_reconciliation_ok:
            raise NodeMarketSmokeError(f"Mock chain bridge did not reconcile after multi-Hub run: {mock_chain_status}")

        requester_events = _bridge_audit_events(config_b, account_id=config.account_id, limit=200)
        worker_events = _bridge_audit_events(config_b, worker_node_id=str(payout.get("worker_node_id", "")), limit=200)
        _verify_expected_audit_types(
            label="multi-Hub requester",
            events=requester_events,
            expected_types={
                "bridge.deposit.requested",
                "bridge.deposit.confirmed",
                "hub.hold.created",
                "hub.hold.charged",
            },
        )
        _verify_expected_audit_types(
            label="multi-Hub worker",
            events=worker_events,
            expected_types={
                "hub.worker.earning.recorded",
                "bridge.payout.requested",
                "bridge.payout.confirmed",
            },
        )
        bridge_audit_readback_ok = True
        progress.emit(
            "multi_hub_audit_readback_ok",
            requester_event_count=len(requester_events),
            worker_event_count=len(worker_events),
            worker_node_id=str(payout.get("worker_node_id", "")),
            readback_hub="hub_b",
        )

        token_events = [event for event in read_jsonl_events(event_log_path) if event.get("event") == "token"]
        expected_spend = config.request_count * config.max_price_credits
        final_spent = int(credit_status_b.get("totals", {}).get("spent_credits", 0) or 0)
        if final_spent < expected_spend:
            raise NodeMarketSmokeError(f"Final spent credits {final_spent} is below expected smoke spend {expected_spend}.")
        if len(token_events) != config.request_count * config.token_count:
            raise NodeMarketSmokeError(
                f"Expected {config.request_count * config.token_count} fake token events, got {len(token_events)}."
            )

        selected_nodes = [node for node in nodes if node.node_id in selected_worker_ids]
        eligible_worker_count = sum(
            1 for node in nodes if node.ring <= config.requested_ring and node.price_credits <= config.max_price_credits
        )
        if config.request_count > 1 and eligible_worker_count > 1 and len(selected_worker_ids) < 2:
            raise NodeMarketSmokeError(
                "Multi-Hub run selected only one worker even though multiple eligible workers were registered. "
                f"selected_worker_ids={selected_worker_ids} eligible_worker_count={eligible_worker_count}"
            )

        route_counts = {
            **registration_counts,
            **route_submit_counts,
            **route_execution_counts,
        }
        hub_b_after_failover_ok = bool(hub_a_stopped_for_failover and status_b.get("backend") == "foundationdb")
        if config.failover_hub_a and started_a is not None:
            # If Hub A was not started by this smoke, we cannot safely kill it.  The
            # default auto mode prevents that; this keeps manual mode honest.
            hub_b_after_failover_ok = False

        report = {
            "ok": True,
            "run_id": run_id,
            "hub_a_url": config.hub_a_url,
            "hub_b_url": config.hub_b_url,
            "shared_namespace": _auto_hub_namespace(config_a),
            "execution_mode": config.execution_mode,
            "registry_backend_hub_a": "foundationdb",
            "registry_backend_hub_b": status_b.get("backend"),
            "credit_ledger_backend_hub_b": credit_status_b.get("backend"),
            "hub_a_started_by_smoke": started_a is None and hub_a_stopped_for_failover,
            "hub_b_started_by_smoke": started_b is not None,
            "hub_a_failover_completed_via_hub_b": hub_b_after_failover_ok,
            "nodes_registered": len(nodes),
            "requests_submitted": len(submitted),
            "requests_completed": len(completed),
            "selected_worker_count": len(selected_worker_ids),
            "selected_worker_ids": selected_worker_ids,
            "eligible_worker_count": eligible_worker_count,
            "selected_worker_rings": sorted({node.ring for node in selected_nodes}),
            "selected_worker_prices": sorted({node.price_credits for node in selected_nodes}),
            "route_counts": route_counts,
            "quote_submit_cross_hub": route_submit_counts.get("submit_hub_a", 0) > 0 and route_submit_counts.get("submit_hub_b", 0) > 0,
            "workers_registered_via_both_hubs": registration_counts.get("hub_a", 0) > 0 and registration_counts.get("hub_b", 0) > 0,
            "leases_polled_via_both_hubs": route_execution_counts.get("poll_hub_a", 0) > 0 and route_execution_counts.get("poll_hub_b", 0) > 0,
            "results_completed_via_both_hubs": route_execution_counts.get("completion_hub_a", 0) > 0 and route_execution_counts.get("completion_hub_b", 0) > 0,
            "cross_hub_result_replay_idempotent": bool(route_execution_counts.get("cross_hub_replay_idempotent")),
            "surprise_payout_rejected_active_work_cross_hub": bool(surprise_payout_rejection.get("rejected")),
            "payout_lock_visible_cross_hub": bool(payout.get("lock_visible_cross_hub")),
            "locked_wallet_excluded_cross_hub": bool(payout.get("locked_wallet_excluded_cross_hub")),
            "worker_payout_id": str(payout.get("payout", {}).get("payout_id", "")),
            "worker_payout_wallet_address": str(payout.get("wallet_address", "")),
            "worker_payout_node_id": str(payout.get("worker_node_id", "")),
            "bridge_deposit_id": str(bridge_funding.get("deposit", {}).get("deposit_id", "")),
            "bridge_audit_readback_ok": bridge_audit_readback_ok,
            "bridge_reconciliation_ok": bridge_reconciliation_ok,
            "bridge_audit_event_count": int(mock_chain_status.get("audit_event_count", 0) or 0),
            "token_events": len(token_events),
            "expected_spend_credits": expected_spend,
            "final_spent_credits_total": final_spent,
            "event_log_path": str(event_log_path),
        }

        required_bools = {
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
        }
        if config.failover_hub_a:
            required_bools["hub_a_failover_completed_via_hub_b"] = report["hub_a_failover_completed_via_hub_b"]
        failed_flags = sorted(key for key, value in required_bools.items() if not value)
        if failed_flags:
            raise NodeMarketSmokeError(f"Multi-Hub smoke failed required checks: {failed_flags} report={report}")

        report_path = config.resolved_report_path()
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            progress.emit("report_write_ok", report_path=report_path)
        progress.emit("done", ok=True)
        return report
    finally:
        if started_a is not None:
            progress.emit("hub_autostart_stop", hub="hub_a", hub_url=config_a.hub_url)
            started_a.stop()
        if started_b is not None:
            progress.emit("hub_autostart_stop", hub="hub_b", hub_url=config_b.hub_url)
            started_b.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a two-Hub Temporal/FDB node-market smoke over a shared Hub namespace.",
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="Repository root containing exp-fdb-hub.py.")
    parser.add_argument("--hub-a-url", default=DEFAULT_MULTI_HUB_A_URL, help="Hub A URL.")
    parser.add_argument("--hub-b-url", default=DEFAULT_MULTI_HUB_B_URL, help="Hub B URL.")
    parser.add_argument("--execution-mode", choices=["live-temporal", "local"], default="live-temporal")
    parser.add_argument("--temporal-address", default="localhost:7233")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_MULTI_HUB_REPORT_PATH)
    parser.add_argument("--event-log-path", type=Path, default=DEFAULT_EVENT_LOG_PATH)
    parser.add_argument("--node-count", type=_positive_int, default=50)
    parser.add_argument("--request-count", type=_positive_int, default=20)
    parser.add_argument("--requested-ring", type=int, default=2)
    parser.add_argument("--max-price-credits", type=_positive_int, default=2)
    parser.add_argument("--deposit-credits", type=_positive_int, default=100)
    parser.add_argument("--token-count", type=_positive_int, default=5)
    parser.add_argument("--token-interval-seconds", type=_positive_float, default=0.02)
    parser.add_argument("--keepalive-interval-seconds", type=_positive_float, default=2.0)
    parser.add_argument("--account-id", default="temporal-fdb-hub-multi-hub-client")
    parser.add_argument("--requester-wallet-address", default="0x0000000000000000000000000000000000000bb1")
    parser.add_argument("--worker-wallet-addresses", default="", help="Comma-separated worker wallet addresses to assign by node index.")
    parser.add_argument("--model", default="temporal-fdb-hub-multi-hub-model")
    parser.add_argument("--task-queue-prefix", default=NODE_MARKET_TASK_QUEUE_PREFIX + "-multi-hub")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--allow-non-fdb-backends", action="store_true")
    parser.add_argument("--progress", action="store_true", help="Emit detailed progress lines.")
    parser.add_argument("--progress-interval-seconds", type=_positive_float, default=2.0)
    parser.add_argument("--http-timeout-seconds", type=_positive_float, default=10.0)
    parser.add_argument("--http-retry-attempts", type=_positive_int, default=DEFAULT_HTTP_RETRY_ATTEMPTS)
    parser.add_argument("--hub-start-mode", choices=["auto", "never"], default="auto")
    parser.add_argument("--hub-start-timeout-seconds", type=_positive_float, default=60.0)
    parser.add_argument("--hub-namespace-prefix", default=DEFAULT_AUTO_HUB_NAMESPACE_PREFIX + "-multi-hub")
    parser.add_argument("--hub-root", type=Path, default=DEFAULT_AUTO_HUB_ROOT / "multi-hub")
    parser.add_argument("--cluster-file", type=Path, default=DEFAULT_AUTO_HUB_CLUSTER_FILE)
    parser.add_argument("--no-failover-hub-a", action="store_true", help="Do not stop Hub A before final readback.")
    return parser


def _config_from_args(args: argparse.Namespace) -> HubMultiHubSmokeConfig:
    return HubMultiHubSmokeConfig(
        repo_root=args.repo_root.resolve(),
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
        worker_wallet_addresses=_split_wallet_addresses(args.worker_wallet_addresses),
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
        failover_hub_a=not args.no_failover_hub_a,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = _config_from_args(args)
    try:
        report = asyncio.run(run_temporal_fdb_hub_multi_hub_smoke(config))
    except Exception as exc:
        print(f"FAIL: Temporal FDB multi-Hub smoke failed: {exc}", file=__import__("sys").stderr)
        return 1
    print("PASS: Temporal FDB multi-Hub smoke succeeded")
    for key in (
        "hub_a_url",
        "hub_b_url",
        "shared_namespace",
        "execution_mode",
        "nodes_registered",
        "requests_completed",
        "selected_worker_count",
        "selected_worker_ids",
        "quote_submit_cross_hub",
        "workers_registered_via_both_hubs",
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
