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


def test_mainnet_hydrates_repo_known_state_without_inventing_coolify_host_c() -> None:
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
    assert mainnet["remote_coolify_hosts"] == ["A", "B"]

    hubs = mainnet["hub"]["instances"]
    assert set(hubs) == {"mainnet-hub1", "mainnet-hub2", "mainnet-hub3"}
    assert hubs["mainnet-hub1"]["coolify_host"] == "A"
    assert hubs["mainnet-hub2"]["coolify_host"] == "A"
    assert hubs["mainnet-hub3"]["coolify_host"] == "B"

    for role in ("deployer", "escrow_owner", "captain", "o1", "o2", "o3"):
        assert mainnet["wallets"][role]["private_key"] is None
    assert "hub_admin" not in mainnet["wallets"]

    hub_admin_hubs = mainnet["hubs"]
    assert set(hub_admin_hubs) == {"mainnet-hub1", "mainnet-hub2", "mainnet-hub3"}
    for hub in hub_admin_hubs.values():
        key = hub["hub_admin_keys"]["address1"]
        assert key["address"] is None
        assert key["private_key"] is None
        assert key["state"] is None
        assert key["chain_authorized"] is None
        assert key["deployed_to_hub"] is None

    assert "contracts" not in mainnet
    for role, wallet in mainnet["wallets"].items():
        assert "credits" not in wallet


def test_sync_prunes_existing_contracts_and_wallet_credits_from_private_state() -> None:
    _sync, state = populated_state(
        {
            "contracts": {"legacy": {"address": "0x0000000000000000000000000000000000000001"}},
            "networks": {
                "mainnet": {
                    "contracts": {
                        "HubCreditBridgeEscrow": {
                            "address": "0x0000000000000000000000000000000000000002",
                            "code_present": True,
                        }
                    },
                    "last_seen": {"contracts": "ok"},
                    "wallets": {
                        "deployer": {
                            "address": "0x0000000000000000000000000000000000000003",
                            "private_key": None,
                            "credits": 123,
                        },
                        "hub_admin": {
                            "address": "0x0000000000000000000000000000000000000004",
                            "private_key": "0x4444444444444444444444444444444444444444444444444444444444444444",
                            "credits": 456,
                        },
                        "credits_only": {"credits": 999},
                    },
                }
            },
        }
    )

    assert "contracts" not in state
    mainnet = state["networks"]["mainnet"]
    assert "contracts" not in mainnet
    assert "contracts" not in mainnet.get("last_seen", {})
    assert "credits" not in mainnet["wallets"]["deployer"]
    assert "hub_admin" not in mainnet["wallets"]
    assert "credits_only" not in mainnet["wallets"]
    assert mainnet["hubs"]["mainnet-hub1"]["hub_admin_keys"]["address1"]["address"] == "0x0000000000000000000000000000000000000004"


def test_legacy_hub_admin_wallet_is_migrated_to_each_known_hub_and_removed() -> None:
    _sync, state = populated_state(
        {
            "networks": {
                "testnet": {
                    "wallets": {
                        "hub_admin": {
                            "address": "0x1000000000000000000000000000000000000006",
                            "private_key": "0x6666666666666666666666666666666666666666666666666666666666666666",
                        }
                    }
                }
            }
        }
    )

    testnet = state["networks"]["testnet"]
    assert "hub_admin" not in testnet["wallets"]
    assert set(testnet["hubs"]) == {"testnet-hub1", "testnet-hub2", "testnet-hub3"}
    for hub_id in ("testnet-hub1", "testnet-hub2", "testnet-hub3"):
        key = testnet["hubs"][hub_id]["hub_admin_keys"]["address1"]
        assert key == {
            "address": "0x1000000000000000000000000000000000000006",
            "private_key": "0x6666666666666666666666666666666666666666666666666666666666666666",
            "state": "active",
            "chain_authorized": True,
            "deployed_to_hub": True,
        }


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
    assert mainnet["remote_coolify_hosts"] == ["A", "B"]
    assert mainnet["hub"]["instances"]["mainnet-hub1"]["coolify_host"] == "A"


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


def test_dev_hub_admin_sync_allocates_next_address_slot_without_clobbering_lifecycle() -> None:
    _sync, state = populated_state(
        {
            "networks": {
                "dev": {
                    "hubs": {
                        "dev-hub1": {
                            "hub_admin_keys": {
                                "address1": {
                                    "address": "0x0000000000000000000000000000000000000004",
                                    "private_key": "0x4444444444444444444444444444444444444444444444444444444444444444",
                                    "state": "chain_revocation_pending",
                                    "chain_authorized": True,
                                    "deployed_to_hub": False,
                                }
                            }
                        }
                    }
                }
            }
        }
    )

    keys = state["networks"]["dev"]["hubs"]["dev-hub1"]["hub_admin_keys"]
    assert keys["address1"] == {
        "address": "0x0000000000000000000000000000000000000004",
        "private_key": "0x4444444444444444444444444444444444444444444444444444444444444444",
        "state": "chain_revocation_pending",
        "chain_authorized": True,
        "deployed_to_hub": False,
    }
    assert keys["address2"]["address"] == "0x81eCa2C8BA8A23cb662803584ec0C2B6B4F68FeC"
    assert keys["address2"]["state"] == "active"
    assert keys["address2"]["chain_authorized"] is True
    assert keys["address2"]["deployed_to_hub"] is True


def test_legacy_hub_admin_migration_uses_next_address_slot_when_address1_is_occupied() -> None:
    _sync, state = populated_state(
        {
            "networks": {
                "testnet": {
                    "wallets": {
                        "hub_admin": {
                            "address": "0x0000000000000000000000000000000000000008",
                            "private_key": "0x8888888888888888888888888888888888888888888888888888888888888888",
                        }
                    },
                    "hubs": {
                        "testnet-hub1": {
                            "hub_admin_keys": {
                                "address1": {
                                    "address": "0x0000000000000000000000000000000000000007",
                                    "private_key": "0x7777777777777777777777777777777777777777777777777777777777777777",
                                    "state": "chain_revocation_pending",
                                    "chain_authorized": True,
                                    "deployed_to_hub": False,
                                }
                            }
                        }
                    },
                }
            }
        }
    )

    testnet = state["networks"]["testnet"]
    assert "hub_admin" not in testnet["wallets"]
    keys = testnet["hubs"]["testnet-hub1"]["hub_admin_keys"]
    assert keys["address1"]["address"] == "0x0000000000000000000000000000000000000007"
    assert keys["address1"]["state"] == "chain_revocation_pending"
    assert keys["address2"] == {
        "address": "0x0000000000000000000000000000000000000008",
        "private_key": "0x8888888888888888888888888888888888888888888888888888888888888888",
        "state": "active",
        "chain_authorized": True,
        "deployed_to_hub": True,
    }
