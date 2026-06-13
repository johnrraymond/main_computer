from __future__ import annotations

import csv
import json
import random
import threading
import time
from argparse import Namespace

from tools.scheduler_lab.hub_client import HubClient, HubHttpResponse
from tools.scheduler_lab.node_list import SCHEMA, build_document, build_nodes, normalize_hub_base_urls
from tools.scheduler_lab.run_lab import (
    BehaviorLedger,
    ProcessLifecycleLedger,
    build_arg_parser,
    build_process_launch_progress_rollup,
    build_process_rollup,
    collect_final_process_phase_counts,
    collect_process_phase_counts,
    effective_request_startup_mode,
    format_process_launch_progress,
    format_process_phase_counts,
    format_process_rollup,
    is_insufficient_credit_response,
    mark_assumed_prefunded_nodes,
    node_can_request,
    node_can_work,
    parse_funded_percent,
    parse_warm_spec,
    parse_worktime_spec,
    resolve_lab_duration,
    apply_lab_duration_plan,
    new_process_rollup_stats,
    process_child_event_path,
    process_child_log_path,
    summarize_child_processes,
    process_parent_runtime_due_flags,
    process_phase_count_summary,
    record_process_phase_event,
    record_process_rollup_event,
    select_nodes,
    should_send_startup_request,
    write_process_rollup_files,
)


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
    monkeypatch.setenv("LAB_PARENT_STATUS_INTERVAL", "2")

    args = build_arg_parser().parse_args([])
    warm = parse_warm_spec(args.warm)

    assert args.execution_mode == "process"
    assert args.b2bfailures == 10
    assert args.forced_alive == 100
    assert args.http_timeout_seconds == 1
    assert args.parent_status_interval == 2
    assert warm is not None
    assert warm.mean_seconds == 2
    assert warm.sigma_seconds == 1


def test_warm_parser_allows_zero_for_immediate_wall() -> None:
    warm = parse_warm_spec("0mu,0sigma")

    assert warm is not None
    assert warm.mean_seconds == 0
    assert warm.sigma_seconds == 0


def test_lab_duration_explicit_cli_wins_over_worktime(monkeypatch) -> None:
    monkeypatch.delenv("LAB_DURATION_SECONDS", raising=False)
    monkeypatch.delenv("FORCED_ALIVE_SECONDS", raising=False)

    raw_argv = ["--duration-seconds", "42", "--worktime", "500mu,120sigma"]
    args = build_arg_parser().parse_args(raw_argv)
    args.worktime_distribution = parse_worktime_spec(args.worktime)

    plan = resolve_lab_duration(args, raw_argv=raw_argv)
    apply_lab_duration_plan(args, plan, raw_argv=raw_argv)

    assert plan.source == "explicit_cli"
    assert args.duration_seconds == 42.0
    assert args.worktime_derived_seconds == 1620.0
    assert args.forced_alive == 42.0
    assert args.forced_alive_source == "duration_window_default"


def test_lab_duration_env_wins_when_cli_duration_omitted(monkeypatch) -> None:
    monkeypatch.setenv("LAB_DURATION_SECONDS", "77")
    monkeypatch.delenv("FORCED_ALIVE_SECONDS", raising=False)

    raw_argv = ["--worktime", "500mu,120sigma"]
    args = build_arg_parser().parse_args(raw_argv)
    args.worktime_distribution = parse_worktime_spec(args.worktime)

    plan = resolve_lab_duration(args, raw_argv=raw_argv)
    apply_lab_duration_plan(args, plan, raw_argv=raw_argv)

    assert plan.source == "env"
    assert args.duration_seconds == 77.0
    assert args.worktime_derived_seconds == 1620.0
    assert args.forced_alive == 77.0


def test_lab_duration_derive_sentinels_bypass_compose_defaults(monkeypatch) -> None:
    monkeypatch.setenv("LAB_DURATION_SECONDS", "auto")
    monkeypatch.setenv("FORCED_ALIVE_SECONDS", "duration")

    raw_argv = ["--worktime", "50mu,25sigma"]
    args = build_arg_parser().parse_args(raw_argv)
    args.worktime_distribution = parse_worktime_spec(args.worktime)

    plan = resolve_lab_duration(args, raw_argv=raw_argv)
    apply_lab_duration_plan(args, plan, raw_argv=raw_argv)

    assert plan.source == "worktime_derived"
    assert plan.worktime_derived_seconds == 175.0
    assert args.duration_seconds == 900.0
    assert args.forced_alive == 900.0
    assert args.forced_alive_source == "duration_window_default"


def test_lab_duration_derives_from_worktime_with_15_minute_floor(monkeypatch) -> None:
    monkeypatch.delenv("LAB_DURATION_SECONDS", raising=False)
    monkeypatch.delenv("FORCED_ALIVE_SECONDS", raising=False)

    raw_argv = ["--worktime", "100mu,30sigma"]
    args = build_arg_parser().parse_args(raw_argv)
    args.worktime_distribution = parse_worktime_spec(args.worktime)

    plan = resolve_lab_duration(args, raw_argv=raw_argv)
    apply_lab_duration_plan(args, plan, raw_argv=raw_argv)

    assert plan.source == "worktime_derived"
    assert plan.worktime_derived_seconds == 330.0
    assert args.duration_seconds == 900.0
    assert args.forced_alive == 900.0


