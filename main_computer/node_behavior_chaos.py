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


@dataclass(frozen=True)
class WorkerConnectionReliabilityEventPlan:
    event_id: str
    node_id: str
    mode: str
    submit_hub: str
    poll_hub: str
    result_hub: str


@dataclass(frozen=True)
class WorkerConnectionReliabilityScenarioConfig:
    """Configuration for worker disconnect behavior while a request is leased.

    The scenario covers both sides of the worker-connection policy:
    reconnect-before-timeout is accepted and charged once; worker-lost timeout
    fails the request, releases the requester hold, and rejects late results.
    """

    recover_before_timeout_events: int = 0
    lost_timeout_events: int = 0
    lease_seconds: float = 1.0
    lost_after_seconds: float = 1.25
    seed: str = ""
    verify_cross_hub: bool = True


def plan_worker_connection_reliability_events(
    nodes: list[Any] | tuple[Any, ...],
    *,
    recover_before_timeout_events: int,
    lost_timeout_events: int,
    seed: str,
    hub_labels: list[str] | tuple[str, ...] = ("hub_a", "hub_b"),
) -> list[WorkerConnectionReliabilityEventPlan]:
    count_recover = max(0, int(recover_before_timeout_events or 0))
    count_lost = max(0, int(lost_timeout_events or 0))
    labels = [str(label) for label in hub_labels if str(label)]
    candidates = [node for node in nodes if _node_id(node)]
    if (count_recover + count_lost) <= 0 or not candidates or not labels:
        return []
    rng = random.Random(f"{seed}:worker-connection-reliability")
    rng.shuffle(candidates)
    modes = ["recover_before_timeout"] * count_recover + ["lost_after_timeout"] * count_lost
    plans: list[WorkerConnectionReliabilityEventPlan] = []
    for index, mode in enumerate(modes, start=1):
        node = candidates[(index - 1) % len(candidates)]
        submit_hub = labels[rng.randrange(len(labels))]
        poll_options = [label for label in labels if label != submit_hub] or labels
        poll_hub = poll_options[rng.randrange(len(poll_options))]
        result_options = labels if len(labels) > 1 else [poll_hub]
        result_hub = result_options[rng.randrange(len(result_options))]
        plans.append(
            WorkerConnectionReliabilityEventPlan(
                event_id=f"worker-connection-{index:02d}",
                node_id=_node_id(node),
                mode=mode,
                submit_hub=submit_hub,
                poll_hub=poll_hub,
                result_hub=result_hub,
            )
        )
    return plans


