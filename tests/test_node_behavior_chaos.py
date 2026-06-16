from __future__ import annotations

from types import SimpleNamespace

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
    heartbeat_payload_for_node,
    plan_node_reconnect_events,
    plan_requester_disconnect_result_retention_events,
    plan_worker_connection_reliability_events,
    summarize_worker_status,
)


def _node(node_id: str, *, ring: int = 1, price_credits: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        node_id=node_id,
        endpoint=f"http://127.0.0.1/{node_id}",
        ring=ring,
        price_credits=price_credits,
        task_queue=f"queue-{node_id}",
        max_concurrency=2,
    )


def test_plan_node_reconnect_events_is_seeded_and_cross_hub() -> None:
    nodes = [_node(f"node-{index:03d}") for index in range(1, 8)]

    first = plan_node_reconnect_events(nodes, event_count=3, seed="unit", hub_labels=("hub_a", "hub_b"))
    second = plan_node_reconnect_events(nodes, event_count=3, seed="unit", hub_labels=("hub_a", "hub_b"))

    assert first == second
    assert len(first) == 3
    assert {plan.event_id for plan in first} == {"node-reconnect-01", "node-reconnect-02", "node-reconnect-03"}
    assert all(plan.offline_hub != plan.reconnect_hub for plan in first)


def test_plan_node_reconnect_events_filters_quote_incompatible_workers() -> None:
    nodes = [
        _node("node-expensive-ring0", ring=0, price_credits=4),
        _node("node-affordable-ring1", ring=1, price_credits=2),
        _node("node-expensive-ring2", ring=2, price_credits=3),
        _node("node-cheap-low-service", ring=3, price_credits=1),
    ]

    plans = plan_node_reconnect_events(
        nodes,
        event_count=3,
        seed="unit",
        hub_labels=("hub_a", "hub_b"),
        requested_ring=2,
        max_price_credits=2,
    )

    assert {plan.node_id for plan in plans} == {"node-affordable-ring1"}


def test_status_summary_detects_offline_available_and_duplicate_records() -> None:
    status_payload = {
        "workers": [
            {"node_id": "node-001", "status": "offline", "capabilities": {"wallet_address": "0x1"}},
            {"node_id": "node-001", "status": "available", "capabilities": {"wallet_address": "0x1"}},
            {"node_id": "node-002", "status": "available"},
        ]
    }

    summary = summarize_worker_status(status_payload, "node-001")

    assert summary["record_count"] == 2
    assert summary["duplicate_records"] == 1
    assert summary["offline"] is True
    assert summary["available"] is True
    assert summary["wallet_addresses"] == ["0x1"]


def test_heartbeat_payload_marks_behavior_event() -> None:
    payload = heartbeat_payload_for_node(
        _node("node-001"),
        status="offline",
        wallet_address="0x0000000000000000000000000000000000000001",
        behavior_event_id="node-reconnect-01",
    )

    assert payload["worker_node_id"] == "node-001"
    assert payload["status"] == "offline"
    assert payload["capabilities"]["keepalive"]["mode"] == "node-behavior-scenario"
    assert payload["capabilities"]["keepalive"]["behavior_event_id"] == "node-reconnect-01"


def test_exercise_node_reconnect_scenario_reuses_same_node_record() -> None:
    nodes = [_node("node-001"), _node("node-002")]
    configs = {
        "hub_a": SimpleNamespace(hub_url="http://hub-a", model="unit-model", http_timeout_seconds=3.0, http_retry_attempts=1),
        "hub_b": SimpleNamespace(hub_url="http://hub-b", model="unit-model", http_timeout_seconds=3.0, http_retry_attempts=1),
    }
    registry: dict[str, dict[str, object]] = {
        node.node_id: {
            "node_id": node.node_id,
            "status": "available",
            "capabilities": {"wallet_address": f"0x{int(node.node_id[-3:]):040x}"},
        }
        for node in nodes
    }
    calls: list[tuple[str, str, dict[str, object]]] = []

    def post_json(hub_url: str, path: str, payload: dict[str, object], **kwargs: object) -> dict[str, object]:
        calls.append((hub_url, path, payload))
        node_id = str(payload.get("worker_node_id") or payload.get("node_id") or "")
        if path == "/api/hub/v1/workers/heartbeat":
            registry[node_id]["status"] = str(payload.get("status") or "available")
            registry[node_id]["capabilities"] = dict(payload.get("capabilities", {}))
            return {"ok": True, "worker": dict(registry[node_id])}
        if path == "/api/hub/v1/workers/register":
            registry[node_id] = {
                "node_id": node_id,
                "status": "available",
                "capabilities": dict(payload.get("capabilities", {})),
            }
            return {"ok": True, "worker": dict(registry[node_id])}
        raise AssertionError(path)

    def get_json(hub_url: str, path: str, **kwargs: object) -> dict[str, object]:
        return {"ok": True, "workers": [dict(item) for item in registry.values()]}

    def worker_payload(node: object, *, model: str, wallet_address: str) -> dict[str, object]:
        return {
            "node_id": getattr(node, "node_id"),
            "endpoint": getattr(node, "endpoint"),
            "model": model,
            "capabilities": {"wallet_address": wallet_address},
        }

    def wallet_address(config: object, node: object) -> str:
        return f"0x{int(getattr(node, 'node_id')[-3:]):040x}"

    result = exercise_node_reconnect_scenario(
        configs=configs,
        nodes=nodes,
        scenario=NodeReconnectScenarioConfig(event_count=1, seed="unit"),
        post_json=post_json,
        get_json=get_json,
        worker_payload=worker_payload,
        worker_wallet_address=wallet_address,
    )

    assert result["completed_count"] == 1
    assert result["offline_seen_count"] == 1
    assert result["available_after_reconnect_count"] == 1
    assert result["duplicate_registration_count"] == 0
    assert result["cross_hub_status_ok"] is True
    assert compact_node_behavior_summary(result)["completed_count"] == 1
    assert any(path == "/api/hub/v1/workers/register" for _, path, _ in calls)


