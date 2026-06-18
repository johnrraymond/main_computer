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
    (tmp_path / "genesis.json").write_text('{"config":{"chainId":42424241}}\n', encoding="utf-8")

    module.install_rpc_node_files(tmp_path, validators=validators)

    rpc_node_dir = tmp_path / "rpc-node"
    active_data_dir = (rpc_node_dir / "active-data-dir.txt").read_text(encoding="utf-8").strip()
    assert active_data_dir.startswith("data-")
    assert active_data_dir != "data"
    assert (rpc_node_dir / active_data_dir).is_dir()
    assert not (rpc_node_dir / active_data_dir / "key").exists()
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


def test_qbft_genesis_funds_dev_deployer_accounts(tmp_path: Path) -> None:
    module = _load_smoke_module()
    config_path = tmp_path / "qbftConfigFile.json"

    module.write_qbft_config(
        config_path,
        chain_id=42424241,
        block_period_seconds=2,
        request_timeout_seconds=4,
    )

    config = json.loads(config_path.read_text(encoding="utf-8"))
    genesis = config["genesis"]
    alloc = genesis["alloc"]

    assert genesis["config"]["londonBlock"] == 0
    assert genesis["config"]["shanghaiTime"] == module.DEFAULT_SHANGHAI_TIME
    assert genesis["baseFeePerGas"] == module.DEFAULT_GENESIS_BASE_FEE_PER_GAS
    assert genesis["baseFeePerGas"] != "0x0"
    assert "zeroBaseFee" not in genesis["config"]
    assert alloc["f39fd6e51aad88f6f4ce6ab8827279cfffb92266"]["balance"] == module.DEFAULT_FUNDED_ACCOUNT_BALANCE
    assert len(alloc) == 4


def _write_generated_network_files(root: Path) -> Path:
    network_files = root / "networkFiles"
    keys_dir = network_files / "keys"
    network_files.mkdir()
    keys_dir.mkdir()
    (network_files / "genesis.json").write_text('{"config":{"chainId":42424241},"alloc":{}}\n', encoding="utf-8")
    for index in range(1, 5):
        key_dir = keys_dir / f"{index:040x}"
        key_dir.mkdir()
        (key_dir / "key").write_text(f"validator-{index}-private-key\n", encoding="utf-8")
        (key_dir / "key.pub").write_text(f"{index}" * 128 + "\n", encoding="utf-8")
    return network_files


def test_validator_runtime_uses_genesis_scoped_data_dirs_instead_of_stable_data(tmp_path: Path) -> None:
    module = _load_smoke_module()
    network_files = _write_generated_network_files(tmp_path)
    stale_data = tmp_path / "validator-4" / "data"
    stale_data.mkdir(parents=True)
    (stale_data / "DATABASE_METADATA").write_text("old genesis db\n", encoding="utf-8")

    validators = module.install_validator_files(
        network_files,
        tmp_path,
        docker_subnet="172.28.241.0/24",
    )

    validator_4 = validators[3]
    active_data_dir = (tmp_path / "validator-4" / "active-data-dir.txt").read_text(encoding="utf-8").strip()
    assert active_data_dir.startswith("data-")
    assert active_data_dir != "data"
    assert validator_4["data_dir"] == f"validator-4/{active_data_dir}"
    assert validator_4["container_data_path"] == f"/smoke/validator-4/{active_data_dir}"
    assert (tmp_path / validator_4["data_dir"] / "key").read_text(encoding="utf-8") == "validator-4-private-key\n"
    assert not (tmp_path / validator_4["data_dir"] / "DATABASE_METADATA").exists()
    assert (stale_data / "DATABASE_METADATA").read_text(encoding="utf-8") == "old genesis db\n"


def test_start_validator_uses_generated_container_data_path(tmp_path: Path, monkeypatch) -> None:
    module = _load_smoke_module()
    captured: dict[str, list[str]] = {}

    def fake_run(command, *, check=True, capture=False):
        captured["command"] = command
        return module.subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module, "run", fake_run)

    module.start_validator(
        tmp_path,
        image="besu:test",
        index=4,
        rpc_port=30004,
        chain_id=42424241,
        docker_subnet="172.28.241.0/24",
        container_data_path="/smoke/validator-4/data-deadbeef",
    )

    command = captured["command"]
    assert "--data-path=/smoke/validator-4/data-deadbeef" in command
    assert "--data-path=/smoke/validator-4/data" not in command


def test_deploy_command_delegates_to_dev_chain_reset_external_publication(tmp_path: Path, monkeypatch) -> None:
    module = _load_smoke_module()
    captured: dict[str, list[str]] = {}

    args = module.parse_args(
        [
            "deploy",
            "--runtime-dir",
            str(tmp_path / "runtime" / "qbft"),
            "--deployment-run-id",
            "qbft-unit",
        ]
    )

    monkeypatch.setattr(module, "docker_available", lambda: True)
    monkeypatch.setattr(module, "wait_for_rpc", lambda url, *, timeout_seconds: None)
    monkeypatch.setattr(module, "verify_chain_ids", lambda urls, *, expected_chain_id: ["0x28757b1"])
    monkeypatch.setattr(module, "funded_deployer_balance_wei", lambda url: 10**18)

    def fake_run(command, *, check=True, capture=False):
        captured["command"] = command
        return module.subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module, "run", fake_run)

    code = module.deploy_testnet(args)

    assert code == 0
    command = captured["command"]
    assert command[:4] == [module.sys.executable, str(module.repo_root() / "tools" / "dev-chain-reset.py"), "--yes", "--external-chain"]
    assert "--environment" in command
    assert command[command.index("--environment") + 1] == "test"
    assert "--source-kind" in command
    assert command[command.index("--source-kind") + 1] == "qbft-smoke-testnet-deploy"
    assert "--host-rpc-url" in command
    assert command[command.index("--host-rpc-url") + 1] == "http://127.0.0.1:30010"
    assert "--container-rpc-url" in command
    assert command[command.index("--container-rpc-url") + 1] == "http://smoke-besu-qbft-rpc:8545"
    assert "--external-docker-network" in command
    assert command[command.index("--external-docker-network") + 1] == "smoke-besu-qbft-network"
    assert "--external-chain-container" in command
    assert command[command.index("--external-chain-container") + 1] == "smoke-besu-qbft-rpc"
    assert "--output-dir" in command
    assert command[command.index("--output-dir") + 1].endswith("runtime/qbft/deployments")
    assert "--deployment-output-dir" in command
    assert command[command.index("--deployment-output-dir") + 1].endswith("runtime/deployments")
    assert "--generate-offices" in command


def test_deploy_command_refuses_old_unfunded_qbft_genesis(tmp_path: Path, monkeypatch, capsys) -> None:
    module = _load_smoke_module()
    args = module.parse_args(["deploy", "--runtime-dir", str(tmp_path / "runtime" / "qbft")])

    monkeypatch.setattr(module, "docker_available", lambda: True)
    monkeypatch.setattr(module, "wait_for_rpc", lambda url, *, timeout_seconds: None)
    monkeypatch.setattr(module, "verify_chain_ids", lambda urls, *, expected_chain_id: ["0x28757b1"])
    monkeypatch.setattr(module, "funded_deployer_balance_wei", lambda url: 0)

    code = module.deploy_testnet(args)
    captured = capsys.readouterr()

    assert code == 1
    assert "deployer has no native balance" in captured.err
    assert "smoke_besu_qbft_one_validator.py down" in captured.err
