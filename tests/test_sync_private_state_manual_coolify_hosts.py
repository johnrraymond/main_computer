from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_TOOL_PATH = REPO_ROOT / "tools" / "sync_private_state.py"


def load_sync_tool() -> Any:
    spec = importlib.util.spec_from_file_location("sync_private_state_under_test", SYNC_TOOL_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def populated_state(existing: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    sync = load_sync_tool()
    builder = sync.StateBuilder(existing)
    sync.populate_state(builder, REPO_ROOT)
    return sync, builder.state


def test_mainnet_hydrates_null_readiness_without_inventing_coolify_host_c() -> None:
    sync, state = populated_state(
        {
            "coolify": {
                "hosts": {
                    "A": {"name": "coolify-a"},
                    "B": {"name": "coolify-b"},
                }
            }
        }
    )

    assert set(state["coolify"]["hosts"]) == {"A", "B"}
    rendered = sync.emit_yaml(sync.order_mapping(state, ()), {})
    assert "mainnet-a" not in rendered
    assert "\n    C:" not in rendered

    mainnet = state["networks"]["mainnet"]
    assert mainnet["display_name"] == "Main Computer Mainnet"
    assert mainnet["kind"] == "mainnet"
    assert mainnet["chain_id"] == 42424240
    assert mainnet["rpc"] == "https://mainnet-rpc.greatlibrary.io"
    assert "remote_coolify_hosts" not in mainnet

    hubs = mainnet["hub"]["instances"]
    assert set(hubs) == {"mainnet-hub1"}
    assert "coolify_host" not in hubs["mainnet-hub1"]

    for role in ("deployer", "escrow_owner", "hub_admin", "captain", "o1", "o2", "o3"):
        assert mainnet["wallets"][role]["address"] is None
        assert mainnet["wallets"][role]["private_key"] is None

    for contract_name in ("AlphaBetaLockout", "HubCreditBridgeEscrow", "XLagBridgeReserve"):
        assert mainnet["contracts"][contract_name]["address"] is None
        assert mainnet["contracts"][contract_name]["version"] is None
    assert mainnet["contracts"]["HubCreditBridgeEscrow"]["owner"] is None
    assert mainnet["contracts"]["HubCreditBridgeEscrow"]["bridge_controller"] is None
    assert mainnet["contracts"]["HubCreditBridgeEscrow"]["paused"] is None
    assert mainnet["contracts"]["XLagBridgeReserve"]["captain"] is None
    assert mainnet["contracts"]["XLagBridgeReserve"]["crew"] is None


def test_stale_mainnet_coolify_host_references_are_removed_when_c_is_not_manual() -> None:
    _sync, state = populated_state(
        {
            "coolify": {
                "hosts": {
                    "A": {"name": "coolify-a"},
                    "B": {"name": "coolify-b"},
                }
            },
            "networks": {
                "mainnet": {
                    "remote_coolify_hosts": ["C"],
                    "hub": {
                        "instances": {
                            "mainnet-hub1": {
                                "coolify_host": "C",
                            }
                        }
                    },
                }
            },
        }
    )

    mainnet = state["networks"]["mainnet"]
    assert "remote_coolify_hosts" not in mainnet
    assert "coolify_host" not in mainnet["hub"]["instances"]["mainnet-hub1"]


def test_testnet_keeps_manual_coolify_host_mapping_for_a_and_b() -> None:
    _sync, state = populated_state(
        {
            "coolify": {
                "hosts": {
                    "A": {"name": "coolify-a"},
                    "B": {"name": "coolify-b"},
                }
            }
        }
    )

    testnet = state["networks"]["testnet"]
    assert testnet["remote_coolify_hosts"] == ["A", "B"]
    assert testnet["hub"]["instances"]["testnet-hub1"]["coolify_host"] == "A"
    assert testnet["hub"]["instances"]["testnet-hub2"]["coolify_host"] == "A"
    assert testnet["hub"]["instances"]["testnet-hub3"]["coolify_host"] == "B"


def test_empty_coolify_hosts_does_not_allocate_remote_host_slots() -> None:
    _sync, state = populated_state({"coolify": {"hosts": {}}})

    assert state["coolify"]["hosts"] == {}
    assert "remote_coolify_hosts" not in state["networks"]["testnet"]
    assert "remote_coolify_hosts" not in state["networks"]["mainnet"]
    assert "coolify_host" not in state["networks"]["mainnet"]["hub"]["instances"]["mainnet-hub1"]