def test_plan_worker_connection_reliability_events_covers_recover_and_lost_modes() -> None:
    nodes = [_node(f"node-{index:03d}") for index in range(1, 6)]

    first = plan_worker_connection_reliability_events(
        nodes,
        recover_before_timeout_events=2,
        lost_timeout_events=1,
        seed="unit",
        hub_labels=("hub_a", "hub_b"),
    )
    second = plan_worker_connection_reliability_events(
        nodes,
        recover_before_timeout_events=2,
        lost_timeout_events=1,
        seed="unit",
        hub_labels=("hub_a", "hub_b"),
    )

    assert first == second
    assert len(first) == 3
    assert [plan.mode for plan in first].count("recover_before_timeout") == 2
    assert [plan.mode for plan in first].count("lost_after_timeout") == 1


def test_plan_worker_connection_reliability_events_filters_quote_incompatible_workers() -> None:
    nodes = [
        _node("node-too-expensive", ring=0, price_credits=4),
        _node("node-compatible-a", ring=1, price_credits=2),
        _node("node-compatible-b", ring=2, price_credits=2),
        _node("node-too-low-service", ring=3, price_credits=1),
    ]

    plans = plan_worker_connection_reliability_events(
        nodes,
        recover_before_timeout_events=2,
        lost_timeout_events=1,
        seed="unit",
        hub_labels=("hub_a", "hub_b"),
        requested_ring=2,
        max_price_credits=2,
    )

    assert len(plans) == 3
    assert {plan.node_id for plan in plans} <= {"node-compatible-a", "node-compatible-b"}


