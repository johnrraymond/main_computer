from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "tools" / "coolify_qbft_network.py"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("coolify_qbft_network_mutate", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _args(**overrides: Any) -> SimpleNamespace:
    values = {
        "add": "",
        "retire": "",
        "promote": "",
        "demote": "",
        "apply": False,
        "packet": "",
        "observe_chain": False,
        "chain_observation_timeout_s": 8.0,
        "rpc_url": "",
        "rpc_user_agent": "test-agent",
        "ack_consensus_change": False,
        "ack_mainnet_consensus_change": False,
        "config_export_transport": "public-entry",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def write_mutation_private_state(tmp_path: Path) -> Path:
    state_path = tmp_path / "main_computer.private.yaml"
    state_path.write_text(
        """
coolify:
  project_name: Main Computer
  hosts:
    A:
      name: coolify-a
      public_ip: 198.51.100.10
      url: http://198.51.100.10:8000/
      api_token: secret-a
      server_uuid: server-a
      destination_uuid: destination-a
    B:
      name: coolify-b
      public_ip: 198.51.100.11
      url: http://198.51.100.11:8000/
      api_token: secret-b
      server_uuid: server-b
      destination_uuid: destination-b

networks:
  testnet:
    display_name: Main Computer Testnet
    kind: testnet
    chain_id: 42424241
    rpc: https://testnet-rpc.greatlibrary.io
    qbft:
      instances:
        validator-rpc-1:
          coolify_host: A
          roles: [rpc, validator]
          rpc_host_port: 30010
          p2p_host_port: 30321
        validator-2:
          coolify_host: B
          roles: [validator]
          p2p_host_port: 30312
        validator-rpc-2:
          coolify_host: B
          roles: [rpc, validator]
          rpc_host_port: 30011
          p2p_host_port: 30322
        rpc-2:
          coolify_host: B
          roles: [rpc]
          rpc_host_port: 30012
""".lstrip(),
        encoding="utf-8",
    )
    return state_path


def test_private_state_rpc_only_instance_does_not_require_p2p_host_port(tmp_path: Path) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", private_state_path=write_mutation_private_state(tmp_path))
    rpc_service = module.planned_service_by_id(plan)["rpc-2"]

    assert rpc_service.roles == ("rpc",)
    assert rpc_service.rpc_host_port == 30012
    assert rpc_service.p2p_host_port is None

    packet = module.mutate_network(plan, _args(add="rpc-2"))

    assert packet["mutation"] == "add-rpc"
    assert packet["affected_rpc_backends"] == ["http://198.51.100.11:30012"]
    assert packet["affected_public_rpc_entry_hosts"] == ["b"]


def test_mutate_add_validator_plan_is_read_only_and_names_host_service(tmp_path: Path) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", private_state_path=write_mutation_private_state(tmp_path))

    packet = module.mutate_network(plan, _args(add="validator-2"))

    assert packet["ok"] is True
    assert packet["mode"] == "plan"
    assert packet["mutation"] == "add-validator"
    assert packet["affected_instances"] == ["validator-2"]
    assert packet["affected_coolify_hosts"] == ["b"]
    assert packet["affected_coolify_services"] == ["main-computer-qbft-testnet-b"]
    assert packet["affected_rpc_backends"] == []
    assert packet["affected_public_rpc_entries"] == []
    assert packet["affected_public_rpc_entry_hosts"] == []
    assert packet["requires_consensus_change"] is True
    assert packet["requires_ack"] == ["consensus-validator-change"]
    assert "propose-validator-vote" in packet["phases"]
    assert packet["execution"]["no_mutation_performed"] is True


def test_mutate_add_validator_rpc_updates_public_entry_after_consensus(tmp_path: Path) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", private_state_path=write_mutation_private_state(tmp_path))

    packet = module.mutate_network(plan, _args(add="validator-rpc-2"))

    assert packet["mutation"] == "add-validator-rpc"
    assert packet["affected_rpc_backends"] == ["http://198.51.100.11:30011"]
    assert packet["affected_public_rpc_entries"] == ["https://testnet-rpc.greatlibrary.io"]
    assert packet["affected_public_rpc_entry_hosts"] == ["b"]

    phases = packet["phases"]
    assert phases.index("wait-validator-set") < phases.index("update-public-rpc-entry")
    assert phases.index("verify-chain-id") < phases.index("propose-validator-vote")


def test_mutate_retire_validator_rpc_removes_public_entry_before_consensus_work(tmp_path: Path) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", private_state_path=write_mutation_private_state(tmp_path))

    packet = module.mutate_network(plan, _args(retire="validator-rpc-1"))

    assert packet["mutation"] == "retire-validator-rpc"
    assert packet["affected_rpc_backends"] == ["http://198.51.100.10:30010"]
    assert packet["affected_public_rpc_entries"] == ["https://testnet-rpc.greatlibrary.io"]
    assert packet["affected_public_rpc_entry_hosts"] == ["a"]

    phases = packet["phases"]
    assert phases.index("remove-public-rpc-entry") < phases.index("propose-validator-vote")
    assert phases.index("verify-public-rpc") < phases.index("propose-validator-vote")
    assert phases.index("wait-validator-set-removal") < phases.index("stop-coolify-service")


def test_mutate_apply_validator_is_still_intentionally_refused(tmp_path: Path) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", private_state_path=write_mutation_private_state(tmp_path))

    packet = module.mutate_network(plan, _args(add="validator-2", apply=True))

    assert packet["ok"] is False
    assert packet["mode"] == "apply"
    assert "rpc-only adds" in packet["error"]
    assert packet["execution"]["no_mutation_performed"] is True


def test_mutate_apply_rpc_add_slurps_config_stages_public_entry_after_direct_rpc(
    tmp_path: Path, monkeypatch: Any
) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", private_state_path=write_mutation_private_state(tmp_path))
    calls: list[tuple[str, Any]] = []
    bundle = {
        "ok": True,
        "source_host": "a",
        "lineage_hash": "abc123",
        "files": {
            "genesis_json": '{"config":{"chainId":42424241}}\n',
            "static_nodes_all_json": "[]\n",
            "network_metadata_json": '{"network":"testnet"}\n',
        },
        "sha256": {},
    }

    def fake_discover_qbft_config_bundle(discover_plan: Any, discover_args: Any, target_services: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        calls.append(("discover-config", discover_plan, discover_args, target_services))
        assert [service.id for service in target_services] == ["rpc-2"]
        return bundle, {"ok": True, "selected_source_host": "a", "selected_lineage_hash": "abc123"}

    def fake_coolify_sync(sync_plan: Any, sync_args: Any, *, deploy: bool = False) -> dict[str, Any]:
        calls.append(("coolify-sync", sync_plan, sync_args, deploy))
        assert [host.id for host in sync_plan.hosts] == ["b"]
        assert [service.id for service in sync_plan.services] == ["rpc-2"]
        assert sync_args.host == "b"
        assert sync_args.no_bootstrap is True
        assert sync_args._runtime_import_bundle is bundle
        assert deploy is True
        return {"ok": True, "service_uuid": "svc-b", "service_name": "main-computer-qbft-testnet-b"}

    def fake_wait_for_rpc(wait_plan: Any, wait_args: Any) -> dict[str, Any]:
        calls.append(("wait-rpc", wait_plan, wait_args))
        assert wait_plan is plan
        assert wait_args.rpc_url == "http://198.51.100.11:30012"
        return {"ok": True, "rpc_url": wait_args.rpc_url, "chain_id": hex(plan.chain_id)}

    def fake_observe_chain_state(observe_plan: Any, observe_args: Any) -> dict[str, Any]:
        calls.append(("observe-chain", observe_plan, observe_args))
        assert observe_plan is plan
        assert observe_args.rpc_url == ""
        return {
            "ok": True,
            "canonical_rpc": "ok",
            "consensus": "ok",
            "chain": {"chain_id": hex(plan.chain_id), "block_number": 123},
            "probes": [],
        }

    monkeypatch.setattr(module, "discover_qbft_config_bundle", fake_discover_qbft_config_bundle)
    monkeypatch.setattr(module, "coolify_sync", fake_coolify_sync)
    monkeypatch.setattr(module, "wait_for_rpc", fake_wait_for_rpc)
    monkeypatch.setattr(module, "observe_chain_state", fake_observe_chain_state)

    packet = module.mutate_network(plan, _args(add="rpc-2", apply=True))

    assert packet["ok"] is True
    assert packet["mode"] == "apply"
    assert packet["mutation"] == "add-rpc"
    assert packet["execution"]["implemented"] is True
    assert packet["execution"]["no_mutation_performed"] is False
    assert [call[0] for call in calls] == [
        "discover-config",
        "coolify-sync",
        "wait-rpc",
        "coolify-sync",
        "observe-chain",
    ]
    assert calls[1][2]._include_rpc_public_entry is False
    assert calls[3][2]._include_rpc_public_entry is True
    assert [phase["phase"] for phase in packet["apply_phases"]] == [
        "slurp-current-config",
        "seed-new-node-bootstrap",
        "deploy-service",
        "wait-direct-rpc",
        "commit-full-network-topology",
        "update-public-rpc-entry",
        "verify-public-rpc",
    ]


def test_mutate_apply_rpc_add_dry_run_does_not_wait_or_probe(tmp_path: Path, monkeypatch: Any) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", private_state_path=write_mutation_private_state(tmp_path))
    calls: list[str] = []

    def fake_discover_qbft_config_bundle(discover_plan: Any, discover_args: Any, target_services: Any) -> tuple[None, dict[str, Any]]:
        calls.append("discover-config")
        return None, {"ok": True, "dry_run": True, "candidate_hosts": ["a"]}

    def fake_coolify_sync(sync_plan: Any, sync_args: Any, *, deploy: bool = False) -> dict[str, Any]:
        calls.append("coolify-sync")
        assert [service.id for service in sync_plan.services] == ["rpc-2"]
        assert sync_args.no_bootstrap is True
        assert deploy is True
        return {"ok": True, "dry_run": True, "compose": "services: {}"}

    def fail_wait_for_rpc(wait_plan: Any, wait_args: Any) -> dict[str, Any]:
        raise AssertionError("dry-run mutate apply must not wait for live RPC")

    def fail_observe_chain_state(observe_plan: Any, observe_args: Any) -> dict[str, Any]:
        raise AssertionError("dry-run mutate apply must not probe canonical RPC")

    monkeypatch.setattr(module, "discover_qbft_config_bundle", fake_discover_qbft_config_bundle)
    monkeypatch.setattr(module, "coolify_sync", fake_coolify_sync)
    monkeypatch.setattr(module, "wait_for_rpc", fail_wait_for_rpc)
    monkeypatch.setattr(module, "observe_chain_state", fail_observe_chain_state)

    packet = module.mutate_network(plan, _args(add="rpc-2", apply=True, dry_run=True))

    assert packet["ok"] is True
    assert packet["mode"] == "apply"
    assert packet["execution"]["implemented"] is True
    assert packet["execution"]["dry_run"] is True
    assert packet["execution"]["no_mutation_performed"] is True
    assert calls == ["discover-config", "coolify-sync", "coolify-sync"]
    assert [phase["phase"] for phase in packet["apply_phases"]] == [
        "slurp-current-config",
        "seed-new-node-bootstrap",
        "deploy-service",
        "wait-direct-rpc",
        "commit-full-network-topology",
        "update-public-rpc-entry",
        "verify-public-rpc",
    ]



def test_qbft_config_exporter_scans_host_volume_legacy_volume_and_bind_root(tmp_path: Path) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", private_state_path=write_mutation_private_state(tmp_path))

    compose = module.render_qbft_config_exporter_compose(plan, "a", token="tok", port=38173)

    assert "main-computer-qbft-testnet-a-runtime:/sources/managed-host-volume:ro" in compose
    assert "main-computer-qbft-testnet-runtime:/sources/legacy-network-volume:ro" in compose
    assert 'source: "/srv/main-computer/qbft-testnet/runtime"' in compose
    assert "target: /sources/host-runtime-root" in compose
    assert "external: true" not in compose
    assert "alpine:3.20" in compose
    assert "python:3.12-alpine" not in compose
    assert "files_b64" in compose


def test_candidate_config_source_hosts_prefer_non_mutated_hosts(tmp_path: Path) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", private_state_path=write_mutation_private_state(tmp_path))
    rpc_service = module.planned_service_by_id(plan)["rpc-2"]

    assert module.candidate_qbft_config_source_hosts(plan, [rpc_service], _args()) == ["a"]


def test_public_entry_dynamic_config_can_expose_tokenized_config_export(tmp_path: Path) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", private_state_path=write_mutation_private_state(tmp_path))

    config = module.render_rpc_public_entry_dynamic_config(
        plan,
        "a",
        config_export={"token": "tok", "port": 38173},
    )

    assert "Path(`/__main-computer/qbft-config/tok.json`)" in config
    assert "priority: 10000" in config
    assert "http://198.51.100.10:38173" in config
    assert "testnet-rpc.greatlibrary.io" in config


def test_render_compose_for_host_can_add_config_export_sidecar(tmp_path: Path) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", private_state_path=write_mutation_private_state(tmp_path))

    compose = module.render_compose_for_host(
        plan,
        "a",
        include_bootstrap=False,
        config_export={"token": "tok", "port": 38173},
    )

    assert "qbft-config-export:" in compose
    assert '"0.0.0.0:38173:8080"' in compose
    assert "main-computer-qbft-testnet-a-runtime:/sources/managed-host-volume:ro" in compose
    assert "Path(`/__main-computer/qbft-config/tok.json`)" in compose


def test_export_qbft_config_from_host_via_public_entry_redeploys_source_and_cleans_up(
    tmp_path: Path, monkeypatch: Any
) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", private_state_path=write_mutation_private_state(tmp_path))
    sync_calls: list[Any] = []
    fetch_calls: list[Any] = []

    def fake_token() -> str:
        return "tok"

    def fake_coolify_sync(sync_plan: Any, sync_args: Any, *, deploy: bool = False) -> dict[str, Any]:
        sync_calls.append((sync_plan, sync_args, deploy))
        assert sync_plan is plan
        assert sync_args.host == "a"
        assert sync_args.coolify_service_name == "main-computer-qbft-testnet-a-config-export"
        assert sync_args._compose_override
        assert deploy is True
        return {"ok": True, "service_name": sync_args.coolify_service_name}

    def fake_fetch_json_url(url: str, *, timeout_s: float, headers: dict[str, str] | None = None) -> dict[str, Any]:
        fetch_calls.append((url, timeout_s, headers))
        assert url == "http://198.51.100.10/__main-computer/qbft-config/tok.json"
        assert headers == {"Host": "testnet-rpc.greatlibrary.io"}
        return {
            "ok": True,
            "source_mount": "managed-host-volume",
            "files_b64": {
                "genesis_json": "eyJjb25maWciOnt9fQo=",
                "static_nodes_all_json": "W10K",
                "network_metadata_json": "eyJuZXR3b3JrIjoidGVzdG5ldCJ9Cg==",
            },
            "sha256": {"genesis_json": "genesis-hash"},
        }

    monkeypatch.setattr(module, "qbft_config_export_token", fake_token)
    monkeypatch.setattr(module, "coolify_sync", fake_coolify_sync)
    monkeypatch.setattr(module, "fetch_json_url", fake_fetch_json_url)

    result = module.export_qbft_config_from_host_via_public_entry(plan, _args(), "a")

    assert result["ok"] is True
    assert result["transport"] == "public-entry"
    assert result["lineage_hash"] == "genesis-hash"
    assert len(fetch_calls) == 1
    assert len(sync_calls) == 2
    assert "qbft-config-export-public-entry:" in sync_calls[0][1]._compose_override
    assert "qbft-config-export-public-entry-cleanup:" in sync_calls[1][1]._compose_override


def test_normalize_qbft_config_bundle_preserves_export_source_diagnostics(tmp_path: Path) -> None:
    module = _load_module()

    result = module.normalize_qbft_config_bundle(
        {
            "ok": False,
            "source_mount": "",
            "source_candidates": [
                {
                    "label": "managed-host-volume",
                    "missing_files": ["/smoke/genesis.json"],
                }
            ],
            "missing_files": ["managed-host-volume:/smoke/genesis.json"],
            "sha256": {},
        },
        source_host="a",
        source_url="http://198.51.100.10:38173/tok.json",
    )

    assert result["ok"] is False
    assert result["source_mount"] == ""
    assert result["source_candidates"][0]["label"] == "managed-host-volume"
    assert result["missing_files"] == ["managed-host-volume:/smoke/genesis.json"]



def test_normalize_qbft_config_bundle_accepts_base64_payload(tmp_path: Path) -> None:
    module = _load_module()

    result = module.normalize_qbft_config_bundle(
        {
            "ok": True,
            "source_mount": "managed-host-volume",
            "files_b64": {
                "genesis_json": "eyJjb25maWciOnt9fQo=",
                "static_nodes_all_json": "W10K",
                "network_metadata_json": "eyJuZXR3b3JrIjoidGVzdG5ldCJ9Cg==",
            },
            "sha256": {"genesis_json": "genesis-hash"},
        },
        source_host="a",
        source_url="http://198.51.100.10:38173/tok.json",
    )

    assert result["ok"] is True
    assert result["source_mount"] == "managed-host-volume"
    assert result["lineage_hash"] == "genesis-hash"
    assert result["files"]["genesis_json"] == '{"config":{}}\n'
    assert result["files"]["static_nodes_all_json"] == "[]\n"


def test_qbft_config_export_timeout_has_separate_default() -> None:
    module = _load_module()

    assert module.qbft_config_export_timeout_s(SimpleNamespace(config_export_timeout_s=123.5)) == 123.5
    assert module.qbft_config_export_timeout_s(SimpleNamespace()) == module.DEFAULT_QBFT_CONFIG_EXPORT_TIMEOUT_S

def test_mutate_packet_path_writes_read_only_packet(tmp_path: Path) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", private_state_path=write_mutation_private_state(tmp_path))
    packet_path = tmp_path / "packets" / "mutate-rpc-2.json"

    packet = module.mutate_network(plan, _args(add="rpc-2", packet=str(packet_path)))

    assert packet_path.exists()
    assert packet["packet_path"] == str(packet_path)
    written = packet_path.read_text(encoding="utf-8")
    assert '"mutation": "add-rpc"' in written
    assert '"no_mutation_performed": true' in written


def test_observe_chain_prefers_canonical_rpc_and_reads_qbft_validators(tmp_path: Path, monkeypatch: Any) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", private_state_path=write_mutation_private_state(tmp_path))
    calls: list[tuple[str, str, list[Any]]] = []

    def fake_json_rpc(url: str, method: str, params: list[Any] | None = None, **kwargs: Any) -> Any:
        calls.append((url, method, list(params or [])))
        assert url == "https://testnet-rpc.greatlibrary.io"
        if method == "eth_chainId":
            return hex(plan.chain_id)
        if method == "eth_blockNumber":
            return "0x2a"
        if method == "net_peerCount":
            return "0x3"
        if method == "qbft_getValidatorsByBlockNumber":
            assert params == ["latest"]
            return ["0x1111111111111111111111111111111111111111"]
        raise AssertionError(f"unexpected JSON-RPC method: {method}")

    monkeypatch.setattr(module, "json_rpc", fake_json_rpc)

    observation = module.observe_chain_state(plan, _args())

    assert observation["ok"] is True
    assert observation["canonical_rpc"] == "ok"
    assert observation["direct_rpc"] == "not-inspected"
    assert observation["consensus"] == "ok"
    assert observation["chain"]["chain_id"] == hex(plan.chain_id)
    assert observation["chain"]["block_number"] == 42
    assert observation["chain"]["peer_count"] == 3
    assert observation["chain"]["validator_count"] == 1
    assert [call[1] for call in calls] == [
        "eth_chainId",
        "eth_blockNumber",
        "net_peerCount",
        "qbft_getValidatorsByBlockNumber",
    ]


def test_mutate_observe_chain_embeds_read_only_observed_state(tmp_path: Path, monkeypatch: Any) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", private_state_path=write_mutation_private_state(tmp_path))

    def fake_json_rpc(url: str, method: str, params: list[Any] | None = None, **kwargs: Any) -> Any:
        assert url == "https://testnet-rpc.greatlibrary.io"
        if method == "eth_chainId":
            return hex(plan.chain_id)
        if method == "eth_blockNumber":
            return "0x10"
        if method == "net_peerCount":
            return "0x1"
        if method == "qbft_getValidatorsByBlockNumber":
            return [
                "0x1111111111111111111111111111111111111111",
                "0x2222222222222222222222222222222222222222",
            ]
        raise AssertionError(f"unexpected JSON-RPC method: {method}")

    monkeypatch.setattr(module, "json_rpc", fake_json_rpc)

    packet = module.mutate_network(plan, _args(add="validator-2", observe_chain=True))

    assert packet["ok"] is True
    assert packet["execution"]["no_mutation_performed"] is True
    assert packet["observed_state"]["private_state"] == "loaded"
    assert packet["observed_state"]["coolify"] == "not-inspected"
    assert packet["observed_state"]["public_rpc"] == "ok"
    assert packet["observed_state"]["consensus"] == "ok"
    assert packet["observed_state"]["chain"]["block_number"] == 16
    assert packet["observed_state"]["chain"]["validator_count"] == 2


def test_mutate_observe_chain_reports_rpc_errors_without_mutating(tmp_path: Path, monkeypatch: Any) -> None:
    module = _load_module()
    plan = module.build_plan("testnet", private_state_path=write_mutation_private_state(tmp_path))

    def fake_json_rpc(url: str, method: str, params: list[Any] | None = None, **kwargs: Any) -> Any:
        raise RuntimeError("rpc edge unavailable")

    monkeypatch.setattr(module, "json_rpc", fake_json_rpc)

    packet = module.mutate_network(plan, _args(add="validator-2", observe_chain=True))

    assert packet["ok"] is False
    assert packet["execution"]["no_mutation_performed"] is True
    assert packet["observed_state"]["public_rpc"] == "error"
    assert packet["observed_state"]["consensus"] == "error"
    assert "rpc edge unavailable" in packet["observed_state"]["errors"][0]

