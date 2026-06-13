from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from tools.scheduler_lab.report import build_report, render_markdown


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record, sort_keys=True) + "\n" for record in records), encoding="utf-8")


def test_report_suppresses_node_scoring_during_shared_transport_outage(tmp_path: Path) -> None:
    run_id = "20260612T214859Z-1-d6bee111"
    nodes = [
        {"node_id": "worker-0001", "kind": "worker", "cohort": "normal", "behavior_mode": "mixed_market"},
        {"node_id": "requester-0001", "kind": "requester", "cohort": "normal", "behavior_mode": "requester_centric"},
    ]
    _write_jsonl(tmp_path / f"scheduler-lab-runtime-nodes-{run_id}.jsonl", nodes)

    for index, node in enumerate(nodes):
        events = [
            {
                "event": "node.process.started",
                "node_id": node["node_id"],
                "node_kind": node["kind"],
                "worker_enabled": True,
                "requester_enabled": True,
            }
        ]
        for attempt in range(10):
            events.append(
                {
                    "event": "worker.register",
                    "node_id": node["node_id"],
                    "status": 0,
                    "ok": False,
                    "hub_base_url": "http://host.docker.internal:8874",
                    "response_summary": {"error": "connection refused"},
                }
            )
            events.append(
                {
                    "event": "node.transport_failure",
                    "node_id": node["node_id"],
                    "hub_base_url": "http://host.docker.internal:8874",
                    "consecutive_transport_failures": attempt + 1,
                }
            )
        events.append({"event": "node.self_terminated.b2bfailures", "node_id": node["node_id"]})
        _write_jsonl(tmp_path / f"node-process-{run_id}-{index:05d}-{node['node_id']}.events.jsonl", events)

    report = build_report(tmp_path, run_id=run_id)

    assert report["run_health"]["category"] == "transport_outage"
    assert report["run_health"]["score_nodes"] is False
    assert report["run_health"]["transport_failure_ratio"] == 1.0
    assert report["run_health"]["transport_failures"] == 20
    assert report["run_health"]["synthetic_transport_failure_events"] == 20
    assert report["run_health"]["failure_scope"] == "single_endpoint_or_shared_network"
    assert report["run_health"]["top_transport_endpoint"] == "http://host.docker.internal:8874"
    assert report["run_health"]["endpoint_breakdown"][0]["transport_failure_ratio"] == 1.0
    assert report["run_health"]["endpoint_breakdown"][0]["affected_nodes"] == 2
    assert report["pipeline_adequacy"]["worker_pipeline_usable"] is False
    assert report["pipeline_adequacy"]["stages"][4]["stage"] == "leases"
    assert report["average_reliability_percent"] is None
    assert report["median_reliability_percent"] is None
    assert report["category_counts"] == {"unscored_transport_outage": 2}
    assert all(row["category"] == "unscored_transport_outage" for row in report["nodes"])
    assert all(row["reliability_percent"] is None for row in report["nodes"])
    assert all(row["transport_failures"] == 10 for row in report["nodes"])
    assert all(row["synthetic_transport_failure_events"] == 10 for row in report["nodes"])

    markdown = render_markdown(report)
    assert markdown.index("## Run Health Assessment") < markdown.index("## Node behavior ratings")
    assert "Individual node scoring is suppressed" in markdown
    assert "transport_failure_ratio | 100%" in markdown
    assert "failure_scope | single_endpoint_or_shared_network" in markdown
    assert "top_transport_endpoint | http://host.docker.internal:8874" in markdown
    assert "## Pipeline Adequacy" in markdown
    assert "| worker_polls | 0 | 20 | no |" in markdown
    assert "## Hub endpoint health" in markdown
    assert "synthetic_transport_failure_events | 20" in markdown
    assert "unscored_transport_outage" in markdown