def test_worker_connection_reliability_scenario_classifies_recover_and_lost_paths() -> None:
    nodes = [_node("node-001"), _node("node-002")]
    configs = {
        "hub_a": SimpleNamespace(
            hub_url="http://hub-a",
            model="unit-model",
            account_id="requester",
            max_price_credits=2,
            requested_ring=2,
            run_id="unit-run",
            http_timeout_seconds=3.0,
            http_retry_attempts=1,
        ),
        "hub_b": SimpleNamespace(
            hub_url="http://hub-b",
            model="unit-model",
            account_id="requester",
            max_price_credits=2,
            requested_ring=2,
            run_id="unit-run",
            http_timeout_seconds=3.0,
            http_retry_attempts=1,
        ),
    }
    requests: dict[str, dict[str, object]] = {}
    leases: dict[str, dict[str, object]] = {}
    node_status: dict[str, str] = {node.node_id: "available" for node in nodes}
    charges: dict[str, int] = {}
    earnings: dict[str, int] = {}

    def post_json(hub_url: str, path: str, payload: dict[str, object], **kwargs: object) -> dict[str, object]:
        if path == "/api/hub/v1/requests/quote":
            node_id = str(payload["requested_worker_node_id"])
            return {
                "quote": {
                    "quote_id": f"quote-{payload['idempotency_key']}",
                    "selected_worker_node_id": node_id,
                    "selected_offer": {"worker_node_id": node_id, "credits_per_request": 2},
                    "quoted_credits": 2,
                    "max_credits": 2,
                    "execution_mode": "worker_pull_v0",
                }
            }
        if path == "/api/hub/v1/requests":
            request_id = f"request-{len(requests)+1}"
            requests[request_id] = {
                "request_id": request_id,
                "state": "queued",
                "terminal_reason": "",
                "worker_node_id": str(payload["requested_worker_node_id"]),
            }
            return {"request": dict(requests[request_id])}
        if path == "/api/hub/v1/workers/poll":
            node_id = str(payload["worker_node_id"])
            request = next(
                item for item in requests.values()
                if item["state"] == "queued" and item["worker_node_id"] == node_id
            )
            request["state"] = "leased"
            lease = {"request_id": request["request_id"], "lease_id": f"lease-{request['request_id']}"}
            leases[str(request["request_id"])] = lease
            return {"ok": True, "lease": dict(lease)}
        if path == "/api/hub/v1/workers/heartbeat":
            node_status[str(payload["worker_node_id"])] = str(payload.get("status") or "available")
            return {"ok": True}
        if path == "/api/hub/v1/workers/register":
            node_status[str(payload["node_id"])] = "available"
            return {"ok": True, "worker": {"node_id": payload["node_id"], "status": "available"}}
        if path == "/api/hub/v1/workers/results":
            request_id = str(payload["request_id"])
            request = requests[request_id]
            request["state"] = "completed"
            charges[request_id] = 1
            earnings[request_id] = 1
            return {
                "ok": True,
                "request": {"request_id": request_id, "state": "completed"},
                "duplicate_completion_additional_charge": 0,
            }
        raise AssertionError(path)

    def get_json(hub_url: str, path: str, **kwargs: object) -> dict[str, object]:
        if "/charges" in path:
            request_id = path.split("/requests/")[1].split("/charges")[0]
            count = charges.get(request_id, 0)
            return {"charge_count": count, "charges": [{}] * count}
        if "/worker-earnings" in path:
            request_id = path.split("request_id=")[-1]
            count = earnings.get(request_id, 0)
            return {"worker_earning_count": count, "worker_earnings": [{}] * count}
        if "/events" in path:
            request_id = path.split("/requests/")[1].split("/events")[0]
            request = requests[request_id]
            if request["state"] == "failed":
                return {"events": [{"type": "payment.hold.released"}, {"type": "request.failed"}]}
            return {"events": [{"type": "request.completed"}]}
        if "/requests/" in path:
            request_id = path.rsplit("/", 1)[-1]
            return {"request": dict(requests[request_id])}
        return {"workers": [{"node_id": node_id, "status": status} for node_id, status in node_status.items()]}

    def post_json_expect_http_error(hub_url: str, path: str, payload: dict[str, object], **kwargs: object) -> dict[str, object]:
        request_id = str(payload["request_id"])
        requests[request_id]["state"] = "failed"
        requests[request_id]["terminal_reason"] = "worker_lost_timeout"
        charges[request_id] = 0
        return {"error": "Worker result lease has expired; request failed without charging requester.", "_http_status": 400}

    def worker_payload(node: object, *, model: str, wallet_address: str) -> dict[str, object]:
        return {
            "node_id": getattr(node, "node_id"),
            "endpoint": getattr(node, "endpoint"),
            "model": model,
            "capabilities": {"wallet_address": wallet_address},
        }

    def wallet_address(config: object, node: object) -> str:
        return f"0x{int(getattr(node, 'node_id')[-3:]):040x}"

    result = exercise_worker_connection_reliability_scenario(
        configs=configs,
        nodes=nodes,
        scenario=WorkerConnectionReliabilityScenarioConfig(
            recover_before_timeout_events=1,
            lost_timeout_events=1,
            lease_seconds=1.0,
            lost_after_seconds=0.0,
            seed="unit",
        ),
        post_json=post_json,
        get_json=get_json,
        post_json_expect_http_error=post_json_expect_http_error,
        worker_payload=worker_payload,
        worker_wallet_address=wallet_address,
    )

    assert result["recover_before_timeout_completed_count"] == 1
    assert result["lost_timeout_failed_count"] == 1
    assert result["late_result_rejected_count"] == 1
    assert result["no_charge_after_lost_count"] == 1
    assert result["payout_integrity_ok"] is True
    assert compact_worker_connection_reliability_summary(result)["lost_timeout_failed_count"] == 1



def test_plan_requester_disconnect_result_retention_events_filters_quote_incompatible_workers() -> None:
    nodes = [
        _node("node-too-expensive", ring=0, price_credits=4),
        _node("node-compatible-a", ring=1, price_credits=2),
        _node("node-compatible-b", ring=2, price_credits=2),
        _node("node-too-low-service", ring=3, price_credits=1),
    ]

    plans = plan_requester_disconnect_result_retention_events(
        nodes,
        event_count=3,
        seed="unit",
        hub_labels=("hub_a", "hub_b"),
        requested_ring=2,
        max_price_credits=2,
    )

    assert len(plans) == 3
    assert {plan.node_id for plan in plans} <= {"node-compatible-a", "node-compatible-b"}