def test_lab_duration_derives_long_window_from_slow_worktime(monkeypatch) -> None:
    monkeypatch.delenv("LAB_DURATION_SECONDS", raising=False)
    monkeypatch.delenv("FORCED_ALIVE_SECONDS", raising=False)

    raw_argv = ["--worktime", "500mu,120sigma"]
    args = build_arg_parser().parse_args(raw_argv)
    args.worktime_distribution = parse_worktime_spec(args.worktime)

    plan = resolve_lab_duration(args, raw_argv=raw_argv)
    apply_lab_duration_plan(args, plan, raw_argv=raw_argv)

    assert plan.source == "worktime_derived"
    assert plan.worktime_derived_seconds == 1620.0
    assert args.duration_seconds == 1620.0
    assert args.forced_alive == 1620.0


def test_lab_duration_defaults_to_15_minutes_without_worktime(monkeypatch) -> None:
    monkeypatch.delenv("LAB_DURATION_SECONDS", raising=False)
    monkeypatch.delenv("FORCED_ALIVE_SECONDS", raising=False)

    raw_argv: list[str] = []
    args = build_arg_parser().parse_args(raw_argv)
    args.worktime_distribution = parse_worktime_spec(args.worktime)

    plan = resolve_lab_duration(args, raw_argv=raw_argv)
    apply_lab_duration_plan(args, plan, raw_argv=raw_argv)

    assert plan.source == "default_minimum"
    assert args.duration_seconds == 900.0
    assert args.forced_alive == 900.0


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
        if attempts["count"] == 1:
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

    first_response = runner.call("test.call", fail_then_success)
    second_response = runner.call("test.call", fail_then_success)

    assert first_response.status == 0
    assert second_response.status == 200
    assert attempts["count"] == 2
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

    first_response = runner.call("test.call", transport_failure)
    assert first_response.status == 0
    assert not sink.closed

    try:
        runner.call("test.call", transport_failure)
    except SystemExit as exc:
        assert exc.code == SELF_TERMINATED_B2B_FAILURES_EXIT_CODE
    else:
        raise AssertionError("expected b2b self-termination after enough post-grace failures")

    assert sink.closed
    assert any(event.get("event") == "node.self_terminated.b2bfailures" for event in sink.events)



def test_node_process_call_once_does_not_trap_on_transport_failure() -> None:
    from tools.scheduler_lab.node_process import NodeHttpRunner

    class DummySink:
        def __init__(self) -> None:
            self.events = []

        def emit(self, event):
            self.events.append(event)

        def close(self) -> None:
            pass

    attempts = {"count": 0}

    def transport_failure():
        attempts["count"] += 1
        return HubHttpResponse(ok=False, status=0, payload={"error": "connection refused"}, elapsed_ms=0.1, base_url="http://hub-dead")

    sink = DummySink()
    runner = NodeHttpRunner(
        node={"node_id": "node-1"},
        sink=sink,
        b2bfailures=0,
        forced_alive_seconds=0,
        started_at=0.0,
    )

    response = runner.call_once("test.call_once", transport_failure)

    assert response.status == 0
    assert attempts["count"] == 1
    assert any(event.get("event") == "test.call_once" and event.get("status") == 0 for event in sink.events)
    assert any(event.get("event") == "node.transport_failure" for event in sink.events)


def test_process_parent_phase_counters_are_node_oriented() -> None:
    phase_nodes: dict[str, set[str]] = {}
    events = [
        {"event": "node.process.started", "node_id": "node-1"},
        {"event": "node.process.warm_finished", "node_id": "node-1"},
        {"event": "requester.request.startup_surge.attempted", "node_id": "node-1"},
        {"event": "requester.request.startup_surge.pre_bootstrap", "node_id": "node-1", "status": 0},
        {"event": "requester.request.startup_surge.pre_bootstrap", "node_id": "node-1", "status": 0},
        {"event": "requester.request.startup_surge.pre_bootstrap", "node_id": "node-1", "status": 200},
        {"event": "node.funding.bootstrap.attempted", "node_id": "node-1"},
        {"event": "node.funding.bootstrap.assumed_prefunded", "node_id": "node-1"},
        {"event": "node.funding.balance_checked", "node_id": "node-1", "status": 200},
        {"event": "worker.register.attempted", "node_id": "node-1"},
        {"event": "worker.register", "node_id": "node-1", "status": 200},
        {"event": "node.process.runtime_entered", "node_id": "node-1"},
        {"event": "node.self_terminated.b2bfailures", "node_id": "node-1"},
    ]

    for event in events:
        record_process_phase_event(event, phase_nodes)

    counts = process_phase_count_summary(phase_nodes)

    assert counts["nodes_started"] == 1
    assert counts["warm_finished"] == 1
    assert counts["startup_request_attempted"] == 1
    assert counts["startup_request_transport_failures"] == 1
    assert counts["startup_request_http_response"] == 1
    assert counts["bootstrap_attempted"] == 1
    assert counts["bootstrap_assumed_prefunded"] == 1
    assert counts["bootstrap_balance_checked"] == 1
    assert counts["worker_register_attempted"] == 1
    assert counts["worker_register_http_response"] == 1
    assert counts["entered_runtime_loop"] == 1
    assert counts["self_terminated_b2bfailures"] == 1
    assert "startup_req_transport_failures=1" in format_process_phase_counts(counts)



