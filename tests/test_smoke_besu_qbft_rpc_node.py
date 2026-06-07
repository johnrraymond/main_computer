from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "tools" / "smoke_besu_qbft_one_validator.py"


def _load_smoke_module():
    spec = importlib.util.spec_from_file_location("smoke_besu_qbft_one_validator", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_default_topology_has_four_validator_ports_and_one_public_rpc_port() -> None:
    module = _load_smoke_module()
    args = module.parse_args([])

    assert module.resolve_rpc_ports(args) == [30001, 30002, 30003, 30004]
    assert module.resolve_public_rpc_port(args) == 30010
    assert module.rpc_url_for_port(module.resolve_public_rpc_port(args)) == "http://127.0.0.1:30010"
    assert module.rpc_node_ip(args.docker_subnet) == "172.28.241.20"
    assert module.RPC_NODE_CONTAINER in module.all_smoke_containers()


def test_rpc_node_runtime_uses_validator_static_nodes_without_validator_key(tmp_path: Path) -> None:
    module = _load_smoke_module()
    validators = [
        {"enode": "enode://a@172.28.241.11:30303"},
        {"enode": "enode://b@172.28.241.12:30303"},
    ]

    module.install_rpc_node_files(tmp_path, validators=validators)

    rpc_node_dir = tmp_path / "rpc-node"
    assert (rpc_node_dir / "data").is_dir()
    assert not (rpc_node_dir / "data" / "key").exists()
    static_nodes = json.loads((rpc_node_dir / "static-nodes.json").read_text(encoding="utf-8"))
    assert static_nodes == [validator["enode"] for validator in validators]


def test_metadata_records_public_rpc_node_profile(tmp_path: Path) -> None:
    module = _load_smoke_module()
    args = module.parse_args([])

    module.write_metadata(
        tmp_path,
        args=args,
        rpc_ports=[30001, 30002, 30003, 30004],
        public_rpc_port=30010,
        validators=[],
    )

    metadata = module.load_metadata(tmp_path)
    assert metadata["validator_rpc_urls"] == [
        "http://127.0.0.1:30001",
        "http://127.0.0.1:30002",
        "http://127.0.0.1:30003",
        "http://127.0.0.1:30004",
    ]
    assert metadata["public_rpc_url"] == "http://127.0.0.1:30010"
    assert metadata["rpc_node"] == {
        "container": "smoke-besu-qbft-rpc",
        "ip_address": "172.28.241.20",
        "rpc_port": 30010,
        "rpc_url": "http://127.0.0.1:30010",
        "role": "non-validator-rpc",
    }


def test_public_rpc_port_must_not_collide_with_validator_ports() -> None:
    module = _load_smoke_module()

    with pytest.raises(RuntimeError, match="unique"):
        module.assert_host_ports_available([30001, 30002, 30010, 30010])