def test_report_scores_nodes_when_market_activity_is_observed(tmp_path: Path) -> None:
    run_id = "20260612T220000Z-1-healthy"
    nodes = [
        {"node_id": "worker-0001", "kind": "worker", "cohort": "normal", "behavior_mode": "worker_centric"},
        {"node_id": "requester-0001", "kind": "requester", "cohort": "normal", "behavior_mode": "requester_centric"},
    ]
    _write_jsonl(tmp_path / f"scheduler-lab-runtime-nodes-{run_id}.jsonl", nodes)
    worker_events = [
        {"event": "node.process.started", "node_id": "worker-0001", "worker_enabled": True, "requester_enabled": False},
        {"event": "worker.register", "node_id": "worker-0001", "status": 200, "ok": True},
    ]
    for index in range(20):
        worker_events.append({"event": "worker.heartbeat", "node_id": "worker-0001", "status": 200, "ok": True})
        poll_event = {
            "event": "worker.poll",
            "node_id": "worker-0001",
            "status": 200,
            "ok": True,
        }
        if index < 10:
            poll_event["response_summary"] = {"lease": {"lease_id": f"lease-{index}", "request_id": f"req-{index}"}}
        worker_events.append(poll_event)
        if index < 10:
            worker_events.extend(
                [
                    {"event": "worker.execution.started", "node_id": "worker-0001", "lease_id": f"lease-{index}", "request_id": f"req-{index}"},
                    {"event": "worker.execution.finished", "node_id": "worker-0001", "lease_id": f"lease-{index}", "request_id": f"req-{index}"},
                    {"event": "worker.result.submitted", "node_id": "worker-0001", "status": 200, "ok": True, "lease_id": f"lease-{index}", "request_id": f"req-{index}"},
                ]
            )
    _write_jsonl(tmp_path / f"node-process-{run_id}-00000-worker-0001.events.jsonl", worker_events)

    requester_events = [
        {"event": "node.process.started", "node_id": "requester-0001", "worker_enabled": False, "requester_enabled": True},
    ]
    for index in range(20):
        requester_events.extend(
            [
                {
                    "event": "requester.request.attempted",
                    "node_id": "requester-0001",
                    "lab_request_key": f"requester-0001-{index}",
                },
                {
                    "event": "requester.request.submitted",
                    "node_id": "requester-0001",
                    "status": 200,
                    "ok": True,
                    "request_id": f"req-{index}",
                    "response_summary": {"request": {"request_id": f"req-{index}", "state": "queued"}},
                },
            ]
        )
    _write_jsonl(tmp_path / f"node-process-{run_id}-00001-requester-0001.events.jsonl", requester_events)

    report = build_report(tmp_path, run_id=run_id)

    assert report["run_health"]["category"] == "market_activity_observed"
    assert report["run_health"]["score_nodes"] is True
    categories = {row["node_id"]: row["category"] for row in report["nodes"]}
    assert categories["worker-0001"] in {"excellent", "reliable"}
    assert categories["requester-0001"] in {"excellent", "reliable"}
    assert "unscored_transport_outage" not in report["category_counts"]
    assert report["pipeline_adequacy"]["usable_for_worker_reliability_scoring"] is True


def test_report_marks_requester_only_samples_as_worker_pipeline_inadequate(tmp_path: Path) -> None:
    run_id = "20260612T222832Z-1-requester-only"
    _write_jsonl(
        tmp_path / f"scheduler-lab-runtime-nodes-{run_id}.jsonl",
        [
            {"node_id": "requester-0001", "kind": "requester", "cohort": "normal", "behavior_mode": "requester_centric"},
            {"node_id": "worker-0001", "kind": "worker", "cohort": "normal", "behavior_mode": "worker_centric"},
        ],
    )
    requester_events = [
        {"event": "node.process.started", "node_id": "requester-0001", "requester_enabled": True, "worker_enabled": False},
    ]
    for index in range(20):
        requester_events.extend(
            [
                {"event": "requester.request.attempted", "node_id": "requester-0001", "lab_request_key": f"requester-0001-{index}"},
                {
                    "event": "requester.request.submitted",
                    "node_id": "requester-0001",
                    "status": 200,
                    "ok": True,
                    "request_id": f"req-{index}",
                    "response_summary": {"request": {"request_id": f"req-{index}", "state": "queued"}},
                },
            ]
        )
    _write_jsonl(tmp_path / f"node-process-{run_id}-00000-requester-0001.events.jsonl", requester_events)
    _write_jsonl(
        tmp_path / f"node-process-{run_id}-00001-worker-0001.events.jsonl",
        [
            {"event": "node.process.started", "node_id": "worker-0001", "worker_enabled": True, "requester_enabled": False},
            {"event": "worker.register", "node_id": "worker-0001", "status": 200, "ok": True},
        ],
    )

    report = build_report(tmp_path, run_id=run_id)

    assert report["run_health"]["category"] == "sample_inadequate"
    assert report["run_health"]["score_nodes"] is False
    assert report["pipeline_adequacy"]["requester_samples_usable"] is True
    assert report["pipeline_adequacy"]["worker_pipeline_usable"] is False
    assert report["category_counts"] == {"unscored_sample_inadequate": 2}
    markdown = render_markdown(report)
    assert "Run is not usable for worker reliability scoring" in markdown
    assert "| worker_polls | 0 | 20 | no |" in markdown


