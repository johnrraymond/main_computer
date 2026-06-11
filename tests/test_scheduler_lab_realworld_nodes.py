from __future__ import annotations

import json
import random

from tools.scheduler_lab.hub_client import HubClient, HubHttpResponse
from tools.scheduler_lab.node_list import SCHEMA, build_document, build_nodes, normalize_hub_base_urls
from tools.scheduler_lab.run_lab import build_arg_parser, effective_request_startup_mode, is_insufficient_credit_response, mark_assumed_prefunded_nodes, node_can_request, node_can_work, parse_funded_percent, parse_worktime_spec, select_nodes, should_send_startup_request


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
