from __future__ import annotations

from tools.scheduler_lab.hub_client import HubHttpResponse
from tools.scheduler_lab.node_list import SCHEMA, build_document, build_nodes
from tools.scheduler_lab.run_lab import is_insufficient_credit_response, node_can_request, node_can_work, select_nodes


def test_generated_nodes_have_common_adaptive_behavior_fields() -> None:
    nodes = build_nodes(total=36, seed=20260611)

    assert SCHEMA == "main-computer-hub-lab-node-grid/v2"
    assert all(node.get("account_id") for node in nodes)
    assert all(node.get("behavior_mode") for node in nodes)
    assert all(node.get("funding_remediation") for node in nodes)
    assert all("initial_credits" in node for node in nodes)
    assert all("local_busy_probability_per_minute" in node for node in nodes)

    # Workers are no longer pure suppliers; at least some can buy work too.
    assert any(node["kind"] == "worker" and node_can_request(node) for node in nodes)

    # Requesters are no longer pure consumers; low-credit remediation can push them into work.
    assert any(node["kind"] == "requester" and node_can_work(node) for node in nodes)

    document = build_document(nodes, seed=20260611, hub_base_url="http://hub.example", network="dev", ring=2, chain_id=42424242)
    assert document["summary"]["behavior_modes"]
    assert document["summary"]["funding_remediation"]


def test_role_selection_uses_capability_instead_of_static_kind() -> None:
    nodes = build_nodes(total=36, seed=20260611)

    worker_selected = select_nodes(nodes, "workers")
    requester_selected = select_nodes(nodes, "requesters")

    assert all(node_can_work(node) for node in worker_selected)
    assert all(node_can_request(node) for node in requester_selected)
    assert any(node["kind"] == "requester" for node in worker_selected)
    assert any(node["kind"] == "worker" for node in requester_selected)


def test_insufficient_credit_400_is_classified_as_economic_event() -> None:
    response = HubHttpResponse(
        ok=False,
        status=400,
        payload={"error": "Insufficient Compute Credits for account lab-account-requester-0001: 0 credits available, 1 credits required."},
        elapsed_ms=3.5,
    )

    assert is_insufficient_credit_response(response)
    assert not is_insufficient_credit_response(HubHttpResponse(ok=False, status=400, payload={"error": "bad model"}, elapsed_ms=1.0))
