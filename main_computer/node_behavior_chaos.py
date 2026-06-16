from __future__ import annotations

import random
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable
from urllib.parse import urlencode


class NodeBehaviorScenarioError(RuntimeError):
    """Raised when a node behavior scenario leaves the Hub in an unsafe state."""


@dataclass(frozen=True)
class NodeReconnectEventPlan:
    event_id: str
    node_id: str
    offline_hub: str
    reconnect_hub: str


@dataclass(frozen=True)
class NodeReconnectScenarioConfig:
    """Configuration for deterministic node disconnect/reconnect behavior.

    The scenario intentionally starts with a conservative Hub-visible failure
    mode: mark a worker offline, prove the shared registry observes it, then
    reconnect the same node id/wallet by re-registering and heartbeating it as
    available.  Silent TCP drops can be layered on later once the heartbeat
    manager can safely suspend per-node loops during in-flight work.
    """

    event_count: int = 0
    seed: str = ""
    offline_status: str = "offline"
    reconnect_status: str = "available"
    verify_cross_hub: bool = True


def _node_id(node: Any) -> str:
    return str(getattr(node, "node_id", "") or "").strip()


def _node_ring(node: Any) -> int:
    return int(getattr(node, "ring", 0) or 0)


def _node_task_queue(node: Any) -> str:
    return str(getattr(node, "task_queue", "") or "")


def _node_max_concurrency(node: Any) -> int:
    return max(1, int(getattr(node, "max_concurrency", 1) or 1))


def plan_node_reconnect_events(
    nodes: list[Any] | tuple[Any, ...],
    *,
    event_count: int,
    seed: str,
    hub_labels: list[str] | tuple[str, ...] = ("hub_a", "hub_b"),
) -> list[NodeReconnectEventPlan]:
    """Return a deterministic reconnect plan without mutating Hub state."""

    count = max(0, int(event_count or 0))
    labels = [str(label) for label in hub_labels if str(label)]
    if count <= 0 or not nodes or not labels:
        return []
    rng = random.Random(f"{seed}:node-reconnect")
    candidates = [node for node in nodes if _node_id(node)]
    rng.shuffle(candidates)
    plans: list[NodeReconnectEventPlan] = []
    for index, node in enumerate(candidates[:count], start=1):
        offline_hub = labels[rng.randrange(len(labels))]
        if len(labels) > 1:
            reconnect_options = [label for label in labels if label != offline_hub]
            reconnect_hub = reconnect_options[rng.randrange(len(reconnect_options))]
        else:
            reconnect_hub = offline_hub
        plans.append(
            NodeReconnectEventPlan(
                event_id=f"node-reconnect-{index:02d}",
                node_id=_node_id(node),
                offline_hub=offline_hub,
                reconnect_hub=reconnect_hub,
            )
        )
    return plans