def test_report_identifies_endpoint_specific_transport_outage(tmp_path: Path) -> None:
    run_id = "20260612T221000Z-1-two-hubs"
    _write_jsonl(
        tmp_path / f"scheduler-lab-runtime-nodes-{run_id}.jsonl",
        [
            {"node_id": "requester-0001", "kind": "requester", "cohort": "normal", "behavior_mode": "requester_centric"},
            {"node_id": "worker-0001", "kind": "worker", "cohort": "normal", "behavior_mode": "worker_centric"},
        ],
    )
    _write_jsonl(
        tmp_path / f"node-process-{run_id}-00000-requester-0001.events.jsonl",
        [
            {"event": "node.process.started", "node_id": "requester-0001", "requester_enabled": True},
            {"event": "requester.request.attempted", "node_id": "requester-0001"},
            {
                "event": "requester.request.submitted",
                "node_id": "requester-0001",
                "status": 200,
                "ok": True,
                "hub_base_url": "http://hub-good:8870",
                "request_id": "req-1",
                "response_summary": {"request": {"request_id": "req-1", "state": "queued"}},
            },
        ],
    )
    worker_events = [{"event": "node.process.started", "node_id": "worker-0001", "worker_enabled": True}]
    for _ in range(10):
        worker_events.append(
            {
                "event": "worker.register",
                "node_id": "worker-0001",
                "status": 0,
                "ok": False,
                "hub_base_url": "http://hub-bad:8870",
                "response_summary": {"error": "connection refused"},
            }
        )
        worker_events.append(
            {
                "event": "node.transport_failure",
                "node_id": "worker-0001",
                "hub_base_url": "http://hub-bad:8870",
            }
        )
    worker_events.append({"event": "node.self_terminated.b2bfailures", "node_id": "worker-0001"})
    _write_jsonl(tmp_path / f"node-process-{run_id}-00001-worker-0001.events.jsonl", worker_events)

    report = build_report(tmp_path, run_id=run_id)

    assert report["run_health"]["category"] == "transport_outage"
    assert report["run_health"]["score_nodes"] is False
    assert report["run_health"]["failure_scope"] == "endpoint_specific_transport_outage"
    assert report["run_health"]["top_transport_endpoint"] == "http://hub-bad:8870"
    endpoints = {endpoint["endpoint"]: endpoint for endpoint in report["run_health"]["endpoint_breakdown"]}
    assert endpoints["http://hub-bad:8870"]["transport_failure_ratio"] == 1.0
    assert endpoints["http://hub-good:8870"]["transport_failure_ratio"] == 0.0
    markdown = render_markdown(report)
    assert "endpoint_specific_transport_outage" in markdown
    assert "http://hub-bad:8870" in markdown
    assert "http://hub-good:8870" in markdown


def test_report_cli_writes_markdown_json_and_csv(tmp_path: Path) -> None:
    run_id = "20260612T221500Z-1-cli"
    _write_jsonl(
        tmp_path / f"scheduler-lab-runtime-nodes-{run_id}.jsonl",
        [{"node_id": "requester-0001", "kind": "requester", "cohort": "normal", "behavior_mode": "requester_centric"}],
    )
    _write_jsonl(
        tmp_path / f"node-process-{run_id}-00000-requester-0001.events.jsonl",
        [
            {"event": "node.process.started", "node_id": "requester-0001", "requester_enabled": True},
            {"event": "requester.request.attempted", "node_id": "requester-0001"},
            {
                "event": "requester.request.submitted",
                "node_id": "requester-0001",
                "status": 200,
                "ok": True,
                "request_id": "req-cli",
                "response_summary": {"request": {"request_id": "req-cli", "state": "queued"}},
            },
        ],
    )

    markdown_path = tmp_path / "node-report.md"
    json_path = tmp_path / "node-report.json"
    csv_path = tmp_path / "node-report.csv"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.scheduler_lab.report",
            "--output-dir",
            str(tmp_path),
            "--run-id",
            run_id,
            "--output",
            str(markdown_path),
            "--json-output",
            str(json_path),
            "--csv-output",
            str(csv_path),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "wrote" in result.stdout
    assert markdown_path.exists()
    assert json_path.exists()
    assert csv_path.exists()
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["schema"] == "main-computer-hub-lab-node-behavior-report/v4"
    assert "Run Health Assessment" in markdown_path.read_text(encoding="utf-8")
    rows = list(csv.DictReader(csv_path.open("r", encoding="utf-8")))
    assert rows[0]["node_id"] == "requester-0001"


def test_report_marks_no_market_activity_as_unscored_not_bad_nodes(tmp_path: Path) -> None:
    run_id = "20260612T223000Z-1-no-market"
    _write_jsonl(
        tmp_path / f"scheduler-lab-runtime-nodes-{run_id}.jsonl",
        [{"node_id": "worker-0001", "kind": "worker", "cohort": "normal", "behavior_mode": "worker_centric"}],
    )
    _write_jsonl(
        tmp_path / f"node-process-{run_id}-00000-worker-0001.events.jsonl",
        [
            {"event": "node.process.started", "node_id": "worker-0001", "worker_enabled": True},
            {"event": "worker.register", "node_id": "worker-0001", "status": 200, "ok": True},
            {"event": "worker.heartbeat", "node_id": "worker-0001", "status": 200, "ok": True},
        ],
    )

    report = build_report(tmp_path, run_id=run_id)

    assert report["run_health"]["category"] == "no_valid_market_activity"
    assert report["run_health"]["score_nodes"] is False
    assert report["category_counts"] == {"unscored_no_valid_market_activity": 1}
