"""Offline contracts for the integrated Experimental Docker/P1 live lab."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.exp_hub_lab import run_lab as p1
from tools.scheduler_lab import live_session
from tools.scheduler_lab.run_lab import build_arg_parser


def _args(monkeypatch, tmp_path, *, workers=2, requesters=1):
    monkeypatch.setenv("EXP_HUB_LAB_DOCKER_HOST_LOOPBACK_MAP", "1")
    monkeypatch.setenv("LAB_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("LAB_LIVE_CONCURRENCY", "2")
    monkeypatch.setenv("LAB_LIVE_TIMEOUT_SECONDS", "5")
    return build_arg_parser().parse_args([
        "--request-mode", "live_session_v1", "--auth-mode", "multisession-wallet",
        "--hub-base-urls", ",".join(f"http://host.docker.internal:{port}" for port in (8970, 8971, 8972)),
        "--nodes", str(workers + requesters),
        "--workers", str(workers), "--requesters", str(requesters), "--ring", "3",
        "--run-id", "test-live",
    ])


def _happy():
    return {
        "ok": True, "scenario_id": "example", "requests": [
            {"response": {"session_id": "sess-1"}},
            {"response": {"session_id": "sess-2"}},
            {"response": {"session_id": "sess-3"}},
        ],
        "metrics": {
            "requests_attempted": 3, "requests_accepted": 3,
            "charged_results": 2, "released_failures": 1,
            "invariant_violations": 0, "remote_handoffs": 3,
        },
        "proof": {key: True for key in (
            "cluster_has_three_hubs", "continuations_point_to_owner_hub",
            "remote_entries_handoff_to_owner",
            "cross_hub_http_retry_idempotent", "duplicate_success_was_idempotent",
            "late_success_after_failure_did_not_charge",
            "authenticated_worker_migrated_between_hubs",
            "no_outstanding_holds_after_stress", "worker_connected_with_msk",
            "requests_signed_with_msk", "multi_session_auth_required",
            "owner_hub_charged_exp_ledger",
        )},
    }


def test_docker_transport_maps_only_explicit_loopback(monkeypatch):
    monkeypatch.delenv("EXP_HUB_LAB_DOCKER_HOST_LOOPBACK_MAP", raising=False)
    url = "http://127.0.0.1:8970/path?q=1"
    assert p1._docker_lab_transport_url(url) == url
    monkeypatch.setenv("EXP_HUB_LAB_DOCKER_HOST_LOOPBACK_MAP", "1")
    assert p1._docker_lab_transport_url(url) == "http://host.docker.internal:8970/path?q=1"
    assert p1._docker_lab_transport_url("http://192.0.2.3:8970/path") == "http://192.0.2.3:8970/path"
    assert p1._docker_lab_transport_url("https://127.0.0.1:8970/path") == "https://127.0.0.1:8970/path"


def test_integrated_live_lane_isolated_strict_msk_and_accounting(monkeypatch, tmp_path):
    args = _args(monkeypatch, tmp_path)
    monkeypatch.setattr(p1, "check_cluster", lambda **kwargs: {"ok": True})
    calls = []
    def fake_run(**kwargs):
        calls.append(kwargs)
        return _happy()
    monkeypatch.setattr(p1, "verify_handoff", fake_run)
    assert live_session.run_live_session_lab(args) == 0
    summary = json.loads((tmp_path / "test-live-live-summary.json").read_text())
    events = (tmp_path / "test-live-live-events.jsonl").read_text().strip().splitlines()
    assert summary["ok"] is True
    assert summary["worker_scenarios_passed"] == 2
    assert summary["charged_results"] == 4 and summary["released_failures"] == 2
    assert len(events) == 2
    assert len({call["requester_account"] for call in calls}) == 2
    assert all(call["require_multisession_auth"] and call["isolate_worker"] for call in calls)
    assert all(call["failure_indices"] == (1,) and call["replay_http_index"] == 0 for call in calls)
    assert not any("private_key" in event for event in events)


def test_preflight_refuses_to_generate_paid_work(monkeypatch, tmp_path):
    args = _args(monkeypatch, tmp_path)
    monkeypatch.setattr(p1, "check_cluster", lambda **kwargs: {"ok": False, "checks": [{"error": "temporal_not_ready"}]})
    monkeypatch.setattr(p1, "verify_handoff", lambda **kwargs: pytest.fail("must not submit work"))
    assert live_session.run_live_session_lab(args) == 1
    summary = json.loads((tmp_path / "test-live-live-summary.json").read_text())
    assert summary["ok"] is False and summary["requests_attempted"] == 0
    assert summary["errors"][0]["gate"] == "cluster_preflight"


def test_missing_financial_proof_fails_lab(monkeypatch, tmp_path):
    args = _args(monkeypatch, tmp_path)
    monkeypatch.setattr(p1, "check_cluster", lambda **kwargs: {"ok": True})
    def false_success(**kwargs):
        result = _happy()
        result["proof"]["no_outstanding_holds_after_stress"] = False
        return result
    monkeypatch.setattr(p1, "verify_handoff", false_success)
    assert live_session.run_live_session_lab(args) == 1
    summary = json.loads((tmp_path / "test-live-live-summary.json").read_text())
    assert summary["invariant_violations"] == 2 and not summary["ok"]


def test_live_mode_rejects_unauthenticated_and_wrong_network(monkeypatch, tmp_path):
    args = _args(monkeypatch, tmp_path)
    args.auth_mode = "legacy"
    with pytest.raises(ValueError, match="strict wallet"):
        live_session.run_live_session_lab(args)
    args.auth_mode = "multisession-wallet"
    args.hub_base_urls = "http://127.0.0.1:8970,http://127.0.0.1:8971,http://127.0.0.1:8972"
    with pytest.raises(ValueError, match="host.docker.internal"):
        live_session.run_live_session_lab(args)


def test_experimental_lane_uses_original_integrated_launcher(monkeypatch):
    from tools.exp_hub_lab import experimental_lane
    import main_computer.exp_fdb_hub as exp
    monkeypatch.setattr(experimental_lane, "_require_ports_free", lambda: None)
    monkeypatch.setenv("MAIN_COMPUTER_EXP_TEMPORAL_ADDRESS", "127.0.0.1:7233")
    seen = []
    monkeypatch.setattr(exp, "main", lambda args: seen.extend(args) or 0)
    assert experimental_lane.run_lane([
        "--serve-exp-docker-lab", "--docker-workers", "5", "--docker-requesters", "1"
    ]) == 0
    assert "--docker" in seen and "--docker-request-mode" in seen
    assert seen[seen.index("--docker-request-mode")+1] == "live_session_v1"
    assert seen[seen.index("--host")+1] == "0.0.0.0"
    assert seen[seen.index("--bridge-backend")+1] == "mock-chain"
    assert "--require-multisession-auth" in seen


def test_hub_forces_host_loopback_advertisement_for_docker(tmp_path, monkeypatch):
    import main_computer.exp_fdb_hub as exp
    args = exp.build_parser().parse_args([
        "--docker", "--docker-request-mode", "live_session_v1", "--host", "0.0.0.0", "--require-multisession-auth"
    ])
    # Parser is not sufficient: main must normalize the advertised host.
    monkeypatch.setenv("MAIN_COMPUTER_EXP_TEMPORAL_ADDRESS", "127.0.0.1:7233")
    monkeypatch.setattr(exp, "serve_exp_fdb_hubs", lambda parsed: (parsed.host, parsed.hub_url))
    assert exp.main([
        "--docker", "--docker-request-mode", "live_session_v1", "--host", "0.0.0.0", "--require-multisession-auth"
    ]) == ("0.0.0.0", "http://127.0.0.1")
