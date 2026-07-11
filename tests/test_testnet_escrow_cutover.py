from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "tools" / "testnet_escrow_cutover.py"

PRIVATE_RPC = "https://private-rpc.example.invalid"
ADDR_1 = "0x1111111111111111111111111111111111111111"
ADDR_2 = "0x2222222222222222222222222222222222222222"
ADDR_3 = "0x3333333333333333333333333333333333333333"
ADDR_4 = "0x4444444444444444444444444444444444444444"
SHARED_ADMIN = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OLD_ESCROW = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
NEW_ESCROW = "0xcccccccccccccccccccccccccccccccccccccccc"
ALPHA_BETA_LOCKOUT = "0x0101010101010101010101010101010101010101"
XLAG_BRIDGE_RESERVE = "0x0202020202020202020202020202020202020202"


def load_cutover():
    spec = importlib.util.spec_from_file_location("testnet_escrow_cutover_tests", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_fixture_repo(tmp_path: Path, *, mismatched_hubs: bool = False, private_rpc: str = PRIVATE_RPC) -> None:
    (tmp_path / "main_computer" / "config").mkdir(parents=True)
    (tmp_path / "deploy" / "hub-topology").mkdir(parents=True)
    (tmp_path / "runtime" / "state").mkdir(parents=True)
    (tmp_path / "runtime" / "deployments" / "testnet").mkdir(parents=True)

    (tmp_path / "main_computer" / "config" / "hub_networks.json").write_text(
        json.dumps(
            {
                "version": 2,
                "networks": {
                    "testnet": {
                        "chain_id": 42424241,
                        "chain_rpc_url": "https://hub-networks-rpc.example.invalid",
                        "deployment_manifest_path": "runtime/deployments/testnet/latest.json",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "deploy" / "hub-topology" / "testnet-coolify-deployment.json").write_text(
        json.dumps(
            {
                "network_key": "testnet",
                "hubs": [
                    {"hub_id": "testnet-hub1", "public_url": "https://hub1.example.invalid"},
                    {"hub_id": "testnet-hub2", "public_url": "https://hub2.example.invalid"},
                    {"hub_id": "testnet-hub3", "public_url": "https://hub3.example.invalid"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "main_computer" / "config" / "testnet_contracts.json").write_text(
        json.dumps(
            {
                "alpha-beta-lockout": ALPHA_BETA_LOCKOUT,
                "hub_credit_bridge_escrow": OLD_ESCROW,
                "xlag-bridge-reserve": XLAG_BRIDGE_RESERVE,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "runtime" / "deployments" / "testnet" / "latest.json").write_text(
        json.dumps(
            {
                "schema": "main-computer.deployment.v1",
                "environment": "testnet",
                "chain": {"chain_id": 42424241, "rpc_url": "https://stale-metadata-rpc.example.invalid"},
                "hub_admin": {
                    "address": SHARED_ADMIN,
                    "wallet_path": "runtime/deployments/testnet/hub-admin-wallet.json",
                    "private_key": "0x" + "a" * 64,
                },
                "offices": [
                    {"office": "O0", "address": ADDR_1},
                    {"office": "O1", "address": ADDR_2},
                    {"office": "O2", "address": ADDR_3},
                    {"office": "O3", "address": ADDR_4},
                ],
                "contracts": {
                    "alpha-beta-lockout": {
                        "target": "AlphaBetaLockout.sol:AlphaBetaLockout",
                        "address": ALPHA_BETA_LOCKOUT,
                    },
                    "hub_credit_bridge_escrow": {
                        "target": "src/HubCreditBridgeEscrow.sol:HubCreditBridgeEscrow",
                        "address": OLD_ESCROW,
                        "transaction_hash": "0x" + "b" * 64,
                        "legacy_field": "preserved-until-overridden",
                    },
                },
                "deployments": {
                    "hub_credit_bridge_escrow": {
                        "target": "src/HubCreditBridgeEscrow.sol:HubCreditBridgeEscrow",
                        "address": OLD_ESCROW,
                    },
                    "xlag-bridge-reserve": {
                        "target": "src/XLagBridgeReserve.sol:XLagBridgeReserve",
                        "address": XLAG_BRIDGE_RESERVE,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    hub3_address = "0x9999999999999999999999999999999999999999" if mismatched_hubs else SHARED_ADMIN
    (tmp_path / "runtime" / "state" / "main_computer.private.yaml").write_text(
        yaml.safe_dump(
            {
                "networks": {
                    "testnet": {
                        "rpc_url": private_rpc,
                        "wallets": {
                            "escrow_owner": {"private_key": "0x" + "e" * 64},
                            "captain": {"address": ADDR_1},
                            "o1": {"address": ADDR_2},
                            "o2": {"address": ADDR_3},
                            "o3": {"address": ADDR_4},
                        },
                        "hubs": {
                            "testnet-hub1": {
                                "hub_admin_keys": {
                                    "address1": {
                                        "address": SHARED_ADMIN,
                                        "state": "active",
                                        "chain_authorized": True,
                                        "deployed_to_hub": True,
                                    }
                                }
                            },
                            "testnet-hub2": {
                                "hub_admin_keys": {
                                    "address1": {
                                        "address": SHARED_ADMIN,
                                        "state": "active",
                                        "chain_authorized": True,
                                        "deployed_to_hub": True,
                                    }
                                }
                            },
                            "testnet-hub3": {
                                "hub_admin_keys": {
                                    "address1": {
                                        "address": hub3_address,
                                        "state": "active",
                                        "chain_authorized": True,
                                        "deployed_to_hub": True,
                                    }
                                }
                            },
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def parsed_args(cutover, *extra: str):
    return cutover.build_parser().parse_args(["preflight", "--network", "testnet", *extra])


def test_build_context_discovers_shared_hub_admin_officers_and_private_rpc(tmp_path, monkeypatch) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path)
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)

    args = parsed_args(cutover, "--skip-chain-preflight")
    ctx = cutover.build_context(args)

    assert ctx.profile.rpc_url == PRIVATE_RPC
    assert ctx.old_escrow_address == OLD_ESCROW
    assert ctx.shared_hub_admin_address == SHARED_ADMIN
    assert ctx.hub_ids == ("testnet-hub1", "testnet-hub2", "testnet-hub3")
    assert ctx.officer_addresses == (ADDR_1, ADDR_2, ADDR_3, ADDR_4)


def test_build_context_rejects_mixed_active_hub_admins(tmp_path, monkeypatch) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path, mismatched_hubs=True)
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)

    args = parsed_args(cutover, "--skip-chain-preflight")

    with pytest.raises(cutover.TestnetEscrowCutoverError, match="active Hub signers differ"):
        cutover.build_context(args)


def test_missing_private_state_is_the_default_action_required_checkpoint(tmp_path, monkeypatch, capsys) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path)
    (tmp_path / "runtime" / "state" / "main_computer.private.yaml").unlink()
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)

    code = cutover.main(["preflight", "--network", "testnet"])

    assert code == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert "action required: operator private state is required." in output.out
    assert "--private-file" in output.out
    assert "--deployment" not in output.out


def test_missing_deployment_metadata_happens_after_private_state(tmp_path, monkeypatch, capsys) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path)
    (tmp_path / "runtime" / "deployments" / "testnet" / "latest.json").unlink()
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)

    code = cutover.main(["preflight", "--network", "testnet"])

    assert code == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert "action required: testnet deployment metadata is required." in output.out
    assert "escrow-only cutover needs current metadata" in output.out
    assert "testnet_escrow_cutover.py deploy" in output.out
    assert "coolify_qbft_network.py" not in output.out
    assert "dev-chain-reset.py" not in output.out


def test_merged_metadata_replaces_only_escrow_and_preserves_private_hub_admin(tmp_path, monkeypatch) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path)
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)

    args = parsed_args(cutover, "--skip-chain-preflight")
    ctx = cutover.build_context(args)
    merged_deployment = cutover.merged_deployment_payload(ctx, new_address=NEW_ESCROW, transaction_hash="0x" + "c" * 64)
    merged_public = cutover.merged_public_contracts(ctx, new_address=NEW_ESCROW)

    assert merged_deployment["contracts"]["alpha-beta-lockout"]["address"] == ALPHA_BETA_LOCKOUT
    assert merged_deployment["deployments"]["xlag-bridge-reserve"]["address"] == XLAG_BRIDGE_RESERVE
    assert merged_deployment["contracts"]["hub_credit_bridge_escrow"]["address"] == NEW_ESCROW
    assert merged_deployment["deployments"]["hub_credit_bridge_escrow"]["address"] == NEW_ESCROW
    assert merged_deployment["deployments"]["hub_credit_bridge_escrow"]["bridge_controller_address"] == SHARED_ADMIN
    assert merged_deployment["deployments"]["hub_credit_bridge_escrow"]["officer_addresses"] == [ADDR_1, ADDR_2, ADDR_3, ADDR_4]
    assert merged_deployment["hub_admin"]["private_key"] == "0x" + "a" * 64
    assert merged_public == {
        "alpha-beta-lockout": ALPHA_BETA_LOCKOUT,
        "hub_credit_bridge_escrow": NEW_ESCROW,
        "xlag-bridge-reserve": XLAG_BRIDGE_RESERVE,
    }


def test_write_outputs_do_not_modify_source_overrides(tmp_path, monkeypatch) -> None:
    cutover = load_cutover()
    local_repo = tmp_path / "local_repo"
    source_repo = tmp_path / "source_repo"
    write_fixture_repo(local_repo)
    write_fixture_repo(source_repo)
    monkeypatch.setattr(cutover, "repo_root", lambda: local_repo)

    source_deployment = source_repo / "runtime" / "deployments" / "testnet" / "latest.json"
    source_contracts = source_repo / "main_computer" / "config" / "testnet_contracts.json"
    local_deployment = local_repo / "runtime" / "deployments" / "testnet" / "latest.json"
    local_contracts = local_repo / "main_computer" / "config" / "testnet_contracts.json"

    source_deployment_before = source_deployment.read_text(encoding="utf-8")
    source_contracts_before = source_contracts.read_text(encoding="utf-8")

    args = parsed_args(
        cutover,
        "--skip-chain-preflight",
        "--private-file",
        str(source_repo / "runtime" / "state" / "main_computer.private.yaml"),
        "--deployment",
        str(source_deployment),
        "--contracts-path",
        str(source_contracts),
    )
    ctx = cutover.build_context(args)

    assert ctx.deployment_source_path == source_deployment
    assert ctx.public_contracts_source_path == source_contracts
    assert ctx.deployment_output_path == local_deployment
    assert ctx.public_contracts_output_path == local_contracts

    cutover.write_cutover_outputs(ctx, new_address=NEW_ESCROW, transaction_hash="0x" + "c" * 64, rid="unit-test")

    assert source_deployment.read_text(encoding="utf-8") == source_deployment_before
    assert source_contracts.read_text(encoding="utf-8") == source_contracts_before
    assert json.loads(local_deployment.read_text(encoding="utf-8"))["deployments"]["hub_credit_bridge_escrow"]["address"] == NEW_ESCROW
    assert json.loads(local_contracts.read_text(encoding="utf-8"))["hub_credit_bridge_escrow"] == NEW_ESCROW


def test_preflight_blocks_nonzero_live_old_escrow_balance(tmp_path, monkeypatch) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path)
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)

    def fake_rpc(url: str, method: str, params: list | None = None, *, timeout_s: float = 20.0):
        assert url == PRIVATE_RPC
        if method == "eth_getCode":
            return "0x6000"
        if method == "eth_getBalance":
            return "0x7b"
        raise AssertionError(method)

    monkeypatch.setattr(cutover, "rpc", fake_rpc)
    args = parsed_args(cutover)
    ctx = cutover.build_context(args)

    with pytest.raises(cutover.TestnetEscrowCutoverError, match="old escrow balance is nonzero"):
        cutover.preflight_chain(ctx, args)


def test_preflight_allows_missing_old_escrow_when_preserved_contracts_are_live(tmp_path, monkeypatch, capsys) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path)
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)

    def fake_rpc(url: str, method: str, params: list | None = None, *, timeout_s: float = 20.0):
        assert url == PRIVATE_RPC
        if method == "eth_getCode":
            address = params[0]
            return "0x" if address == OLD_ESCROW else "0x6000"
        if method == "eth_getBalance":
            raise AssertionError("old escrow balance should not be read without old escrow code")
        raise AssertionError(method)

    monkeypatch.setattr(cutover, "rpc", fake_rpc)

    code = cutover.main(["preflight", "--network", "testnet"])

    assert code == 0
    output = capsys.readouterr().out
    assert "current escrow baseline: not live on selected RPC" in output
    assert "escrow-only deploy may continue without old-state migration" in output
    assert "result: ready for escrow-only HubCreditBridgeEscrow deploy preview" in output
    assert "next useful command:" in output
    assert (
        "python .\\tools\\testnet_escrow_cutover.py deploy --network testnet --dry-run"
        in output
    )



def test_preflight_prints_exact_dry_run_deploy_command_for_supplied_paths(tmp_path, monkeypatch, capsys) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path)
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)

    def fake_rpc(url: str, method: str, params: list | None = None, *, timeout_s: float = 20.0):
        assert url == PRIVATE_RPC
        if method == "eth_getCode":
            address = params[0]
            return "0x" if address == OLD_ESCROW else "0x6000"
        if method == "eth_getBalance":
            raise AssertionError("old escrow balance should not be read without old escrow code")
        raise AssertionError(method)

    monkeypatch.setattr(cutover, "rpc", fake_rpc)

    code = cutover.main(
        [
            "preflight",
            "--network",
            "testnet",
            "--private-file",
            "runtime/state/main_computer.private.yaml",
            "--deployment",
            "runtime/deployments/testnet/latest.json",
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "next useful command:" in output
    assert (
        "python .\\tools\\testnet_escrow_cutover.py deploy --network testnet "
        "--private-file runtime/state/main_computer.private.yaml "
        "--deployment runtime/deployments/testnet/latest.json --dry-run"
    ) in output



def test_preflight_blocks_when_preserved_contract_metadata_is_not_live(tmp_path, monkeypatch, capsys) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path)
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)

    def fake_rpc(url: str, method: str, params: list | None = None, *, timeout_s: float = 20.0):
        assert url == PRIVATE_RPC
        if method == "eth_getCode":
            return "0x"
        raise AssertionError(method)

    monkeypatch.setattr(cutover, "rpc", fake_rpc)

    code = cutover.main(["preflight", "--network", "testnet"])

    assert code == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert "action required: preserved contract metadata is not valid for the selected RPC." in output.out
    assert "non-escrow contract" in output.out
    assert "separate root-contract deployment, not this escrow-only cutover" in output.out
    assert "coolify_qbft_network.py" not in output.out


def test_preflight_skip_chain_prints_ready_without_chain_checks(tmp_path, monkeypatch, capsys) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path)
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)

    code = cutover.main(["preflight", "--network", "testnet", "--skip-chain-preflight"])

    assert code == 0
    output = capsys.readouterr().out
    assert "testnet escrow cutover preflight" in output
    assert "network: testnet" in output
    assert "chain preflight: skipped" in output
    assert "result: ready for escrow-only HubCreditBridgeEscrow deploy preview" in output


def test_plan_coolify_defaults_project_name_in_next_command(tmp_path, monkeypatch, capsys) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path)
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(cutover, "infer_git_repo", lambda root: "https://github.com/example/main-computer")
    monkeypatch.setattr(cutover, "infer_git_branch", lambda root: "testnet-cutover")

    code = cutover.main(["plan-coolify", "--network", "testnet"])

    assert code == 0
    output = capsys.readouterr().out
    assert "--coolify-project-name \"My first project\"" in output
    assert "--git-branch testnet-cutover" in output
    assert "--private-state runtime/state/main_computer.private.yaml" in output
    assert "--force-domain-override" not in output
