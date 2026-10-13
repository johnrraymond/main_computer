"""The historical Experimental Hub Lab must exercise the real Stable Hub server."""
from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from main_computer.stable_hub import create_stable_hub_server
from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
from main_computer.stable_hub_topology import StableHubTopologyError, load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
from tools.exp_hub_lab import run_lab as exp_lab
from tools.exp_hub_lab.stable_parity import require_isolated_smoke_topology, verify_stable_interfaces
from tools.stable_hub_lab import run_lab as stable_lab

SMOKE = Path("deploy/hub-topology/smoke-topology.json")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_exp_lab_delegates_commands_to_actual_stable_lab(monkeypatch):
    received = []
    def fake_stable_main(args):
        received.append(args)
        return 17
    monkeypatch.setattr(stable_lab, "main", fake_stable_main)
    assert exp_lab.main(["--topology", str(SMOKE), "--serve-cluster"]) == 17
    assert received == [["--topology", str(SMOKE), "--serve-cluster"]]
    assert exp_lab.main(["--topology", str(SMOKE), "--verify"]) == 17
    assert received[-1][-1] == "--verify"
    assert exp_lab.main(["--topology", str(SMOKE), "--verify-stress"]) == 17
    assert received[-1][-1] == "--verify-stress"


def test_exp_lab_rejects_old_exp_protocol_switches(capsys):
    assert exp_lab.main(["--topology", str(SMOKE), "--verify-full-e2e"]) != 0
    assert exp_lab.main(["--topology", str(SMOKE), "--enable-smoke-bridge"]) != 0


def test_exp_lab_rejects_live_network_and_remote_topologies(tmp_path, monkeypatch):
    monkeypatch.setattr(stable_lab, "main", lambda args: pytest.fail("must never call production-compatible lab"))
    mainnet = Path("deploy/hub-topology/mainnet-topology.json")
    assert exp_lab.main(["--topology", str(mainnet), "--serve-cluster"]) == 2
    doc = json.loads(SMOKE.read_text())
    doc["hubs"][0]["hub_url"] = "https://real-hub.example"
    bad = tmp_path / "remote.json"
    bad.write_text(json.dumps(doc))
    with pytest.raises(StableHubTopologyError, match="loopback"):
        require_isolated_smoke_topology(bad)
    assert exp_lab.main(["--topology", str(bad), "--verify"]) == 2


def test_exp_lab_still_requires_isolated_foundationdb_namespace(tmp_path):
    doc = json.loads(SMOKE.read_text())
    doc["storage"]["namespace"] = "main-computer-mainnet"
    bad = tmp_path / "namespace.json"
    bad.write_text(json.dumps(doc))
    with pytest.raises(StableHubTopologyError, match="namespace"):
        require_isolated_smoke_topology(bad)


def test_exp_lab_default_is_smoke_topology_not_deployment_topology():
    assert require_isolated_smoke_topology(SMOKE).network["network_kind"] == "smoke"
    assert exp_lab.main(["--topology", str(SMOKE), "--validate-only", "--json"]) == 0


def test_live_read_only_parity_uses_real_stable_hubs(tmp_path):
    ports = [_free_port(), _free_port()]
    document = json.loads(SMOKE.read_text())
    document["hubs"] = document["hubs"][:2]
    document["entry_urls"] = []
    for hub, port in zip(document["hubs"], ports):
        url = f"http://127.0.0.1:{port}"
        hub["hub_url"] = hub["public_url"] = url
        document["entry_urls"].append(url)
    topology_path = tmp_path / "smoke-topology.json"
    topology_path.write_text(json.dumps(document))
    topology = load_stable_hub_topology(topology_path)
    keys = InMemoryStableMultiSessionKeyStore()
    sessions = InMemoryStableWorkerSessionStore()
    servers = [create_stable_hub_server(
        topology=topology, hub_id=hub["hub_id"], bind_host="127.0.0.1",
        bind_port=port, multisession_key_store=keys, worker_session_store=sessions,
    ) for hub, port in zip(document["hubs"], ports)]
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
    for thread in threads:
        thread.start()
    try:
        result = verify_stable_interfaces(topology_path=topology_path, timeout=3.0)
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=3)
    assert result["ok"] is True, result
    assert result["shared_wallet_identity"] is True
    assert result["read_only"] is True
    assert len(result["checks"]) == 2
    assert all(all(row["proof"].values()) for row in result["checks"])
    assert all(row["temporal_configured"] is False for row in result["checks"])
    assert sessions.load().get("payout_accounts", {}) == {}


def test_offline_parity_suite_is_available_without_live_chain(monkeypatch):
    received = []
    monkeypatch.setattr("tools.exp_hub_lab.stable_parity.run_offline_regressions", lambda **kwargs: received.append(kwargs) or 0)
    assert exp_lab.main(["--verify-suite", "--json"]) == 0
    assert received == [{"json_output": True}]
