from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_TOOL_PATH = REPO_ROOT / "tools" / "sync_private_state.py"


def load_sync_tool() -> Any:
    spec = importlib.util.spec_from_file_location("sync_private_state_qbft_under_test", SYNC_TOOL_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def populated_builder(existing: dict[str, Any]) -> tuple[Any, Any]:
    sync = load_sync_tool()
    builder = sync.StateBuilder(existing)
    sync.populate_state(builder, REPO_ROOT)
    return sync, builder


def test_testnet_qbft_instances_are_populated_without_legacy_summary_fields() -> None:
    _sync, builder = populated_builder(
        {
            "coolify": {
                "hosts": {
                    "A": {"name": "coolify-a"},
                    "B": {"name": "coolify-b"},
                }
            },
            "networks": {
                "testnet": {
                    "rpc": "http://198.199.75.153:30010",
                    "qbft": {
                        "coolify_host": "A",
                        "validators": 1,
                        "rpc_port": 30010,
                        "validator_p2p_ports": [30311],
                    },
                }
            },
        }
    )

    testnet = builder.state["networks"]["testnet"]
    assert testnet["rpc"] == "https://testnet-rpc.greatlibrary.io"

    qbft = testnet["qbft"]
    assert set(qbft) == {"instances"}
    assert qbft["instances"] == {
        "validator-1": {
            "coolify_host": "A",
            "roles": ["validator", "rpc"],
            "rpc_host_port": 30010,
            "p2p_host_port": 30311,
        }
    }


def test_local_test_qbft_instances_match_current_seed_shape() -> None:
    _sync, builder = populated_builder({})

    instances = builder.state["networks"]["test"]["qbft"]["instances"]
    assert instances["validator-1"] == {
        "coolify_host": "local_test",
        "roles": ["validator"],
        "rpc_host_port": 30001,
        "p2p_host_port": 30311,
    }
    assert instances["validator-4"] == {
        "coolify_host": "local_test",
        "roles": ["validator"],
        "rpc_host_port": 30004,
        "p2p_host_port": 30314,
    }
    assert instances["rpc-1"] == {
        "coolify_host": "local_test",
        "roles": ["rpc"],
        "rpc_host_port": 30010,
        "p2p_host_port": 30320,
    }


def test_operator_qbft_instance_fields_are_preserved() -> None:
    _sync, builder = populated_builder(
        {
            "coolify": {
                "hosts": {
                    "A": {"name": "coolify-a"},
                    "B": {"name": "coolify-b"},
                }
            },
            "networks": {
                "testnet": {
                    "qbft": {
                        "instances": {
                            "validator-1": {
                                "coolify_host": "B",
                                "roles": ["validator"],
                                "rpc_host_port": 31010,
                                "p2p_host_port": 31311,
                            }
                        }
                    }
                }
            },
        }
    )

    assert builder.state["networks"]["testnet"]["qbft"]["instances"]["validator-1"] == {
        "coolify_host": "B",
        "roles": ["validator"],
        "rpc_host_port": 31010,
        "p2p_host_port": 31311,
    }


def test_stale_qbft_coolify_host_reference_is_removed_and_warned() -> None:
    _sync, builder = populated_builder(
        {
            "coolify": {
                "hosts": {
                    "A": {"name": "coolify-a"},
                    "B": {"name": "coolify-b"},
                }
            },
            "networks": {
                "testnet": {
                    "qbft": {
                        "instances": {
                            "validator-1": {
                                "coolify_host": "C",
                                "roles": ["validator", "rpc"],
                                "rpc_host_port": 30010,
                                "p2p_host_port": 30311,
                            }
                        }
                    }
                }
            },
        }
    )

    instance = builder.state["networks"]["testnet"]["qbft"]["instances"]["validator-1"]
    assert "coolify_host" not in instance
    assert any(
        warning["code"] == "qbft_missing_coolify_host"
        and warning["path"] == "networks.testnet.qbft.instances.validator-1.coolify_host"
        for warning in builder.warnings
    )


def test_qbft_validation_warnings_are_structured_and_non_fatal() -> None:
    _sync, builder = populated_builder(
        {
            "coolify": {"hosts": {"A": {"name": "coolify-a"}}},
            "networks": {
                "testnet": {
                    "qbft": {
                        "instances": {
                            "validator-1": {
                                "coolify_host": "A",
                                "roles": ["validator", "bogus"],
                                "p2p_host_port": 30311,
                            },
                            "rpc-1": {
                                "coolify_host": "A",
                                "roles": ["rpc"],
                                "p2p_host_port": 30311,
                            },
                        }
                    }
                }
            },
        }
    )

    codes = {warning["code"] for warning in builder.warnings}
    assert "qbft_instance_invalid_role" in codes
    assert "qbft_rpc_missing_rpc_host_port" in codes
    assert "qbft_duplicate_host_port" in codes
