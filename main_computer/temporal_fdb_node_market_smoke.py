from __future__ import annotations

import argparse
import asyncio
import json
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from main_computer.credit_units import credit_count_to_wei, credit_wei_to_decimal_text
from main_computer.hub_credit_ledger import HubCreditLedger
from main_computer.protected_mode_pretest import (
    DEFAULT_PROTECTED_NETWORK,
    ProtectedModePretestError,
    find_repo_root,
    load_protected_network_profile,
    normalize_network_name,
)
from tools.temporal_lab.activities import FakeTokenActivities
from tools.temporal_lab.event_log import DEFAULT_EVENT_LOG_PATH, read_jsonl_events
from tools.temporal_lab.local_temporal import DEFAULT_NAMESPACE
from tools.temporal_lab.models import FakeTokenRequest, normalize_ring


ExecutionMode = Literal["live-temporal", "direct-activity"]
LedgerBackend = Literal["foundationdb", "json"]
FdbStartMode = Literal["auto", "never", "always"]


DEFAULT_NODE_MARKET_REPORT_PATH = Path("runtime") / "temporal_lab" / "temporal_fdb_node_market_report.json"
DEFAULT_NODE_MARKET_LEDGER_ROOT = Path("runtime") / "temporal_lab" / "temporal_fdb_node_market_hub_credit_ledger"
DEFAULT_FDB_CLUSTER_FILE = Path(".foundationdb") / "docker.cluster"
NODE_MARKET_TASK_QUEUE_PREFIX = "scheduler-lab-node-market"


class _ProgressReporter:
    def __init__(self, *, enabled: bool, interval_seconds: float, stream: Any | None = None) -> None:
        self.enabled = bool(enabled)
        self.interval_seconds = max(0.25, float(interval_seconds))
        self.stream = stream if stream is not None else sys.stdout
        self.started_at = time.perf_counter()
        self._last_by_key: dict[str, float] = {}

    def emit(self, event: str, **fields: Any) -> None:
        if not self.enabled:
            return
        elapsed = time.perf_counter() - self.started_at
        details = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
        suffix = f" {details}" if details else ""
        print(f"[node-market +{elapsed:7.2f}s] {event}{suffix}", file=self.stream, flush=True)

    def heartbeat(self, key: str, event: str, **fields: Any) -> None:
        if not self.enabled:
            return
        now = time.perf_counter()
        last = self._last_by_key.get(key)
        if last is not None and now - last < self.interval_seconds:
            return
        self._last_by_key[key] = now
        self.emit(event, **fields)


class NodeMarketSmokeError(ProtectedModePretestError):
    """Raised when the node-market smoke cannot complete safely."""


def _temporal_lab_startup_help(temporal_address: str, namespace: str) -> str:
    return (
        "Temporal is not reachable for the live Temporal lab. After a reboot, start the local lab pieces from "
        "the repository root in an activated virtualenv:\n"
        "  python -m tools.temporal_lab.local_temporal up --pull\n"
        "  python -m tools.temporal_lab.local_temporal status\n"
        f"Expected Temporal address: {temporal_address}\n"
        f"Expected Temporal namespace: {namespace}"
    )


def _temporal_host_port(temporal_address: str) -> tuple[str, int] | None:
    text = str(temporal_address or "").strip()
    if not text:
        return None
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.rsplit("/", 1)[-1]
    if text.startswith("[") and "]:" in text:
        host, port_text = text[1:].split("]:", 1)
    elif ":" in text:
        host, port_text = text.rsplit(":", 1)
    else:
        return None
    try:
        return host or "localhost", int(port_text)
    except ValueError:
        return None


def _tcp_connects(host: str, port: int, *, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=max(0.2, float(timeout))):
            return True
    except OSError:
        return False


def _ensure_temporal_listening(config: "NodeMarketSmokeConfig", *, progress: _ProgressReporter) -> None:
    endpoint = _temporal_host_port(config.temporal_address)
    if endpoint is None:
        return
    host, port = endpoint
    if _tcp_connects(host, port, timeout=2.0):
        return
    progress.emit("temporal_not_reachable", address=config.temporal_address, namespace=config.namespace)
    raise NodeMarketSmokeError(_temporal_lab_startup_help(config.temporal_address, config.namespace))


def _run_storage_step(
    *,
    label: str,
    timeout_seconds: float,
    progress: _ProgressReporter,
    operation: Any,
) -> Any:
    """Run one potentially blocking storage call with visible progress and a bound.

    FDB client calls can block for a long time when a cluster file exists but the
    server behind it is stopped or stale. The smoke should surface that as a
    diagnostic failure instead of appearing to hang with no output.
    """

    timeout = _positive_float(timeout_seconds, field_name="storage_operation_timeout_seconds", minimum=0.0)
    if timeout <= 0:
        return operation()

    result_queue: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()

    def _target() -> None:
        try:
            result_queue.put(("ok", operation()))
        except BaseException as exc:  # pragma: no cover - re-raised in caller
            result_queue.put(("error", exc))

    thread = threading.Thread(
        target=_target,
        name=f"node-market-storage-{label[:40]}",
        daemon=True,
    )
    started = time.perf_counter()
    thread.start()
    while thread.is_alive():
        elapsed = time.perf_counter() - started
        if elapsed >= timeout:
            raise NodeMarketSmokeError(
                f"{label} timed out after {timeout:g}s. "
                "This usually means the FoundationDB cluster file exists but the local FDB server "
                "is unreachable or unhealthy. Re-run "
                "`python scripts/smoke_foundationdb_credit_ledger_primitives.py --keep-container` "
                "or delete .foundationdb/docker.cluster so this smoke can bootstrap a fresh FDB container."
            )
        thread.join(timeout=min(0.25, max(0.0, timeout - elapsed)))
        waited = time.perf_counter() - started
        progress.heartbeat(
            f"storage:{label}",
            "storage_operation_waiting",
            operation=label,
            waited_seconds=round(waited, 1),
            timeout_seconds=timeout,
        )

    status, payload = result_queue.get()
    if status == "error":
        if isinstance(payload, NodeMarketSmokeError):
            raise payload
        raise NodeMarketSmokeError(f"{label} failed: {type(payload).__name__}: {payload}") from payload
    return payload


