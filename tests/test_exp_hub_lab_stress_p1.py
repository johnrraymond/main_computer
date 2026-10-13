"""P1 verifies the stress lane targets real Experimental Hub and fails closed."""
from __future__ import annotations

import json
from tools.exp_hub_lab import run_lab, experimental_lane
from tools.exp_hub_lab.experimental_stress import verify_experimental_stress, verify_experimental_recovery, render_stress


def _happy(index=0):
    return {
        "ok": True,
        "proof": {
            "continuations_point_to_owner_hub": True,
            "multi_session_auth_required": True,
            "requester_msk_issued": True,
            "worker_msk_issued": True,
            "worker_connected_with_msk": True,
            "requests_signed_with_msk": True,
            "duplicate_success_was_idempotent": True,
            "cross_hub_http_retry_idempotent": True,
            "authenticated_worker_migrated_between_hubs": True,
            "accepted_work_is_held_not_charged": True,
            "owner_hub_charged_exp_ledger": True,
            "no_outstanding_holds_after_stress": True,
            "late_success_after_failure_did_not_charge": True,
        },
        "metrics": {
            "requests_attempted": 3, "requests_accepted": 3,
            "charged_results": 2, "released_failures": 1,
            "injected_worker_failures": 1, "remote_handoffs": 3,
            "invariant_violations": 0,
        },
    }


def test_stress_runs_real_exp_lane_with_isolated_workers_and_concurrent_scenarios(monkeypatch):
    calls = []
    monkeypatch.setattr(run_lab, "check_cluster", lambda **kw: {"ok": True})

    def fake_handoff(**kwargs):
        calls.append(kwargs)
        return _happy()

    monkeypatch.setattr(run_lab, "verify_handoff", fake_handoff)
    result = verify_experimental_stress(hub_urls=experimental_lane.EXP_URLS, runs=10, parallel=3)
    assert result["ok"] is True
    assert result["runtime"] == "main_computer.exp_fdb_hub"
    assert (result["runs_passed"], result["parallel_scenarios"]) == (10, 3)
    assert result["totals"]["requests_attempted"] == 30
    assert result["totals"]["charged_results"] == 20
    assert result["totals"]["released_failures"] == 10
    assert len({call["requester_account"] for call in calls}) == 10
    assert all(call["isolate_worker"] is True for call in calls)
    assert all(call["require_multisession_auth"] is True for call in calls)
    assert all(call["replay_http_index"] == 0 for call in calls)
    assert all(call["replay_success_index"] == 0 for call in calls)
    assert all(call["replay_failed_index_as_success"] == 1 for call in calls)
    assert all(call["reconnect_after_index"] == 0 for call in calls)
    assert all(call["failure_indices"] == (1,) for call in calls)


def test_stress_rejects_false_duplicate_success_proof(monkeypatch):
    monkeypatch.setattr(run_lab, "check_cluster", lambda **kw: {"ok": True})
    def fake_handoff(**kwargs):
        result = _happy()
        result["proof"]["duplicate_success_was_idempotent"] = False
        return result
    monkeypatch.setattr(run_lab, "verify_handoff", fake_handoff)
    result = verify_experimental_stress(hub_urls=experimental_lane.EXP_URLS, runs=2, parallel=2)
    assert result["ok"] is False
    assert result["runs_passed"] == 0
    assert all("replay_idempotent" in [k for k, v in item["checks"].items() if not v] for item in result["runs"])


def test_stress_surfaces_fdb_errors_and_does_not_silently_pass(monkeypatch):
    monkeypatch.setattr(run_lab, "check_cluster", lambda **kw: {"ok": True})
    monkeypatch.setattr(run_lab, "verify_handoff", lambda **kw: (_ for _ in ()).throw(RuntimeError("FDBError 1020")))
    result = verify_experimental_stress(hub_urls=experimental_lane.EXP_URLS, runs=2, parallel=2)
    assert result["ok"] is False
    assert result["runs_passed"] == 0
    assert all("FDBError 1020" in item["error"] for item in result["runs"])
    assert "run 1 FAIL" in render_stress(result)


