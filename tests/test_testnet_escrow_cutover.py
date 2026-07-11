from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "tools" / "testnet_escrow_cutover.py"

ADDR_1 = "0x1111111111111111111111111111111111111111"
ADDR_2 = "0x2222222222222222222222222222222222222222"
ADDR_3 = "0x3333333333333333333333333333333333333333"
ADDR_4 = "0x4444444444444444444444444444444444444444"
SHARED_ADMIN = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OLD_ESCROW = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
NEW_ESCROW = "0xcccccccccccccccccccccccccccccccccccccccc"


def load_cutover():
    spec = importlib.util.spec_from_file_location("testnet_escrow_cutover_tests", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_fixture_repo(tmp_path: Path, *, mismatched_hubs: bool = False) -> None:
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
                        "chain_rpc_url": "https://rpc.example.invalid",
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
                "alpha-beta-lockout": "0x0101010101010101010101010101010101010101",
                "hub_credit_bridge_escrow": OLD_ESCROW,
                "xlag-bridge-reserve": "0x0202020202020202020202020202020202020202",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "runtime" / "deployments" / "testnet" / "latest.json").write_text(
        json.dumps(
            {
                "schema": "main-computer.deployment.v1",
                "environment": "testnet",
                "chain": {"chain_id": 42424241, "rpc_url": "https://rpc.example.invalid"},
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
                        "address": "0x0101010101010101010101010101010101010101",
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
                        "address": "0x0202020202020202020202020202020202020202",
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


def test_build_context_discovers_shared_hub_admin_and_officers(tmp_path, monkeypatch) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path)
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)

    args = cutover.build_parser().parse_args(["preflight", "--network", "testnet", "--skip-chain-preflight"])
    ctx = cutover.build_context(args)

    assert ctx.old_escrow_address == OLD_ESCROW
    assert ctx.shared_hub_admin_address == SHARED_ADMIN
    assert ctx.hub_ids == ("testnet-hub1", "testnet-hub2", "testnet-hub3")
    assert ctx.officer_addresses == (ADDR_1, ADDR_2, ADDR_3, ADDR_4)


def test_build_context_rejects_mixed_active_hub_admins(tmp_path, monkeypatch) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path, mismatched_hubs=True)
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)

    args = cutover.build_parser().parse_args(["preflight", "--network", "testnet", "--skip-chain-preflight"])

    with pytest.raises(cutover.TestnetEscrowCutoverError, match="active Hub signers differ"):
        cutover.build_context(args)


def test_merged_metadata_replaces_only_escrow_and_preserves_private_hub_admin(tmp_path, monkeypatch) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path)
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)

    args = cutover.build_parser().parse_args(["preflight", "--network", "testnet", "--skip-chain-preflight"])
    ctx = cutover.build_context(args)
    merged_deployment = cutover.merged_deployment_payload(ctx, new_address=NEW_ESCROW, transaction_hash="0x" + "c" * 64)
    merged_public = cutover.merged_public_contracts(ctx, new_address=NEW_ESCROW)

    assert merged_deployment["contracts"]["alpha-beta-lockout"]["address"] == "0x0101010101010101010101010101010101010101"
    assert merged_deployment["deployments"]["xlag-bridge-reserve"]["address"] == "0x0202020202020202020202020202020202020202"
    assert merged_deployment["contracts"]["hub_credit_bridge_escrow"]["address"] == NEW_ESCROW
    assert merged_deployment["deployments"]["hub_credit_bridge_escrow"]["address"] == NEW_ESCROW
    assert merged_deployment["deployments"]["hub_credit_bridge_escrow"]["bridge_controller_address"] == SHARED_ADMIN
    assert merged_deployment["deployments"]["hub_credit_bridge_escrow"]["officer_addresses"] == [ADDR_1, ADDR_2, ADDR_3, ADDR_4]
    assert merged_deployment["hub_admin"]["private_key"] == "0x" + "a" * 64
    assert merged_public == {
        "alpha-beta-lockout": "0x0101010101010101010101010101010101010101",
        "hub_credit_bridge_escrow": NEW_ESCROW,
        "xlag-bridge-reserve": "0x0202020202020202020202020202020202020202",
    }


def test_preflight_blocks_nonzero_old_escrow_balance(tmp_path, monkeypatch) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path)
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)

    def fake_rpc(url: str, method: str, params: list | None = None, *, timeout_s: float = 20.0):
        assert url == "https://rpc.example.invalid"
        if method == "eth_getCode":
            return "0x6000"
        if method == "eth_getBalance":
            return "0x7b"
        raise AssertionError(method)

    monkeypatch.setattr(cutover, "rpc", fake_rpc)
    args = cutover.build_parser().parse_args(["preflight", "--network", "testnet"])
    ctx = cutover.build_context(args)

    with pytest.raises(cutover.TestnetEscrowCutoverError, match="old escrow balance is nonzero"):
        cutover.preflight_chain(ctx, args)