def test_process_parent_rollup_files_are_written_for_experiment_timeline(tmp_path) -> None:
    class DummyProcess:
        def __init__(self, code):
            self.code = code

        def poll(self):
            return self.code

    phase_nodes: dict[str, set[str]] = {}
    rollup_stats = new_process_rollup_stats()
    events = [
        {"event": "node.process.started", "node_id": "node-1"},
        {"event": "node.process.warm_finished", "node_id": "node-1"},
        {
            "event": "requester.request.startup_surge.pre_bootstrap",
            "node_id": "node-1",
            "status": 200,
            "hub_base_url": "http://host.docker.internal:8870",
        },
        {
            "event": "worker.poll",
            "node_id": "node-1",
            "status": 0,
            "hub_base_url": "http://host.docker.internal:8874",
        },
        {
            "event": "node.transport_failure",
            "node_id": "node-1",
            "hub_base_url": "http://host.docker.internal:8874",
        },
        {"event": "node.self_terminated.b2bfailures", "node_id": "node-2"},
    ]

    for event in events:
        record_process_phase_event(event, phase_nodes)
        record_process_rollup_event(event, rollup_stats)

    rollup = build_process_rollup(
        run_id="20260612-010203",
        started_at=time.monotonic() - 61.0,
        node_count=2,
        assumed_prefunded_count=1,
        children=[({"node_id": "node-1"}, DummyProcess(None)), ({"node_id": "node-2"}, DummyProcess(75))],
        phase_nodes=phase_nodes,
        rollup_stats=rollup_stats,
    )
    rollup.update(
        {
            "final_drain_quiet_seconds": 0.5,
            "final_drain_max_seconds": 2.0,
            "final_drain_elapsed_seconds": 0.587,
            "final_drain_checks": 8,
            "final_drain_timed_out": False,
            "final_drain_passes": 1,
            "final_drain_events_scanned": 1464,
            "final_unread_event_bytes": 0,
        }
    )

    rollups_jsonl = tmp_path / "scheduler-lab-process-rollups.jsonl"
    latest_json = tmp_path / "scheduler-lab-process-rollup-latest.json"
    rollups_csv = tmp_path / "scheduler-lab-process-rollups.csv"
    write_process_rollup_files(
        rollup,
        rollups_jsonl=rollups_jsonl,
        latest_rollup_json=latest_json,
        rollups_csv=rollups_csv,
    )

    assert rollups_jsonl.exists()
    assert latest_json.exists()
    assert rollups_csv.exists()
    loaded = json.loads(latest_json.read_text(encoding="utf-8"))
    assert loaded["run_id"] == "20260612-010203"
    assert loaded["nodes_alive"] == 1
    assert loaded["nodes_exited"] == 1
    assert loaded["self_terminated_b2bfailures"] == 1
    assert loaded["http_status_counts"]["0"] == 1
    assert loaded["http_status_counts"]["200"] == 1
    assert loaded["hub_counts"]["8874"] == 2
    assert loaded["endpoint_counts"]["workers_poll"] == 1
    assert loaded["endpoint_counts"]["transport_failures"] == 1
    assert loaded["transport_failures"] == 1
    assert loaded["market_http_responses"] == 1
    assert loaded["transport_failure_ratio"] == 0.5
    formatted = format_process_rollup(loaded)
    assert "alive=1" in formatted
    assert "http0=1" in formatted
    assert "transport=1" in formatted
    assert "market_http=1" in formatted
    with rollups_csv.open(encoding="utf-8", newline="") as csv_handle:
        rows = list(csv.DictReader(csv_handle))
    assert rows
    csv_header = list(rows[0].keys())
    assert "event_counts_json" in csv_header
    assert len(csv_header) == len(set(csv_header))
    assert rows[0]["final_drain_quiet_seconds"] == "0.5"
    assert rows[0]["final_drain_max_seconds"] == "2.0"
    assert rows[0]["final_drain_elapsed_seconds"] == "0.587"
    assert rows[0]["final_drain_checks"] == "8"
    assert rows[0]["final_drain_timed_out"] == "False"
    assert rows[0]["final_drain_passes"] == "1"
    assert rows[0]["final_drain_events_scanned"] == "1464"
    assert rows[0]["final_unread_event_bytes"] == "0"



def test_process_launch_progress_rollup_is_parent_only() -> None:
    class DummyProcess:
        def __init__(self, code):
            self.code = code

        def poll(self):
            return self.code

    rollup = build_process_launch_progress_rollup(
        run_id="20260612-010203",
        started_at=time.monotonic() - 10.0,
        node_count=3,
        assumed_prefunded_count=2,
        children=[
            ({"node_id": "node-1"}, DummyProcess(None)),
            ({"node_id": "node-2"}, DummyProcess(75)),
        ],
    )

    assert rollup["children_launched"] == 2
    assert rollup["children_remaining"] == 1
    assert rollup["nodes_started"] == 2
    assert rollup["phase_counts"]["nodes_started"] == 2
    assert rollup["nodes_alive"] == 1
    assert rollup["nodes_exited"] == 1
    assert rollup["event_counts"] == {}
    assert rollup["http_status_counts"] == {}
    assert rollup["phase_counts"]["warm_finished"] == 0
    assert "launched=2/3" in format_process_launch_progress(rollup)


def test_process_event_path_matches_child_sink_naming_without_touching_files(tmp_path) -> None:
    node = {"node_id": "worker/with space"}
    path = process_child_event_path(tmp_path, node, 7)

    assert path == tmp_path / "node-process-00007-worker_with_space.events.jsonl"
    assert not path.exists()


