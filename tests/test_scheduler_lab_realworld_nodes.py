from __future__ import annotations

import json
import random
import time

from tools.scheduler_lab.hub_client import HubClient, HubHttpResponse
from tools.scheduler_lab.node_list import SCHEMA, build_document, build_nodes, normalize_hub_base_urls
from tools.scheduler_lab.run_lab import build_arg_parser, effective_request_startup_mode, is_insufficient_credit_response, mark_assumed_prefunded_nodes, node_can_request, node_can_work, parse_funded_percent, parse_warm_spec, parse_worktime_spec, select_nodes, should_send_startup_request


def test_generated_nodes_have_common_adaptive_behavior_fields() -> None:
    nodes = build_nodes(total=36, seed=20260611)

    assert SCHEMA == "main-computer-hub-lab-node-grid/v2"
    assert all(node.get("account_id") for node in nodes)
    assert all(node.get("hub_base_urls_json") for node in nodes)
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


def test_scheduler_lab_nodes_can_advertise_multiple_hub_urls() -> None:
    hub_urls = ["http://host.docker.internal:8870", "http://host.docker.internal:8871", "http://host.docker.internal:8872"]
    nodes = build_nodes(total=8, seed=20260611, hub_base_urls=hub_urls)

    assert all(json.loads(str(node["hub_base_urls_json"])) == hub_urls for node in nodes)

    document = build_document(nodes, seed=20260611, hub_base_url=hub_urls[0], hub_base_urls=hub_urls, network="dev", ring=2, chain_id=42424242)
    assert document["lab"]["hub_base_urls"] == hub_urls
    assert normalize_hub_base_urls(",".join(hub_urls), hub_urls[0]) == hub_urls


def test_hub_client_randomly_chooses_hub_url_per_attempt() -> None:
    hub_urls = ["http://hub-a.example:8870", "http://hub-b.example:8871", "http://hub-c.example:8872"]
    client = HubClient(hub_urls[0], base_urls=hub_urls, rng=random.Random(7))

    choices = {client._choose_base_url().rstrip("/") for _ in range(40)}

    assert choices.issubset(set(hub_urls))
    assert len(choices) > 1


def test_run_lab_docker_counts_can_be_supplied_by_environment(monkeypatch) -> None:
    monkeypatch.setenv("LAB_TOTAL", "42")
    monkeypatch.setenv("LAB_WORKERS", "31")
    monkeypatch.setenv("LAB_REQUESTERS", "11")

    args = build_arg_parser().parse_args([])

    assert args.total == 42
    assert args.workers == 31
    assert args.requesters == 11





def test_scheduler_lab_funded_percent_marks_accounts_as_preexisting() -> None:
    nodes = build_nodes(total=100, seed=20260611)
    marked = mark_assumed_prefunded_nodes(nodes, funded_percent=parse_funded_percent("90"), seed=20260611)

    eligible = sum(1 for node in nodes if int(node.get("initial_credits", 0)) > 0)

    assert marked == round(eligible * 0.90)
    assert sum(1 for node in nodes if node.get("_assumed_prefunded")) == marked
    assert parse_funded_percent("0.9") == 90.0
    assert parse_funded_percent("90%") == 90.0


def test_scheduler_lab_accepts_nodes_and_worktime_runtime_distribution(monkeypatch) -> None:
    monkeypatch.setenv("LAB_NODES", "1000")
    monkeypatch.setenv("LAB_WORKTIME", "100mu,30sigma")

    args = build_arg_parser().parse_args([])
    spec = parse_worktime_spec(args.worktime)

    assert args.nodes == 1000
    assert spec is not None
    assert spec.mean_seconds == 100
    assert spec.sigma_seconds == 30


def test_worktime_parser_accepts_positional_seconds_and_named_sd() -> None:
    positional = parse_worktime_spec("12,3")
    named = parse_worktime_spec("mu=15s,sd=4s")

    assert positional is not None
    assert positional.mean_seconds == 12
    assert positional.sigma_seconds == 3
    assert named is not None
    assert named.mean_seconds == 15
    assert named.sigma_seconds == 4


def test_funded_reattach_defaults_to_startup_request_surge(monkeypatch) -> None:
    monkeypatch.setenv("LAB_FUNDED", "90")

    args = build_arg_parser().parse_args([])
    nodes = build_nodes(total=40, seed=20260611)
    mark_assumed_prefunded_nodes(nodes, funded_percent=args.funded, seed=20260611)

    assert effective_request_startup_mode(args) == "surge"
    assert any(should_send_startup_request(node, args) for node in nodes if node_can_request(node))


