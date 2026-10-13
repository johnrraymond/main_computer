"""The parity entrypoint must not silently replace the Experimental server with Stable."""
from __future__ import annotations

import json

from tools.exp_hub_lab import experimental_lane
from tools.exp_hub_lab import run_lab


def test_real_exp_cluster_uses_separate_ports_namespace_and_mock_bridge(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(experimental_lane, "_require_ports_free", lambda: None)
    monkeypatch.setattr("main_computer.exp_fdb_hub.main", lambda args: calls.append(args) or 0)
    assert run_lab.main(["--serve-exp-cluster"]) == 0
    args = calls[0]
    assert args[args.index("--ports") + 1] == "8970,8971,8972"
    assert args[args.index("--namespace") + 1] == experimental_lane.EXP_NAMESPACE
    assert args[args.index("--bridge-backend") + 1] == "mock-chain"
    assert "--no-fdb-autostart" in args
    assert "--require-multisession-auth" in args
    assert "--network-kind" in args and "experimental" in args


def test_real_exp_cluster_fails_if_port_taken(monkeypatch, capsys) -> None:
    def busy():
        raise RuntimeError("Experimental Hub parity port 8971 is already occupied")
    monkeypatch.setattr(experimental_lane, "_require_ports_free", busy)
    assert run_lab.main(["--serve-exp-cluster"]) == 2
    assert "8971" in capsys.readouterr().out


def test_real_exp_handoff_calls_legacy_exp_verifier_not_stable_alias(monkeypatch, capsys) -> None:
    calls = []
    def fake_verify(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "mode": "verify-handoff", "proof": {"remote_entries_handoff_to_owner": True}}
    monkeypatch.setattr(run_lab, "verify_handoff", fake_verify)
    assert run_lab.main(["--verify-exp-handoff", "--json"]) == 0
    assert tuple(calls[0]["hub_urls"]) == experimental_lane.EXP_URLS
    assert calls[0]["require_multisession_auth"] is True
    assert json.loads(capsys.readouterr().out)["mode"] == "verify-handoff"


def test_dual_runtime_report_keeps_servers_separate(monkeypatch, capsys) -> None:
    monkeypatch.setattr("tools.stable_hub_lab.run_lab.build_stable_hub_verification_result",
                        lambda **kwargs: {"ok": True, "proof": {"entry_hub_handed_off_to_owner_hub": True}})
    monkeypatch.setattr(run_lab, "verify_handoff",
                        lambda **kwargs: {"ok": True, "proof": {
                            "remote_entries_handoff_to_owner": True,
                            "continuations_point_to_owner_hub": True,
                        }, "metrics": {"requests_attempted": 3}})
    assert run_lab.main(["--compare-handoff", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["stable"]["service"] == "main_computer.stable_hub"
    assert result["experimental"]["service"] == "main_computer.exp_fdb_hub"
    assert result["experimental"]["remote_handoff"] is True
    assert "financial lifecycle" in result["scope"]


def test_dual_runtime_parity_fails_when_exp_handoff_fails(monkeypatch, capsys) -> None:
    monkeypatch.setattr("tools.stable_hub_lab.run_lab.build_stable_hub_verification_result",
                        lambda **kwargs: {"ok": True, "proof": {}})
    monkeypatch.setattr(run_lab, "verify_handoff", lambda **kwargs: {"ok": False, "proof": {}})
    assert run_lab.main(["--compare-handoff", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_real_exp_accounting_verifier_injects_failure_without_stable_alias(monkeypatch, capsys) -> None:
    calls = []
    def fake_verify(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "mode": "verify-handoff", "proof": {"owner_hub_charged_exp_ledger": True}}
    monkeypatch.setattr(run_lab, "verify_handoff", fake_verify)
    assert run_lab.main(["--verify-exp-accounting", "--json"]) == 0
    assert calls[0]["failure_indices"] == (2,)
    assert calls[0]["hub_urls"] == experimental_lane.EXP_URLS
    assert json.loads(capsys.readouterr().out)["ok"] is True