def test_process_child_log_path_matches_event_path_stem_without_touching_files(tmp_path) -> None:
    node = {"node_id": "worker/with space"}
    path = process_child_log_path(tmp_path, node, 7, run_id="run/with space")

    assert path == tmp_path / "node-process-run_with_space-00007-worker_with_space.log"
    assert not path.exists()


def test_summarize_child_processes_surfaces_startup_crash_log_tail(tmp_path) -> None:
    class DummyProcess:
        def __init__(self, code):
            self.code = code

        def poll(self):
            return self.code

    log_path = tmp_path / "node-process-run-00000-worker-1.log"
    log_path.write_text("before\nTraceback: boom\n", encoding="utf-8")
    summary = summarize_child_processes(
        [({"node_id": "worker-1"}, DummyProcess(1))],
        event_paths=[tmp_path / "node-process-run-00000-worker-1.events.jsonl"],
        log_paths=[log_path],
    )

    assert summary["child_startup_failure"] is True
    assert summary["child_startup_failure_count"] == 1
    assert summary["child_process_exit_codes"] == {"1": 1}
    assert summary["child_processes_without_event_file"] == 1
    assert "Traceback: boom" in summary["child_log_tail_samples"][0]["log_tail"]


def test_process_rollup_scanner_uses_known_paths_and_offsets(tmp_path) -> None:
    selected = tmp_path / "node-process-00000-node-1.events.jsonl"
    ignored = tmp_path / "node-process-00001-node-2.events.jsonl"
    selected.write_text(
        json.dumps({"event": "node.process.warm_finished", "node_id": "node-1"}) + "\n"
        + json.dumps({"event": "worker.poll", "node_id": "node-1", "status": 200, "hub_base_url": "http://host.docker.internal:8872"}) + "\n",
        encoding="utf-8",
    )
    ignored.write_text(
        json.dumps({"event": "node.self_terminated.b2bfailures", "node_id": "node-2"}) + "\n",
        encoding="utf-8",
    )
    offsets: dict = {}
    phase_nodes: dict[str, set[str]] = {}
    stats = new_process_rollup_stats()
    scan_state: dict = {}

    counts = collect_process_phase_counts(
        tmp_path,
        offsets,
        phase_nodes,
        stats,
        event_paths=[selected],
        max_scan_seconds=2.0,
        scan_state=scan_state,
    )

    assert counts["warm_finished"] == 1
    assert counts["self_terminated_b2bfailures"] == 0
    assert stats["endpoint_counts"]["workers_poll"] == 1
    assert stats["hub_counts"]["8872"] == 1
    assert scan_state["rollup_events_scanned"] == 2

    with selected.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"event": "node.process.runtime_entered", "node_id": "node-1"}) + "\n")

    counts = collect_process_phase_counts(tmp_path, offsets, phase_nodes, stats, event_paths=[selected], scan_state=scan_state)

    assert counts["entered_runtime_loop"] == 1
    assert stats["event_counts"]["node.process.warm_finished"] == 1
    assert scan_state["rollup_events_scanned"] == 1



def test_behavior_ledger_transport_storm_probes_hub_without_banning_actor() -> None:
    ledger = BehaviorLedger()

    for index in range(20):
        ledger.record(
            {
                "event": "worker.register",
                "node_id": "node-1",
                "account_id": "lab-account-node-1",
                "status": 0,
                "ok": False,
                "hub_base_url": "http://host.docker.internal:8874",
                "ts": f"2026-06-12T19:00:{index:02d}+00:00",
            }
        )

    snapshot = ledger.snapshot()

    assert snapshot["tat_counts"]["transport.no_http_response"] == 20
    assert snapshot["behavior_hubs_probe"] == 1
    assert snapshot["behavior_nodes_temporary_ban"] == 0
    assert all(
        not (item["subject_kind"] == "node" and item["behavior_state"] == "temporary_ban")
        for item in snapshot["top_behavior"]
    )


def test_behavior_ledger_does_not_double_count_synthetic_transport_failure_event() -> None:
    ledger = BehaviorLedger()

    concrete_failure = {
        "event": "worker.register",
        "node_id": "node-1",
        "account_id": "lab-account-node-1",
        "status": 0,
        "ok": False,
        "hub_base_url": "http://host.docker.internal:8874",
        "ts": "2026-06-12T19:00:00+00:00",
    }
    synthetic_companion = dict(concrete_failure)
    synthetic_companion["event"] = "node.transport_failure"

    ledger.record(concrete_failure)
    ledger.record(synthetic_companion)

    snapshot = ledger.snapshot()

    assert snapshot["tat_counts"] == {"transport.no_http_response": 1}


def test_behavior_ledger_does_not_count_status_zero_request_as_scheduler_rejection() -> None:
    ledger = BehaviorLedger()

    ledger.record(
        {
            "event": "requester.request.startup_surge.pre_bootstrap",
            "node_id": "requester-1",
            "account_id": "lab-account-requester-1",
            "status": 0,
            "ok": False,
            "hub_base_url": "http://host.docker.internal:8872",
            "ts": "2026-06-12T19:00:00+00:00",
        }
    )

    snapshot = ledger.snapshot()

    assert snapshot["tat_counts"] == {"transport.no_http_response": 1}



