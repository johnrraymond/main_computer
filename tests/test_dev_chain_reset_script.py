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

    manifest = json.loads((deploy_root / "dev" / "latest.json").read_text(encoding="utf-8"))
    assert not (deploy_root / "current.json").exists()
    assert manifest["schema"] == "main-computer.deployment.v1"
    assert manifest["chain"]["rpc_url"] == "http://127.0.0.1:18545"
    assert manifest["contracts"]["xlag-bridge-reserve"]["target"] == "src/XLagBridgeReserve.sol:XLagBridgeReserve"
    escrow = manifest["contracts"]["hub_credit_bridge_escrow"]
    assert escrow["target"] == "src/HubCreditBridgeEscrow.sol:HubCreditBridgeEscrow"
    assert escrow["payment_asset"] == "native"
    assert escrow["approval_required"] is False
    assert escrow["bridge_controller_address"] == manifest["hub_admin"]["address"]
    assert manifest["hub_admin"]["wallet_path"] == "deployments/dev/hub-admin-wallet-42424242.json"
    assert manifest["smoke_client"]["address"] == "0x000000000000000000000000000000000000c11e"
    assert manifest["smoke_client"]["wallet_path"] == "deployments/dev/smoke-client-wallet-42424242.json"
    assert manifest["smoke_client"]["funding_wei"] == "5000000000000000000"
    assert not (deploy_root / "hub-admin-wallet.json").exists()
    assert "private_key" not in json.dumps(manifest)
    assert "mnemonic" not in json.dumps(manifest)



def test_dry_run_writes_committed_public_contract_config(tmp_path: Path, monkeypatch) -> None:
    reset = load_dev_chain_reset()
    monkeypatch.setattr(reset, "repo_root", lambda: tmp_path)
    deploy_root = tmp_path / "deployments"

    code = reset.main([
        "--dry-run",
        "--run-id",
        "unit-contract-config",
        "--deployment-output-dir",
        str(deploy_root),
    ])

    assert code == 0
    contract_config = json.loads((tmp_path / "main_computer" / "config" / "dev_contracts.json").read_text(encoding="utf-8"))
    # Dry-run previews do not have actual deployed addresses yet.  The public
    # config may be empty, but it must never contain chain/RPC/Coolify/runtime
    # metadata.
    assert contract_config == {}
    forbidden = json.dumps(contract_config)
    assert "schema" not in contract_config
    assert "network" not in contract_config
    assert "chain_id" not in contract_config
    assert "chain_rpc_url" not in contract_config
    assert "target" not in forbidden
    assert "transaction_hash" not in forbidden
    assert "constructor_args" not in forbidden
    assert "private_key" not in forbidden
    assert "wallet_path" not in forbidden
    assert "mnemonic" not in forbidden

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

    wallet_path = tmp_path / "deployments" / "dev" / "hub-admin-wallet-42424242.json"
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



def test_smoke_client_wallet_is_created_and_reused(tmp_path: Path, monkeypatch) -> None:
    reset = load_dev_chain_reset()
    monkeypatch.setattr(reset, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        reset,
        "derive_address_for_private_key",
        lambda args, root, private_key: "0x2222222222222222222222222222222222222222",
    )
    parser = reset.build_parser()
    args = parser.parse_args([
        "--yes",
        "--run-id",
        "unit-smoke-wallet",
        "--deployment-output-dir",
        str(tmp_path / "deployments"),
    ])

    first = reset.resolve_smoke_client_wallet(args, tmp_path, create_missing=True)
    second = reset.resolve_smoke_client_wallet(args, tmp_path, create_missing=True)

    wallet_path = tmp_path / "deployments" / "dev" / "smoke-client-wallet-42424242.json"
    payload = json.loads(wallet_path.read_text(encoding="utf-8"))
    assert first is not None
    assert second is not None
    assert first.address == "0x2222222222222222222222222222222222222222"
    assert second.address == first.address
    assert second.private_key == first.private_key
    assert payload["schema"] == "main-computer.smoke-client-wallet.v1"
    assert payload["chain_id"] == 42424242
    assert payload["address"] == first.address
    assert payload["private_key"] == first.private_key


