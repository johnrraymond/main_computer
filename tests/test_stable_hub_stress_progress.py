"""Live progress for long-running Stable Hub Lab stress campaigns."""
from __future__ import annotations

import json
import time

import pytest

from main_computer.stable_hub_topology import StableHubTopologyError
from tools.stable_hub_lab import run_lab


def test_stress_monitor_reports_phase_heartbeats_and_completion():
    lines: list[str] = []
    monitor = run_lab._StressProgressMonitor(
        index=3, total=25, interval=0.02, log=lines.append,
    )
    monitor.start()
    monitor.phase("payout.ledger_read")
    time.sleep(0.16)
    monitor.finish(passed=True)

    assert lines[0].startswith("STRESS_PROGRESS run=3/25 START")
    assert any("phase=payout.ledger_read" in line for line in lines)
    assert any("STILL_WAITING phase=payout.ledger_read" in line for line in lines)
    assert "PASS last_phase=payout.ledger_read" in lines[-1]
    after_finish = len(lines)
    time.sleep(0.16)
    assert len(lines) == after_finish  # daemon watcher is stopped, not leaked


def test_stress_runner_exposes_phase_and_completes_without_changing_results(monkeypatch):
    lines: list[str] = []
    calls = []

    def fake_scenario(**kwargs):
        calls.append(kwargs)
        assert kwargs["origin"].endswith(f"stress-run-{len(calls)}")
        kwargs["progress"]("hub_protocol_preflight")
        time.sleep(0.15)
        return {"ok": True, "cluster_id": "smoke", "metrics": {
            "requests_attempted": 4, "holds": 3, "charges": 2,
        }}

    monkeypatch.setattr(run_lab, "build_stable_hub_integrated_market_stress_result", fake_scenario)
    result = run_lab.build_stable_hub_integrated_market_stress_runs_result(
        runs=2, progress_log=lines.append, progress_interval=0.02,
    )
    assert result["ok"] is True
    assert result["metrics"]["runs_ok"] == 2
    assert result["metrics"]["requests_attempted"] == 8
    assert [r["run_index"] for r in result["runs"]] == [1, 2]
    assert any("run=1/2 STILL_WAITING phase=hub_protocol_preflight" in line for line in lines)
    assert any("run=2/2 PASS" in line for line in lines)


def test_stress_failure_reports_exact_phase_and_run(monkeypatch):
    lines: list[str] = []

    def fake_scenario(**kwargs):
        kwargs["progress"]("work.result.authorization_wait request=req_test")
        raise StableHubTopologyError("injected error")

    monkeypatch.setattr(run_lab, "build_stable_hub_integrated_market_stress_result", fake_scenario)
    with pytest.raises(StableHubTopologyError, match=r"stress run 1/25 failed at phase=work.result.authorization_wait request=req_test"):
        run_lab.build_stable_hub_integrated_market_stress_runs_result(
            runs=25, progress_log=lines.append, progress_interval=0.02,
        )
    assert any("run=1/25 FAIL last_phase=work.result.authorization_wait" in line for line in lines)
    assert not any("run=2/25" in line for line in lines)


def test_stress_default_library_invocation_is_quiet(monkeypatch):
    def fake_scenario(**kwargs):
        assert kwargs["progress"] is None
        return {"ok": True, "cluster_id": "smoke", "metrics": {}}

    monkeypatch.setattr(run_lab, "build_stable_hub_integrated_market_stress_result", fake_scenario)
    result = run_lab.build_stable_hub_integrated_market_stress_runs_result(runs=2)
    assert result["ok"] is True


def test_json_cli_does_not_mix_progress_with_json(monkeypatch, capsys):
    received = {}

    def fake_multi(**kwargs):
        received.update(kwargs)
        return {"ok": True, "runs": [], "metrics": {"runs_ok": 2}}

    monkeypatch.setattr(run_lab, "build_stable_hub_integrated_market_stress_runs_result", fake_multi)
    assert run_lab.main(["--verify-stress", "--verify-stress-runs", "2", "--json"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["ok"] is True
    assert output.err == ""
    assert received["progress_log"] is None


def test_progress_interval_must_be_positive(capsys):
    assert run_lab.main(["--verify-stress", "--stress-progress-interval", "0"]) == 2
    assert "must be positive" in capsys.readouterr().err
