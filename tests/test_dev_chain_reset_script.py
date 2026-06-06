from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_dev_chain_reset():
    spec = importlib.util.spec_from_file_location("dev_chain_reset", ROOT / "tools" / "dev-chain-reset.py")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_parser_accepts_run_id_and_soft_pool_options() -> None:
    reset = load_dev_chain_reset()
    parser = reset.build_parser()
    args = parser.parse_args(["--dry-run", "--run-id", "first-soft-test", "--accounts", "4"])

    assert args.run_id == "first-soft-test"
    assert args.accounts == 4
    assert args.port_strategy == "replace-project"
    assert reset.resolved_run_id(args) == "first-soft-test"


def test_host_port_shorthand_updates_rpc_url() -> None:
    reset = load_dev_chain_reset()
    parser = reset.build_parser()
    args = parser.parse_args(["--dry-run", "--host-port", "18546"])

    reset.validate_args(args)

    assert args.host_rpc_url == "http://127.0.0.1:18546"
    assert reset.anvil_command(args, "unit")[reset.anvil_command(args, "unit").index("-p") + 1] == "127.0.0.1:18546:8545"


def test_default_deployments_cover_governance_xlag_and_hub_escrow_contracts() -> None:
    reset = load_dev_chain_reset()
    parser = reset.build_parser()
    args = parser.parse_args(["--yes", "--run-id", "unit"])

    specs = reset.deployment_specs(args, "0x1111111111111111111111111111111111111111")

    assert [spec.key for spec in specs] == ["alpha-beta-lockout", "xlag-bridge-reserve", "hub_credit_bridge_escrow"]
    assert specs[0].target == "AlphaBetaLockout.sol:AlphaBetaLockout"
    assert specs[1].target == "src/XLagBridgeReserve.sol:XLagBridgeReserve"
    assert specs[2].target == "src/HubCreditBridgeEscrow.sol:HubCreditBridgeEscrow"
    assert specs[0].constructor_args[0].startswith("[0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266")
    assert specs[1].constructor_args[-3:] == ["1000000000000000000", "1", "1"]
    assert specs[2].constructor_args == ["0x1111111111111111111111111111111111111111"]
    assert specs[2].metadata["bridge_controller_address"] == "0x1111111111111111111111111111111111111111"


def test_soft_deploy_commands_create_isolated_network_and_anvil_pool(monkeypatch) -> None:
    reset = load_dev_chain_reset()
    monkeypatch.setattr(reset, "docker_executable", lambda: "docker")
    parser = reset.build_parser()
    args = parser.parse_args(["--yes", "--run-id", "first-soft-test", "--project-name", "main-computer-dev"])
    rid = reset.resolved_run_id(args)

    network = reset.network_create_command(args, rid)
    anvil = reset.anvil_command(args, rid)

    assert network == ["docker", "network", "create", "main-computer-dev-soft-first-soft-test"]
    assert "--name" in anvil
    assert "main-computer-dev-chain-first-soft-test" in anvil
    assert "--accounts" in anvil
    assert anvil[anvil.index("--accounts") + 1] == "4"
    assert "-p" in anvil
    assert "127.0.0.1:18545:8545" in anvil


def test_docker_deploy_command_uses_soft_network_and_forge_entrypoint(monkeypatch) -> None:
    reset = load_dev_chain_reset()
    monkeypatch.setattr(reset, "docker_executable", lambda: "docker")
    parser = reset.build_parser()
    args = parser.parse_args(["--yes", "--run-id", "first-soft-test", "--project-name", "main-computer-dev"])
    spec = reset.deployment_specs(args)[1]

    cmd = reset.docker_deploy_command(args, spec, ROOT / "contracts", reset.resolved_run_id(args))

    assert cmd[:4] == ["docker", "run", "--rm", "--network"]
    assert "main-computer-dev-soft-first-soft-test" in cmd
    assert "--entrypoint" in cmd
    assert "forge" in cmd
    assert "create" in cmd
    assert "src/XLagBridgeReserve.sol:XLagBridgeReserve" in cmd
    assert "--constructor-args" in cmd
    assert "http://main-computer-dev-chain-first-soft-test:8545" in cmd