def test_requester_disconnect_result_retention_scenario_pickup_and_payout_integrity() -> None:
    nodes = [_node("node-001"), _node("node-002")]
    configs = {
        "hub_a": SimpleNamespace(
            hub_url="http://hub-a",
            model="unit-model",
            account_id="requester",
            max_price_credits=2,
            requested_ring=2,
            run_id="unit-run",
            http_timeout_seconds=3.0,
            http_retry_attempts=1,
        ),
        "hub_b": SimpleNamespace(
            hub_url="http://hub-b",
            model="unit-model",
            account_id="requester",
            max_price_credits=2,
            requested_ring=2,
            run_id="unit-run",
            http_timeout_seconds=3.0,
            http_retry_attempts=1,
        ),
    }
    requests: dict[str, dict[str, object]] = {}
    charges: dict[str, int] = {}
    earnings: dict[str, int] = {}

    def post_json(hub_url: str, path: str, payload: dict[str, object], **kwargs: object) -> dict[str, object]:
        if path == "/api/hub/v1/requests/quote":
            node_id = str(payload["requested_worker_node_id"])
            return {
                "quote": {
                    "quote_id": f"quote-{payload['idempotency_key']}",
                    "selected_worker_node_id": node_id,
                    "selected_offer": {"worker_node_id": node_id, "credits_per_request": 2},
                    "quoted_credits": 2,
                    "max_credits": 2,
                    "execution_mode": "worker_pull_v0",
                }
            }
        if path == "/api/hub/v1/requests":
            request_id = f"request-{len(requests)+1}"
            requests[request_id] = {
                "request_id": request_id,
                "state": "queued",
                "worker_node_id": str(payload["requested_worker_node_id"]),
                "response": {},
            }
            return {"request": dict(requests[request_id])}
        if path == "/api/hub/v1/workers/poll":
            node_id = str(payload["worker_node_id"])
            request = next(
                item for item in requests.values()
                if item["state"] == "queued" and item["worker_node_id"] == node_id
            )
            request["state"] = "leased"
            return {"ok": True, "lease": {"request_id": request["request_id"], "lease_id": f"lease-{request['request_id']}"}}
        if path == "/api/hub/v1/workers/results":
            request_id = str(payload["request_id"])
            response = dict(dict(payload["result"])["response"])  # type: ignore[index]
            response["metadata"] = {
                **dict(response.get("metadata", {})),
                "hub": {
                    "result_retention": {
                        "retained": True,
                        "window_seconds": 3600,
                        "expires_at": "2999-01-01T00:00:00+00:00",
                    }
                },
            }
            requests[request_id]["state"] = "completed"
            requests[request_id]["response"] = response
            charges[request_id] = 1
            earnings[request_id] = 1
            return {"ok": True, "request": {"request_id": request_id, "state": "completed", "response": response}}
        raise AssertionError(path)

    def get_json(hub_url: str, path: str, **kwargs: object) -> dict[str, object]:
        if path.endswith("/charges"):
            request_id = path.split("/requests/")[1].split("/charges")[0]
            count = charges.get(request_id, 0)
            return {"charge_count": count, "charges": [{}] * count}
        if "/worker-earnings" in path:
            request_id = path.split("request_id=")[-1]
            count = earnings.get(request_id, 0)
            return {"worker_earning_count": count, "worker_earnings": [{}] * count}
        if "/result?" in path:
            request_id = path.split("/requests/")[1].split("/result")[0]
            return {
                "ok": True,
                "result_available": True,
                "retained": True,
                "expired": False,
                "result": dict(requests[request_id]["response"]),
                "request": dict(requests[request_id]),
            }
        raise AssertionError(path)

    result = exercise_requester_disconnect_result_retention_scenario(
        configs=configs,
        nodes=nodes,
        scenario=RequesterDisconnectResultRetentionScenarioConfig(
            event_count=1,
            pickup_after_seconds=0.0,
            result_retention_window_seconds=3600,
            seed="unit",
        ),
        post_json=post_json,
        get_json=get_json,
    )

    assert result["result_retained_count"] == 1
    assert result["result_pickup_count"] == 1
    assert result["expired_count"] == 0
    assert result["worker_payout_integrity_ok"] is True
    assert compact_requester_disconnect_result_retention_summary(result)["result_pickup_count"] == 1
