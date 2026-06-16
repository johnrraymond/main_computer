from __future__ import annotations

from types import SimpleNamespace

from main_computer.node_behavior_chaos import (
    NodeReconnectScenarioConfig,
    compact_node_behavior_summary,
    exercise_node_reconnect_scenario,
    heartbeat_payload_for_node,
    plan_node_reconnect_events,
    summarize_worker_status,
)


def _node(node_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        node_id=node_id,
        endpoint=f"http://127.0.0.1/{node_id}",
        ring=1,
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