class CreditLedger(Protocol):
    def issue(
        self,
        *,
        account_id: str,
        credits: int,
        memo: str = "",
        owner_address: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def create_hold_credit_wei(
        self,
        *,
        account_id: str,
        request_id: str,
        credit_wei: int | str,
        expires_at: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def charge_hold_credit_wei(
        self,
        *,
        hold_id: str,
        charged_credit_wei: int | str,
        worker_node_id: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def release_hold(
        self,
        *,
        hold_id: str,
        reason: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def status(self, *, recent_limit: int = 25) -> dict[str, Any]:
        ...

    def list_holds(self, *, account_id: str = "", request_id: str = "", active_only: bool = False, limit: int = 100) -> list[Any]:
        ...


class WorkerRegistry(Protocol):
    backend: str

    def register_worker(
        self,
        *,
        node_id: str,
        endpoint: str,
        model: str = "",
        models: list[str] | None = None,
        capabilities: dict[str, Any] | None = None,
        credits_per_request: int = 1,
        queue_depth: int = 0,
        active_requests: int = 0,
        max_concurrency: int = 1,
    ) -> Any:
        ...

    def heartbeat_worker(
        self,
        node_id: str,
        *,
        status: str = "available",
        model: str = "",
        models: list[str] | None = None,
        capabilities: dict[str, Any] | None = None,
        queue_depth: int | None = None,
        active_requests: int | None = None,
        max_concurrency: int | None = None,
    ) -> Any:
        ...

    def lease_worker(
        self,
        model: str = "",
        *,
        request_id: str = "",
        preferred_node_id: str = "",
        lease_seconds: float | None = None,
    ) -> Any | None:
        ...

    def release_worker(self, node_id: str, *, request_id: str = "", success: bool = True) -> None:
        ...

    def status(self) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class WorkerNodeSpec:
    node_id: str
    ring: int
    price_credits: int
    task_queue: str
    endpoint: str
    max_concurrency: int = 4

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "ring": self.ring,
            "price_credits": self.price_credits,
            "task_queue": self.task_queue,
            "endpoint": self.endpoint,
            "max_concurrency": self.max_concurrency,
        }


@dataclass(frozen=True)
class RequestSpec:
    request_id: str
    account_id: str
    requested_ring: int
    max_price_credits: int
    token_count: int
    token_interval_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "account_id": self.account_id,
            "requested_ring": self.requested_ring,
            "max_price_credits": self.max_price_credits,
            "token_count": self.token_count,
            "token_interval_seconds": self.token_interval_seconds,
        }


@dataclass(frozen=True)
class WorkerMatch:
    request: RequestSpec
    worker: WorkerNodeSpec
    partition_size: int
    candidate_node_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.as_dict(),
            "worker": self.worker.as_dict(),
            "partition_size": self.partition_size,
            "candidate_node_ids": list(self.candidate_node_ids),
            "filter": {
                "worker_ring_must_be_lte_requested_ring": True,
                "worker_price_must_be_lte_max_price": True,
            },
        }


@dataclass(frozen=True)
class NodeMarketSmokeConfig:
    repo_root: Path
    network: str = DEFAULT_PROTECTED_NETWORK
    deployment_path: Path | None = None
    execution_mode: ExecutionMode = "live-temporal"
    ledger_backend: LedgerBackend = "foundationdb"
    ledger_root: Path | None = DEFAULT_NODE_MARKET_LEDGER_ROOT
    report_path: Path | None = DEFAULT_NODE_MARKET_REPORT_PATH
    event_log_path: Path = DEFAULT_EVENT_LOG_PATH
    reset_json_ledger: bool = True
    temporal_address: str = "localhost:7233"
    namespace: str = DEFAULT_NAMESPACE
    fdb_cluster_file: Path = DEFAULT_FDB_CLUSTER_FILE
    fdb_namespace: str | None = None
    fdb_api_version: int = 740
    bootstrap_fdb: bool = True
    fdb_start_mode: FdbStartMode = "auto"
    bootstrap_fdb_keep_container: bool = True
    storage_operation_timeout_seconds: float = 15.0
    node_count: int = 50
    request_count: int = 20
    requested_ring: int = 2
    max_price_credits: int = 2
    deposit_credits: int = 100
    token_count: int = 5
    token_interval_seconds: float = 0.02
    keepalive_interval_seconds: float = 0.5
    account_id: str = "temporal-fdb-node-market-client"
    task_queue_prefix: str = NODE_MARKET_TASK_QUEUE_PREFIX
    run_id: str | None = None
    emit_progress: bool = False
    progress_interval_seconds: float = 2.0

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

    def resolved_fdb_cluster_file(self) -> Path:
        return self.fdb_cluster_file if self.fdb_cluster_file.is_absolute() else self.repo_root / self.fdb_cluster_file


class InMemoryWorkerRegistry:
    backend = "memory"

    def __init__(self) -> None:
        self._workers: dict[str, dict[str, Any]] = {}

    def register_worker(
        self,
        *,
        node_id: str,
        endpoint: str,
        model: str = "",
        models: list[str] | None = None,
        capabilities: dict[str, Any] | None = None,
        credits_per_request: int = 1,
        queue_depth: int = 0,
        active_requests: int = 0,
        max_concurrency: int = 1,
    ) -> dict[str, Any]:
        now = time.time()
        worker = {
            "node_id": node_id,
            "endpoint": endpoint,
            "model": model,
            "models": list(models or ([model] if model else [])),
            "status": "available" if active_requests < max_concurrency else "busy",
            "credits_per_request": int(credits_per_request),
            "capabilities": dict(capabilities or {}),
            "queue_depth": int(queue_depth),
            "active_requests": int(active_requests),
            "max_concurrency": int(max_concurrency),
            "registered_at": now,
            "last_seen_at": now,
            "stale": False,
        }
        self._workers[node_id] = worker
        return dict(worker)

    def heartbeat_worker(
        self,
        node_id: str,
        *,
        status: str = "available",
        model: str = "",
        models: list[str] | None = None,
        capabilities: dict[str, Any] | None = None,
        queue_depth: int | None = None,
        active_requests: int | None = None,
        max_concurrency: int | None = None,
    ) -> dict[str, Any]:
        worker = self._workers[node_id]
        worker["last_seen_at"] = time.time()
        worker["status"] = status
        if capabilities is not None:
            worker["capabilities"] = dict(capabilities)
        if queue_depth is not None:
            worker["queue_depth"] = int(queue_depth)
        if active_requests is not None:
            worker["active_requests"] = int(active_requests)
        if max_concurrency is not None:
            worker["max_concurrency"] = int(max_concurrency)
        if worker["status"] == "available" and int(worker.get("active_requests", 0)) >= int(worker.get("max_concurrency", 1)):
            worker["status"] = "busy"
        worker["stale"] = False
        return dict(worker)

    def lease_worker(
        self,
        model: str = "",
        *,
        request_id: str = "",
        preferred_node_id: str = "",
        lease_seconds: float | None = None,
    ) -> dict[str, Any] | None:
        if not preferred_node_id or preferred_node_id not in self._workers:
            return None
        worker = self._workers[preferred_node_id]
        if str(worker.get("status")) not in {"available", "configured"}:
            return None
        active = int(worker.get("active_requests", 0)) + 1
        worker["active_requests"] = active
        worker["status"] = "busy" if active >= int(worker.get("max_concurrency", 1)) else "available"
        worker["last_request_id"] = request_id
        worker["last_seen_at"] = time.time()
        return dict(worker)

    def release_worker(self, node_id: str, *, request_id: str = "", success: bool = True) -> None:
        worker = self._workers.get(node_id)
        if not worker:
            return
        worker["active_requests"] = max(0, int(worker.get("active_requests", 0)) - 1)
        worker["status"] = "available" if success else "offline"
        worker["last_request_id"] = request_id
        worker["last_seen_at"] = time.time()

    def status(self) -> dict[str, Any]:
        workers = [dict(item) for item in self._workers.values()]
        return {
            "ok": True,
            "backend": self.backend,
            "worker_count": len(workers),
            "available_worker_count": sum(1 for worker in workers if worker.get("status") == "available"),
            "workers": sorted(workers, key=lambda item: str(item.get("node_id", ""))),
        }


def _positive_int(value: object, *, field_name: str, minimum: int = 1) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise NodeMarketSmokeError(f"{field_name} must be an integer") from exc
    if parsed < minimum:
        raise NodeMarketSmokeError(f"{field_name} must be >= {minimum}")
    return parsed


def _positive_float(value: object, *, field_name: str, minimum: float = 0.0) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise NodeMarketSmokeError(f"{field_name} must be a number") from exc
    if parsed < minimum:
        raise NodeMarketSmokeError(f"{field_name} must be >= {minimum:g}")
    return parsed


def _as_worker_payload(worker: Any) -> dict[str, Any]:
    if hasattr(worker, "as_dict"):
        return dict(worker.as_dict())
    if isinstance(worker, dict):
        return dict(worker)
    return dict(getattr(worker, "__dict__", {}))


def _worker_capabilities(spec: WorkerNodeSpec) -> dict[str, Any]:
    return {
        "protected_node_market_smoke": True,
        "assigned_ring": spec.ring,
        "task_queue": spec.task_queue,
        "pricing": {"credits_per_request": spec.price_credits},
        "keepalive": {"mode": "simulated-open-connection"},
    }


def _ring_price_for_node(index: int) -> tuple[int, int]:
    """Return a deterministic advertised ring/price.

    Lower ring numbers are higher-service workers. Price is advertised by the
    worker; it is not inferred from ring. The defaults intentionally make ring 1
    the golden-path match for a request asking for ring 2 at max price 2:
    ring 0 workers are high-service but too expensive, ring 1 workers match,
    ring 2 workers are too expensive, and ring 3 workers are cheap but not high
    enough service for a ring 2 request.
    """

    bucket = index % 10
    if bucket == 0:
        return 0, 4
    if bucket in {1, 2}:
        return 1, 2
    if bucket in {3, 4, 5}:
        return 2, 3
    return 3, 1


def build_worker_nodes(*, node_count: int, run_id: str, task_queue_prefix: str) -> list[WorkerNodeSpec]:
    count = _positive_int(node_count, field_name="node_count")
    nodes: list[WorkerNodeSpec] = []
    for offset in range(count):
        number = offset + 1
        ring, price = _ring_price_for_node(offset)
        node_id = f"node-{number:03d}"
        nodes.append(
            WorkerNodeSpec(
                node_id=node_id,
                ring=ring,
                price_credits=price,
                task_queue=f"{task_queue_prefix}-{run_id}-{node_id}",
                endpoint=f"http://127.0.0.1:{47000 + (offset % 1000)}/{node_id}",
            )
        )
    return nodes


def _live_available_worker_payloads(registry_status: dict[str, Any]) -> list[dict[str, Any]]:
    workers = registry_status.get("workers", [])
    if not isinstance(workers, list):
        return []
    result: list[dict[str, Any]] = []
    for item in workers:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "available")).lower()
        active = int(item.get("active_requests", 0) or 0)
        max_concurrency = max(1, int(item.get("max_concurrency", 1) or 1))
        stale = bool(item.get("stale", False))
        if status in {"available", "configured"} and not stale and active < max_concurrency:
            result.append(item)
    return result