def test_preflight_no_code_explains_stale_metadata_value_sources(tmp_path, monkeypatch) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path)
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)

    def fake_rpc(url: str, method: str, params: list | None = None, *, timeout_s: float = 20.0):
        assert url == "https://rpc.example.invalid"
        if method == "eth_getCode":
            return "0x"
        raise AssertionError(method)

    monkeypatch.setattr(cutover, "rpc", fake_rpc)
    args = cutover.build_parser().parse_args(["preflight", "--network", "testnet"])
    ctx = cutover.build_context(args)

    with pytest.raises(cutover.TestnetEscrowCutoverError) as excinfo:
        cutover.preflight_chain(ctx, args)

    message = str(excinfo.value)
    assert message.startswith("action required: supplied deployment metadata is stale for the selected RPC.")
    assert "RPC comes from --rpc-url if supplied, otherwise private state networks.<network>.rpc" in message
    assert "escrow address comes from deployment metadata/public contract config" in message
    assert "--private-file runtime/state/main_computer.private.yaml --deployment runtime/deployments/testnet/latest.json --rpc-url" in message
    assert "coolify_qbft_network.py deploy-contracts testnet --dry-run" in message
    assert "Re-run with the operator private state" not in message
    assert "path-to-main_computer.private.yaml" not in message


def test_preflight_skip_chain_prints_ready_without_writes(tmp_path, monkeypatch, capsys) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path)
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)

    code = cutover.main(["preflight", "--network", "testnet", "--skip-chain-preflight"])

    assert code == 0
    output = capsys.readouterr().out
    assert "testnet escrow cutover preflight" in output
    assert f"old_escrow: {OLD_ESCROW}" in output
    assert f"shared_hub_admin: {SHARED_ADMIN}" in output
    assert "chain preflight: skipped" in output
    assert "result: ready to deploy new HubCreditBridgeEscrow shape" in output


def test_missing_default_private_state_prefers_private_file_action(tmp_path, monkeypatch) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path)
    (tmp_path / "runtime" / "state" / "main_computer.private.yaml").unlink()
    (tmp_path / "runtime" / "deployments" / "testnet" / "latest.json").unlink()
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)

    args = cutover.build_parser().parse_args(["preflight", "--network", "testnet", "--skip-chain-preflight"])

    with pytest.raises(cutover.TestnetEscrowCutoverError) as excinfo:
        cutover.build_context(args)

    message = str(excinfo.value)
    assert message.startswith("action required: operator private state is required.")
    assert "--private-file" in message
    assert "<path-to-main_computer.private.yaml>" in message
    assert "--deployment" not in message
    assert "dev-chain-reset.py" not in message
    assert "Copy-Item" not in message


def test_missing_deployment_metadata_after_private_state_points_to_qbft_surface(tmp_path, monkeypatch) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path)
    missing = tmp_path / "runtime" / "deployments" / "testnet" / "latest.json"
    missing.unlink()
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)

    args = cutover.build_parser().parse_args(["preflight", "--network", "testnet", "--skip-chain-preflight"])

    with pytest.raises(cutover.TestnetEscrowCutoverError) as excinfo:
        cutover.build_context(args)

    message = str(excinfo.value)
    assert message.startswith("action required: testnet deployment metadata is missing.")
    assert "replace only hub_credit_bridge_escrow" in message
    assert "coolify_qbft_network.py deploy-contracts testnet --dry-run" in message
    assert str(missing) in message
    assert "dev-chain-reset.py" not in message
    assert "looked for existing candidates" not in message
    assert "Copy-Item" not in message


def test_private_state_rpc_overrides_hub_networks_fallback(tmp_path, monkeypatch) -> None:
    cutover = load_cutover()
    write_fixture_repo(tmp_path)
    private_path = tmp_path / "runtime" / "state" / "main_computer.private.yaml"
    state = yaml.safe_load(private_path.read_text(encoding="utf-8"))
    state["networks"]["testnet"]["rpc"] = "https://private-state-rpc.example.invalid"
    private_path.write_text(yaml.safe_dump(state), encoding="utf-8")
    monkeypatch.setattr(cutover, "repo_root", lambda: tmp_path)

    args = cutover.build_parser().parse_args(["preflight", "--network", "testnet", "--skip-chain-preflight"])
    ctx = cutover.build_context(args)

    assert ctx.profile.rpc_url == "https://private-state-rpc.example.invalid"