def worker_status_records(status_payload: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
    clean_node_id = str(node_id or "").strip()
    workers = status_payload.get("workers", [])
    if not isinstance(workers, list):
        return []
    return [
        dict(worker)
        for worker in workers
        if isinstance(worker, dict) and str(worker.get("node_id", "")).strip() == clean_node_id
    ]


def summarize_worker_status(status_payload: dict[str, Any], node_id: str) -> dict[str, Any]:
    records = worker_status_records(status_payload, node_id)
    statuses = [str(record.get("status", "") or "").lower() for record in records]
    wallet_addresses = sorted(
        {
            str(
                (record.get("capabilities", {}) if isinstance(record.get("capabilities"), dict) else {}).get("wallet_address", "")
            )
            for record in records
            if str(
                (record.get("capabilities", {}) if isinstance(record.get("capabilities"), dict) else {}).get("wallet_address", "")
            )
        }
    )
    return {
        "node_id": str(node_id or ""),
        "record_count": len(records),
        "statuses": statuses,
        "wallet_addresses": wallet_addresses,
        "offline": any(status in {"offline", "stale", "draining"} for status in statuses),
        "available": any(status in {"available", "configured"} for status in statuses),
        "duplicate_records": max(0, len(records) - 1),
    }


def _wait_for_worker_summary(
    *,
    config: Any,
    node_id: str,
    event_id: str,
    get_json: Callable[..., dict[str, Any]],
    want: str,
    attempts: int = 5,
    delay_seconds: float = 0.15,
) -> dict[str, Any]:
    """Poll Hub status briefly for cross-Hub registry propagation."""

    last_summary: dict[str, Any] = {}
    for attempt in range(1, max(1, attempts) + 1):
        status_payload = get_json(
            config.hub_url,
            f"/api/hub/v1/status?{urlencode({'node_behavior_event': event_id, 'attempt': attempt})}",
            timeout=config.http_timeout_seconds,
        )
        last_summary = summarize_worker_status(status_payload, node_id)
        if want == "offline" and last_summary.get("offline") is True:
            return last_summary
        if want == "available" and last_summary.get("available") is True and int(last_summary.get("record_count", 0) or 0) == 1:
            return last_summary
        if attempt < max(1, attempts):
            time.sleep(max(0.0, delay_seconds))
    return last_summary


def heartbeat_payload_for_node(
    node: Any,
    *,
    status: str,
    wallet_address: str,
    behavior_event_id: str,
) -> dict[str, Any]:
    return {
        "worker_node_id": _node_id(node),
        "status": str(status or "available"),
        "assigned_ring": _node_ring(node),
        "wallet_address": str(wallet_address or ""),
        "capabilities": {
            "worker_pull_v0": True,
            "assigned_ring": _node_ring(node),
            "task_queue": _node_task_queue(node),
            "wallet_address": str(wallet_address or ""),
            "keepalive": {
                "mode": "node-behavior-scenario",
                "behavior_event_id": behavior_event_id,
            },
        },
        "max_concurrency": _node_max_concurrency(node),
    }


def exercise_node_reconnect_scenario(
    *,
    configs: dict[str, Any],
    nodes: list[Any] | tuple[Any, ...],
    scenario: NodeReconnectScenarioConfig,
    post_json: Callable[..., dict[str, Any]],
    get_json: Callable[..., dict[str, Any]],
    worker_payload: Callable[..., dict[str, Any]],
    worker_wallet_address: Callable[[Any, Any], str],
    progress: Any | None = None,
) -> dict[str, Any]:
    """Exercise deterministic offline -> reconnect behavior through Hub HTTP APIs.

    All operations are ordinary Hub API calls.  The scenario does not mutate
    local test internals, does not inspect files, and does not rely on private
    registry state.
    """

    hub_labels = tuple(configs.keys())
    plans = plan_node_reconnect_events(
        list(nodes),
        event_count=scenario.event_count,
        seed=scenario.seed,
        hub_labels=hub_labels,
    )
    nodes_by_id = {_node_id(node): node for node in nodes if _node_id(node)}
    events: list[dict[str, Any]] = []
    duplicate_registration_count = 0
    offline_seen_count = 0
    available_after_reconnect_count = 0

    def emit(event: str, **fields: Any) -> None:
        if progress is not None and hasattr(progress, "emit"):
            progress.emit(event, **fields)

    for plan in plans:
        node = nodes_by_id.get(plan.node_id)
        if node is None:
            raise NodeBehaviorScenarioError(f"Reconnect plan referenced unknown node: {plan.node_id}")
        offline_config = configs[plan.offline_hub]
        reconnect_config = configs[plan.reconnect_hub]
        wallet_address = worker_wallet_address(reconnect_config, node)

        emit(
            "node_behavior_disconnect_start",
            event_id=plan.event_id,
            node_id=plan.node_id,
            offline_hub=plan.offline_hub,
            reconnect_hub=plan.reconnect_hub,
        )
        post_json(
            offline_config.hub_url,
            "/api/hub/v1/workers/heartbeat",
            heartbeat_payload_for_node(
                node,
                status=scenario.offline_status,
                wallet_address=wallet_address,
                behavior_event_id=plan.event_id,
            ),
            timeout=offline_config.http_timeout_seconds,
            retry_attempts=offline_config.http_retry_attempts,
        )
        offline_summary = _wait_for_worker_summary(
            config=offline_config,
            node_id=plan.node_id,
            event_id=plan.event_id,
            get_json=get_json,
            want="offline",
        )
        if offline_summary["offline"]:
            offline_seen_count += 1
        else:
            raise NodeBehaviorScenarioError(
                f"Node {plan.node_id} was not observed offline during {plan.event_id}: {offline_summary}"
            )

        emit(
            "node_behavior_reconnect_start",
            event_id=plan.event_id,
            node_id=plan.node_id,
            reconnect_hub=plan.reconnect_hub,
        )
        register_result = post_json(
            reconnect_config.hub_url,
            "/api/hub/v1/workers/register",
            worker_payload(node, model=reconnect_config.model, wallet_address=wallet_address),
            timeout=reconnect_config.http_timeout_seconds,
            retry_attempts=reconnect_config.http_retry_attempts,
        )
        heartbeat_result = post_json(
            reconnect_config.hub_url,
            "/api/hub/v1/workers/heartbeat",
            heartbeat_payload_for_node(
                node,
                status=scenario.reconnect_status,
                wallet_address=wallet_address,
                behavior_event_id=plan.event_id,
            ),
            timeout=reconnect_config.http_timeout_seconds,
            retry_attempts=reconnect_config.http_retry_attempts,
        )

        verify_labels = hub_labels if scenario.verify_cross_hub else (plan.reconnect_hub,)
        verify_summaries: dict[str, dict[str, Any]] = {}
        for label in verify_labels:
            verify_config = configs[label]
            summary = _wait_for_worker_summary(
                config=verify_config,
                node_id=plan.node_id,
                event_id=plan.event_id,
                get_json=get_json,
                want="available",
            )
            verify_summaries[label] = summary
            duplicate_registration_count += int(summary.get("duplicate_records", 0) or 0)
            if int(summary.get("record_count", 0) or 0) != 1:
                raise NodeBehaviorScenarioError(
                    f"Node {plan.node_id} had duplicate/missing registration after reconnect on {label}: {summary}"
                )
            if summary.get("available") is not True:
                raise NodeBehaviorScenarioError(
                    f"Node {plan.node_id} was not available after reconnect on {label}: {summary}"
                )

        available_after_reconnect_count += 1
        emit(
            "node_behavior_reconnect_done",
            event_id=plan.event_id,
            node_id=plan.node_id,
            offline_hub=plan.offline_hub,
            reconnect_hub=plan.reconnect_hub,
        )
        events.append(
            {
                "event_id": plan.event_id,
                "event_kind": "node_reconnect",
                "node_id": plan.node_id,
                "offline_hub": plan.offline_hub,
                "reconnect_hub": plan.reconnect_hub,
                "offline_summary": offline_summary,
                "verify_summaries": verify_summaries,
                "register": register_result,
                "heartbeat": heartbeat_result,
            }
        )

    return {
        "requested_count": max(0, int(scenario.event_count or 0)),
        "planned_count": len(plans),
        "completed_count": len(events),
        "offline_seen_count": offline_seen_count,
        "available_after_reconnect_count": available_after_reconnect_count,
        "duplicate_registration_count": duplicate_registration_count,
        "cross_hub_status_ok": all(
            int(summary.get("record_count", 0) or 0) == 1 and bool(summary.get("available"))
            for event in events
            for summary in dict(event.get("verify_summaries", {})).values()
            if isinstance(summary, dict)
        ),
        "events": events,
    }


def compact_node_behavior_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "requested_count": int(result.get("requested_count", 0) or 0),
        "completed_count": int(result.get("completed_count", 0) or 0),
        "offline_seen_count": int(result.get("offline_seen_count", 0) or 0),
        "available_after_reconnect_count": int(result.get("available_after_reconnect_count", 0) or 0),
        "duplicate_registration_count": int(result.get("duplicate_registration_count", 0) or 0),
        "cross_hub_status_ok": bool(result.get("cross_hub_status_ok", True)),
    }