def _spec_by_id(nodes: list[WorkerNodeSpec]) -> dict[str, WorkerNodeSpec]:
    return {node.node_id: node for node in nodes}


def match_worker_for_request(
    *,
    request: RequestSpec,
    nodes: list[WorkerNodeSpec],
    registry_status: dict[str, Any],
    assignment_counts: dict[str, int],
) -> WorkerMatch:
    specs = _spec_by_id(nodes)
    live_payloads = _live_available_worker_payloads(registry_status)
    candidates: list[WorkerNodeSpec] = []
    for payload in live_payloads:
        node_id = str(payload.get("node_id") or "")
        spec = specs.get(node_id)
        if spec is None:
            continue
        if spec.ring <= request.requested_ring and spec.price_credits <= request.max_price_credits:
            candidates.append(spec)

    if not candidates:
        raise NodeMarketSmokeError(
            "no eligible live worker for request "
            f"{request.request_id}: requested_ring={request.requested_ring}, max_price={request.max_price_credits}"
        )

    candidates.sort(
        key=lambda item: (
            assignment_counts.get(item.node_id, 0),
            item.price_credits,
            item.ring,
            item.node_id,
        )
    )
    selected = candidates[0]
    return WorkerMatch(
        request=request,
        worker=selected,
        partition_size=len(candidates),
        candidate_node_ids=tuple(item.node_id for item in candidates),
    )


def _make_request_payload(match: WorkerMatch) -> FakeTokenRequest:
    payload = {
        "source": "temporal_fdb_node_market_smoke",
        "protected_mode": True,
        "requested_ring": match.request.requested_ring,
        "selected_worker_node_id": match.worker.node_id,
        "worker_price_credits": match.worker.price_credits,
        "worker_task_queue": match.worker.task_queue,
    }
    return FakeTokenRequest(
        request_id=match.request.request_id,
        account_id=match.request.account_id,
        credits_offered=match.request.max_price_credits,
        token_count=match.request.token_count,
        token_interval_seconds=match.request.token_interval_seconds,
        payload=payload,
        ring=match.worker.ring,
        idempotency_key=f"idem-{match.request.request_id}",
    )


async def _run_keepalive_loop(
    *,
    registry: WorkerRegistry,
    spec: WorkerNodeSpec,
    interval_seconds: float,
    stop_event: asyncio.Event,
) -> None:
    capabilities = _worker_capabilities(spec)
    while not stop_event.is_set():
        try:
            registry.heartbeat_worker(
                spec.node_id,
                status="available",
                capabilities=capabilities,
                max_concurrency=spec.max_concurrency,
            )
        except Exception:
            # A failed keepalive should be visible through the final registry
            # and workflow result. Do not crash the whole smoke from a cleanup race.
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(0.05, interval_seconds))
        except asyncio.TimeoutError:
            continue


async def _execute_direct_activity(
    *,
    request: FakeTokenRequest,
    event_log_path: Path,
    worker_id: str,
) -> dict[str, Any]:
    return await FakeTokenActivities(event_log_path=event_log_path, worker_id=worker_id).emit_fake_tokens(request.to_dict())


