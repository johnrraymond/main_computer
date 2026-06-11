from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import signal
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from tools.scheduler_lab.hub_client import HubClient, HubHttpResponse
from tools.scheduler_lab.node_list import (
    DEFAULT_HUB_BASE_URL,
    DEFAULT_SEED,
    build_document,
    build_nodes,
    infer_total_from_filename,
    load_nodes,
    write_nodes,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class NodeRuntimeState:
    local_busy_until: float = 0.0
    request_blocked_until: float = 0.0
    worker_force_until: float = 0.0


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value == "":
            return default
        return int(value)
    except Exception:
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value == "":
            return default
        return float(value)
    except Exception:
        return default


def env_flag(name: str, default: bool) -> bool:
    fallback = "1" if default else "0"
    return str(os.environ.get(name, fallback)).strip().lower() in {"1", "true", "yes", "on"}


def parse_json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return default


def sample_lognormal_ms(rng: random.Random, median_ms: float, sigma: float, *, clamp_min: float = 0.0, clamp_max: float = 60_000.0) -> float:
    median_ms = max(0.001, float(median_ms))
    sigma = max(0.0, float(sigma))
    value = median_ms if sigma == 0 else rng.lognormvariate(math.log(median_ms), sigma)
    return max(clamp_min, min(clamp_max, value))


def sample_exponential_ms(rng: random.Random, mean_ms: float, *, clamp_min: float = 10.0, clamp_max: float = 60_000.0) -> float:
    mean_ms = max(0.001, float(mean_ms))
    value = rng.expovariate(1.0 / mean_ms)
    return max(clamp_min, min(clamp_max, value))


def hazard_probability_per_tick(per_minute: float, tick_seconds: float) -> float:
    clean_per_minute = max(0.0, float(per_minute))
    clean_tick = max(0.0, float(tick_seconds))
    return max(0.0, min(1.0, 1.0 - math.exp(-(clean_per_minute / 60.0) * clean_tick)))


def event_payload(kind: str, node: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {
        "ts": utc_now(),
        "event": kind,
        "node_id": node.get("node_id", ""),
        "node_kind": node.get("kind", ""),
        "behavior_mode": node.get("behavior_mode", ""),
        "cohort": node.get("cohort", ""),
        **fields,
    }


class EventSink:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.events_path = output_dir / "scheduler-lab-events.jsonl"
        self.summary_path = output_dir / "scheduler-lab-summary.json"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._handle = self.events_path.open("a", encoding="utf-8", newline="\n")
        self._lock = asyncio.Lock()
        self.counts: Counter[str] = Counter()
        self.latency_ms: dict[str, list[float]] = {}

    async def emit(self, event: dict[str, Any]) -> None:
        async with self._lock:
            name = str(event.get("event", "unknown"))
            self.counts[name] += 1
            elapsed = event.get("elapsed_ms")
            if isinstance(elapsed, (int, float)):
                self.latency_ms.setdefault(name, []).append(float(elapsed))
            self._handle.write(json.dumps(event, sort_keys=True) + "\n")
            self._handle.flush()

    async def close(self) -> None:
        async with self._lock:
            self._handle.close()

    def summary(self, *, nodes: list[dict[str, Any]], started_at: float) -> dict[str, Any]:
        kinds = Counter(str(node.get("kind", "")) for node in nodes)
        cohorts = Counter(str(node.get("cohort", "")) for node in nodes)
        behavior_modes = Counter(str(node.get("behavior_mode", "")) for node in nodes if str(node.get("behavior_mode", "")))
        funding_remediation = Counter(str(node.get("funding_remediation", "")) for node in nodes if str(node.get("funding_remediation", "")))
        problematic = sum(1 for node in nodes if "problematic" in str(node.get("tags", "")).split(","))
        initial_credits = sum(as_int(node.get("initial_credits"), 0) for node in nodes)
        latency_summary: dict[str, dict[str, float]] = {}
        for event_name, values in self.latency_ms.items():
            if not values:
                continue
            ordered = sorted(values)
            p95_index = min(len(ordered) - 1, int(math.ceil(len(ordered) * 0.95)) - 1)
            latency_summary[event_name] = {
                "count": len(ordered),
                "mean_ms": round(sum(ordered) / len(ordered), 3),
                "p95_ms": round(ordered[p95_index], 3),
                "max_ms": round(ordered[-1], 3),
            }
        return {
            "schema": "main-computer-hub-lab-run-summary/v2",
            "generated_at": utc_now(),
            "duration_observed_seconds": round(time.monotonic() - started_at, 3),
            "node_count": len(nodes),
            "node_kinds": dict(kinds),
            "cohorts": dict(cohorts),
            "behavior_modes": dict(behavior_modes),
            "funding_remediation": dict(funding_remediation),
            "problematic_nodes": problematic,
            "configured_initial_credits": initial_credits,
            "events": dict(self.counts),
            "latency": latency_summary,
            "events_path": str(self.events_path),
        }

    def write_summary(self, summary: dict[str, Any]) -> None:
        self.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


async def http_call(sink: EventSink, node: dict[str, Any], event_name: str, func, *args: Any, **kwargs: Any) -> HubHttpResponse:
    response: HubHttpResponse = await asyncio.to_thread(func, *args, **kwargs)
    await sink.emit(
        event_payload(
            event_name,
            node,
            ok=response.ok,
            status=response.status,
            elapsed_ms=round(response.elapsed_ms, 3),
            response_summary=_short_payload(response.payload),
        )
    )
    return response


def _short_payload(payload: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key in ("ok", "error", "reason_code", "request_count", "idempotent"):
        if key in payload:
            clean[key] = payload[key]
    if isinstance(payload.get("account"), dict):
        account = payload["account"]
        clean["account"] = {
            "account_id": account.get("account_id"),
            "available_credits": account.get("available_credits", account.get("available_credits_display")),
            "held_credits": account.get("held_credits", account.get("held_credits_display")),
            "spent_credits": account.get("spent_credits", account.get("spent_credits_display")),
        }
    if isinstance(payload.get("transaction"), dict):
        tx = payload["transaction"]
        clean["transaction"] = {
            "transaction_id": tx.get("transaction_id"),
            "account_id": tx.get("account_id"),
            "credits": tx.get("credits"),
            "transaction_type": tx.get("transaction_type"),
        }
    if isinstance(payload.get("request"), dict):
        request = payload["request"]
        clean["request"] = {
            "request_id": request.get("request_id"),
            "state": request.get("state"),
            "error": request.get("error"),
        }
    if isinstance(payload.get("lease"), dict):
        lease = payload["lease"]
        clean["lease"] = {
            "request_id": lease.get("request_id"),
            "lease_id": lease.get("lease_id"),
            "model": lease.get("model"),
            "credits_per_request": lease.get("credits_per_request"),
        }
    elif payload.get("lease") is None:
        clean["lease"] = None
    return clean


def node_account_id(node: dict[str, Any], args: argparse.Namespace) -> str:
    return str(node.get("account_id") or f"{args.account_id_prefix}-{node.get('node_id')}")


def request_probability(node: dict[str, Any]) -> float:
    return max(0.0, min(1.0, as_float(node.get("request_probability"), 1.0 if node.get("kind") == "requester" else 0.0)))


def worker_offer_probability(node: dict[str, Any]) -> float:
    return max(0.0, min(1.0, as_float(node.get("worker_offer_probability"), 1.0 if node.get("kind") == "worker" else 0.0)))


def node_can_request(node: dict[str, Any]) -> bool:
    return request_probability(node) > 0.0 and str(node.get("offered_credits", "")).strip() != ""


def node_can_work(node: dict[str, Any]) -> bool:
    if worker_offer_probability(node) <= 0.0:
        return False
    if as_int(node.get("max_concurrency"), 0) <= 0:
        return False
    models = parse_json(node.get("models_json"), [])
    return bool(models or str(node.get("model", "")).strip())


def account_available_credits(payload: dict[str, Any]) -> int:
    account = payload.get("account") if isinstance(payload, dict) else None
    if not isinstance(account, dict):
        return 0
    for key in ("available_credits", "available_credits_display"):
        value = account.get(key)
        try:
            return int(float(str(value or "0").split()[0]))
        except Exception:
            continue
    return 0


def is_insufficient_credit_response(response: HubHttpResponse) -> bool:
    if response.status not in {400, 402}:
        return False
    error = str(response.payload.get("error", "") if isinstance(response.payload, dict) else "")
    reason = str(response.payload.get("reason_code", "") if isinstance(response.payload, dict) else "")
    text = f"{reason} {error}".lower()
    return "insufficient" in text and "credit" in text


async def top_up_account_to(
    *,
    sink: EventSink,
    node: dict[str, Any],
    client: HubClient,
    account_id: str,
    desired_credits: int,
    reason: str,
) -> None:
    desired = max(0, int(desired_credits))
    if desired <= 0:
        await sink.emit(event_payload("node.funding.skipped_zero_target", node, account_id=account_id, reason=reason))
        return

    balance = await http_call(sink, node, "node.funding.balance_checked", client.get_credit_balance, account_id)
    available = account_available_credits(balance.payload)
    if balance.ok and available >= desired:
        await sink.emit(event_payload("node.funding.not_needed", node, account_id=account_id, available_credits=available, desired_credits=desired, reason=reason))
        return

    delta = desired if not balance.ok else max(0, desired - available)
    if delta <= 0:
        await sink.emit(event_payload("node.funding.not_needed", node, account_id=account_id, available_credits=available, desired_credits=desired, reason=reason))
        return

    await http_call(
        sink,
        node,
        "node.funding.issued",
        client.issue_credits,
        account_id=account_id,
        credits=delta,
        memo=f"scheduler lab {reason} for {node.get('node_id')}",
        metadata={
            "scheduler_lab": True,
            "node_id": node.get("node_id"),
            "behavior_mode": node.get("behavior_mode", ""),
            "reason": reason,
            "desired_credits": desired,
            "observed_available_credits": available,
        },
    )


async def bootstrap_node_funding(node: dict[str, Any], *, args: argparse.Namespace, sink: EventSink, client: HubClient) -> None:
    if not args.bootstrap_funding:
        return
    desired = as_int(node.get("initial_credits"), 0)
    if desired <= 0:
        await sink.emit(event_payload("node.funding.bootstrap.skipped_unfunded_start", node, account_id=node_account_id(node, args)))
        return
    await top_up_account_to(
        sink=sink,
        node=node,
        client=client,
        account_id=node_account_id(node, args),
        desired_credits=desired,
        reason="bootstrap",
    )


async def maybe_start_local_work(
    node: dict[str, Any],
    *,
    state: NodeRuntimeState,
    sink: EventSink,
    rng: random.Random,
    tick_seconds: float,
) -> bool:
    now = time.monotonic()
    if now < state.local_busy_until:
        return True
    hazard = as_float(node.get("local_busy_probability_per_minute"), 0.0)
    if hazard <= 0:
        return False
    if rng.random() >= hazard_probability_per_tick(hazard, tick_seconds):
        return False
    duration_ms = sample_lognormal_ms(
        rng,
        as_float(node.get("local_busy_median_ms"), 0.0),
        0.55,
        clamp_min=250.0,
        clamp_max=max(250.0, as_float(node.get("local_busy_max_ms"), 60_000.0)),
    )
    state.local_busy_until = now + duration_ms / 1000.0
    await sink.emit(event_payload("node.local_work.started", node, duration_ms=round(duration_ms, 3), busy_until_monotonic=round(state.local_busy_until, 3)))
    return True


async def run_worker_loop(
    node: dict[str, Any],
    *,
    args: argparse.Namespace,
    sink: EventSink,
    stop_at: float,
    state: NodeRuntimeState,
    client: HubClient,
    rng: random.Random,
) -> None:
    await http_call(sink, node, "worker.register", client.register_worker, node)

    heartbeat_interval = max(0.1, as_float(node.get("heartbeat_interval_ms"), 2000.0) / 1000.0)
    heartbeat_drop = max(0.0, min(1.0, as_float(node.get("heartbeat_drop_probability"), 0.0)))
    poll_interval = max(0.05, float(args.worker_poll_interval_ms) / 1000.0)
    active_requests = 0
    next_heartbeat = time.monotonic()

    while time.monotonic() < stop_at:
        now = time.monotonic()
        busy_with_local_work = False
        if args.enable_local_busy:
            busy_with_local_work = await maybe_start_local_work(node, state=state, sink=sink, rng=rng, tick_seconds=poll_interval)

        if now >= next_heartbeat:
            if rng.random() >= heartbeat_drop:
                status = "busy" if busy_with_local_work else "available"
                await http_call(sink, node, "worker.heartbeat", client.heartbeat_worker, node, active_requests=active_requests, status=status)
            else:
                await sink.emit(event_payload("worker.heartbeat.dropped_by_lab", node))
            jitter = as_float(node.get("heartbeat_jitter_ms"), 0.0) / 1000.0
            next_heartbeat = now + heartbeat_interval + rng.uniform(0.0, max(0.0, jitter))

        if busy_with_local_work:
            await sink.emit(event_payload("worker.poll.skipped_local_work", node, remaining_ms=round(max(0.0, state.local_busy_until - time.monotonic()) * 1000.0, 3)))
            await asyncio.sleep(min(poll_interval, max(0.0, stop_at - time.monotonic())))
            continue

        offer_probability = worker_offer_probability(node)
        if time.monotonic() < state.worker_force_until:
            offer_probability = max(offer_probability, 0.95)
        if rng.random() > offer_probability:
            await asyncio.sleep(min(poll_interval, max(0.0, stop_at - time.monotonic())))
            continue

        response = await http_call(sink, node, "worker.poll", client.poll_worker, node, lease_seconds=args.lease_seconds)
        lease = response.payload.get("lease") if isinstance(response.payload, dict) else None
        if isinstance(lease, dict):
            active_requests += 1
            try:
                await execute_lease(node, lease, client=client, sink=sink, rng=rng, args=args)
            finally:
                active_requests = max(0, active_requests - 1)
        await asyncio.sleep(min(poll_interval, max(0.0, stop_at - time.monotonic())))


async def execute_lease(
    node: dict[str, Any],
    lease: dict[str, Any],
    *,
    client: HubClient,
    sink: EventSink,
    rng: random.Random,
    args: argparse.Namespace,
) -> None:
    if rng.random() < max(0.0, min(1.0, as_float(node.get("post_ready_disconnect_probability"), 0.0))):
        await sink.emit(event_payload("worker.lease.disconnect_before_result", node, lease_id=lease.get("lease_id"), request_id=lease.get("request_id")))
        return
    if rng.random() < max(0.0, min(1.0, as_float(node.get("model_load_failure_probability"), 0.0))):
        await http_call(
            sink,
            node,
            "worker.result.failure_submitted",
            client.submit_worker_result,
            node,
            lease,
            {
                "status": "failed",
                "error": "scheduler lab simulated model load failure",
                "provider": "scheduler-lab",
                "model": lease.get("model") or node.get("model"),
            },
        )
        return

    normal_ms = as_float(node.get("runtime_normal_median_ms"), 1600.0)
    slow_ms = as_float(node.get("runtime_slow_median_ms"), 5500.0)
    runtime_ms = sample_lognormal_ms(rng, slow_ms if rng.random() < 0.15 else normal_ms, 0.45, clamp_min=10, clamp_max=args.max_runtime_ms)
    await sink.emit(event_payload("worker.execution.started", node, lease_id=lease.get("lease_id"), request_id=lease.get("request_id"), runtime_ms=round(runtime_ms, 3)))
    await asyncio.sleep(runtime_ms / 1000.0)

    if rng.random() < max(0.0, min(1.0, as_float(node.get("execution_crash_probability"), 0.0))):
        await sink.emit(event_payload("worker.execution.crashed", node, lease_id=lease.get("lease_id"), request_id=lease.get("request_id")))
        return

    if rng.random() < max(0.0, min(1.0, as_float(node.get("result_submit_drop_probability"), 0.0))):
        await sink.emit(event_payload("worker.result.dropped_by_lab", node, lease_id=lease.get("lease_id"), request_id=lease.get("request_id")))
        return

    delay_ms = sample_lognormal_ms(rng, as_float(node.get("result_submit_delay_median_ms"), 80.0), 0.45, clamp_min=0, clamp_max=5000)
    if delay_ms:
        await asyncio.sleep(delay_ms / 1000.0)
    result = {
        "status": "success",
        "content": f"scheduler lab result from {node.get('node_id')} for {lease.get('request_id')}",
        "provider": "scheduler-lab",
        "model": lease.get("model") or node.get("model"),
        "metadata": {
            "scheduler_lab": True,
            "worker_node_id": node.get("node_id"),
            "cohort": node.get("cohort"),
            "lease_id": lease.get("lease_id"),
        },
    }
    await http_call(sink, node, "worker.result.submitted", client.submit_worker_result, node, lease, result)
    if rng.random() < max(0.0, min(1.0, as_float(node.get("result_submit_duplicate_probability"), 0.0))):
        await http_call(sink, node, "worker.result.duplicate_submitted", client.submit_worker_result, node, lease, result)


async def handle_low_credit_remediation(
    node: dict[str, Any],
    *,
    args: argparse.Namespace,
    sink: EventSink,
    client: HubClient,
    rng: random.Random,
    state: NodeRuntimeState,
    response: HubHttpResponse,
) -> None:
    account_id = node_account_id(node, args)
    configured = str(node.get("funding_remediation") or "work_to_earn").strip().lower()
    remediation = configured
    if configured == "mixed":
        remediation = "work_to_earn" if node_can_work(node) and rng.random() < 0.70 else "faucet"

    backoff_s = max(0.1, as_float(node.get("insufficient_credit_backoff_ms"), 3000.0) / 1000.0)
    work_seconds = max(backoff_s, as_float(node.get("low_credit_work_seconds"), 30.0))

    await sink.emit(
        event_payload(
            "requester.request.rejected.insufficient_credits",
            node,
            account_id=account_id,
            remediation=configured,
            chosen_remediation=remediation,
            status=response.status,
            error=str(response.payload.get("error", "")) if isinstance(response.payload, dict) else "",
        )
    )

    if remediation == "faucet":
        desired = max(
            as_int(node.get("faucet_top_up_credits"), 0),
            as_int(node.get("low_credit_threshold"), 0) + max(1, as_int(node.get("offered_credits"), 1)),
        )
        await sink.emit(event_payload("node.low_credit.remediation_faucet", node, account_id=account_id, desired_credits=desired))
        await top_up_account_to(sink=sink, node=node, client=client, account_id=account_id, desired_credits=desired, reason="low_credit_faucet")
        state.request_blocked_until = time.monotonic() + backoff_s
        return

    if remediation == "work_to_earn" and node_can_work(node):
        state.worker_force_until = time.monotonic() + work_seconds
        state.request_blocked_until = state.worker_force_until
        await sink.emit(event_payload("node.low_credit.remediation_work_to_earn", node, account_id=account_id, work_seconds=round(work_seconds, 3)))
        return

    state.request_blocked_until = time.monotonic() + max(work_seconds, backoff_s)
    await sink.emit(
        event_payload(
            "node.low_credit.remediation_dormant",
            node,
            account_id=account_id,
            dormant_seconds=round(max(work_seconds, backoff_s), 3),
            requested_remediation=configured,
        )
    )


async def run_requester_loop(
    node: dict[str, Any],
    *,
    args: argparse.Namespace,
    sink: EventSink,
    stop_at: float,
    state: NodeRuntimeState,
    client: HubClient,
    rng: random.Random,
) -> None:
    if args.request_mode == "registration_only":
        await sink.emit(event_payload("requester.skipped_registration_only", node))
        return

    request_index = 0
    mean_interval = max(10.0, as_float(node.get("request_interval_mean_ms"), 1400.0))
    burst_probability_per_minute = max(0.0, as_float(node.get("burst_probability_per_minute"), 0.0))
    burst_multiplier = max(1.0, as_float(node.get("burst_multiplier_median"), 1.0))

    while time.monotonic() < stop_at:
        interval_ms = sample_exponential_ms(rng, mean_interval, clamp_min=100, clamp_max=args.max_request_interval_ms)
        # Bursts lower the next interval, but remain deterministic under the node seed.
        if rng.random() < min(1.0, burst_probability_per_minute / 60.0):
            interval_ms = max(25.0, interval_ms / burst_multiplier)
            await sink.emit(event_payload("requester.burst_interval", node, interval_ms=round(interval_ms, 3)))

        if args.enable_local_busy and await maybe_start_local_work(node, state=state, sink=sink, rng=rng, tick_seconds=interval_ms / 1000.0):
            await sink.emit(event_payload("requester.request.skipped_local_work", node, remaining_ms=round(max(0.0, state.local_busy_until - time.monotonic()) * 1000.0, 3)))
            await asyncio.sleep(min(interval_ms / 1000.0, max(0.0, stop_at - time.monotonic())))
            continue

        if time.monotonic() < state.request_blocked_until:
            await sink.emit(event_payload("requester.request.skipped_low_credit_mode", node, remaining_ms=round(max(0.0, state.request_blocked_until - time.monotonic()) * 1000.0, 3)))
            await asyncio.sleep(min(interval_ms / 1000.0, max(0.0, stop_at - time.monotonic())))
            continue

        if rng.random() > request_probability(node):
            await asyncio.sleep(min(interval_ms / 1000.0, max(0.0, stop_at - time.monotonic())))
            continue

        request_index += 1
        prompt = f"scheduler lab request {request_index} from {node.get('node_id')}"
        response = await http_call(
            sink,
            node,
            "requester.request.submitted",
            client.submit_request,
            node,
            request_index=request_index,
            request_mode=args.request_mode,
            account_id_prefix=args.account_id_prefix,
            prompt=prompt,
        )
        if is_insufficient_credit_response(response):
            await handle_low_credit_remediation(node, args=args, sink=sink, client=client, rng=rng, state=state, response=response)
        await asyncio.sleep(min(interval_ms / 1000.0, max(0.0, stop_at - time.monotonic())))


async def run_node(
    node: dict[str, Any],
    *,
    args: argparse.Namespace,
    sink: EventSink,
    stop_at: float,
    worker_enabled: bool,
    requester_enabled: bool,
) -> None:
    seed = as_int(node.get("sim_seed"), DEFAULT_SEED)
    rng = random.Random(seed)
    client = HubClient(str(args.hub_base_url or node.get("hub_base_url") or DEFAULT_HUB_BASE_URL), timeout_seconds=args.http_timeout_seconds, retries=args.http_retries)
    startup_delay = as_float(node.get("startup_delay_ms"), 0.0) / 1000.0
    if startup_delay:
        await asyncio.sleep(min(startup_delay, max(0.0, stop_at - time.monotonic())))

    state = NodeRuntimeState()
    await bootstrap_node_funding(node, args=args, sink=sink, client=client)

    tasks: list[asyncio.Task[None]] = []
    if worker_enabled:
        tasks.append(
            asyncio.create_task(
                run_worker_loop(
                    node,
                    args=args,
                    sink=sink,
                    stop_at=stop_at,
                    state=state,
                    client=client,
                    rng=random.Random(seed ^ 0xA51C0DE),
                )
            )
        )
    if requester_enabled:
        tasks.append(
            asyncio.create_task(
                run_requester_loop(
                    node,
                    args=args,
                    sink=sink,
                    stop_at=stop_at,
                    state=state,
                    client=client,
                    rng=random.Random(seed ^ 0xC0FFEE),
                )
            )
        )

    if not tasks:
        await sink.emit(event_payload("node.skipped_no_enabled_behavior", node, worker_enabled=worker_enabled, requester_enabled=requester_enabled))
        return
    await asyncio.gather(*tasks, return_exceptions=True)


def select_nodes(nodes: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    if role == "all":
        return nodes
    if role == "workers":
        return [node for node in nodes if node_can_work(node)]
    if role == "requesters":
        return [node for node in nodes if node_can_request(node)]
    raise ValueError(f"unsupported role: {role}")


async def run(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    node_list_path = Path(args.node_list)
    if args.generate_node_list or not node_list_path.exists():
        total = args.total if args.total is not None else infer_total_from_filename(node_list_path)
        nodes = build_nodes(
            total=total,
            workers=args.workers,
            requesters=args.requesters,
            seed=args.seed,
            hub_base_url=args.hub_base_url,
            network=args.network,
            ring=args.ring,
            chain_id=args.chain_id,
            problematic_worker_rate=args.problematic_worker_rate,
            problematic_requester_rate=args.problematic_requester_rate,
            problematic_failure_multiplier=args.problematic_failure_multiplier,
            disable_problematic=args.disable_problematic,
        )
        document = build_document(nodes, seed=args.seed, hub_base_url=args.hub_base_url, network=args.network, ring=args.ring, chain_id=args.chain_id)
        write_nodes(node_list_path, document)
    else:
        nodes = load_nodes(node_list_path)

    selected_nodes = select_nodes(nodes, args.role)
    sink = EventSink(output_dir)
    started_at = time.monotonic()
    stop_at = started_at + max(0.1, float(args.duration_seconds))
    stop_event = asyncio.Event()

    def _request_stop(*_unused: object) -> None:
        stop_event.set()

    try:
        loop = asyncio.get_running_loop()
        for signame in ("SIGINT", "SIGTERM"):
            signum = getattr(signal, signame, None)
            if signum is not None:
                try:
                    loop.add_signal_handler(signum, _request_stop)
                except (NotImplementedError, RuntimeError):
                    pass

        await sink.emit({"ts": utc_now(), "event": "lab.started", "role": args.role, "node_count": len(selected_nodes), "hub_base_url": args.hub_base_url, "node_list": str(node_list_path)})
        tasks = []
        for node in selected_nodes:
            worker_enabled = args.role != "requesters" and node_can_work(node)
            requester_enabled = args.role != "workers" and node_can_request(node)
            tasks.append(asyncio.create_task(run_node(node, args=args, sink=sink, stop_at=stop_at, worker_enabled=worker_enabled, requester_enabled=requester_enabled)))

        while time.monotonic() < stop_at and not stop_event.is_set():
            await asyncio.sleep(0.25)

        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await sink.emit({"ts": utc_now(), "event": "lab.finished", "role": args.role, "node_count": len(selected_nodes)})
        summary = sink.summary(nodes=selected_nodes, started_at=started_at)
        sink.write_summary(summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    finally:
        await sink.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run simulated Hub scheduler-lab nodes with adaptive worker/requester behavior.")
    parser.add_argument("--role", choices=["all", "workers", "requesters"], default=os.environ.get("LAB_ROLE", "all"))
    parser.add_argument("--hub-base-url", default=os.environ.get("HUB_BASE_URL", DEFAULT_HUB_BASE_URL))
    parser.add_argument("--node-list", default=os.environ.get("LAB_NODE_LIST", "/lab-output/120-First-Post.jsonl"))
    parser.add_argument("--generate-node-list", action="store_true", default=os.environ.get("GENERATE_NODE_LIST", "1").lower() in {"1", "true", "yes", "on"})
    parser.add_argument("--output-dir", default=os.environ.get("LAB_OUTPUT_DIR", "/lab-output"))
    parser.add_argument("--duration-seconds", type=float, default=float(os.environ.get("LAB_DURATION_SECONDS", "300")))
    parser.add_argument("--total", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--requesters", type=int, default=None)
    parser.add_argument("--seed", type=int, default=int(os.environ.get("LAB_SEED", str(DEFAULT_SEED))))
    parser.add_argument("--network", default=os.environ.get("LAB_NETWORK", "dev"))
    parser.add_argument("--ring", type=int, default=int(os.environ.get("LAB_RING", "2")))
    parser.add_argument("--chain-id", type=int, default=int(os.environ.get("LAB_CHAIN_ID", "42424242")))
    parser.add_argument("--problematic-worker-rate", type=float, default=float(os.environ.get("PROBLEMATIC_WORKER_RATE", "0.08")))
    parser.add_argument("--problematic-requester-rate", type=float, default=float(os.environ.get("PROBLEMATIC_REQUESTER_RATE", "0.05")))
    parser.add_argument("--problematic-failure-multiplier", type=float, default=float(os.environ.get("PROBLEMATIC_FAILURE_MULTIPLIER", "4.0")))
    parser.add_argument("--disable-problematic", action="store_true")
    parser.add_argument("--request-mode", choices=["worker_pull_v0", "legacy", "registration_only"], default=os.environ.get("REQUEST_MODE", "worker_pull_v0"))
    parser.add_argument("--account-id-prefix", default=os.environ.get("LAB_ACCOUNT_ID_PREFIX", "lab-account"))
    parser.add_argument("--bootstrap-funding", dest="bootstrap_funding", action="store_true", default=env_flag("LAB_BOOTSTRAP_FUNDING", True))
    parser.add_argument("--no-bootstrap-funding", dest="bootstrap_funding", action="store_false")
    parser.add_argument("--enable-local-busy", dest="enable_local_busy", action="store_true", default=env_flag("LAB_ENABLE_LOCAL_BUSY", True))
    parser.add_argument("--disable-local-busy", dest="enable_local_busy", action="store_false")
    parser.add_argument("--worker-poll-interval-ms", type=float, default=float(os.environ.get("WORKER_POLL_INTERVAL_MS", "500")))
    parser.add_argument("--lease-seconds", type=float, default=float(os.environ.get("LEASE_SECONDS", "45")))
    parser.add_argument("--max-runtime-ms", type=float, default=float(os.environ.get("MAX_RUNTIME_MS", "30000")))
    parser.add_argument("--max-request-interval-ms", type=float, default=float(os.environ.get("MAX_REQUEST_INTERVAL_MS", "15000")))
    parser.add_argument("--http-timeout-seconds", type=float, default=float(os.environ.get("HTTP_TIMEOUT_SECONDS", "10")))
    parser.add_argument("--http-retries", type=int, default=int(os.environ.get("HTTP_RETRIES", "1")))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