def test_lifecycle_distinguishes_request_transport_failure_from_scheduler_rejection() -> None:
    ledger = ProcessLifecycleLedger()

    ledger.record(
        {
            "ts": "2026-06-12T19:00:00+00:00",
            "event": "requester.request.attempted",
            "node_id": "requester-1",
            "requester_node_id": "requester-1",
            "request_index": 1,
            "lab_request_key": "requester-1-1",
        }
    )
    ledger.record(
        {
            "ts": "2026-06-12T19:00:01+00:00",
            "event": "requester.request.startup_surge.pre_bootstrap",
            "node_id": "requester-1",
            "requester_node_id": "requester-1",
            "request_index": 1,
            "lab_request_key": "requester-1-1",
            "status": 0,
            "ok": False,
            "hub_base_url": "http://host.docker.internal:8872",
        }
    )
    ledger.record(
        {
            "ts": "2026-06-12T19:00:02+00:00",
            "event": "requester.request.attempted",
            "node_id": "requester-2",
            "requester_node_id": "requester-2",
            "request_index": 1,
            "lab_request_key": "requester-2-1",
        }
    )
    ledger.record(
        {
            "ts": "2026-06-12T19:00:03+00:00",
            "event": "requester.request.startup_surge.pre_bootstrap",
            "node_id": "requester-2",
            "requester_node_id": "requester-2",
            "request_index": 1,
            "lab_request_key": "requester-2-1",
            "request_id": "hub-request-2",
            "status": 200,
            "ok": True,
            "hub_base_url": "http://host.docker.internal:8872",
        }
    )

    snapshot = ledger.snapshot()

    assert snapshot["requests_attempted_total"] == 2
    assert snapshot["requests_accepted_total"] == 1
    assert snapshot["requests_rejected_total"] == 0
    assert snapshot["requests_transport_failed_total"] == 1
    assert snapshot["open_requests_total"] == 1
    assert snapshot["requests_transport_failed_delta"] == 1


def test_lifecycle_does_not_open_failed_200_request() -> None:
    ledger = ProcessLifecycleLedger()
    ledger.record(
        {
            "ts": "2026-06-12T20:33:02+00:00",
            "event": "requester.request.attempted",
            "node_id": "worker-0025",
            "requester_node_id": "worker-0025",
            "request_index": 1,
            "lab_request_key": "worker-0025-1",
        }
    )
    ledger.record(
        {
            "ts": "2026-06-12T20:33:03+00:00",
            "event": "requester.request.startup_surge.pre_bootstrap",
            "node_id": "worker-0025",
            "requester_node_id": "worker-0025",
            "request_index": 1,
            "lab_request_key": "worker-0025-1",
            "request_id": "hub-failed-request",
            "request_state": "failed",
            "status": 200,
            "ok": True,
            "hub_base_url": "http://host.docker.internal:8872",
            "response_summary": {
                "ok": True,
                "request": {
                    "request_id": "hub-failed-request",
                    "state": "failed",
                    "error": "Insufficient Compute Credits for account lab-account-worker-0025: 0 credits available, 1 credits required.",
                },
                "lease": None,
            },
        }
    )

    snapshot = ledger.snapshot()

    assert snapshot["requests_attempted_total"] == 1
    assert snapshot["requests_accepted_total"] == 0
    assert snapshot["requests_failed_total"] == 1
    assert snapshot["requests_rejected_total"] == 0
    assert snapshot["requests_transport_failed_total"] == 0
    assert snapshot["open_requests_total"] == 0
    assert snapshot["requests_failed_delta"] == 1


def test_behavior_ledger_does_not_call_failed_200_request_accepted() -> None:
    ledger = BehaviorLedger()
    ledger.record(
        {
            "ts": "2026-06-12T20:33:03+00:00",
            "event": "requester.request.startup_surge.pre_bootstrap",
            "node_id": "worker-0025",
            "actor_node_id": "worker-0025",
            "actor_account_id": "lab-account-worker-0025",
            "requester_node_id": "worker-0025",
            "request_index": 1,
            "lab_request_key": "worker-0025-1",
            "request_id": "hub-failed-request",
            "request_state": "failed",
            "status": 200,
            "ok": True,
            "hub_base_url": "http://host.docker.internal:8872",
            "response_summary": {
                "ok": True,
                "request": {
                    "request_id": "hub-failed-request",
                    "state": "failed",
                    "error": "Insufficient Compute Credits for account lab-account-worker-0025: 0 credits available, 1 credits required.",
                },
                "lease": None,
            },
        }
    )

    snapshot = ledger.snapshot()
    assert snapshot["tat_counts"].get("positive.http_response") == 1
    assert snapshot["tat_counts"].get("economic.insufficient_credit") == 1
    assert "positive.request_accepted" not in snapshot["tat_counts"]
    assert "scheduler.request_rejected" not in snapshot["tat_counts"]


def test_final_process_rollup_waits_for_late_child_event_file_write(tmp_path) -> None:
    events_path = tmp_path / "node-process-00000-late.events.jsonl"
    offsets: dict = {}
    phase_nodes: dict[str, set[str]] = {}
    stats = new_process_rollup_stats()
    scan_state: dict = {}
    cursor: dict = {}

    def write_late_event() -> None:
        time.sleep(0.05)
        events_path.write_text(
            json.dumps({"event": "node.self_terminated.b2bfailures", "node_id": "late-node"}) + "\n",
            encoding="utf-8",
        )

    writer = threading.Thread(target=write_late_event)
    writer.start()
    try:
        counts = collect_final_process_phase_counts(
            tmp_path,
            offsets,
            phase_nodes,
            stats,
            event_paths=[events_path],
            scan_cursor_state=cursor,
            scan_state=scan_state,
            drain_seconds=0.1,
            drain_max_seconds=1.0,
            max_passes=2,
        )
    finally:
        writer.join(timeout=1.0)

    assert counts["self_terminated_b2bfailures"] == 1
    assert stats["event_counts"]["node.self_terminated.b2bfailures"] == 1
    assert scan_state["final_drain_events_scanned"] == 1
    assert scan_state["final_unread_event_bytes"] == 0
    assert scan_state["rollup_partial"] is False