def test_stress_fails_closed_if_three_exp_hubs_not_up(monkeypatch):
    monkeypatch.setattr(run_lab, "check_cluster", lambda **kw: {"ok": False, "checks": []})
    result = verify_experimental_stress(hub_urls=experimental_lane.EXP_URLS, runs=10)
    assert result["ok"] is False
    assert result["error"] == "cluster_check_failed"


def test_exp_stress_cli_is_not_stable_lab_alias(monkeypatch, capsys):
    from tools.exp_hub_lab import experimental_stress
    calls = []
    def fake_stress(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "runtime": "main_computer.exp_fdb_hub", "runs_requested": 2, "runs_passed": 2, "totals": {}}
    monkeypatch.setattr(experimental_stress, "verify_experimental_stress", fake_stress)
    assert run_lab.main(["--verify-exp-stress", "--verify-exp-stress-runs", "2", "--verify-exp-stress-parallel", "2", "--json"]) == 0
    assert calls[0]["hub_urls"] == experimental_lane.EXP_URLS
    assert calls[0]["runs"] == 2 and calls[0]["parallel"] == 2
    assert json.loads(capsys.readouterr().out)["runtime"] == "main_computer.exp_fdb_hub"


def test_stress_rejects_invalid_counts():
    for invalid in (-1, 0, 101):
        try:
            verify_experimental_stress(hub_urls=experimental_lane.EXP_URLS, runs=invalid)
        except ValueError:
            pass
        else:
            assert False, f"invalid runs {invalid} unexpectedly accepted"