def test_external_chain_uses_chain_scoped_hub_admin_wallet_when_dev_wallet_exists(tmp_path: Path, monkeypatch) -> None:
    reset = load_dev_chain_reset()
    monkeypatch.setattr(reset, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        reset,
        "derive_address_for_private_key",
        lambda args, root, private_key: "0x9999999999999999999999999999999999999999",
    )
    deploy_root = tmp_path / "deployments"
    legacy_wallet = deploy_root / "hub-admin-wallet.json"
    legacy_wallet.parent.mkdir(parents=True)
    legacy_wallet.write_text(
        json.dumps(
            {
                "schema": "main-computer.hub-admin-wallet.v1",
                "chain_id": 42424242,
                "address": "0x1234567890123456789012345678901234567890",
                "private_key": "0x" + "1" * 64,
                "source": "generated-local-dev",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    parser = reset.build_parser()
    args = parser.parse_args(
        [
            "--yes",
            "--external-chain",
            "--environment",
            "test",
            "--chain-id",
            "42424241",
            "--deployment-output-dir",
            str(deploy_root),
        ]
    )

    wallet = reset.resolve_hub_admin_wallet(args, tmp_path, create_missing=True)

    chain_wallet = deploy_root / "test" / "hub-admin-wallet-42424241.json"
    payload = json.loads(chain_wallet.read_text(encoding="utf-8"))
    assert wallet is not None
    assert wallet.path == chain_wallet
    assert wallet.address == "0x9999999999999999999999999999999999999999"
    assert payload["chain_id"] == 42424241
    assert payload["address"] == wallet.address
    assert json.loads(legacy_wallet.read_text(encoding="utf-8"))["chain_id"] == 42424242


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
        "smoke_client": {
            "address": "0x2222222222222222222222222222222222222222",
            "wallet_path": "runtime/deployments/dev/smoke-client-wallet-42424242.json",
        },
        "offices": [],
    }

    env = reset.env_payload(payload)

    assert "MAIN_COMPUTER_HUB_CREDIT_BRIDGE_ESCROW_ADDRESS=0x3333333333333333333333333333333333333333" in env
    assert "MAIN_COMPUTER_SMOKE_CLIENT_ADDRESS=0x2222222222222222222222222222222222222222" in env
    assert "MAIN_COMPUTER_SMOKE_CLIENT_WALLET_PATH=runtime/deployments/dev/smoke-client-wallet-42424242.json" in env


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

    def fake_run_command(command, *, timeout_s=None, check=True, echo=True, **_kwargs):
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


def test_generated_office_wallets_are_created_reused_and_used_for_constructor_args(tmp_path: Path, monkeypatch) -> None:
    reset = load_dev_chain_reset()
    monkeypatch.setattr(reset, "repo_root", lambda: tmp_path)
    generated_addresses = iter(
        [
            "0x1111111111111111111111111111111111111111",
            "0x2222222222222222222222222222222222222222",
            "0x3333333333333333333333333333333333333333",
            "0x4444444444444444444444444444444444444444",
        ]
    )
    monkeypatch.setattr(reset, "derive_address_for_private_key", lambda args, root, private_key: next(generated_addresses))
    deploy_root = tmp_path / "deployments"
    parser = reset.build_parser()
    args = parser.parse_args(
        [
            "--yes",
            "--run-id",
            "unit-offices",
            "--environment",
            "test",
            "--chain-id",
            "42424241",
            "--deployment-output-dir",
            str(deploy_root),
            "--generate-offices",
        ]
    )

    reset.validate_args(args)
    office_path, offices = reset.resolve_office_wallets(args, tmp_path, create_missing=True, rid="unit-offices")
    second_path, second_offices = reset.resolve_office_wallets(args, tmp_path, create_missing=True, rid="unit-offices")
    specs = reset.deployment_specs(args)
    private_payload = reset.deploy_payload(args=args, rid="unit-offices", dry_run=False, deployments=reset.planned_deployments(args))
    public_payload = reset.public_deployment_payload(private_payload)

    assert office_path == deploy_root / "test" / "office-wallets-42424241.json"
    assert second_path == office_path
    assert [wallet.address for wallet in second_offices] == [wallet.address for wallet in offices]
    assert len(offices) == 4
    assert specs[0].constructor_args == [
        "[0x1111111111111111111111111111111111111111,0x2222222222222222222222222222222222222222,0x3333333333333333333333333333333333333333,0x4444444444444444444444444444444444444444]"
    ]
    assert private_payload["offices"][0]["title"] == "Captain"
    assert private_payload["offices"][0]["wallet_path"] == "deployments/test/office-wallets-42424241.json"
    assert private_payload["offices"][0]["source"] == "generated-local-qbft-office"
    assert "private_key" in private_payload["offices"][0]
    assert public_payload["offices"][0]["address"] == "0x1111111111111111111111111111111111111111"
    assert public_payload["offices"][0]["wallet_path"] == "deployments/test/office-wallets-42424241.json"
    assert "private_key" not in json.dumps(public_payload)
    assert all(
        office["address"].lower() not in {item["address"].lower() for item in reset.DEFAULT_OFFICE_KEYS}
        for office in public_payload["offices"]
    )


def test_reset_refuses_to_write_when_prod_lock_exists(tmp_path: Path, monkeypatch) -> None:
    reset = load_dev_chain_reset()
    monkeypatch.setattr(reset, "repo_root", lambda: tmp_path)
    (tmp_path / ".prod.lock").write_text('{"deployment":"prod","protected":true}\n', encoding="utf-8")

    code = reset.main(["--dry-run", "--run-id", "locked-reset"])

    assert code == 1
    assert not (tmp_path / "runtime" / "deployments" / "dev" / "latest.json").exists()
    assert not (tmp_path / "runtime" / "dev-chain" / "latest.json").exists()




def test_external_chain_requires_eip1559_latest_block(monkeypatch) -> None:
    reset = load_dev_chain_reset()
    seen: list[tuple[str, str, list | None]] = []

    def fake_rpc(url, method, params=None, *, timeout_s=3.0):
        seen.append((url, method, params))
        assert url == "http://127.0.0.1:30010"
        assert method == "eth_getBlockByNumber"
        return {"number": "0x7", "baseFeePerGas": "0x3b9aca00"}

    monkeypatch.setattr(reset, "rpc", fake_rpc)

    assert reset.require_eip1559_chain("http://127.0.0.1:30010") == 1_000_000_000
    assert seen == [("http://127.0.0.1:30010", "eth_getBlockByNumber", ["latest", False])]


def test_external_chain_rejects_non_london_or_zero_base_fee(monkeypatch) -> None:
    reset = load_dev_chain_reset()

    monkeypatch.setattr(reset, "rpc", lambda *args, **kwargs: {"number": "0x7"})
    try:
        reset.require_eip1559_chain("http://127.0.0.1:30010")
    except RuntimeError as exc:
        assert "no baseFeePerGas" in str(exc)
        assert "instead of using legacy transactions" in str(exc)
    else:
        raise AssertionError("expected non-London chain to fail EIP-1559 preflight")

    monkeypatch.setattr(reset, "rpc", lambda *args, **kwargs: {"number": "0x7", "baseFeePerGas": "0x0"})
    try:
        reset.require_eip1559_chain("http://127.0.0.1:30010")
    except RuntimeError as exc:
        assert "zero-base-fee" in str(exc)
    else:
        raise AssertionError("expected zero-base-fee chain to fail EIP-1559 preflight")


def test_external_chain_push0_preflight_estimates_shanghai_canary(monkeypatch) -> None:
    reset = load_dev_chain_reset()
    seen: list[tuple[str, str, list | None]] = []

    def fake_rpc(url, method, params=None, *, timeout_s=3.0):
        seen.append((url, method, params))
        assert method == "eth_estimateGas"
        assert params == [{"from": reset.DEFAULT_DEPLOYER_ADDRESS, "data": reset.PUSH0_CANARY_INITCODE}]
        return "0xd7d4"

    monkeypatch.setattr(reset, "rpc", fake_rpc)

    assert reset.require_push0_chain("http://127.0.0.1:30010") == 55252
    assert seen == [
        (
            "http://127.0.0.1:30010",
            "eth_estimateGas",
            [{"from": reset.DEFAULT_DEPLOYER_ADDRESS, "data": reset.PUSH0_CANARY_INITCODE}],
        )
    ]


def test_external_chain_push0_preflight_rejects_pre_shanghai_chain(monkeypatch) -> None:
    reset = load_dev_chain_reset()

    def fake_rpc(*args, **kwargs):
        raise RuntimeError({"code": -32000, "message": "Invalid opcode: 0x5f"})

    monkeypatch.setattr(reset, "rpc", fake_rpc)

    try:
        reset.require_push0_chain("http://127.0.0.1:30010")
    except RuntimeError as exc:
        assert "not Shanghai/PUSH0-capable" in str(exc)
        assert "Shanghai active at genesis" in str(exc)
    else:
        raise AssertionError("expected pre-Shanghai chain to fail PUSH0 preflight")


def test_external_chain_modern_preflight_requires_eip1559_and_push0(monkeypatch) -> None:
    reset = load_dev_chain_reset()
    calls: list[str] = []

    def fake_rpc(url, method, params=None, *, timeout_s=3.0):
        calls.append(method)
        if method == "eth_getBlockByNumber":
            return {"number": "0x7", "baseFeePerGas": "0x3b9aca00"}
        if method == "eth_estimateGas":
            return "0xd7d4"
        raise AssertionError(method)

    monkeypatch.setattr(reset, "rpc", fake_rpc)

    assert reset.require_modern_external_chain("http://127.0.0.1:30010") == (1_000_000_000, 55252)
    assert calls == ["eth_getBlockByNumber", "eth_estimateGas"]


def test_external_chain_mode_reuses_existing_qbft_network_for_deploy_commands(monkeypatch) -> None:
    reset = load_dev_chain_reset()
    monkeypatch.setattr(reset, "docker_executable", lambda: "docker")
    parser = reset.build_parser()
    args = parser.parse_args(
        [
            "--yes",
            "--external-chain",
            "--run-id",
            "qbft-unit",
            "--project-name",
            "main-computer-qbft-testnet",
            "--environment",
            "test",
            "--chain-id",
            "42424241",
            "--host-rpc-url",
            "http://127.0.0.1:30010",
            "--container-rpc-url",
            "http://smoke-besu-qbft-rpc:8545",
            "--external-docker-network",
            "smoke-besu-qbft-network",
            "--external-chain-container",
            "smoke-besu-qbft-rpc",
            "--source-kind",
            "qbft-smoke-testnet-deploy",
        ]
    )

    reset.validate_args(args)
    rid = reset.resolved_run_id(args)
    spec = reset.deployment_specs(args)[1]
    cmd = reset.docker_deploy_command(args, spec, ROOT / "contracts", rid)

    assert reset.network_name(args, rid) == "smoke-besu-qbft-network"
    assert reset.container_name(args, rid) == "smoke-besu-qbft-rpc"
    assert reset.container_rpc_url(args, rid) == "http://smoke-besu-qbft-rpc:8545"
    assert cmd[:5] == ["docker", "run", "--rm", "--network", "smoke-besu-qbft-network"]
    assert "http://smoke-besu-qbft-rpc:8545" in cmd
    assert "src/XLagBridgeReserve.sol:XLagBridgeReserve" in cmd


def test_external_chain_dry_run_writes_test_publication_without_anvil_secrets(tmp_path: Path, monkeypatch) -> None:
    reset = load_dev_chain_reset()
    monkeypatch.setattr(reset, "repo_root", lambda: tmp_path)
    deploy_root = tmp_path / "runtime" / "deployments"
    code = reset.main(
        [
            "--dry-run",
            "--external-chain",
            "--run-id",
            "qbft-dry-run",
            "--project-name",
            "main-computer-qbft-testnet",
            "--environment",
            "test",
            "--chain-id",
            "42424241",
            "--host-rpc-url",
            "http://127.0.0.1:30010",
            "--container-rpc-url",
            "http://smoke-besu-qbft-rpc:8545",
            "--external-docker-network",
            "smoke-besu-qbft-network",
            "--external-chain-container",
            "smoke-besu-qbft-rpc",
            "--source-kind",
            "qbft-smoke-testnet-deploy",
            "--output-dir",
            str(tmp_path / "runtime" / "smoke-besu-qbft-four-validators" / "deployments"),
            "--deployment-output-dir",
            str(deploy_root),
        ]
    )

    assert code == 0
    manifest = json.loads((deploy_root / "test" / "latest.json").read_text(encoding="utf-8"))
    assert not (deploy_root / "current.json").exists()

    assert manifest["environment"] == "test"
    assert manifest["source"] == {
        "kind": "qbft-smoke-testnet-deploy",
        "project_name": "main-computer-qbft-testnet",
    }
    assert manifest["chain"] == {
        "chain_id": 42424241,
        "rpc_url": "http://127.0.0.1:30010",
        "host_rpc_url": "http://127.0.0.1:30010",
        "container_rpc_url": "http://smoke-besu-qbft-rpc:8545",
        "network": "smoke-besu-qbft-network",
        "container": "smoke-besu-qbft-rpc",
    }
    assert manifest["smoke_client"]["wallet_path"] == "runtime/deployments/test/smoke-client-wallet-42424241.json"
    assert manifest["smoke_client"]["address"] == "0x000000000000000000000000000000000000c11e"
    assert "mnemonic" not in json.dumps(manifest)
    assert "private_key" not in json.dumps(manifest)
    assert manifest["contracts"]["hub_credit_bridge_escrow"]["payment_asset"] == "native"


def test_zero_timeouts_are_unbounded_for_subprocesses(monkeypatch) -> None:
    reset = load_dev_chain_reset()
    seen: dict[str, object] = {}

    def fake_run(command, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(reset.subprocess, "run", fake_run)

    reset.run_command(["echo", "ok"], timeout_s=0.0)

    assert seen["timeout"] is None


def test_external_chain_preflight_retries_transient_rpc_timeouts(monkeypatch) -> None:
    reset = load_dev_chain_reset()
    monkeypatch.setattr(reset.time, "sleep", lambda _seconds: None)
    calls: list[str] = []

    def fake_rpc(url, method, params=None, *, timeout_s=30.0):
        assert url == "http://127.0.0.1:30010"
        calls.append(method)
        if calls == ["eth_getBlockByNumber", "eth_estimateGas"]:
            raise TimeoutError("timed out")
        if method == "eth_getBlockByNumber":
            return {"number": "0x7", "baseFeePerGas": "0x3b9aca00"}
        if method == "eth_estimateGas":
            return "0xd7d4"
        raise AssertionError(f"unexpected method {method}")

    monkeypatch.setattr(reset, "rpc", fake_rpc)

    assert reset.require_modern_external_chain("http://127.0.0.1:30010", wait_timeout_s=0.0) == (1_000_000_000, 55252)
    assert calls == ["eth_getBlockByNumber", "eth_estimateGas", "eth_getBlockByNumber", "eth_estimateGas"]


def test_dev_chain_reset_defaults_wait_and_deploy_forever() -> None:
    reset = load_dev_chain_reset()
    parser = reset.build_parser()
    args = parser.parse_args(["--dry-run"])

    assert args.wait_timeout_s == 0.0
    assert args.deploy_timeout_s == 0.0



def test_run_scoped_generated_wallets_are_published_without_private_keys(tmp_path: Path, monkeypatch) -> None:
    reset = load_dev_chain_reset()
    parser = reset.build_parser()
    args = parser.parse_args(
        [
            "--yes",
            "--run-id",
            "unique-run",
            "--run-scoped-wallets",
            "--node-wallet-count",
            "2",
            "--payout-admin-wallet-count",
            "1",
            "--deployment-output-dir",
            str(tmp_path / "runtime" / "deployments"),
        ]
    )
    reset.validate_args(args)

    counter = {"value": 0}

    def fake_derive(_args, _root, _private_key):
        counter["value"] += 1
        return "0x" + f"{counter['value']:040x}"

    monkeypatch.setattr(reset, "derive_address_for_private_key", fake_derive)

    node_path, node_wallets = reset.resolve_generated_wallets(
        args,
        tmp_path,
        "unique-run",
        kind=reset.NODE_WALLETS_FILENAME,
        role="node",
        count=args.node_wallet_count,
        create_missing=True,
    )
    payout_path, payout_wallets = reset.resolve_generated_wallets(
        args,
        tmp_path,
        "unique-run",
        kind=reset.PAYOUT_ADMIN_WALLETS_FILENAME,
        role="payout-admin",
        count=args.payout_admin_wallet_count,
        create_missing=True,
    )

    assert node_path == tmp_path / "runtime" / "deployments" / "dev" / "runs" / "unique-run" / "node-wallets-42424242.json"
    assert payout_path == tmp_path / "runtime" / "deployments" / "dev" / "runs" / "unique-run" / "payout-admin-wallets-42424242.json"
    assert [wallet.address for wallet in node_wallets] == [
        "0x0000000000000000000000000000000000000001",
        "0x0000000000000000000000000000000000000002",
    ]
    assert [wallet.address for wallet in payout_wallets] == ["0x0000000000000000000000000000000000000003"]

    payload = reset.generated_wallets_payload(
        node_path,
        node_wallets,
        tmp_path,
        funding_wei=args.node_wallet_funding_wei,
    )
    public = reset.public_generated_wallets_record(payload)

    assert public is not None
    assert public["count"] == 2
    assert public["wallets"][0]["address"] == "0x0000000000000000000000000000000000000001"
    assert "private_key" not in json.dumps(public)


def test_setup_log_uses_operator_visible_prefix(capsys) -> None:
    reset = load_dev_chain_reset()

    reset.setup_log("unit setup phase")

    assert capsys.readouterr().out == "SETUP: unit setup phase\n"


def test_private_key_env_overrides_private_key(monkeypatch) -> None:
    reset = load_dev_chain_reset()
    parser = reset.build_parser()
    key = "0x" + "12" * 32
    monkeypatch.setenv("UNIT_DEPLOYER_PRIVATE_KEY", key)

    args = parser.parse_args(["--dry-run", "--private-key-env", "UNIT_DEPLOYER_PRIVATE_KEY"])

    reset.validate_args(args)

    assert args.private_key == key
    assert args.private_key_env == "UNIT_DEPLOYER_PRIVATE_KEY"


def test_private_key_env_rejects_empty_value(monkeypatch) -> None:
    reset = load_dev_chain_reset()
    parser = reset.build_parser()
    monkeypatch.setenv("UNIT_DEPLOYER_PRIVATE_KEY", "")

    args = parser.parse_args(["--dry-run", "--private-key-env", "UNIT_DEPLOYER_PRIVATE_KEY"])

    try:
        reset.validate_args(args)
    except ValueError as exc:
        assert "not set or is empty" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected empty --private-key-env value to be rejected")


def test_display_command_redacts_private_key_arguments() -> None:
    reset = load_dev_chain_reset()
    key = "0x" + "12" * 32
    address = "0x" + "34" * 20

    displayed = reset.display_command(
        [
            "cast",
            "send",
            address,
            "--private-key",
            key,
            "--private-key=" + key,
            "--json",
        ]
    )

    assert key not in displayed
    assert address in displayed
    assert "--private-key <redacted>" in displayed
    assert "--private-key=<redacted>" in displayed


def test_run_command_failure_redacts_private_key(monkeypatch) -> None:
    reset = load_dev_chain_reset()
    key = "0x" + "56" * 32
    command = ["cast", "send", "--private-key", key]

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "")

    monkeypatch.setattr(reset.subprocess, "run", fake_run)

    try:
        reset.run_command(command)
    except RuntimeError as exc:
        message = str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected run_command to raise on non-zero exit")

    assert key not in message
    assert "--private-key <redacted>" in message


def test_run_command_timeout_redacts_private_key(monkeypatch) -> None:
    reset = load_dev_chain_reset()
    key = "0x" + "78" * 32
    command = ["forge", "create", "--private-key", key]

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(reset.subprocess, "run", fake_run)

    try:
        reset.run_command(command, timeout_s=1)
    except RuntimeError as exc:
        message = str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected run_command timeout to raise")

    assert key not in message
    assert "--private-key <redacted>" in message