def _post_worker_connection_request(
    *,
    config: Any,
    node_id: str,
    event_id: str,
    post_json: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    logical_id = f"{config.run_id}-{event_id}-{node_id}"
    quote_payload = {
        "account_id": config.account_id,
        "client_node_id": config.account_id,
        "model": config.model,
        "prompt": f"Worker connection reliability probe {event_id} for {node_id}",
        "max_credits": int(config.max_price_credits),
        "max_price_credits": int(config.max_price_credits),
        "requested_ring": int(config.requested_ring),
        "worker_node_id": node_id,
        "requested_worker_node_id": node_id,
        "execution_mode": "worker_pull_v0",
        "pricing_mode": "market_offer_fixed_per_call_v0",
        "idempotency_key": f"{logical_id}-quote",
        "metadata": {
            "worker_pull_v0": True,
            "node_behavior_scenario": "worker_connection_reliability",
            "node_behavior_event_id": event_id,
            "requested_worker_node_id": node_id,
        },
    }
    quote = post_json(
        config.hub_url,
        "/api/hub/v1/requests/quote",
        quote_payload,
        timeout=config.http_timeout_seconds,
        retry_attempts=config.http_retry_attempts,
    )["quote"]
    selected_offer = quote.get("selected_offer", {}) if isinstance(quote.get("selected_offer"), dict) else {}
    selected_worker_id = str(selected_offer.get("worker_node_id") or quote.get("selected_worker_node_id") or "")
    if selected_worker_id != node_id:
        raise NodeBehaviorScenarioError(
            f"Worker connection probe {event_id} expected quote for {node_id}, got {selected_worker_id}: {quote}"
        )
    submit_payload = {
        **quote_payload,
        "quote_id": quote["quote_id"],
        "metadata": {
            **quote_payload["metadata"],
            "quote_id": quote["quote_id"],
            "quote": dict(quote),
            "selected_offer": dict(selected_offer),
        },
        "idempotency_key": f"{logical_id}-submit",
    }
    submitted = post_json(
        config.hub_url,
        "/api/hub/v1/requests",
        submit_payload,
        timeout=config.http_timeout_seconds,
        retry_attempts=config.http_retry_attempts,
    )["request"]
    return {"quote": quote, "submitted": submitted}


def _poll_worker_connection_lease(
    *,
    config: Any,
    node_id: str,
    event_id: str,
    lease_seconds: float,
    post_json: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    result = post_json(
        config.hub_url,
        "/api/hub/v1/workers/poll",
        {
            "worker_node_id": node_id,
            "lease_seconds": max(1.0, float(lease_seconds or 1.0)),
            "node_behavior_event_id": event_id,
        },
        timeout=config.http_timeout_seconds,
        retry_attempts=config.http_retry_attempts,
    )
    lease = result.get("lease")
    if not isinstance(lease, dict):
        raise NodeBehaviorScenarioError(f"Worker connection probe {event_id} did not receive a lease for {node_id}: {result}")
    return lease


def _worker_connection_result_payload(*, config: Any, event_id: str, mode: str) -> dict[str, Any]:
    return {
        "status": "success",
        "response": {
            "content": f"worker connection reliability result {event_id}",
            "provider": "node-behavior-chaos",
            "model": config.model,
            "metadata": {
                "node_behavior_scenario": "worker_connection_reliability",
                "node_behavior_event_id": event_id,
                "worker_connection_mode": mode,
            },
        },
    }


def _event_types(events_payload: dict[str, Any]) -> list[str]:
    events = events_payload.get("events", [])
    if not isinstance(events, list):
        return []
    return [str(event.get("event_type") or event.get("type") or "") for event in events if isinstance(event, dict)]


def exercise_worker_connection_reliability_scenario(
    *,
    configs: dict[str, Any],
    nodes: list[Any] | tuple[Any, ...],
    scenario: WorkerConnectionReliabilityScenarioConfig,
    post_json: Callable[..., dict[str, Any]],
    get_json: Callable[..., dict[str, Any]],
    post_json_expect_http_error: Callable[..., dict[str, Any]],
    worker_payload: Callable[..., dict[str, Any]],
    worker_wallet_address: Callable[[Any, Any], str],
    progress: Any | None = None,
) -> dict[str, Any]:
    """Exercise active-lease worker disconnect policy through Hub HTTP APIs."""

    hub_labels = tuple(configs.keys())
    plans = plan_worker_connection_reliability_events(
        list(nodes),
        recover_before_timeout_events=scenario.recover_before_timeout_events,
        lost_timeout_events=scenario.lost_timeout_events,
        seed=scenario.seed,
        hub_labels=hub_labels,
    )
    nodes_by_id = {_node_id(node): node for node in nodes if _node_id(node)}
    events: list[dict[str, Any]] = []
    recover_completed = 0
    lost_failed = 0
    late_result_rejected = 0
    no_charge_after_lost = 0
    duplicate_result_count = 0
    payout_integrity_ok = True

    def emit(event: str, **fields: Any) -> None:
        if progress is not None and hasattr(progress, "emit"):
            progress.emit(event, **fields)

    for plan in plans:
        node = nodes_by_id.get(plan.node_id)
        if node is None:
            raise NodeBehaviorScenarioError(f"Worker connection plan referenced unknown node: {plan.node_id}")
        submit_config = configs[plan.submit_hub]
        poll_config = configs[plan.poll_hub]
        result_config = configs[plan.result_hub]
        wallet_address = worker_wallet_address(submit_config, node)
        emit(
            "node_behavior_worker_connection_start",
            event_id=plan.event_id,
            mode=plan.mode,
            node_id=plan.node_id,
            submit_hub=plan.submit_hub,
            poll_hub=plan.poll_hub,
            result_hub=plan.result_hub,
        )

        request_record = _post_worker_connection_request(
            config=submit_config,
            node_id=plan.node_id,
            event_id=plan.event_id,
            post_json=post_json,
        )
        request_id = str(request_record["submitted"].get("request_id", ""))
        lease = _poll_worker_connection_lease(
            config=poll_config,
            node_id=plan.node_id,
            event_id=plan.event_id,
            lease_seconds=scenario.lease_seconds,
            post_json=post_json,
        )
        if str(lease.get("request_id", "")) != request_id:
            raise NodeBehaviorScenarioError(
                f"Worker connection probe {plan.event_id} leased wrong request: expected={request_id} lease={lease}"
            )

        # Simulate the worker connection dropping while it owns a lease.
        post_json(
            poll_config.hub_url,
            "/api/hub/v1/workers/heartbeat",
            heartbeat_payload_for_node(
                node,
                status="offline",
                wallet_address=wallet_address,
                behavior_event_id=plan.event_id,
            ),
            timeout=poll_config.http_timeout_seconds,
            retry_attempts=poll_config.http_retry_attempts,
        )

        if plan.mode == "recover_before_timeout":
            # Same identity returns before the lease deadline, then submits the
            # original result under the original lease.
            post_json(
                result_config.hub_url,
                "/api/hub/v1/workers/register",
                worker_payload(node, model=result_config.model, wallet_address=wallet_address),
                timeout=result_config.http_timeout_seconds,
                retry_attempts=result_config.http_retry_attempts,
            )
            post_json(
                result_config.hub_url,
                "/api/hub/v1/workers/heartbeat",
                heartbeat_payload_for_node(
                    node,
                    status="available",
                    wallet_address=wallet_address,
                    behavior_event_id=plan.event_id,
                ),
                timeout=result_config.http_timeout_seconds,
                retry_attempts=result_config.http_retry_attempts,
            )
            completion_payload = post_json(
                result_config.hub_url,
                "/api/hub/v1/workers/results",
                {
                    "worker_node_id": plan.node_id,
                    "request_id": request_id,
                    "lease_id": lease["lease_id"],
                    "result": _worker_connection_result_payload(config=result_config, event_id=plan.event_id, mode=plan.mode),
                },
                timeout=result_config.http_timeout_seconds,
                retry_attempts=result_config.http_retry_attempts,
            )
            completion = completion_payload.get("request", {}) if isinstance(completion_payload.get("request"), dict) else {}
            if completion.get("state") != "completed":
                raise NodeBehaviorScenarioError(
                    f"Reconnect-before-timeout probe {plan.event_id} did not complete {request_id}: {completion_payload}"
                )
            replay = post_json(
                submit_config.hub_url,
                "/api/hub/v1/workers/results",
                {
                    "worker_node_id": plan.node_id,
                    "request_id": request_id,
                    "lease_id": lease["lease_id"],
                    "result": _worker_connection_result_payload(config=submit_config, event_id=plan.event_id, mode="duplicate_replay"),
                },
                timeout=submit_config.http_timeout_seconds,
                retry_attempts=submit_config.http_retry_attempts,
            )
            duplicate_charge = int(replay.get("duplicate_completion_additional_charge", 0) or 0)
            duplicate_result_count += 1
            if duplicate_charge != 0:
                raise NodeBehaviorScenarioError(f"Duplicate replay charged again for {request_id}: {replay}")
            charges = get_json(
                result_config.hub_url,
                f"/api/hub/v1/requests/{request_id}/charges",
                timeout=result_config.http_timeout_seconds,
            )
            earnings = get_json(
                result_config.hub_url,
                f"/api/hub/v1/credits/worker-earnings?{urlencode({'worker_node_id': plan.node_id, 'request_id': request_id})}",
                timeout=result_config.http_timeout_seconds,
            )
            if int(charges.get("charge_count", 0) or 0) != 1:
                payout_integrity_ok = False
                raise NodeBehaviorScenarioError(f"Expected one charge after reconnect recovery for {request_id}: {charges}")
            if int(earnings.get("worker_earning_count", 0) or 0) < 1:
                payout_integrity_ok = False
                raise NodeBehaviorScenarioError(f"Expected worker earning after reconnect recovery for {request_id}: {earnings}")
            recover_completed += 1
            event_result = {
                "event_id": plan.event_id,
                "mode": plan.mode,
                "node_id": plan.node_id,
                "request_id": request_id,
                "lease_id": lease["lease_id"],
                "completed": True,
                "charge_count": int(charges.get("charge_count", 0) or 0),
                "worker_earning_count": int(earnings.get("worker_earning_count", 0) or 0),
            }
        else:
            time.sleep(max(0.0, float(scenario.lost_after_seconds or 0.0)))
            late_error = post_json_expect_http_error(
                result_config.hub_url,
                "/api/hub/v1/workers/results",
                {
                    "worker_node_id": plan.node_id,
                    "request_id": request_id,
                    "lease_id": lease["lease_id"],
                    "result": _worker_connection_result_payload(config=result_config, event_id=plan.event_id, mode=plan.mode),
                },
                timeout=result_config.http_timeout_seconds,
                expected_status=400,
            )
            late_result_rejected += 1
            status = get_json(result_config.hub_url, f"/api/hub/v1/requests/{request_id}", timeout=result_config.http_timeout_seconds)["request"]
            if status.get("state") != "failed" or status.get("terminal_reason") != "worker_lost_timeout":
                raise NodeBehaviorScenarioError(f"Lost-worker probe {plan.event_id} did not fail cleanly: {status}")
            charges = get_json(
                result_config.hub_url,
                f"/api/hub/v1/requests/{request_id}/charges",
                timeout=result_config.http_timeout_seconds,
            )
            if int(charges.get("charge_count", 0) or 0) != 0:
                raise NodeBehaviorScenarioError(f"Lost-worker probe {plan.event_id} charged requester unexpectedly: {charges}")
            events_payload = get_json(result_config.hub_url, f"/api/hub/v1/requests/{request_id}/events", timeout=result_config.http_timeout_seconds)
            event_types = _event_types(events_payload)
            if "request.failed" not in event_types or "payment.hold.released" not in event_types:
                raise NodeBehaviorScenarioError(f"Lost-worker probe {plan.event_id} missing failure/hold-release events: {event_types}")
            # Restore node availability for the rest of the stress run.
            post_json(
                result_config.hub_url,
                "/api/hub/v1/workers/register",
                worker_payload(node, model=result_config.model, wallet_address=wallet_address),
                timeout=result_config.http_timeout_seconds,
                retry_attempts=result_config.http_retry_attempts,
            )
            post_json(
                result_config.hub_url,
                "/api/hub/v1/workers/heartbeat",
                heartbeat_payload_for_node(
                    node,
                    status="available",
                    wallet_address=wallet_address,
                    behavior_event_id=plan.event_id,
                ),
                timeout=result_config.http_timeout_seconds,
                retry_attempts=result_config.http_retry_attempts,
            )
            lost_failed += 1
            no_charge_after_lost += 1
            event_result = {
                "event_id": plan.event_id,
                "mode": plan.mode,
                "node_id": plan.node_id,
                "request_id": request_id,
                "lease_id": lease["lease_id"],
                "failed": True,
                "terminal_reason": status.get("terminal_reason"),
                "charge_count": int(charges.get("charge_count", 0) or 0),
                "late_error": late_error,
            }

        emit(
            "node_behavior_worker_connection_done",
            event_id=plan.event_id,
            mode=plan.mode,
            node_id=plan.node_id,
            request_id=request_id,
        )
        events.append(event_result)

    requested_recover = max(0, int(scenario.recover_before_timeout_events or 0))
    requested_lost = max(0, int(scenario.lost_timeout_events or 0))
    return {
        "recover_requested_count": requested_recover,
        "lost_requested_count": requested_lost,
        "planned_count": len(plans),
        "completed_count": len(events),
        "recover_before_timeout_completed_count": recover_completed,
        "lost_timeout_failed_count": lost_failed,
        "late_result_rejected_count": late_result_rejected,
        "no_charge_after_lost_count": no_charge_after_lost,
        "duplicate_result_replay_count": duplicate_result_count,
        "payout_integrity_ok": payout_integrity_ok,
        "events": events,
    }


def compact_worker_connection_reliability_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "recover_requested_count": int(result.get("recover_requested_count", 0) or 0),
        "lost_requested_count": int(result.get("lost_requested_count", 0) or 0),
        "completed_count": int(result.get("completed_count", 0) or 0),
        "recover_before_timeout_completed_count": int(result.get("recover_before_timeout_completed_count", 0) or 0),
        "lost_timeout_failed_count": int(result.get("lost_timeout_failed_count", 0) or 0),
        "late_result_rejected_count": int(result.get("late_result_rejected_count", 0) or 0),
        "no_charge_after_lost_count": int(result.get("no_charge_after_lost_count", 0) or 0),
        "payout_integrity_ok": bool(result.get("payout_integrity_ok", True)),
    }