def test_natural_startup_mode_disables_request_surge(monkeypatch) -> None:
    monkeypatch.setenv("LAB_FUNDED", "90")
    monkeypatch.setenv("LAB_REQUEST_STARTUP_MODE", "natural")

    args = build_arg_parser().parse_args([])
    node = next(node for node in build_nodes(total=12, seed=20260611) if node_can_request(node))
    node["_assumed_prefunded"] = True

    assert effective_request_startup_mode(args) == "natural"
    assert not should_send_startup_request(node, args)


def test_scheduler_lab_process_mode_and_warm_controls_are_exposed(monkeypatch) -> None:
    monkeypatch.setenv("LAB_EXECUTION_MODE", "process")
    monkeypatch.setenv("LAB_WARM", "2mu,1sigma")
    monkeypatch.setenv("B2B_FAILURES", "10")
    monkeypatch.setenv("FORCED_ALIVE_SECONDS", "100")
    monkeypatch.setenv("HTTP_TIMEOUT_SECONDS", "1")

    args = build_arg_parser().parse_args([])
    warm = parse_warm_spec(args.warm)

    assert args.execution_mode == "process"
    assert args.b2bfailures == 10
    assert args.forced_alive == 100
    assert args.http_timeout_seconds == 1
    assert warm is not None
    assert warm.mean_seconds == 2
    assert warm.sigma_seconds == 1


def test_warm_parser_allows_zero_for_immediate_wall() -> None:
    warm = parse_warm_spec("0mu,0sigma")

    assert warm is not None
    assert warm.mean_seconds == 0
    assert warm.sigma_seconds == 0


def test_node_process_module_documents_b2b_immediate_retry() -> None:
    from tools.scheduler_lab import node_process

    source = node_process.NodeHttpRunner.call.__doc__ or ""
    module_text = node_process.__file__

    assert node_process.SELF_TERMINATED_B2B_FAILURES_EXIT_CODE == 75
    assert "node_process.py" in module_text

def test_node_process_forced_alive_delays_b2b_failure_detection(tmp_path) -> None:
    from tools.scheduler_lab.node_process import NodeHttpRunner

    class DummySink:
        def __init__(self) -> None:
            self.events = []
            self.closed = False

        def emit(self, event):
            self.events.append(event)

        def close(self) -> None:
            self.closed = True

    attempts = {"count": 0}

    def fail_then_success():
        attempts["count"] += 1
        if attempts["count"] <= 5:
            return HubHttpResponse(ok=False, status=0, payload={"error": "connection refused"}, elapsed_ms=0.1, base_url="http://hub-dead")
        return HubHttpResponse(ok=True, status=200, payload={"ok": True}, elapsed_ms=0.1, base_url="http://hub-live")

    sink = DummySink()
    runner = NodeHttpRunner(
        node={"node_id": "node-1"},
        sink=sink,
        b2bfailures=2,
        forced_alive_seconds=100,
        started_at=time.monotonic(),
    )

    response = runner.call("test.call", fail_then_success)

    assert response.status == 200
    assert attempts["count"] == 6
    assert runner.consecutive_transport_failures == 0
    assert any(event.get("b2b_detection_enabled") is False for event in sink.events)


def test_node_process_counts_b2b_failures_after_forced_alive(tmp_path) -> None:
    from tools.scheduler_lab.node_process import NodeHttpRunner, SELF_TERMINATED_B2B_FAILURES_EXIT_CODE

    class DummySink:
        def __init__(self) -> None:
            self.events = []
            self.closed = False

        def emit(self, event):
            self.events.append(event)

        def close(self) -> None:
            self.closed = True

    def transport_failure():
        return HubHttpResponse(ok=False, status=0, payload={"error": "connection refused"}, elapsed_ms=0.1, base_url="http://hub-dead")

    sink = DummySink()
    runner = NodeHttpRunner(
        node={"node_id": "node-1"},
        sink=sink,
        b2bfailures=2,
        forced_alive_seconds=10,
        started_at=0.0,
    )

    try:
        runner.call("test.call", transport_failure)
    except SystemExit as exc:
        assert exc.code == SELF_TERMINATED_B2B_FAILURES_EXIT_CODE
    else:
        raise AssertionError("expected b2b self-termination after forced-alive grace elapsed")

    assert sink.closed
    assert any(event.get("event") == "node.self_terminated.b2bfailures" for event in sink.events)