def test_process_rollup_scanner_unlimited_final_pass_reads_all_events(tmp_path) -> None:
    noisy = tmp_path / "node-process-00000-noisy.events.jsonl"
    noisy.write_text(
        "".join(
            json.dumps(
                {
                    "event": "worker.heartbeat",
                    "node_id": "noisy",
                    "status": 0,
                    "hub_base_url": "http://host.docker.internal:8874",
                }
            )
            + "\n"
            for _ in range(25)
        ),
        encoding="utf-8",
    )
    offsets: dict = {}
    phase_nodes: dict[str, set[str]] = {}
    stats = new_process_rollup_stats()
    scan_state: dict = {}
    cursor: dict = {}

    counts = collect_process_phase_counts(
        tmp_path,
        offsets,
        phase_nodes,
        stats,
        event_paths=[noisy],
        max_events_per_file=0,
        scan_cursor_state=cursor,
        scan_state=scan_state,
    )

    assert counts["nodes_started"] == 0
    assert stats["event_counts"]["worker.heartbeat"] == 25
    assert scan_state["rollup_events_scanned"] == 25
    assert scan_state["rollup_files_limited"] == 0
    assert scan_state["rollup_partial"] is False
    assert scan_state["rollup_events_per_file_limit"] == 0


def test_process_rollup_scanner_samples_across_noisy_files(tmp_path) -> None:
    noisy = tmp_path / "node-process-00000-noisy.events.jsonl"
    quiet_runtime = tmp_path / "node-process-00001-runtime.events.jsonl"
    quiet_exit = tmp_path / "node-process-00002-exit.events.jsonl"
    noisy.write_text(
        "".join(
            json.dumps(
                {
                    "event": "worker.heartbeat",
                    "node_id": "noisy",
                    "status": 0,
                    "hub_base_url": "http://host.docker.internal:8874",
                }
            )
            + "\n"
            for _ in range(25)
        ),
        encoding="utf-8",
    )
    quiet_runtime.write_text(
        json.dumps({"event": "node.process.runtime_entered", "node_id": "runtime"}) + "\n",
        encoding="utf-8",
    )
    quiet_exit.write_text(
        json.dumps({"event": "node.self_terminated.b2bfailures", "node_id": "exit"}) + "\n",
        encoding="utf-8",
    )
    offsets: dict = {}
    phase_nodes: dict[str, set[str]] = {}
    stats = new_process_rollup_stats()
    scan_state: dict = {}
    cursor: dict = {}

    counts = collect_process_phase_counts(
        tmp_path,
        offsets,
        phase_nodes,
        stats,
        event_paths=[noisy, quiet_runtime, quiet_exit],
        max_events_per_file=5,
        scan_cursor_state=cursor,
        scan_state=scan_state,
    )

    assert stats["event_counts"]["worker.heartbeat"] == 5
    assert counts["entered_runtime_loop"] == 1
    assert counts["self_terminated_b2bfailures"] == 1
    assert scan_state["rollup_files_total"] == 3
    assert scan_state["rollup_files_scanned"] == 3
    assert scan_state["rollup_files_limited"] == 1
    assert scan_state["rollup_partial"] is True
    assert "per_file_event_budget" in scan_state["rollup_partial_reason"]
    assert cursor["next_index"] == 0


def test_parent_status_interval_does_not_drive_rollup_scans() -> None:
    flags = process_parent_runtime_due_flags(
        now=20.0,
        parent_status_interval=2.0,
        next_parent_status_at=20.0,
        parent_rollup_interval=60.0,
        next_parent_rollup_at=60.0,
    )

    assert flags["status_due"] is True
    assert flags["rollup_due"] is False
    assert flags["scan_due"] is False

    flags = process_parent_runtime_due_flags(
        now=60.0,
        parent_status_interval=2.0,
        next_parent_status_at=60.0,
        parent_rollup_interval=60.0,
        next_parent_rollup_at=60.0,
    )

    assert flags["status_due"] is True
    assert flags["rollup_due"] is True
    assert flags["scan_due"] is True


def test_node_process_bootstrap_top_up_does_not_block_runtime_entry() -> None:
    import inspect

    from tools.scheduler_lab import node_process

    source = inspect.getsource(node_process.top_up_account_to)

    assert 'runner.call_once("node.funding.balance_checked"' in source
    assert 'issue = runner.call_once(' in source
    assert '"node.funding.issued"' in source
    assert "node.funding.top_up_deferred.transport_failure" in source
    assert "runner.call(" not in source


def test_node_process_registration_retry_does_not_block_runtime_entry() -> None:
    import inspect

    from tools.scheduler_lab import node_process

    source = inspect.getsource(node_process.run_node_process)

    assert 'register_response = runner.call_once("worker.register"' in source
    assert "worker_registration_attempted = True" in source
    assert "worker_registered = register_response.status != 0 and register_response.ok" in source
    assert source.index('register_response = runner.call_once("worker.register"') < source.index('"node.process.runtime_entered"')
    assert "if worker_enabled and not worker_registered and now >= next_register:" in source


