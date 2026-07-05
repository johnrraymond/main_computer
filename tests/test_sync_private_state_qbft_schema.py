from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_TOOL_PATH = REPO_ROOT / "tools" / "sync_private_state.py"


def load_sync_tool() -> Any:
    spec = importlib.util.spec_from_file_location("sync_private_state_under_test", SYNC_TOOL_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def populated_state(existing: dict[str, Any]) -> tuple[Any, Any]:
    sync = load_sync_tool()
    builder = sync.StateBuilder(existing)
    sync.populate_state(builder, REPO_ROOT)
    return sync, builder


def test_sync_preserves_manual_qbft_instances_and_orders_schema() -> None:
    sync, builder = populated_state(
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
                        "validators": 99,
                        "rpc_port": 1,
                        "validator_p2p_ports": [2],
                        "instances": {
                            "validator-1": {
                                "p2p_host_port": 30311,
                                "roles": ["validator"],
                                "coolify_host": "A",
                            },
                            "rpc-1": {
                                "p2p_host_port": 30321,
                                "rpc_host_port": 30010,
                                "roles": ["rpc"],
                                "coolify_host": "A",
                            },
                        },
                    }
                }
            },
        }
    )

    qbft = builder.state["networks"]["testnet"]["qbft"]
    assert set(qbft) == {"instances"}
    assert qbft["instances"]["validator-1"]["coolify_host"] == "A"
    assert qbft["instances"]["validator-1"]["roles"] == ["validator"]

    rendered = sync.emit_yaml(sync.order_mapping(builder.state, ()), builder.provenance)
    assert "instances:" in rendered
    assert rendered.index("coolify_host:") < rendered.index("roles:")
    assert "validator_p2p_ports" not in rendered


def test_sync_sanitizes_only_bad_qbft_coolify_host_reference() -> None:
    _sync, builder = populated_state(
        {
            "coolify": {"hosts": {"A": {"name": "coolify-a"}}},
            "networks": {
                "testnet": {
                    "qbft": {
                        "instances": {
                            "validator-1": {
                                "coolify_host": "missing",
                                "roles": ["validator"],
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
    assert instance["roles"] == ["validator"]
    assert instance["p2p_host_port"] == 30311


def test_sync_records_structured_qbft_warnings() -> None:
    _sync, builder = populated_state(
        {
            "coolify": {"hosts": {"A": {"name": "coolify-a"}}},
            "networks": {
                "testnet": {
                    "qbft": {
                        "instances": {
                            "rpc-1": {
                                "coolify_host": "A",
                                "roles": ["rpc", "archive"],
                                "p2p_host_port": 30321,
                            }
                        }
                    }
                }
            },
        }
    )

    codes = {warning["code"] for warning in builder.warnings}
    assert "qbft_instance_invalid_role" in codes
    assert "qbft_rpc_missing_rpc_host_port" in codes

def test_sync_removes_remote_coolify_hosts_from_local_test_network() -> None:
    _sync, builder = populated_state(
        {
            "coolify": {
                "hosts": {
                    "A": {"name": "coolify-a"},
                    "B": {"name": "coolify-b"},
                }
            },
            "networks": {
                "test": {
                    "remote_coolify_hosts": ["A", "B"],
                }
            },
        }
    )

    assert "remote_coolify_hosts" not in builder.state["networks"]["test"]


def test_networks_render_in_private_state_environment_order() -> None:
    sync = load_sync_tool()
    state = {
        "networks": {
            "testnet": {"chain_id": 42424241},
            "test": {"chain_id": 42424241},
            "mainnet": {"chain_id": 42424240},
            "dev": {"chain_id": 31337},
        }
    }

    rendered = sync.emit_yaml(sync.order_mapping(state, ()), {})
    assert rendered.index("  dev:") < rendered.index("  test:")
    assert rendered.index("  test:") < rendered.index("  testnet:")
    assert rendered.index("  testnet:") < rendered.index("  mainnet:")