async def _execute_live_temporal_requests(
    *,
    config: NodeMarketSmokeConfig,
    nodes: list[WorkerNodeSpec],
    requests: list[tuple[WorkerMatch, FakeTokenRequest]],
    event_log_path: Path,
    progress: _ProgressReporter,
) -> list[dict[str, Any]]:
    try:
        from temporalio.client import Client
        from temporalio.worker import Worker
    except ImportError as exc:
        raise NodeMarketSmokeError(
            "temporalio is required for the live 50-node Temporal smoke. "
            "Install with: python -m pip install -r tools/temporal_lab/requirements-temporal.txt"
        ) from exc

    from tools.temporal_lab.workflows import FakeTokenWorkflow

    _ensure_temporal_listening(config, progress=progress)
    progress.emit("temporal_connect_start", address=config.temporal_address, namespace=config.namespace)
    try:
        client = await Client.connect(config.temporal_address, namespace=config.namespace)
    except Exception as exc:
        raise NodeMarketSmokeError(
            _temporal_lab_startup_help(config.temporal_address, config.namespace)
            + f"\nOriginal Temporal client error: {exc}"
        ) from exc
    progress.emit("temporal_connect_ok", address=config.temporal_address, namespace=config.namespace)

    async with AsyncExitStack() as stack:
        for index, spec in enumerate(nodes, start=1):
            activities = FakeTokenActivities(event_log_path=event_log_path, worker_id=spec.node_id)
            worker = Worker(
                client,
                task_queue=spec.task_queue,
                workflows=[FakeTokenWorkflow],
                activities=[activities.emit_fake_tokens],
            )
            await stack.enter_async_context(worker)
            if index == 1 or index == len(nodes) or index % 10 == 0:
                progress.emit(
                    "temporal_worker_started",
                    worker_index=index,
                    workers_total=len(nodes),
                    node_id=spec.node_id,
                    task_queue=spec.task_queue,
                )

        progress.emit("temporal_workers_ready", workers_total=len(nodes), task_queues=len({node.task_queue for node in nodes}))

        async def _one(match: WorkerMatch, request: FakeTokenRequest) -> dict[str, Any]:
            progress.emit(
                "workflow_submit",
                request_id=request.request_id,
                node_id=match.worker.node_id,
                ring=match.worker.ring,
                price_credits=match.worker.price_credits,
                task_queue=match.worker.task_queue,
            )
            started = time.perf_counter()
            result = await client.execute_workflow(
                FakeTokenWorkflow.run,
                request.to_dict(),
                id=request.idempotency_key or request.request_id,
                task_queue=match.worker.task_queue,
            )
            return {
                "request_id": request.request_id,
                "worker_node_id": match.worker.node_id,
                "task_queue": match.worker.task_queue,
                "workflow_result": result,
                "latency_seconds": round(time.perf_counter() - started, 6),
            }

        tasks = [asyncio.create_task(_one(match, request)) for match, request in requests]
        pending: set[asyncio.Task[dict[str, Any]]] = set(tasks)
        results: list[dict[str, Any]] = []
        while pending:
            done, pending = await asyncio.wait(
                pending,
                timeout=progress.interval_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                progress.emit("workflow_waiting", completed=len(results), pending=len(pending), total=len(tasks))
                continue
            for task in done:
                result = task.result()
                results.append(result)
                progress.emit(
                    "workflow_completed",
                    completed=len(results),
                    total=len(tasks),
                    request_id=result["request_id"],
                    node_id=result["worker_node_id"],
                    latency_seconds=result["latency_seconds"],
                )

        return results


async def _execute_requests(
    *,
    config: NodeMarketSmokeConfig,
    nodes: list[WorkerNodeSpec],
    requests: list[tuple[WorkerMatch, FakeTokenRequest]],
    event_log_path: Path,
    progress: _ProgressReporter,
) -> list[dict[str, Any]]:
    if config.execution_mode == "direct-activity":
        results: list[dict[str, Any]] = []
        progress.emit("direct_activity_start", requests=len(requests))
        for index, (match, request) in enumerate(requests, start=1):
            progress.emit(
                "direct_activity_request_start",
                request_index=index,
                requests_total=len(requests),
                request_id=request.request_id,
                node_id=match.worker.node_id,
            )
            started = time.perf_counter()
            result = await _execute_direct_activity(
                request=request,
                event_log_path=event_log_path,
                worker_id=match.worker.node_id,
            )
            result_item = {
                "request_id": request.request_id,
                "worker_node_id": match.worker.node_id,
                "task_queue": match.worker.task_queue,
                "workflow_result": result,
                "latency_seconds": round(time.perf_counter() - started, 6),
            }
            results.append(result_item)
            progress.emit(
                "direct_activity_request_done",
                completed=len(results),
                total=len(requests),
                request_id=request.request_id,
                latency_seconds=result_item["latency_seconds"],
            )
        return results
    if config.execution_mode == "live-temporal":
        return await _execute_live_temporal_requests(
            config=config,
            nodes=nodes,
            requests=requests,
            event_log_path=event_log_path,
            progress=progress,
        )
    raise NodeMarketSmokeError(f"unknown execution mode: {config.execution_mode}")


def _effective_fdb_start_mode(config: NodeMarketSmokeConfig) -> FdbStartMode:
    if not config.bootstrap_fdb:
        return "never"
    mode = str(config.fdb_start_mode or "auto").lower()
    if mode not in {"auto", "never", "always"}:
        raise NodeMarketSmokeError(f"fdb_start_mode must be one of auto, never, always; got {config.fdb_start_mode!r}")
    return mode  # type: ignore[return-value]


def _run_fdb_bootstrap_helper(
    config: NodeMarketSmokeConfig,
    *,
    progress: _ProgressReporter,
    reason: str,
) -> None:
    cluster_file = config.resolved_fdb_cluster_file()
    mode = _effective_fdb_start_mode(config)
    if mode == "never":
        raise NodeMarketSmokeError(
            f"FoundationDB is not ready ({reason}) and FDB startup is disabled. "
            "Run `python scripts/smoke_foundationdb_credit_ledger_primitives.py --keep-container` first, "
            "or rerun this smoke with `--fdb-start-mode auto`."
        )

    helper = config.repo_root / "scripts" / "smoke_foundationdb_credit_ledger_primitives.py"
    if not helper.exists():
        raise NodeMarketSmokeError(f"FDB bootstrap helper is missing: {helper}")

    cmd = [
        sys.executable,
        str(helper),
        "--cluster-file",
        str(cluster_file),
        "--concurrent-holds",
        "11",
        "--workers",
        "2",
        "--namespace",
        f"node-market-fdb-bootstrap-{uuid.uuid4().hex[:10]}",
    ]
    if config.bootstrap_fdb_keep_container:
        cmd.append("--keep-container")

    cluster_file.parent.mkdir(parents=True, exist_ok=True)
    progress.emit(
        "fdb_bootstrap_start",
        reason=reason,
        helper=helper,
        cluster_file=cluster_file,
        keep_container=config.bootstrap_fdb_keep_container,
    )
    process = subprocess.Popen(
        cmd,
        cwd=config.repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    output_lines: list[str] = []
    output_queue: queue.Queue[str] = queue.Queue()

    def _reader() -> None:
        assert process.stdout is not None
        try:
            for raw_line in process.stdout:
                output_queue.put(raw_line.rstrip())
        finally:
            try:
                process.stdout.close()
            except Exception:
                pass

    reader = threading.Thread(target=_reader, name="node-market-fdb-bootstrap-output", daemon=True)
    reader.start()
    started = time.perf_counter()
    while process.poll() is None or not output_queue.empty():
        try:
            line = output_queue.get(timeout=0.25)
        except queue.Empty:
            progress.heartbeat(
                "fdb_bootstrap",
                "fdb_bootstrap_waiting",
                waited_seconds=round(time.perf_counter() - started, 1),
                cluster_file=cluster_file,
            )
            continue
        if not line:
            continue
        output_lines.append(line)
        progress.emit("fdb_bootstrap_output", line=line[:300])

    reader.join(timeout=1.0)
    returncode = process.returncode
    if returncode != 0:
        detail = "\n".join(output_lines[-80:]).strip()
        raise NodeMarketSmokeError(f"FDB bootstrap helper failed with code {returncode}:\n{detail}")
    progress.emit("fdb_bootstrap_ok", cluster_file=cluster_file, reason=reason)
    if not cluster_file.exists():
        raise NodeMarketSmokeError(f"FDB bootstrap completed but cluster file was not created: {cluster_file}")


def _prepare_fdb_dependency_before_probe(config: NodeMarketSmokeConfig, *, progress: _ProgressReporter) -> None:
    cluster_file = config.resolved_fdb_cluster_file()
    mode = _effective_fdb_start_mode(config)
    if mode == "always":
        _run_fdb_bootstrap_helper(config, progress=progress, reason="fdb_start_mode_always")
        return
    if cluster_file.exists():
        progress.emit("fdb_cluster_file_found", cluster_file=cluster_file, start_mode=mode)
        return
    progress.emit("fdb_cluster_file_missing", cluster_file=cluster_file, start_mode=mode)
    _run_fdb_bootstrap_helper(config, progress=progress, reason="cluster_file_missing")


def _probe_fdb_storage(
    *,
    ledger: CreditLedger,
    registry: WorkerRegistry,
    namespace: str,
    config: NodeMarketSmokeConfig,
    progress: _ProgressReporter,
) -> None:
    progress.emit(
        "fdb_storage_probe_start",
        timeout_seconds=config.storage_operation_timeout_seconds,
        namespace=namespace,
    )
    _run_storage_step(
        label="fdb_ledger_status_probe",
        timeout_seconds=config.storage_operation_timeout_seconds,
        progress=progress,
        operation=lambda: ledger.status(recent_limit=1),
    )
    _run_storage_step(
        label="fdb_registry_status_probe",
        timeout_seconds=config.storage_operation_timeout_seconds,
        progress=progress,
        operation=registry.status,
    )
    progress.emit("fdb_storage_probe_ok", namespace=namespace)


def _build_fdb_ledger_and_registry(
    config: NodeMarketSmokeConfig,
    *,
    namespace: str,
) -> tuple[CreditLedger, WorkerRegistry]:
    from main_computer.exp_fdb_credit_ledger import (
        ExperimentalFoundationDbConfig,
        ExperimentalFoundationDbCreditLedger,
    )
    from main_computer.exp_fdb_hub_state import (
        ExperimentalFoundationDbHubState,
        ExperimentalFoundationDbRegistry,
    )

    fdb_config = ExperimentalFoundationDbConfig(
        cluster_file=config.resolved_fdb_cluster_file(),
        namespace=namespace,
        api_version=config.fdb_api_version,
        repo_root=config.repo_root,
    )
    ledger = ExperimentalFoundationDbCreditLedger(fdb_config)
    state = ExperimentalFoundationDbHubState(fdb_config)
    registry = ExperimentalFoundationDbRegistry(
        state,
        root=config.repo_root / "runtime" / "temporal_lab" / "node_market_fdb_registry",
        allow_insecure_dev_network=True,
    )
    return ledger, registry



def _prepare_ledger_and_registry(
    config: NodeMarketSmokeConfig,
    *,
    run_id: str,
    progress: _ProgressReporter,
) -> tuple[CreditLedger, WorkerRegistry, tempfile.TemporaryDirectory[str] | None]:
    if config.ledger_backend == "foundationdb":
        progress.emit("ledger_prepare_start", backend="foundationdb")
        start_mode = _effective_fdb_start_mode(config)
        _prepare_fdb_dependency_before_probe(config, progress=progress)

        namespace = config.fdb_namespace or f"main-computer-node-market-{run_id}"
        ledger, registry = _build_fdb_ledger_and_registry(config, namespace=namespace)
        try:
            _probe_fdb_storage(
                ledger=ledger,
                registry=registry,
                namespace=namespace,
                config=config,
                progress=progress,
            )
        except NodeMarketSmokeError as exc:
            if start_mode != "auto":
                raise
            progress.emit(
                "fdb_storage_probe_failed",
                start_mode=start_mode,
                reason=str(exc)[:300],
            )
            _run_fdb_bootstrap_helper(
                config,
                progress=progress,
                reason="storage_probe_failed",
            )
            ledger, registry = _build_fdb_ledger_and_registry(config, namespace=namespace)
            progress.emit("fdb_storage_reprobe_start", namespace=namespace)
            _probe_fdb_storage(
                ledger=ledger,
                registry=registry,
                namespace=namespace,
                config=config,
                progress=progress,
            )
        progress.emit(
            "ledger_prepare_ok",
            backend="foundationdb",
            namespace=namespace,
            registry_backend=registry.backend,
            fdb_start_mode=start_mode,
        )
        return ledger, registry, None

    if config.ledger_backend == "json":
        progress.emit("ledger_prepare_start", backend="json")
        ledger_root = config.resolved_ledger_root()
        tempdir: tempfile.TemporaryDirectory[str] | None = None
        if ledger_root is None:
            tempdir = tempfile.TemporaryDirectory(prefix="temporal-fdb-node-market-json-ledger-")
            ledger_root = Path(tempdir.name)
        if config.reset_json_ledger and ledger_root.exists():
            shutil.rmtree(ledger_root)
        ledger_root.mkdir(parents=True, exist_ok=True)
        progress.emit("ledger_prepare_ok", backend="json", ledger_root=ledger_root, registry_backend="memory")
        return HubCreditLedger(ledger_root), InMemoryWorkerRegistry(), tempdir

    raise NodeMarketSmokeError(f"unknown ledger backend: {config.ledger_backend}")



def _register_workers(registry: WorkerRegistry, nodes: list[WorkerNodeSpec], *, config: NodeMarketSmokeConfig, progress: _ProgressReporter) -> list[dict[str, Any]]:
    registered: list[dict[str, Any]] = []
    for index, spec in enumerate(nodes, start=1):
        progress.emit(
            "worker_register_start",
            worker_index=index,
            workers_total=len(nodes),
            node_id=spec.node_id,
            ring=spec.ring,
            price_credits=spec.price_credits,
            task_queue=spec.task_queue,
        )
        worker = _run_storage_step(
            label=f"register_worker:{spec.node_id}",
            timeout_seconds=config.storage_operation_timeout_seconds,
            progress=progress,
            operation=lambda spec=spec: registry.register_worker(
                node_id=spec.node_id,
                endpoint=spec.endpoint,
                model="fake-char-stream",
                models=["fake-char-stream"],
                capabilities=_worker_capabilities(spec),
                credits_per_request=spec.price_credits,
                max_concurrency=spec.max_concurrency,
            ),
        )
        registered.append(_as_worker_payload(worker))
        if index == 1 or index == len(nodes) or index % 10 == 0:
            progress.emit(
                "worker_registered",
                worker_index=index,
                workers_total=len(nodes),
                node_id=spec.node_id,
                ring=spec.ring,
                price_credits=spec.price_credits,
                task_queue=spec.task_queue,
            )
    return registered


def _make_request_specs(config: NodeMarketSmokeConfig, *, run_id: str) -> list[RequestSpec]:
    requested_ring = normalize_ring(config.requested_ring)
    max_price = _positive_int(config.max_price_credits, field_name="max_price_credits")
    token_count = _positive_int(config.token_count, field_name="token_count")
    token_interval = _positive_float(config.token_interval_seconds, field_name="token_interval_seconds")
    request_count = _positive_int(config.request_count, field_name="request_count")
    return [
        RequestSpec(
            request_id=f"node-market-{run_id}-{index + 1:04d}",
            account_id=config.account_id,
            requested_ring=requested_ring,
            max_price_credits=max_price,
            token_count=token_count,
            token_interval_seconds=token_interval,
        )
        for index in range(request_count)
    ]


def _stream_summary(event_log_path: Path, request_ids: set[str]) -> dict[str, Any]:
    events = [event for event in read_jsonl_events(event_log_path) if str(event.get("request_id")) in request_ids]
    token_events = [event for event in events if event.get("event") == "token"]
    by_request: dict[str, list[str]] = {}
    for event in token_events:
        by_request.setdefault(str(event["request_id"]), []).append(str(event.get("text", "")))
    streams = {
        request_id: {
            "token_count": len(parts),
            "text": "".join(parts),
            "tokens": parts,
        }
        for request_id, parts in sorted(by_request.items())
    }
    return {
        "event_count": len(events),
        "token_event_count": len(token_events),
        "completed_request_count": sum(1 for event in events if event.get("event") == "done"),
        "streams": streams,
    }


async def run_temporal_fdb_node_market_smoke(config: NodeMarketSmokeConfig) -> dict[str, Any]:
    progress = _ProgressReporter(
        enabled=config.emit_progress,
        interval_seconds=_positive_float(config.progress_interval_seconds, field_name="progress_interval_seconds", minimum=0.25),
    )
    network = normalize_network_name(config.network)
    progress.emit(
        "start",
        execution_mode=config.execution_mode,
        ledger_backend=config.ledger_backend,
        nodes=config.node_count,
        requests=config.request_count,
        requested_ring=config.requested_ring,
        max_price_credits=config.max_price_credits,
    )
    progress.emit("network_profile_load_start", network=network)
    profile = load_protected_network_profile(
        repo_root=config.repo_root,
        network=network,
        deployment_path=config.resolved_deployment_path(),
        live_chain=False,
    )
    progress.emit("network_profile_load_ok", network=network, chain_id=profile.chain_id)

    run_id = config.run_id or uuid.uuid4().hex[:10]
    progress.emit("run_id_selected", run_id=run_id)
    event_log_path = config.resolved_event_log_path()
    event_log_path.parent.mkdir(parents=True, exist_ok=True)
    event_log_path.unlink(missing_ok=True)
    progress.emit("event_log_ready", event_log_path=event_log_path)

    ledger, registry, tempdir = _prepare_ledger_and_registry(config, run_id=run_id, progress=progress)
    stop_keepalives = asyncio.Event()
    keepalive_tasks: list[asyncio.Task[None]] = []
    started_at = time.perf_counter()

    try:
        node_count = _positive_int(config.node_count, field_name="node_count")
        request_count = _positive_int(config.request_count, field_name="request_count")
        deposit_credits = _positive_int(config.deposit_credits, field_name="deposit_credits")
        if deposit_credits < request_count * _positive_int(config.max_price_credits, field_name="max_price_credits"):
            raise NodeMarketSmokeError("deposit_credits must cover request_count * max_price_credits for the golden path")

        progress.emit("scenario_validated", node_count=node_count, request_count=request_count, deposit_credits=deposit_credits)
        nodes = build_worker_nodes(node_count=node_count, run_id=run_id, task_queue_prefix=config.task_queue_prefix)
        progress.emit("workers_built", workers_total=len(nodes), worker_specific_task_queues=len({node.task_queue for node in nodes}))
        registered_workers = _register_workers(registry, nodes, config=config, progress=progress)
        progress.emit("workers_registered", workers_total=len(registered_workers))
        for spec in nodes:
            keepalive_tasks.append(
                asyncio.create_task(
                    _run_keepalive_loop(
                        registry=registry,
                        spec=spec,
                        interval_seconds=config.keepalive_interval_seconds,
                        stop_event=stop_keepalives,
                    )
                )
            )

        progress.emit("keepalives_started", workers_total=len(keepalive_tasks), interval_seconds=config.keepalive_interval_seconds)
        progress.emit("credit_issue_start", account_id=config.account_id, credits=deposit_credits)
        _run_storage_step(
            label="credit_issue",
            timeout_seconds=config.storage_operation_timeout_seconds,
            progress=progress,
            operation=lambda: ledger.issue(
                account_id=config.account_id,
                credits=deposit_credits,
                owner_address=profile.smoke_client_address,
                memo="fund requester for Temporal FDB node-market smoke",
                metadata={"temporal_fdb_node_market_smoke": True, "run_id": run_id, "network": network},
            ),
        )
        progress.emit("credit_issue_ok", account_id=config.account_id, credits=deposit_credits)

        request_specs = _make_request_specs(config, run_id=run_id)
        progress.emit("request_specs_built", requests_total=len(request_specs))
        assignment_counts: dict[str, int] = {}
        planned: list[tuple[WorkerMatch, FakeTokenRequest, dict[str, Any]]] = []
        for request in request_specs:
            registry_status_for_match = _run_storage_step(
                label=f"registry_status_for_match:{request.request_id}",
                timeout_seconds=config.storage_operation_timeout_seconds,
                progress=progress,
                operation=registry.status,
            )
            match = match_worker_for_request(
                request=request,
                nodes=nodes,
                registry_status=registry_status_for_match,
                assignment_counts=assignment_counts,
            )
            leased = _run_storage_step(
                label=f"lease_worker:{match.worker.node_id}:{request.request_id}",
                timeout_seconds=config.storage_operation_timeout_seconds,
                progress=progress,
                operation=lambda match=match, request=request: registry.lease_worker(
                    "fake-char-stream",
                    request_id=request.request_id,
                    preferred_node_id=match.worker.node_id,
                ),
            )
            if leased is None:
                raise NodeMarketSmokeError(f"selected worker could not be leased: {match.worker.node_id}")
            assignment_counts[match.worker.node_id] = assignment_counts.get(match.worker.node_id, 0) + 1

            hold_wei = credit_count_to_wei(match.worker.price_credits)
            hold = _run_storage_step(
                label=f"create_hold:{request.request_id}",
                timeout_seconds=config.storage_operation_timeout_seconds,
                progress=progress,
                operation=lambda match=match, request=request, hold_wei=hold_wei: ledger.create_hold_credit_wei(
                    account_id=request.account_id,
                    request_id=request.request_id,
                    credit_wei=str(hold_wei),
                    memo=f"node-market protected hold for {request.request_id}",
                    metadata={
                        "temporal_fdb_node_market_smoke": True,
                        "run_id": run_id,
                        "requested_ring": request.requested_ring,
                        "max_price_credits": request.max_price_credits,
                        "selected_worker_ring": match.worker.ring,
                        "selected_worker_price_credits": match.worker.price_credits,
                        "selected_worker_node_id": match.worker.node_id,
                        "selected_worker_task_queue": match.worker.task_queue,
                    },
                ),
            )
            planned.append((match, _make_request_payload(match), hold))
            if len(planned) == 1 or len(planned) == len(request_specs) or len(planned) % 5 == 0:
                progress.emit(
                    "request_planned",
                    planned=len(planned),
                    total=len(request_specs),
                    request_id=request.request_id,
                    partition_size=match.partition_size,
                    selected_node=match.worker.node_id,
                    selected_ring=match.worker.ring,
                    selected_price_credits=match.worker.price_credits,
                )

        progress.emit("request_planning_ok", planned=len(planned), requests_total=len(request_specs))
        progress.emit("execution_start", execution_mode=config.execution_mode, requests_total=len(planned))
        workflow_results = await _execute_requests(
            config=config,
            nodes=nodes,
            requests=[(match, request) for match, request, _hold in planned],
            event_log_path=event_log_path,
            progress=progress,
        )
        progress.emit("execution_ok", results=len(workflow_results))
        result_by_request = {str(item["request_id"]): item for item in workflow_results}

        settlements: list[dict[str, Any]] = []
        releases: list[dict[str, Any]] = []
        request_reports: list[dict[str, Any]] = []
        progress.emit("settlement_start", requests_total=len(planned))
        for index, (match, request, hold) in enumerate(planned, start=1):
            result = result_by_request[request.request_id]
            outcome = result.get("workflow_result", {}).get("result", {}) if isinstance(result.get("workflow_result"), dict) else {}
            ok = isinstance(outcome, dict) and outcome.get("ok") is True
            hold_id = hold["hold"]["hold_id"]
            if ok:
                settlement = _run_storage_step(
                    label=f"charge_hold:{hold_id}",
                    timeout_seconds=config.storage_operation_timeout_seconds,
                    progress=progress,
                    operation=lambda hold_id=hold_id, match=match, request=request: ledger.charge_hold_credit_wei(
                        hold_id=hold_id,
                        charged_credit_wei=str(credit_count_to_wei(match.worker.price_credits)),
                        worker_node_id=match.worker.node_id,
                        memo=f"node-market settlement for {request.request_id}",
                        metadata={
                            "temporal_fdb_node_market_smoke": True,
                            "run_id": run_id,
                            "worker_task_queue": match.worker.task_queue,
                        },
                    ),
                )
                settlements.append(settlement)
                _run_storage_step(
                    label=f"release_worker_success:{match.worker.node_id}:{request.request_id}",
                    timeout_seconds=config.storage_operation_timeout_seconds,
                    progress=progress,
                    operation=lambda match=match, request=request: registry.release_worker(match.worker.node_id, request_id=request.request_id, success=True),
                )
            else:
                release = _run_storage_step(
                    label=f"release_hold:{hold_id}",
                    timeout_seconds=config.storage_operation_timeout_seconds,
                    progress=progress,
                    operation=lambda hold_id=hold_id: ledger.release_hold(
                        hold_id=hold_id,
                        reason="node-market workflow did not complete successfully",
                        metadata={"temporal_fdb_node_market_smoke": True, "run_id": run_id},
                    ),
                )
                releases.append(release)
                _run_storage_step(
                    label=f"release_worker_failure:{match.worker.node_id}:{request.request_id}",
                    timeout_seconds=config.storage_operation_timeout_seconds,
                    progress=progress,
                    operation=lambda match=match, request=request: registry.release_worker(match.worker.node_id, request_id=request.request_id, success=False),
                )

            request_reports.append(
                {
                    "request": match.request.as_dict(),
                    "match": match.as_dict(),
                    "hold": hold,
                    "workflow": result,
                    "settled": bool(ok),
                }
            )
            if index == 1 or index == len(planned) or index % 5 == 0:
                progress.emit(
                    "settlement_progress",
                    settled=len(settlements),
                    released=len(releases),
                    processed=index,
                    total=len(planned),
                    request_id=request.request_id,
                    worker_node_id=match.worker.node_id,
                )

        progress.emit("settlement_ok", settled=len(settlements), released=len(releases))
        progress.emit("final_status_read_start")
        final_status = _run_storage_step(
            label="final_ledger_status",
            timeout_seconds=config.storage_operation_timeout_seconds,
            progress=progress,
            operation=lambda: ledger.status(recent_limit=25),
        )
        registry_status = _run_storage_step(
            label="final_registry_status",
            timeout_seconds=config.storage_operation_timeout_seconds,
            progress=progress,
            operation=registry.status,
        )
        progress.emit("final_registry_status_read_ok", workers=len(registry_status.get("workers", [])))
        stream_summary = _stream_summary(event_log_path, {request.request_id for request in request_specs})
        progress.emit(
            "stream_summary_ok",
            completed_request_count=stream_summary["completed_request_count"],
            token_event_count=stream_summary["token_event_count"],
        )
        spent_wei = int(final_status["totals"]["spent_credit_wei"])
        held_wei = int(final_status["totals"]["held_credit_wei"])
        available_wei = int(final_status["totals"]["available_credit_wei"])
        expected_spent_credits = sum(match.worker.price_credits for match, _request, _hold in planned)
        expected_spent_wei = credit_count_to_wei(expected_spent_credits)
        deposit_wei = credit_count_to_wei(deposit_credits)
        selected_worker_rings = [item["match"]["worker"]["ring"] for item in request_reports]
        selected_worker_prices = [item["match"]["worker"]["price_credits"] for item in request_reports]

        invariants = {
            "workers_registered": len(registered_workers) == node_count,
            "workers_live_after_registration": len(_live_available_worker_payloads(registry_status)) == node_count,
            "requests_planned": len(planned) == request_count,
            "all_selected_workers_meet_ring_threshold": all(ring <= config.requested_ring for ring in selected_worker_rings),
            "all_selected_workers_meet_price_offer": all(price <= config.max_price_credits for price in selected_worker_prices),
            "all_workflows_completed": all(item["settled"] for item in request_reports),
            "all_holds_charged": len(settlements) == request_count and len(releases) == 0,
            "all_streams_completed": stream_summary["completed_request_count"] == request_count,
            "all_token_streams_observed": stream_summary["token_event_count"] == request_count * config.token_count,
            "final_held_zero": held_wei == 0,
            "final_spent_matches_worker_prices": spent_wei == expected_spent_wei,
            "ledger_conservation": available_wei + spent_wei + held_wei == deposit_wei,
            "worker_specific_task_queues_used": len({item["match"]["worker"]["task_queue"] for item in request_reports}) > 1,
        }
        ok = all(invariants.values())

        report = {
            "ok": ok,
            "mode": "temporal-fdb-node-market-smoke-v1",
            "run_id": run_id,
            "execution_mode": config.execution_mode,
            "ledger_backend": config.ledger_backend,
            "registry_backend": registry.backend,
            "network_profile": profile.as_dict(),
            "account_id": config.account_id,
            "event_log_path": str(event_log_path),
            "fdb": {
                "cluster_file": str(config.resolved_fdb_cluster_file()) if config.ledger_backend == "foundationdb" else "",
                "namespace": config.fdb_namespace or f"main-computer-node-market-{run_id}",
                "bootstrap_enabled": config.bootstrap_fdb,
                "start_mode": config.fdb_start_mode,
            },
            "scenario": {
                "node_count": node_count,
                "request_count": request_count,
                "requested_ring": config.requested_ring,
                "max_price_credits": config.max_price_credits,
                "deposit_credits": deposit_credits,
                "token_count": config.token_count,
                "ring_rule": "worker.ring <= requester.requested_ring",
                "price_rule": "worker.price_credits <= requester.max_price_credits",
                "selection_rule": "least_assigned_then_lowest_price_then_lowest_ring_then_node_id",
                "storage_operation_timeout_seconds": config.storage_operation_timeout_seconds,
            },
            "workers": [node.as_dict() for node in nodes],
            "registered_workers": registered_workers,
            "request_results": request_reports,
            "settlements": settlements,
            "releases": releases,
            "stream_summary": stream_summary,
            "ledger_final_status": final_status,
            "registry_final_status": registry_status,
            "metrics": {
                "duration_seconds": round(time.perf_counter() - started_at, 6),
                "selected_worker_count": len(set(item["match"]["worker"]["node_id"] for item in request_reports)),
                "selected_worker_rings": sorted(set(selected_worker_rings)),
                "selected_worker_prices": sorted(set(selected_worker_prices)),
                "expected_spent_credits": expected_spent_credits,
                "expected_spent_wei": str(expected_spent_wei),
                "available_credits_display": credit_wei_to_decimal_text(available_wei),
                "spent_credits_display": credit_wei_to_decimal_text(spent_wei),
            },
            "invariants": invariants,
        }

        if not ok:
            failed = [name for name, value in invariants.items() if not value]
            raise NodeMarketSmokeError(f"Temporal FDB node-market smoke invariants failed: {failed}")

        report_path = config.resolved_report_path()
        if report_path is not None:
            progress.emit("report_write_start", report_path=report_path)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            report["report_path"] = str(report_path)
            progress.emit("report_write_ok", report_path=report_path)
        progress.emit("done", ok=True, duration_seconds=report["metrics"]["duration_seconds"])
        return report
    finally:
        stop_keepalives.set()
        if keepalive_tasks:
            progress.emit("keepalives_stopping", workers_total=len(keepalive_tasks))
            await asyncio.gather(*keepalive_tasks, return_exceptions=True)
            progress.emit("keepalives_stopped", workers_total=len(keepalive_tasks))
        if tempdir is not None:
            tempdir.cleanup()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Golden-path local market smoke: worker nodes register/keepalive, "
            "the hub filters by ring+price, Temporal routes to worker-specific queues, "
            "and FDB-backed credits are held and settled."
        )
    )
    parser.add_argument("--network", default=DEFAULT_PROTECTED_NETWORK, choices=("dev", "test", "testnet", "mainnet"))
    parser.add_argument("--deployment", type=Path, default=None)
    parser.add_argument("--execution-mode", choices=("live-temporal", "direct-activity"), default="live-temporal")
    parser.add_argument("--ledger-backend", choices=("foundationdb", "json"), default="foundationdb")
    parser.add_argument("--ledger-root", type=Path, default=DEFAULT_NODE_MARKET_LEDGER_ROOT)
    parser.add_argument("--keep-json-ledger", action="store_true")
    parser.add_argument("--report", type=Path, default=DEFAULT_NODE_MARKET_REPORT_PATH)
    parser.add_argument("--event-log", type=Path, default=DEFAULT_EVENT_LOG_PATH)
    parser.add_argument("--address", default="localhost:7233")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--fdb-cluster-file", type=Path, default=DEFAULT_FDB_CLUSTER_FILE)
    parser.add_argument("--fdb-namespace", default=None)
    parser.add_argument("--fdb-api-version", type=int, default=740)
    parser.add_argument(
        "--fdb-start-mode",
        choices=("auto", "never", "always"),
        default="auto",
        help=(
            "How to prepare dev FoundationDB before the market smoke. "
            "auto probes existing FDB and starts the existing FDB primitive smoke if unhealthy; "
            "always starts it first; never only probes and fails if unavailable."
        ),
    )
    parser.add_argument(
        "--no-bootstrap-fdb",
        action="store_true",
        help="legacy alias for --fdb-start-mode never",
    )
    parser.add_argument("--stop-fdb-container-after-bootstrap", action="store_true")
    parser.add_argument(
        "--storage-operation-timeout-seconds",
        type=float,
        default=15.0,
        help=(
            "Maximum seconds to wait for each FDB/registry/ledger operation before failing "
            "with a diagnostic instead of appearing to hang. Use 0 to disable."
        ),
    )
    parser.add_argument("--nodes", type=int, default=50)
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--requested-ring", type=int, default=2)
    parser.add_argument("--max-price-credits", type=int, default=2)
    parser.add_argument("--deposit-credits", type=int, default=100)
    parser.add_argument("--token-count", type=int, default=5)
    parser.add_argument("--token-interval-seconds", type=float, default=0.02)
    parser.add_argument("--keepalive-interval-seconds", type=float, default=0.5)
    parser.add_argument("--account-id", default="temporal-fdb-node-market-client")
    parser.add_argument("--task-queue-prefix", default=NODE_MARKET_TASK_QUEUE_PREFIX)
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress progress lines and only print the final PASS/FAIL summary",
    )
    parser.add_argument(
        "--progress-interval-seconds",
        type=float,
        default=2.0,
        help="seconds between heartbeat progress lines while a long phase is still running",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = find_repo_root(Path.cwd())

    try:
        report = asyncio.run(
            run_temporal_fdb_node_market_smoke(
                NodeMarketSmokeConfig(
                    repo_root=repo_root,
                    network=args.network,
                    deployment_path=args.deployment,
                    execution_mode=args.execution_mode,
                    ledger_backend=args.ledger_backend,
                    ledger_root=args.ledger_root,
                    report_path=args.report,
                    event_log_path=args.event_log,
                    reset_json_ledger=not args.keep_json_ledger,
                    temporal_address=args.address,
                    namespace=args.namespace,
                    fdb_cluster_file=args.fdb_cluster_file,
                    fdb_namespace=args.fdb_namespace,
                    fdb_api_version=args.fdb_api_version,
                    bootstrap_fdb=not args.no_bootstrap_fdb,
                    fdb_start_mode="never" if args.no_bootstrap_fdb else args.fdb_start_mode,
                    bootstrap_fdb_keep_container=not args.stop_fdb_container_after_bootstrap,
                    storage_operation_timeout_seconds=args.storage_operation_timeout_seconds,
                    node_count=args.nodes,
                    request_count=args.requests,
                    requested_ring=args.requested_ring,
                    max_price_credits=args.max_price_credits,
                    deposit_credits=args.deposit_credits,
                    token_count=args.token_count,
                    token_interval_seconds=args.token_interval_seconds,
                    keepalive_interval_seconds=args.keepalive_interval_seconds,
                    account_id=args.account_id,
                    task_queue_prefix=args.task_queue_prefix,
                    run_id=args.run_id,
                    emit_progress=not args.quiet,
                    progress_interval_seconds=args.progress_interval_seconds,
                )
            )
        )
    except NodeMarketSmokeError as exc:
        print(f"FAIL: {exc}")
        return 2
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1

    metrics = report["metrics"]
    print("PASS: Temporal FDB node-market golden path smoke succeeded")
    if report.get("report_path"):
        print(f"report: {report['report_path']}")
    print(f"execution_mode: {report['execution_mode']}")
    print(f"ledger_backend: {report['ledger_backend']}")
    print(f"registry_backend: {report['registry_backend']}")
    print(f"network: {report['network_profile']['network']}")
    print(f"chain_id: {report['network_profile']['chain_id']}")
    print(f"nodes_registered: {report['scenario']['node_count']}")
    print(f"requests_settled: {report['scenario']['request_count']}")
    print(f"requested_ring: {report['scenario']['requested_ring']}")
    print(f"max_price_credits: {report['scenario']['max_price_credits']}")
    print(f"selected_worker_rings: {metrics['selected_worker_rings']}")
    print(f"selected_worker_prices: {metrics['selected_worker_prices']}")
    print(f"selected_worker_count: {metrics['selected_worker_count']}")
    print(f"streams_completed: {report['stream_summary']['completed_request_count']}")
    print(f"token_events: {report['stream_summary']['token_event_count']}")
    print(f"final_available_credit_wei: {report['ledger_final_status']['totals']['available_credit_wei']}")
    print(f"final_spent_credit_wei: {report['ledger_final_status']['totals']['spent_credit_wei']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