def test_node_process_worker_pipeline_is_not_gated_on_observed_register_success() -> None:
    import inspect

    from tools.scheduler_lab import node_process

    source = inspect.getsource(node_process.run_node_process)

    assert "if worker_enabled and worker_registration_attempted and now >= next_heartbeat:" in source
    assert "if worker_enabled and worker_registration_attempted and now >= next_poll:" in source
    assert "if worker_enabled and worker_registered and now >= next_heartbeat:" not in source
    assert "if worker_enabled and worker_registered and now >= next_poll:" not in source


def test_process_defaults_keep_observation_window_before_b2b_exit(monkeypatch) -> None:
    from tools.scheduler_lab import node_process

    monkeypatch.delenv("FORCED_ALIVE_SECONDS", raising=False)
    monkeypatch.delenv("WORKER_REGISTER_RETRY_INTERVAL_MS", raising=False)

    parent_args = build_arg_parser().parse_args([])
    child_args = node_process.build_arg_parser().parse_args(["--node-list", "nodes.jsonl", "--node-index", "0"])

    assert parent_args.forced_alive is None
    parent_args.worktime_distribution = parse_worktime_spec(parent_args.worktime)
    duration_plan = resolve_lab_duration(parent_args, raw_argv=[])
    apply_lab_duration_plan(parent_args, duration_plan, raw_argv=[])
    assert parent_args.forced_alive == 900.0
    assert child_args.forced_alive == 900.0
    assert parent_args.worker_register_retry_interval_ms == 1000.0
    assert child_args.worker_register_retry_interval_ms == 1000.0


def test_node_process_accepts_compose_duration_sentinels(monkeypatch) -> None:
    from tools.scheduler_lab import node_process

    monkeypatch.setenv("LAB_DURATION_SECONDS", "auto")
    monkeypatch.setenv("FORCED_ALIVE_SECONDS", "duration")
    monkeypatch.setenv("LAB_WORKTIME", "50mu,25sigma")

    args = node_process.build_arg_parser().parse_args(["--node-list", "nodes.jsonl", "--node-index", "0"])

    assert args.duration_seconds == 900.0
    assert args.forced_alive == 900.0


def test_node_process_cli_duration_overrides_compose_auto_env(monkeypatch) -> None:
    from tools.scheduler_lab import node_process

    monkeypatch.setenv("LAB_DURATION_SECONDS", "auto")
    monkeypatch.setenv("FORCED_ALIVE_SECONDS", "duration")
    monkeypatch.setenv("LAB_WORKTIME", "50mu,25sigma")

    args = node_process.build_arg_parser().parse_args(
        [
            "--node-list",
            "nodes.jsonl",
            "--node-index",
            "0",
            "--duration-seconds",
            "123",
            "--forced-alive",
            "123",
        ]
    )

    assert args.duration_seconds == 123.0
    assert args.forced_alive == 123.0


def test_node_process_sends_startup_surge_before_bootstrap() -> None:
    import inspect

    from tools.scheduler_lab import node_process

    source = inspect.getsource(node_process.run_node_process)

    assert "startup_request_sent = False" in source
    assert source.index('"requester.request.startup_surge.pre_bootstrap"') < source.index("bootstrap_node_funding_sync")
    assert "retry_transport=False" in source
    assert "startup_request_sent = startup_response.status != 0" in source
    assert "and not startup_request_sent" in source


def test_assumed_prefunded_process_bootstrap_probes_balance_without_issuing() -> None:
    from tools.scheduler_lab.node_process import bootstrap_node_funding_sync

    class DummySink:
        def __init__(self) -> None:
            self.events: list[dict] = []

        def emit(self, event):
            self.events.append(event)

    class DummyClient:
        def __init__(self) -> None:
            self.balance_account_ids: list[str] = []
            self.issued = False

        def get_credit_balance(self, account_id: str) -> HubHttpResponse:
            self.balance_account_ids.append(account_id)
            return HubHttpResponse(
                ok=True,
                status=200,
                payload={"account": {"account_id": account_id, "available_credits": 8}},
                elapsed_ms=1.0,
                base_url="http://hub-live",
            )

        def issue_credits(self, **_kwargs):
            self.issued = True
            raise AssertionError("assumed-prefunded bootstrap must not issue credits")

    class DummyRunner:
        def __init__(self) -> None:
            self.call_once_events: list[str] = []
            self.call_events: list[str] = []

        def call_once(self, event_name: str, func, *args, **kwargs) -> HubHttpResponse:
            self.call_once_events.append(event_name)
            return func(*args, **kwargs)

        def call(self, event_name: str, func, *args, **kwargs) -> HubHttpResponse:
            self.call_events.append(event_name)
            return func(*args, **kwargs)

    node = {
        "node_id": "worker-0001",
        "account_id": "lab-account-worker-0001",
        "initial_credits": 8,
        "_assumed_prefunded": True,
    }
    args = Namespace(bootstrap_funding=True, funded=90.0, account_id_prefix="lab-account")
    sink = DummySink()
    client = DummyClient()
    runner = DummyRunner()

    bootstrap_node_funding_sync(node, args=args, sink=sink, client=client, runner=runner)

    assert client.balance_account_ids == ["lab-account-worker-0001"]
    assert client.issued is False
    assert runner.call_once_events == ["node.funding.balance_checked"]
    assert runner.call_events == []
    assumed_events = [event for event in sink.events if event.get("event") == "node.funding.bootstrap.assumed_prefunded"]
    assert assumed_events
    assert assumed_events[0]["balance_status"] == 200