def test_dry_run_writes_soft_chroot_outputs(tmp_path: Path, monkeypatch) -> None:
    reset = load_dev_chain_reset()
    monkeypatch.setattr(reset, "repo_root", lambda: tmp_path)
    deploy_root = tmp_path / "deployments"
    code = reset.main([
        "--dry-run",
        "--run-id",
        "unit-dry-run",
        "--output-dir",
        str(tmp_path / "legacy-dev-chain"),
        "--deployment-output-dir",
        str(deploy_root),
    ])

    assert code == 0
    latest = json.loads((tmp_path / "legacy-dev-chain" / "latest.json").read_text(encoding="utf-8"))

    assert latest["schema"] == "main-computer.deployment.v1"
    assert latest["environment"] == "dev"
    assert latest["run_id"] == "unit-dry-run"
    assert latest["dry_run"] is True
    assert latest["chain"]["accounts"] == 4
    assert latest["chain"]["container"] == "main-computer-dev-chain-unit-dry-run"
    assert latest["deployments"]["xlag-bridge-reserve"]["target"] == "src/XLagBridgeReserve.sol:XLagBridgeReserve"
    assert latest["deployments"]["hub_credit_bridge_escrow"]["target"] == "src/HubCreditBridgeEscrow.sol:HubCreditBridgeEscrow"
    assert latest["hub_admin"]["address"] == "0x0000000000000000000000000000000000000a11"
    assert (tmp_path / "legacy-dev-chain" / "runs" / "unit-dry-run" / "deploy.env").exists()

    current = json.loads((deploy_root / "current.json").read_text(encoding="utf-8"))
    env_latest = json.loads((deploy_root / "dev" / "latest.json").read_text(encoding="utf-8"))
    assert current == env_latest
    assert current["schema"] == "main-computer.deployment.v1"
    assert current["chain"]["rpc_url"] == "http://127.0.0.1:18545"
    assert current["contracts"]["xlag-bridge-reserve"]["target"] == "src/XLagBridgeReserve.sol:XLagBridgeReserve"
    escrow = current["contracts"]["hub_credit_bridge_escrow"]
    assert escrow["target"] == "src/HubCreditBridgeEscrow.sol:HubCreditBridgeEscrow"
    assert escrow["payment_asset"] == "native"
    assert escrow["approval_required"] is False
    assert escrow["bridge_controller_address"] == current["hub_admin"]["address"]
    assert current["hub_admin"]["wallet_path"] == "deployments/hub-admin-wallet.json"
    assert not (deploy_root / "hub-admin-wallet.json").exists()
    assert "private_key" not in json.dumps(current)
    assert "mnemonic" not in json.dumps(current)


def test_hub_admin_wallet_is_created_and_reused(tmp_path: Path, monkeypatch) -> None:
    reset = load_dev_chain_reset()
    monkeypatch.setattr(reset, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        reset,
        "derive_address_for_private_key",
        lambda args, root, private_key: "0x1234567890123456789012345678901234567890",
    )
    parser = reset.build_parser()
    args = parser.parse_args([
        "--yes",
        "--run-id",
        "unit-wallet",
        "--deployment-output-dir",
        str(tmp_path / "deployments"),
    ])

    first = reset.resolve_hub_admin_wallet(args, tmp_path, create_missing=True)
    second = reset.resolve_hub_admin_wallet(args, tmp_path, create_missing=True)

    wallet_path = tmp_path / "deployments" / "hub-admin-wallet.json"
    payload = json.loads(wallet_path.read_text(encoding="utf-8"))
    assert first is not None
    assert second is not None
    assert first.address == "0x1234567890123456789012345678901234567890"
    assert second.address == first.address
    assert second.private_key == first.private_key
    assert payload["schema"] == "main-computer.hub-admin-wallet.v1"
    assert payload["chain_id"] == 42424242
    assert payload["address"] == first.address
    assert payload["private_key"] == first.private_key


def test_hub_admin_private_key_is_not_published(tmp_path: Path) -> None:
    reset = load_dev_chain_reset()
    wallet = reset.HubAdminWallet(
        path=tmp_path / "runtime" / "deployments" / "hub-admin-wallet.json",
        address="0x1234567890123456789012345678901234567890",
        private_key="0x" + "7" * 64,
        source="generated-local-dev",
    )
    parser = reset.build_parser()
    args = parser.parse_args(["--dry-run"])
    payload = reset.deploy_payload(
        args=args,
        rid="unit",
        dry_run=True,
        deployments=reset.planned_deployments(args, wallet.address),
        hub_admin=reset.hub_admin_payload(wallet, tmp_path, args),
    )

    public = reset.public_deployment_payload(payload)

    assert public["hub_admin"]["address"] == wallet.address
    assert "private_key" not in json.dumps(public)
    assert public["contracts"]["hub_credit_bridge_escrow"]["bridge_controller_address"] == wallet.address


def test_env_payload_publishes_hub_credit_bridge_escrow_address() -> None:
    reset = load_dev_chain_reset()
    payload = {
        "run_id": "unit",
        "chain": {"host_rpc_url": "http://127.0.0.1:18545", "chain_id": 42424242},
        "deployments": {
            "hub_credit_bridge_escrow": {"address": "0x3333333333333333333333333333333333333333"},
        },
        "offices": [],
    }

    env = reset.env_payload(payload)

    assert "MAIN_COMPUTER_HUB_CREDIT_BRIDGE_ESCROW_ADDRESS=0x3333333333333333333333333333333333333333" in env


