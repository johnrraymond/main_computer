from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from main_computer.protected_temporal_flow_smoke import (
    ProtectedTemporalFlowConfig,
    _execution_task_queue,
    run_protected_temporal_flow_smoke,
)
from tools.temporal_lab.activities import FakeTokenActivities
from tools.temporal_lab.event_log import read_jsonl_events
from tools.temporal_lab.models import FakeTokenRequest, RingDecision


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_protected_temporal_flow_script_bootstraps_repo_root_for_path_invocation() -> None:
    source = (REPO_ROOT / "scripts" / "smoke_protected_temporal_flow.py").read_text(encoding="utf-8")

    assert "REPO_ROOT = Path(__file__).resolve().parents[1]" in source
    assert "sys.path.insert(0, str(REPO_ROOT))" in source
    assert source.index("sys.path.insert(0, str(REPO_ROOT))") < source.index(
        "from main_computer.protected_temporal_flow_smoke"
    )


def test_direct_activity_protected_temporal_flow_creates_charges_and_releases(tmp_path) -> None:
    report_path = tmp_path / "protected_temporal_flow_report.json"
    event_log = tmp_path / "events.jsonl"

    report = asyncio.run(
        run_protected_temporal_flow_smoke(
            ProtectedTemporalFlowConfig(
                repo_root=REPO_ROOT,
                ledger_root=tmp_path / "ledger",
                report_path=report_path,
                event_log_path=event_log,
                execution_mode="direct-activity",
                deposit_credits="25",
                success_credits_offered=3,
                failure_credits_offered=3,
                token_count=3,
                token_interval_seconds=0.0,
            )
        )
    )

    assert report["ok"] is True
    assert report["execution_mode"] == "direct-activity"
    assert report["steps"]["success_flow"]["decision"]["ring"] == 2
    assert report["steps"]["success_flow"]["decision"]["task_queue"] == "scheduler-lab-fake-tokens-ring-2"
    assert report["steps"]["success_flow"]["final_hold"]["status"] == "charged"
    assert report["steps"]["failure_flow"]["final_hold"]["status"] == "released"
    assert report["steps"]["failure_flow"]["workflow_error"] is None
    assert report["steps"]["failure_flow"]["workflow_failed_result"] is True
    assert report["steps"]["failure_flow"]["workflow_result"]["result"]["ok"] is False

    invariants = report["invariants"]
    assert invariants["success_hold_charged"] is True
    assert invariants["success_charge_matches_required_wei"] is True
    assert invariants["failure_outcome_failed"] is True
    assert invariants["failure_workflow_failed"] is True
    assert invariants["failure_default_path_has_no_traceback_error"] is True
    assert invariants["failure_hold_released"] is True
    assert invariants["final_held_zero"] is True
    assert invariants["final_spent_equals_success_required"] is True
    assert invariants["final_available_plus_spent_equals_deposit"] is True

    final_totals = report["steps"]["final_status"]["totals"]
    assert final_totals["available_credit_wei"] == "22000000000000000000"
    assert final_totals["spent_credit_wei"] == "3000000000000000000"
    assert final_totals["held_credit_wei"] == "0"

    assert report_path.exists()
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted["mode"] == "protected-temporal-flow-smoke-v1"
    assert persisted["failure_mode"] == "business-result"

    events = read_jsonl_events(event_log)
    assert any(event["event"] == "done" for event in events)
    assert any(event["event"] == "failed" for event in events)


def test_cli_direct_activity_mode_returns_success(tmp_path) -> None:
    report_path = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/smoke_protected_temporal_flow.py",
            "--execution-mode",
            "direct-activity",
            "--ledger-root",
            str(tmp_path / "ledger"),
            "--report",
            str(report_path),
            "--event-log",
            str(tmp_path / "events.jsonl"),
            "--token-interval-seconds",
            "0",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )

    assert result.returncode == 0
    assert "PASS: protected Temporal bridge-credit flow smoke succeeded" in result.stdout
    assert "success_hold_status: charged" in result.stdout
    assert "failure_hold_status: released" in result.stdout
    assert report_path.exists()
    assert "Completing activity as failed" not in result.stderr
    assert "forced fake token failure" not in result.stderr


def test_force_failure_payload_raises_and_writes_failed_event(tmp_path) -> None:
    event_log = tmp_path / "events.jsonl"
    request = FakeTokenRequest(
        request_id="req-force-failure",
        account_id="acct-force-failure",
        credits_offered=3,
        ring=2,
        token_count=3,
        token_interval_seconds=0.0,
        payload={"force_failure": True},
    )

    with pytest.raises(RuntimeError, match="forced fake token failure"):
        asyncio.run(
            FakeTokenActivities(event_log_path=event_log, worker_id="worker-force-failure").emit_fake_tokens(
                request.to_dict()
            )
        )

    events = read_jsonl_events(event_log)
    assert [event["event"] for event in events] == ["start", "failed"]
    assert events[-1]["reason"] == "forced_fake_token_failure"


def test_business_failure_payload_returns_failed_result_without_traceback(tmp_path) -> None:
    event_log = tmp_path / "events.jsonl"
    request = FakeTokenRequest(
        request_id="req-business-failure",
        account_id="acct-business-failure",
        credits_offered=3,
        ring=2,
        token_count=3,
        token_interval_seconds=0.0,
        payload={"force_business_failure": True},
    )

    result = asyncio.run(
        FakeTokenActivities(event_log_path=event_log, worker_id="worker-business-failure").emit_fake_tokens(
            request.to_dict()
        )
    )

    assert result["result"]["ok"] is False
    assert result["result"]["error_code"] == "forced_smoke_business_failure"
    events = read_jsonl_events(event_log)
    assert [event["event"] for event in events] == ["start", "failed"]
    assert events[-1]["reason"] == "forced_smoke_business_failure"


def test_live_temporal_smoke_uses_isolated_task_queue_by_default() -> None:
    decision = RingDecision(
        accepted=True,
        request_id="req",
        account_id="acct",
        credits_offered=3,
        token_count=3,
        ring=2,
        partition="ring-2",
        task_queue="scheduler-lab-fake-tokens-ring-2",
        required_credits=3,
        credits_per_token=1,
        service_rank=1,
        reason="accepted",
    )

    isolated = _execution_task_queue(
        config=ProtectedTemporalFlowConfig(repo_root=REPO_ROOT, execution_mode="live-temporal"),
        decision=decision,
        smoke_run_id="abc123",
    )
    assert isolated == "scheduler-lab-fake-tokens-ring-2-protected-smoke-abc123"

    direct = _execution_task_queue(
        config=ProtectedTemporalFlowConfig(repo_root=REPO_ROOT, execution_mode="direct-activity"),
        decision=decision,
        smoke_run_id="abc123",
    )
    assert direct == "scheduler-lab-fake-tokens-ring-2"


def test_live_temporal_workflow_disables_activity_retries_for_failure_smoke() -> None:
    source = (REPO_ROOT / "tools" / "temporal_lab" / "workflows.py").read_text(encoding="utf-8")

    assert "RetryPolicy(maximum_attempts=1)" in source
    assert "retry_policy=activity_retry_policy(request)" in source
