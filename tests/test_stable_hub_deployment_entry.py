from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[1]


def _launcher() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hub_stable_launcher", REPO_ROOT / "run-stable-hub.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stable_launcher_refuses_to_select_implicit_identity(monkeypatch, tmp_path: Path) -> None:
    entry = _launcher()
    monkeypatch.delenv("MAIN_COMPUTER_HUB_CONTROL_TOPOLOGY_PATH", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_HUB_CONTROL_HUB_ID", raising=False)
    assert entry.main() == 2


def test_stable_launcher_uses_frozen_hub_control_identity(monkeypatch, tmp_path: Path) -> None:
    entry = _launcher()
    frozen = tmp_path / "topology.json"
    frozen.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_TOPOLOGY_PATH", str(frozen))
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_HUB_ID", "mainneta-hub1")
    monkeypatch.setenv("MAIN_COMPUTER_HUB_PORT", "8790")
    recorded = []
    monkeypatch.setattr(entry, "stable_hub_main", lambda argv: recorded.extend(argv) or 0)
    assert entry.main() == 0
    assert recorded == ["--topology", str(frozen), "--hub-id", "mainneta-hub1", "--bind-host", "0.0.0.0", "--bind-port", "8790"]


def test_stable_dockerfile_disables_mainnet_dev_credit_default() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile.hub.stable").read_text(encoding="utf-8")
    assert 'CMD ["python", "/app/run-stable-hub.py"]' in dockerfile
    assert "MAIN_COMPUTER_STABLE_HUB_DEV_ACCOUNT_CREDITS=0" in dockerfile
    assert "eth-account==0.13.7" in dockerfile


def test_production_stable_hub_never_seeds_development_credit() -> None:
    from dataclasses import replace
    from main_computer.stable_hub_topology import load_stable_hub_topology
    from main_computer.stable_hub_worker_sessions import (
        InMemoryStableWorkerSessionStore, StableHubPayoutLedgerDirectory,
    )
    topology = load_stable_hub_topology(REPO_ROOT / "deploy/hub-topology/dev-topology.json")
    for network in ("mainnet", "testnet"):
        production_topology = replace(topology, network={**topology.network, "network_key": network})
        ledger = StableHubPayoutLedgerDirectory(
            topology=production_topology,
            hub_id=production_topology.hubs[0].hub_id,
            store=InMemoryStableWorkerSessionStore(),
        )
        account = ledger._ensure_account({}, account_id="acct_new", wallet_address="")
        assert account["available_credit_wei"] == "0"
        assert account["metadata"]["source"] == "stable_hub_production_zero"


def test_stable_launcher_materializes_frozen_topology_and_fdb(monkeypatch, tmp_path: Path) -> None:
    entry = _launcher()
    topology = tmp_path / "hub" / "hub-topology.json"
    cluster = tmp_path / "hub" / "fdb.cluster"
    encoded = base64.b64encode(json.dumps({"hubs": [{"hub_id": "testnetb-hub1"}]}).encode()).decode()
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_TOPOLOGY_PATH", str(topology))
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_HUB_ID", "testnetb-hub1")
    monkeypatch.setenv("MAIN_COMPUTER_HUB_PORT", "8785")
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_TOPOLOGY_B64", encoded)
    monkeypatch.setenv("MCT", encoded)
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_FDB_CLUSTER_CONTENTS", "test:test@10.124.0.3:4550")
    monkeypatch.setenv("MCF", "test:test@10.124.0.3:4550")
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_CLUSTER_FILE", str(cluster))
    seen = []
    monkeypatch.setattr(entry, "stable_hub_main", lambda argv: seen.append(argv) or 0)

    assert entry.main() == 0
    assert json.loads(topology.read_text()) == {"hubs": [{"hub_id": "testnetb-hub1"}]}
    assert cluster.read_text() == "test:test@10.124.0.3:4550\n"
    assert seen[0][-2:] == ["--bind-port", "8785"]
    mtime = (topology.stat().st_mtime_ns, cluster.stat().st_mtime_ns)
    assert entry.main() == 0
    assert (topology.stat().st_mtime_ns, cluster.stat().st_mtime_ns) == mtime


def test_stable_launcher_refreshes_stale_volume_projection(monkeypatch, tmp_path: Path) -> None:
    entry = _launcher()
    topology = tmp_path / "hub-topology.json"
    topology.write_text('{"old": true}')
    cluster = tmp_path / "fdb.cluster"
    cluster.write_text("obsolete\n")
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_TOPOLOGY_PATH", str(topology))
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_HUB_ID", "testnetb-hub1")
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_TOPOLOGY_B64", base64.b64encode(b'{"fresh": true}').decode())
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_FDB_CLUSTER_CONTENTS", "current:current@10.124.0.3:4550")
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_CLUSTER_FILE", str(cluster))
    monkeypatch.setattr(entry, "stable_hub_main", lambda _: 0)
    assert entry.main() == 0
    assert json.loads(topology.read_text()) == {"fresh": True}
    assert cluster.read_text() == "current:current@10.124.0.3:4550\n"


def test_stable_launcher_rejects_invalid_or_disagreeing_topology(monkeypatch, tmp_path: Path) -> None:
    entry = _launcher()
    topology = tmp_path / "hub-topology.json"
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_TOPOLOGY_PATH", str(topology))
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_HUB_ID", "testnetb-hub1")
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_FDB_CLUSTER_CONTENTS", "one:one@10.124.0.3:4550")
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_CLUSTER_FILE", str(tmp_path / "fdb.cluster"))
    called = []
    monkeypatch.setattr(entry, "stable_hub_main", lambda _: called.append(True) or 0)
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_TOPOLOGY_B64", "!bad-base64!")
    assert entry.main() == 2
    assert not topology.exists()
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_TOPOLOGY_B64", base64.b64encode(b"{}").decode())
    monkeypatch.setenv("MCT", base64.b64encode(b'{"different":true}').decode())
    assert entry.main() == 2
    assert not called
