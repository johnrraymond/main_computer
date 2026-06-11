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


def event_payload(kind: str, node: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {
        "ts": utc_now(),
        "event": kind,
        "node_id": node.get("node_id", ""),
        "node_kind": node.get("kind", ""),
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
        problematic = sum(1 for node in nodes if "problematic" in str(node.get("tags", "")).split(","))
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
            "schema": "main-computer-hub-lab-run-summary/v1",
            "generated_at": utc_now(),
            "duration_observed_seconds": round(time.monotonic() - started_at, 3),
            "node_count": len(nodes),
            "node_kinds": dict(kinds),
            "cohorts": dict(cohorts),
            "problematic_nodes": problematic,
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
    for key in ("ok", "error", "request_count", "idempotent"):
        if key in payload:
            clean[key] = payload[key]
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


async def run_worker(node: dict[str, Any], *, args: argparse.Namespace, sink: EventSink, stop_at: float) -> None:
    rng = random.Random(as_int(node.get("sim_seed"), DEFAULT_SEED))
    client = HubClient(str(args.hub_base_url or node.get("hub_base_url") or DEFAULT_HUB_BASE_URL), timeout_seconds=args.http_timeout_seconds, retries=args.http_retries)
    startup_delay = as_float(node.get("startup_delay_ms"), 0.0) / 1000.0
    if startup_delay:
        await asyncio.sleep(min(startup_delay, max(0.0, stop_at - time.monotonic())))
    await http_call(sink, node, "worker.register", client.register_worker, node)

    heartbeat_interval = max(0.1, as_float(node.get("heartbeat_interval_ms"), 2000.0) / 1000.0)
    heartbeat_drop = max(0.0, min(1.0, as_float(node.get("heartbeat_drop_probability"), 0.0)))
    poll_interval = max(0.05, float(args.worker_poll_interval_ms) / 1000.0)
    active_requests = 0
    next_heartbeat = time.monotonic()

    while time.monotonic() < stop_at:
        now = time.monotonic()
        if now >= next_heartbeat:
            if rng.random() >= heartbeat_drop:
                await http_call(sink, node, "worker.heartbeat", client.heartbeat_worker, node, active_requests=active_requests)
            else:
                await sink.emit(event_payload("worker.heartbeat.dropped_by_lab", node))
            jitter = as_float(node.get("heartbeat_jitter_ms"), 0.0) / 1000.0
            next_heartbeat = now + heartbeat_interval + rng.uniform(0.0, max(0.0, jitter))

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


async def run_requester(node: dict[str, Any], *, args: argparse.Namespace, sink: EventSink, stop_at: float) -> None:
    rng = random.Random(as_int(node.get("sim_seed"), DEFAULT_SEED))
    client = HubClient(str(args.hub_base_url or node.get("hub_base_url") or DEFAULT_HUB_BASE_URL), timeout_seconds=args.http_timeout_seconds, retries=args.http_retries)
    startup_delay = as_float(node.get("startup_delay_ms"), 0.0) / 1000.0
    if startup_delay:
        await asyncio.sleep(min(startup_delay, max(0.0, stop_at - time.monotonic())))

    if args.request_mode == "registration_only":
        await sink.emit(event_payload("requester.skipped_registration_only", node))
        return

    request_index = 0
    mean_interval = max(10.0, as_float(node.get("request_interval_mean_ms"), 1400.0))
    burst_probability_per_minute = max(0.0, as_float(node.get("burst_probability_per_minute"), 0.0))
    burst_multiplier = max(1.0, as_float(node.get("burst_multiplier_median"), 1.0))

    while time.monotonic() < stop_at:
        request_index += 1
        interval_ms = sample_exponential_ms(rng, mean_interval, clamp_min=100, clamp_max=args.max_request_interval_ms)
        # Bursts lower the next interval, but remain deterministic under the node seed.
        if rng.random() < min(1.0, burst_probability_per_minute / 60.0):
            interval_ms = max(25.0, interval_ms / burst_multiplier)
            await sink.emit(event_payload("requester.burst_interval", node, interval_ms=round(interval_ms, 3)))

        prompt = f"scheduler lab request {request_index} from {node.get('node_id')}"
        await http_call(
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
        await asyncio.sleep(min(interval_ms / 1000.0, max(0.0, stop_at - time.monotonic())))


def select_nodes(nodes: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    if role == "all":
        return nodes
    if role == "workers":
        return [node for node in nodes if node.get("kind") == "worker"]
    if role == "requesters":
        return [node for node in nodes if node.get("kind") == "requester"]
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
            if node.get("kind") == "worker":
                tasks.append(asyncio.create_task(run_worker(node, args=args, sink=sink, stop_at=stop_at)))
            elif node.get("kind") == "requester":
                tasks.append(asyncio.create_task(run_requester(node, args=args, sink=sink, stop_at=stop_at)))

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
    parser = argparse.ArgumentParser(description="Run simulated Hub scheduler-lab workers/requesters in a container.")
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