def test_real_three_exp_handlers_execute_replay_failure_and_migration(tmp_path, monkeypatch):
    """Actual loopback HTTP/WebSockets and shared state, no live external FDB."""
    import threading
    from main_computer.config import MainComputerConfig
    from main_computer.exp_fdb_hub import ExperimentalFoundationDbHubServerHandler, _exp_fdb_stable_hub_id
    from main_computer.stable_hub_topology import StableHubNode, StableHubTopology
    from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
    from test_exp_hub_live_session_keepalive import _TestExpLiveSessionHub, _configure_exp_test_hub_topology

    config = MainComputerConfig(
        workspace=tmp_path, hub_root=tmp_path / "hub-runtime", hub_bridge_backend="mock",
        hub_allow_insecure_dev_network=True, hub_network="dev",
        hub_network_kind="local-dev-chain", chain_id=42424242, hub_credits_per_request=1,
        hub_require_multisession_auth=True,
    )
    hubs = [_TestExpLiveSessionHub(("127.0.0.1", 0), config) for _ in range(3)]
    urls = [f"http://127.0.0.1:{hub.server_port}" for hub in hubs]
    topology = StableHubTopology(
        kind="main_computer.stable_hub_topology.v1", cluster_id="pytest-exp-p1-stress",
        network={"network_key": "dev", "kind": "local-dev-chain", "chain_id": 42424242},
        storage={"backend": "memory"}, entry_urls=tuple(urls),
        hubs=tuple(StableHubNode(
            hub_id=_exp_fdb_stable_hub_id(hub.server_port), hub_url=urls[i],
            public_url=urls[i], roles=("entry", "worker-owner", "requester", "execution"),
        ) for i, hub in enumerate(hubs)),
    )
    shared_store = InMemoryStableWorkerSessionStore()
    shared_ledger = hubs[0].credit_ledger
    for hub in hubs:
        _configure_exp_test_hub_topology(
            hub, topology=topology,
            hub_id=_exp_fdb_stable_hub_id(hub.server_port), store=shared_store,
        )
        hub.credit_ledger = shared_ledger
        hub.dispatcher.plex_service.credit_ledger = shared_ledger

    import hashlib
    def mock_wallet_auth(self, *, body, **kwargs):
        auth = body.get("multisession_authorization") or {}
        address = str(auth["wallet_address"])
        return {"wallet_address": address, "account_id": "wallet:" + address[2:],
                "multisession_key_id": str(auth["multisession_key_id"])}
    def mock_issued_key(*, scope, **kwargs):
        wallet = "0x" + hashlib.sha256(scope.encode()).hexdigest()[:40]
        return {"account_id": "wallet:" + wallet[2:], "wallet_address": wallet,
                "key_id": "p1_mock_signed_" + scope, "key": {"chain_id": "0x28757b2"}}
    monkeypatch.setattr(ExperimentalFoundationDbHubServerHandler, "_authorize_worker_route", mock_wallet_auth)
    def mock_validate_auth(self, auth, **kwargs):
        address = str(auth["wallet_address"])
        return {"wallet_address": address, "account_id": "wallet:" + address[2:],
                "multisession_key_id": str(auth["multisession_key_id"]),
                "chain_id": str(auth.get("chain_id") or "0x28757b2")}
    monkeypatch.setattr(ExperimentalFoundationDbHubServerHandler, "_validate_multisession_authorization", mock_validate_auth)
    monkeypatch.setattr(run_lab, "_new_dev_wallet_private_key", lambda: ("mock_private_key", "0x" + "a" * 40))
    monkeypatch.setattr(run_lab, "_issue_lab_multisession_key", mock_issued_key)
    workers = [threading.Thread(target=hub.serve_forever, daemon=True) for hub in hubs]
    for thread in workers:
        thread.start()
    try:
        # A real strict-auth server must reject a public paid route even when
        # the caller provides an account_id and guessed request identity.
        unsigned_status, unsigned = run_lab._post_json_url_status(
            urls[0] + "/api/hub/v1/work/requests",
            {"request_id": "p1-unsigned-probe", "client_node_id": "unsigned",
             "account_id": "forged", "max_credits": 3,
             "messages": [{"role": "user", "content": "unsigned"}]}, timeout=6,
        )
        assert unsigned_status == 403, unsigned
        result = run_lab.verify_handoff(
            hub_urls=urls, requester_account="p1-exp-stress-account", funding_credits=12,
            stress_requests=3, worker_credits=1, timeout=6,
            failure_indices=(1,), replay_success_index=0,
            replay_failed_index_as_success=1, reconnect_after_index=0,
            isolate_worker=True, require_multisession_auth=True,
        )
        assert result["ok"], {"failed_proofs": [k for k,v in result.get("proof", {}).items() if not v], "metrics": result.get("metrics")}
        assert result["metrics"]["charged_results"] == 2
        assert result["metrics"]["released_failures"] == 1
        assert result["metrics"]["remote_handoffs"] >= 2
        assert result["reconnection"]["previous_owner_url"] == urls[2]
        assert result["reconnection"]["new_owner_url"] == urls[0]
        # Multiple *actual* exp HTTP handlers receive overlapping scenarios.
        # Worker identity is unique per scenario to avoid cross-selection.
        stress = verify_experimental_stress(hub_urls=urls, runs=3, parallel=3, timeout=6)
        assert stress["ok"], [(item["run"], item.get("error"),
                                [k for k, v in item.get("checks", {}).items() if not v])
                               for item in stress.get("runs", [])]
        assert stress["totals"]["requests_attempted"] == 9
        assert stress["totals"]["charged_results"] == 6
        assert stress["totals"]["released_failures"] == 3
        recovery = verify_experimental_recovery(hub_urls=urls, timeout=6)
        assert recovery["ok"], {"checks": recovery.get("checks"), "proof": recovery.get("proof"),
                               "metrics": recovery.get("metrics")}
        assert recovery["checks"]["inflight_disconnect_released_hold"]
        assert recovery["checks"]["cross_hub_http_retry_idempotent"]
    finally:
        for hub in hubs:
            hub.shutdown()
            hub.server_close()
        for thread in workers:
            thread.join(timeout=2)
