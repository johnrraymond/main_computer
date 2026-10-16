"""The parity entrypoint must not silently replace the Experimental server with Stable."""
from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

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
    monkeypatch.setattr(experimental_lane, "_comparison_cluster_session", lambda **kwargs: nullcontext())
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
    monkeypatch.setattr(experimental_lane, "_comparison_cluster_session", lambda **kwargs: nullcontext())
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


def test_comparison_startup_failure_returns_diagnostic_not_false_handoff_proof(monkeypatch, capsys) -> None:
    def blocked(**kwargs):
        raise RuntimeError("Local FoundationDB unavailable")
    monkeypatch.setattr(experimental_lane, "_comparison_cluster_session", blocked)
    assert run_lab.main(["--compare-handoff", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert "cluster startup failed" in result["stable"]["error"].lower()
    assert "FoundationDB unavailable" in result["experimental"]["error"]


def test_comparison_launches_only_missing_local_clusters_and_owns_cleanup(monkeypatch, tmp_path) -> None:
    from tools.hub_crash_lab import run_lab as crash_lab
    from tools.stable_hub_lab import run_lab as stable_lab

    topology = SimpleNamespace(
        hubs=tuple(SimpleNamespace(hub_id=f"smoke-hub{i}", hub_url=f"http://127.0.0.1:887{i-1}")
                   for i in range(1, 4)),
        entry_urls=tuple(f"http://127.0.0.1:887{i}" for i in range(3)),
        storage={"cluster_file": str(tmp_path / "docker.cluster")},
    )
    cluster_path = tmp_path / "docker.cluster"
    cluster_path.write_text("docker:docker@127.0.0.1:4550", encoding="utf-8")
    topology_path = tmp_path / "deploy" / "hub-topology" / "smoke-topology.json"
    topology_path.parent.mkdir(parents=True)
    topology_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("main_computer.stable_hub_topology.load_stable_hub_topology", lambda _: topology)
    monkeypatch.setattr(run_lab, "DEFAULT_TOPOLOGY", topology_path)
    called = []
    monkeypatch.setattr(crash_lab, "_preflight_local_docker_fdb", lambda cluster: called.append(("fdb", str(cluster))))
    monkeypatch.setattr(experimental_lane, "_comparison_port_open", lambda port: False)
    monkeypatch.setattr(experimental_lane, "_comparison_services_ready", lambda **kwargs: (True, True))
    monkeypatch.setattr(stable_lab, "_child_command", lambda topo, hub: ["stable-server", str(hub)])
    terminated = []
    monkeypatch.setattr(stable_lab, "_terminate_children", lambda children: terminated.extend(children))
    class Process:
        def __init__(self, cmd, **kwargs):
            self.cmd = cmd
            self.returncode = None
        def poll(self):
            return None
    started = []
    def start(cmd, **kwargs):
        p = Process(cmd, **kwargs)
        started.append(p)
        return p
    monkeypatch.setattr(experimental_lane.subprocess, "Popen", start)
    with experimental_lane._comparison_cluster_session(cluster_file=cluster_path, timeout=1.0):
        assert len(started) == 4
        assert [p.cmd[0] for p in started[:3]] == ["stable-server"] * 3
        assert "--serve-exp-cluster" in started[-1].cmd
    assert len(terminated) == 4
    assert called == [("fdb", str(cluster_path.resolve()))]


def test_comparison_refuses_partially_occupied_ports_without_starting_hubs(monkeypatch, tmp_path) -> None:
    import pytest
    from tools.hub_crash_lab import run_lab as crash_lab
    topology = SimpleNamespace(
        hubs=tuple(SimpleNamespace(hub_id=f"smoke-hub{i}", hub_url=f"http://127.0.0.1:887{i-1}")
                   for i in range(1, 4)),
        entry_urls=tuple(f"http://127.0.0.1:887{i}" for i in range(3)),
        storage={"cluster_file": str(tmp_path / "docker.cluster")},
    )
    cluster_path = tmp_path / "docker.cluster"
    cluster_path.write_text("docker:docker@127.0.0.1:4550", encoding="utf-8")
    topology_path = tmp_path / "deploy" / "hub-topology" / "smoke-topology.json"
    topology_path.parent.mkdir(parents=True)
    topology_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("main_computer.stable_hub_topology.load_stable_hub_topology", lambda _: topology)
    monkeypatch.setattr(run_lab, "DEFAULT_TOPOLOGY", topology_path)
    monkeypatch.setattr(crash_lab, "_preflight_local_docker_fdb", lambda cluster: None)
    monkeypatch.setattr(experimental_lane, "_comparison_port_open", lambda port: port == 8870)
    with pytest.raises(RuntimeError, match="Some Stable comparison ports are occupied"):
        with experimental_lane._comparison_cluster_session(cluster_file=cluster_path, timeout=1.0):
            pytest.fail("Must not start comparison services")


def test_comparison_reuses_verified_clusters_without_starting_or_stopping_them(monkeypatch, tmp_path) -> None:
    from tools.hub_crash_lab import run_lab as crash_lab
    from tools.stable_hub_lab import run_lab as stable_lab
    import pytest

    topology = SimpleNamespace(
        hubs=tuple(SimpleNamespace(hub_id=f"smoke-hub{i}", hub_url=f"http://127.0.0.1:887{i-1}")
                   for i in range(1, 4)),
        entry_urls=tuple(f"http://127.0.0.1:887{i}" for i in range(3)),
        storage={"cluster_file": str(tmp_path / "docker.cluster")},
    )
    cluster_path = tmp_path / "docker.cluster"
    cluster_path.write_text("docker:docker@127.0.0.1:4550", encoding="utf-8")
    topology_path = tmp_path / "deploy" / "hub-topology" / "smoke-topology.json"
    topology_path.parent.mkdir(parents=True)
    topology_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("main_computer.stable_hub_topology.load_stable_hub_topology", lambda _: topology)
    monkeypatch.setattr(run_lab, "DEFAULT_TOPOLOGY", topology_path)
    monkeypatch.setattr(crash_lab, "_preflight_local_docker_fdb", lambda cluster: None)
    monkeypatch.setattr(experimental_lane, "_comparison_port_open", lambda port: True)
    monkeypatch.setattr(experimental_lane, "_comparison_services_ready", lambda **kwargs: (True, True))
    monkeypatch.setattr(experimental_lane.subprocess, "Popen", lambda *a, **kw: pytest.fail("Must reuse live clusters"))
    monkeypatch.setattr(stable_lab, "_terminate_children", lambda children: pytest.fail("Must not stop live clusters") if children else None)
    with experimental_lane._comparison_cluster_session(cluster_file=cluster_path, timeout=1.0):
        pass


def test_comparison_rejects_wrong_identity_on_occupied_ports(monkeypatch, tmp_path) -> None:
    from tools.hub_crash_lab import run_lab as crash_lab
    import pytest
    topology = SimpleNamespace(
        hubs=tuple(SimpleNamespace(hub_id=f"smoke-hub{i}", hub_url=f"http://127.0.0.1:887{i-1}")
                   for i in range(1, 4)),
        entry_urls=tuple(f"http://127.0.0.1:887{i}" for i in range(3)),
        storage={"cluster_file": str(tmp_path / "docker.cluster")},
    )
    cluster_path = tmp_path / "docker.cluster"
    cluster_path.write_text("docker:docker@127.0.0.1:4550", encoding="utf-8")
    topology_path = tmp_path / "deploy" / "hub-topology" / "smoke-topology.json"
    topology_path.parent.mkdir(parents=True)
    topology_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("main_computer.stable_hub_topology.load_stable_hub_topology", lambda _: topology)
    monkeypatch.setattr(run_lab, "DEFAULT_TOPOLOGY", topology_path)
    monkeypatch.setattr(crash_lab, "_preflight_local_docker_fdb", lambda cluster: None)
    monkeypatch.setattr(experimental_lane, "_comparison_port_open", lambda port: True)
    monkeypatch.setattr(experimental_lane, "_comparison_services_ready", lambda **kwargs: (True, False))
    with pytest.raises(RuntimeError, match="failing Experimental identity"):
        with experimental_lane._comparison_cluster_session(cluster_file=cluster_path, timeout=1.0):
            pytest.fail("Must not run against an unknown service")