def test_session_scoped_process_child_event_path_without_touching_files(tmp_path) -> None:
    node = {"node_id": "worker/with space"}
    path = process_child_event_path(tmp_path, node, 7, run_id="20260612T010203Z-99-abcdef12")

    assert path == tmp_path / "node-process-20260612T010203Z-99-abcdef12-00007-worker_with_space.events.jsonl"
    assert not path.exists()


def test_process_rollup_reconstructs_request_lifecycle_and_latency() -> None:
    class DummyProcess:
        def __init__(self, code):
            self.code = code

        def poll(self):
            return self.code

    stats = new_process_rollup_stats()
    phase_nodes: dict[str, set[str]] = {}
    events = [
        {
            "ts": "2026-06-11T00:00:00+00:00",
            "event": "requester.request.attempted",
            "node_id": "requester-1",
            "actor_node_id": "requester-1",
            "actor_account_id": "account-requester-1",
            "lab_request_key": "requester-1-1",
            "request_index": 1,
            "requester_node_id": "requester-1",
        },
        {
            "ts": "2026-06-11T00:00:01+00:00",
            "event": "requester.request.submitted",
            "node_id": "requester-1",
            "actor_node_id": "requester-1",
            "actor_account_id": "account-requester-1",
            "lab_request_key": "requester-1-1",
            "request_id": "hub-request-1",
            "ok": True,
            "status": 200,
            "hub_base_url": "http://hub.example:8872",
            "response_summary": {"request": {"request_id": "hub-request-1"}},
        },
        {
            "ts": "2026-06-11T00:00:10+00:00",
            "event": "worker.poll",
            "node_id": "worker-1",
            "actor_node_id": "worker-1",
            "actor_account_id": "account-worker-1",
            "worker_node_id": "worker-1",
            "request_id": "hub-request-1",
            "lease_id": "lease-1",
            "ok": True,
            "status": 200,
            "hub_base_url": "http://hub.example:8872",
            "response_summary": {"lease": {"request_id": "hub-request-1", "lease_id": "lease-1"}},
        },
        {
            "ts": "2026-06-11T00:00:11+00:00",
            "event": "worker.execution.started",
            "node_id": "worker-1",
            "actor_node_id": "worker-1",
            "worker_node_id": "worker-1",
            "request_id": "hub-request-1",
            "lease_id": "lease-1",
        },
        {
            "ts": "2026-06-11T00:00:21+00:00",
            "event": "worker.execution.finished",
            "node_id": "worker-1",
            "actor_node_id": "worker-1",
            "worker_node_id": "worker-1",
            "request_id": "hub-request-1",
            "lease_id": "lease-1",
        },
        {
            "ts": "2026-06-11T00:00:22+00:00",
            "event": "worker.result.submitted",
            "node_id": "worker-1",
            "actor_node_id": "worker-1",
            "worker_node_id": "worker-1",
            "request_id": "hub-request-1",
            "lease_id": "lease-1",
            "ok": True,
            "status": 200,
            "hub_base_url": "http://hub.example:8872",
        },
    ]

    for event in events:
        record_process_rollup_event(event, stats)

    rollup = build_process_rollup(
        run_id="20260612T010203Z-99-abcdef12",
        started_at=time.monotonic() - 30.0,
        node_count=2,
        assumed_prefunded_count=0,
        children=[({"node_id": "requester-1"}, DummyProcess(None)), ({"node_id": "worker-1"}, DummyProcess(None))],
        phase_nodes=phase_nodes,
        rollup_stats=stats,
    )

    assert rollup["schema_version"] == 3
    assert rollup["rollup_source"] == "child_event_scan"
    assert rollup["requests_attempted_total"] == 1
    assert rollup["requests_accepted_total"] == 1
    assert rollup["requests_leased_total"] == 1
    assert rollup["requests_settled_total"] == 1
    assert rollup["open_requests_total"] == 0
    assert rollup["queue_wait_seconds_p50"] == 9.0
    assert rollup["work_seconds_p50"] == 10.0
    assert rollup["settle_seconds_p50"] == 21.0
    assert rollup["settlement_metrics_representative"] is True
    assert rollup["lab_interpretation"] == "settlement_healthy"
    assert rollup["behavior"]["behavior_state_counts"]


def test_process_rollup_behavior_classifier_distinguishes_transport_dominated() -> None:
    class DummyProcess:
        def __init__(self, code):
            self.code = code

        def poll(self):
            return self.code

    stats = new_process_rollup_stats()
    phase_nodes: dict[str, set[str]] = {}
    for index in range(12):
        event = {
            "ts": f"2026-06-11T00:00:{index:02d}+00:00",
            "event": "worker.poll",
            "node_id": f"node-{index}",
            "actor_node_id": f"node-{index}",
            "actor_account_id": f"account-{index}",
            "status": 0,
            "ok": False,
            "hub_base_url": "http://dead-hub.example:8874",
        }
        record_process_rollup_event(event, stats)

    rollup = build_process_rollup(
        run_id="20260612T010203Z-99-abcdef12",
        started_at=time.monotonic() - 30.0,
        node_count=12,
        assumed_prefunded_count=0,
        children=[({"node_id": "node-0"}, DummyProcess(None))],
        phase_nodes=phase_nodes,
        rollup_stats=stats,
    )

    assert rollup["transport_dominated"] is True
    assert rollup["settlement_metrics_representative"] is False
    assert rollup["lab_interpretation"] == "transport_dominated"
    assert rollup["behavior_hubs_probe"] == 1
    assert rollup["top_behavior"]