def test_parse_deployment_address_from_forge_json_and_text() -> None:
    reset = load_dev_chain_reset()
    payload = {
        "deployedTo": "0x1111111111111111111111111111111111111111",
        "transactionHash": "0x" + "a" * 64,
    }

    assert reset.parse_deployment_address(json.dumps(payload)) == "0x1111111111111111111111111111111111111111"
    assert reset.parse_transaction_hash(json.dumps(payload)) == "0x" + "a" * 64
    assert (
        reset.parse_deployment_address("Deployed to: 0x2222222222222222222222222222222222222222")
        == "0x2222222222222222222222222222222222222222"
    )


def test_deployed_contracts_suppresses_transient_forge_warning_after_code_verification(monkeypatch, capsys) -> None:
    reset = load_dev_chain_reset()
    parser = reset.build_parser()
    args = parser.parse_args(["--yes", "--run-id", "unit"])
    deployed = [
        "0x1111111111111111111111111111111111111111",
        "0x2222222222222222222222222222222222222222",
        "0x3333333333333333333333333333333333333333",
    ]
    run_count = {"value": 0}

    def fake_run_command(command, *, timeout_s=None, check=True, echo=True):
        index = run_count["value"]
        run_count["value"] += 1
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"deployedTo": deployed[index], "transactionHash": "0x" + str(index + 1) * 64}) + "\n",
            stderr=(
                "2026-06-05T23:14:06.037244Z ERROR alloy_provider::blocks: "
                "failed to fetch block number=3 err=error sending request for url "
                "(http://main-computer-dev-chain-unit:8545/)\n"
            ),
        )

    def fake_rpc(url, method, params=None, *, timeout_s=3.0):
        assert url == args.host_rpc_url
        assert timeout_s == 1.0
        assert method == "eth_getCode"
        assert params[1] == "latest"
        return "0x60006000"

    monkeypatch.setattr(reset, "run_command", fake_run_command)
    monkeypatch.setattr(reset, "rpc", fake_rpc)

    result = reset.deployed_contracts(args, "unit", "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    captured = capsys.readouterr()

    assert run_count["value"] == 3
    assert result["hub_credit_bridge_escrow"]["address"] == deployed[2]
    assert "alloy_provider::blocks" not in captured.out
    assert "alloy_provider::blocks" not in captured.err
    assert "Verifying hub_credit_bridge_escrow.code via http://127.0.0.1:18545" in captured.out
    assert "PASS: hub_credit_bridge_escrow.code" in captured.out
    assert "transient block-fetch warning" in captured.out


def test_parse_docker_port_owners_and_published_port_detection() -> None:
    reset = load_dev_chain_reset()
    output = (
        "abc123\tmain-computer-dev-chain-old\t127.0.0.1:18545->8545/tcp\n"
        "def456\tother-service\t0.0.0.0:18000->8000/tcp\n"
    )

    owners = reset.parse_docker_port_owners(output)

    assert [owner.name for owner in owners] == ["main-computer-dev-chain-old", "other-service"]
    assert reset.port_owner_publishes_host_port(owners[0], 18545)
    assert not reset.port_owner_publishes_host_port(owners[1], 18545)


def test_project_chain_container_detection_uses_project_name_prefix() -> None:
    reset = load_dev_chain_reset()
    parser = reset.build_parser()
    args = parser.parse_args(["--dry-run", "--project-name", "main-computer-dev"])
    owner = reset.DockerPortOwner(
        container_id="abc123",
        name="main-computer-dev-chain-any-user-frobber-v1",
        ports="127.0.0.1:18545->8545/tcp",
    )
    foreign = reset.DockerPortOwner(
        container_id="def456",
        name="unrelated-anvil",
        ports="127.0.0.1:18545->8545/tcp",
    )

    assert reset.is_project_chain_container(args, owner)
    assert not reset.is_project_chain_container(args, foreign)


def test_host_rpc_url_with_port_preserves_scheme_and_host() -> None:
    reset = load_dev_chain_reset()

    assert reset.host_rpc_url_with_port("http://127.0.0.1:18545", 18546) == "http://127.0.0.1:18546"
    assert reset.host_rpc_port("http://127.0.0.1:18546") == "18546"


def test_parse_offices_requires_four_addresses() -> None:
    reset = load_dev_chain_reset()

    assert len(reset.parse_offices(None)) == 4

    try:
        reset.parse_offices("0x1111111111111111111111111111111111111111")
    except ValueError as exc:
        assert "exactly four" in str(exc)
    else:
        raise AssertionError("expected invalid office list to fail")


def test_reset_refuses_to_write_when_prod_lock_exists(tmp_path: Path, monkeypatch) -> None:
    reset = load_dev_chain_reset()
    monkeypatch.setattr(reset, "repo_root", lambda: tmp_path)
    (tmp_path / ".prod.lock").write_text('{"deployment":"prod","protected":true}\n', encoding="utf-8")

    code = reset.main(["--dry-run", "--run-id", "locked-reset"])

    assert code == 1
    assert not (tmp_path / "runtime" / "deployments" / "current.json").exists()
    assert not (tmp_path / "runtime" / "dev-chain" / "latest.json").exists()
