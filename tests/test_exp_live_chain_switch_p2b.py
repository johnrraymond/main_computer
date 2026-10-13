"""P2-B dev-chain opt-in must be real, local-only, and fail closed."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.exp_hub_lab import experimental_lane
from tools.exp_hub_lab import live_chain


def _manifest(tmp_path: Path) -> Path:
    obj = {
        "schema": "main-computer.deployment.v1", "environment": "dev",
        "chain": {"chain_id": 42424242, "host_rpc_url": "http://127.0.0.1:18545"},
        "contracts": {
            "hub_credit_bridge_escrow": {"address": "0x" + "12" * 20},
            "xlag-bridge-reserve": {"address": "0x" + "34" * 20},
        },
        "smoke_client": {"wallet_path": "private-local-wallet.json"},
        "hub_admin": {"wallet_path": "admin.json"},
    }
    path = tmp_path / "dev.json"
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def test_chain_preflight_requires_real_rpc_deployed_contracts(monkeypatch, tmp_path):
    path = _manifest(tmp_path)
    # Tests use a temporary repo-root fixture without loading any signer file.
    monkeypatch.setattr(live_chain.Path, "is_relative_to", lambda self, other: True)
    calls = []
    def rpc(_url, method, params):
        calls.append((method, params))
        return "0x28757b2" if method == "eth_chainId" else "0x6001"
    monkeypatch.setattr(live_chain, "_rpc", rpc)
    config = live_chain.preflight_local_dev_chain(path)
    assert config.chain_id == 42424242
    assert config.rpc_url == "http://127.0.0.1:18545"
    assert [call[0] for call in calls] == ["eth_chainId", "eth_getCode", "eth_getCode"]
    assert "private" not in str(config.public_status())


@pytest.mark.parametrize("bad_rpc", ["https://127.0.0.1:18545", "http://example.org:8545", "http://127.0.0.1", "http://127.0.0.1:18545/foo"])
def test_chain_preflight_rejects_remote_or_nonexplicit_rpc(monkeypatch, tmp_path, bad_rpc):
    path = _manifest(tmp_path)
    monkeypatch.setattr(live_chain.Path, "is_relative_to", lambda self, other: True)
    monkeypatch.setattr(live_chain, "_rpc", lambda *_args: pytest.fail("must not dial RPC"))
    with pytest.raises(ValueError, match="loopback"):
        live_chain.preflight_local_dev_chain(path, rpc_override=bad_rpc)


def test_chain_preflight_chain_mismatch_fails_before_work(monkeypatch, tmp_path):
    path = _manifest(tmp_path)
    monkeypatch.setattr(live_chain.Path, "is_relative_to", lambda self, other: True)
    monkeypatch.setattr(live_chain, "_rpc", lambda *_args: "0x1")
    with pytest.raises(RuntimeError, match="chain id mismatch"):
        live_chain.preflight_local_dev_chain(path)


def test_chain_preflight_missing_code_fails_before_work(monkeypatch, tmp_path):
    path = _manifest(tmp_path)
    monkeypatch.setattr(live_chain.Path, "is_relative_to", lambda self, other: True)
    monkeypatch.setattr(live_chain, "_rpc", lambda _url, method, _params: "0x28757b2" if method == "eth_chainId" else "0x")
    with pytest.raises(RuntimeError, match="no contract bytecode"):
        live_chain.preflight_local_dev_chain(path)


def test_live_chain_launcher_passes_dev_chain_and_real_smoke(monkeypatch, tmp_path):
    monkeypatch.setenv("MAIN_COMPUTER_EXP_TEMPORAL_ADDRESS", "127.0.0.1:7233")
    monkeypatch.setattr(experimental_lane, "_require_ports_free", lambda: None)
    path = tmp_path / "local.json"
    chain = live_chain.LocalDevChain(path, "http://127.0.0.1:18545", 42424242,
                                    "0x" + "12" * 20, "0x" + "34" * 20)
    monkeypatch.setattr(live_chain, "preflight_local_dev_chain", lambda *_args, **_kwargs: chain)
    args = []
    monkeypatch.setattr("main_computer.exp_fdb_hub.main", lambda argv: args.extend(argv) or 0)
    assert experimental_lane.run_lane(["--serve-exp-docker-lab", "--live-chain", "--docker-workers", "2", "--docker-requesters", "1"]) == 0
    assert args[args.index("--bridge-backend") + 1] == "dev-chain"
    assert args[args.index("--network-key") + 1] == "exp-handoff-parity"
    assert args[args.index("--chain-rpc-url") + 1] == chain.rpc_url
    assert args[args.index("--chain-id") + 1] == str(chain.chain_id)
    assert "--enable-smoke-bridge" in args
    assert "--live-chain-smoke" in args
    assert args[args.index("--dev-chain-deployment-path") + 1] == str(path)
    assert "--require-multisession-auth" in args
    assert "--docker" in args
    # A real chain is an external settlement rail, not a different Hub network.
    # Verify the runtime-derived P2-A namespace, not just the literal flag.
    from main_computer.exp_fdb_hub import (
        build_experimental_config, build_experimental_stable_topology, build_parser,
    )
    from main_computer.stable_hub_worker_sessions import stable_temporal_namespace_for_topology
    hub_args = build_parser().parse_args(args)
    config, fdb_config = build_experimental_config(hub_args, port=8970)
    topology = build_experimental_stable_topology(
        hub_args, config=config, fdb_config=fdb_config, port=8970,
    )
    assert stable_temporal_namespace_for_topology(topology) == "main-computer-exp-handoff-parity"
    assert config.hub_bridge_backend == "dev-chain"
    assert config.chain_id == 42424242


def test_default_launch_is_still_mock(monkeypatch):
    monkeypatch.setattr(experimental_lane, "_require_ports_free", lambda: None)
    args = []
    monkeypatch.setattr("main_computer.exp_fdb_hub.main", lambda argv: args.extend(argv) or 0)
    assert experimental_lane.run_lane(["--serve-exp-cluster"]) == 0
    assert args[args.index("--bridge-backend") + 1] == "mock-chain"
    assert "--live-chain-smoke" not in args


def test_live_chain_preflight_refusal_does_not_start_hubs(monkeypatch):
    monkeypatch.setattr(experimental_lane, "_require_ports_free", lambda: None)
    monkeypatch.setattr(live_chain, "preflight_local_dev_chain", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("unreachable RPC")))
    monkeypatch.setattr("main_computer.exp_fdb_hub.main", lambda args: pytest.fail("must not start Hubs"))
    assert experimental_lane.run_lane(["--serve-exp-cluster", "--live-chain"]) == 2


def test_cannot_enable_live_chain_on_mock_verifier():
    with pytest.raises(SystemExit, match="2"):
        experimental_lane.run_lane(["--verify-exp-stress", "--live-chain"])


def test_main_rejects_fake_live_smoke(monkeypatch):
    from main_computer import exp_fdb_hub
    monkeypatch.setattr(exp_fdb_hub, "serve_exp_fdb_hubs", lambda _args: pytest.fail("must not start"))
    with pytest.raises(SystemExit, match="requires explicit dev-chain"):
        exp_fdb_hub.main(["--docker", "--docker-request-mode", "live_session_v1", "--live-chain-smoke"])


def test_smoke_backend_respects_explicit_host_rpc_over_docker_internal(monkeypatch, tmp_path):
    from main_computer import dev_chain_bridge
    from main_computer import hub_bridge_backend
    seen = {}
    class FakeAdapter:
        def __init__(self, **kw):
            self.rpc_url = kw["rpc_url_override"]
            self.escrow_address = "0x" + "12" * 20
            self.requester_wallet = type("Wallet", (), {"address": "0x" + "34" * 20})()
            self.bridge_controller_wallet = type("Wallet", (), {"address": "0x" + "56" * 20})()
            self.signer_configured = True
    def fake_from_deployment(**kwargs):
        seen.update(kwargs)
        return FakeAdapter(**kwargs)
    monkeypatch.setattr(dev_chain_bridge.DevChainBridgeAdapter, "from_deployment", fake_from_deployment)
    manifest = tmp_path / "latest.json"
    manifest.write_text("{}")
    backend = hub_bridge_backend.build_hub_bridge_backend(
        backend_name="dev-chain", repo_root=tmp_path,
        dev_chain_deployment_path=manifest, network_key="dev",
        chain_rpc_url="http://127.0.0.1:18545", enable_smoke_bridge=True,
    )
    assert backend.status()["mode"] == "smoke-bridge"
    assert seen["rpc_url_override"] == "http://127.0.0.1:18545"


def test_live_chain_rejects_chain_id_unusable_by_p1_msk(monkeypatch, tmp_path):
    path = _manifest(tmp_path)
    data = json.loads(path.read_text())
    data["chain"]["chain_id"] = 1
    path.write_text(json.dumps(data))
    monkeypatch.setattr(live_chain.Path, "is_relative_to", lambda self, other: True)
    monkeypatch.setattr(live_chain, "_rpc", lambda *_args: pytest.fail("must not contact wrong chain"))
    with pytest.raises(ValueError, match="42424242"):
        live_chain.preflight_local_dev_chain(path)
