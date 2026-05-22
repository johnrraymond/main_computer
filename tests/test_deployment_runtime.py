from __future__ import annotations

import json
from pathlib import Path

from main_computer.config import MainComputerConfig
from main_computer.dev_chain_runtime import apply_dev_chain_runtime_config


def _deployment_payload(*, rpc_url: str = "http://127.0.0.1:18546", chain_id: int = 42424242) -> dict:
    return {
        "schema": "main-computer.deployment.v1",
        "environment": "dev",
        "run_id": "prod-like-dev",
        "chain": {
            "chain_id": chain_id,
            "rpc_url": rpc_url,
            "host_rpc_url": rpc_url,
        },
        "contracts": {
            "alpha-beta-lockout": {
                "target": "AlphaBetaLockout.sol:AlphaBetaLockout",
                "address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            },
            "xlag-bridge-reserve": {
                "target": "src/XLagBridgeReserve.sol:XLagBridgeReserve",
                "address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            },
        },
        "offices": [
            {
                "office": "O0",
                "title": "Captain",
                "address": "0x1111111111111111111111111111111111111111",
                "private_key": "0x" + "1" * 64,
            }
        ],
    }


def test_production_shaped_deployment_runtime_takes_precedence_over_legacy_dev_chain(tmp_path: Path) -> None:
    current = tmp_path / "runtime" / "deployments" / "current.json"
    current.parent.mkdir(parents=True)
    current.write_text(json.dumps(_deployment_payload()), encoding="utf-8")

    legacy = tmp_path / "runtime" / "dev-chain" / "latest.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps(
            {
                "run_id": "legacy",
                "chain": {"chain_id": 111, "host_rpc_url": "http://127.0.0.1:18545"},
                "deployments": {
                    "xlag-bridge-reserve": {"address": "0xcccccccccccccccccccccccccccccccccccccccc"},
                    "alpha-beta-lockout": {"address": "0xdddddddddddddddddddddddddddddddddddddddd"},
                },
            }
        ),
        encoding="utf-8",
    )

    config = apply_dev_chain_runtime_config(MainComputerConfig(workspace=tmp_path), tmp_path)

    assert config.dev_chain_runtime_path == current
    assert config.dev_chain_runtime_source == "deployment-runtime"
    assert config.dev_chain_run_id == "prod-like-dev"
    assert config.energy_chain_rpc_url == "http://127.0.0.1:18546"
    assert config.energy_chain_rpc_url_source == "deployment-runtime"
    assert config.xlag_contract_address == "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    assert config.alpha_beta_lockout_contract_address == "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert len(config.dev_chain_offices) == 1
    assert "private_key" not in config.dev_chain_offices[0]


def test_legacy_dev_chain_runtime_still_works_when_public_deployment_is_absent(tmp_path: Path) -> None:
    legacy = tmp_path / "runtime" / "dev-chain" / "latest.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps(
            {
                "run_id": "legacy-only",
                "chain": {"chain_id": 42424243, "host_rpc_url": "http://127.0.0.1:18547"},
                "deployments": {
                    "xlag-bridge-reserve": {"address": "0xcccccccccccccccccccccccccccccccccccccccc"},
                    "alpha-beta-lockout": {"address": "0xdddddddddddddddddddddddddddddddddddddddd"},
                },
            }
        ),
        encoding="utf-8",
    )

    config = apply_dev_chain_runtime_config(MainComputerConfig(workspace=tmp_path), tmp_path)

    assert config.dev_chain_runtime_path == legacy
    assert config.dev_chain_runtime_source == "runtime-dev-chain"
    assert config.dev_chain_run_id == "legacy-only"
    assert config.energy_chain_rpc_url == "http://127.0.0.1:18547"
    assert config.energy_chain_id == 42424243


def test_invalid_public_deployment_runtime_does_not_fall_through_to_legacy(tmp_path: Path) -> None:
    current = tmp_path / "runtime" / "deployments" / "current.json"
    current.parent.mkdir(parents=True)
    current.write_text("[]", encoding="utf-8")

    legacy = tmp_path / "runtime" / "dev-chain" / "latest.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps(_deployment_payload(rpc_url="http://127.0.0.1:18599")), encoding="utf-8")

    config = apply_dev_chain_runtime_config(MainComputerConfig(workspace=tmp_path), tmp_path)

    assert config.dev_chain_runtime_path == current
    assert config.dev_chain_runtime_source == "invalid"
    assert "deployment runtime current.json" in str(config.dev_chain_runtime_error)
    assert config.energy_chain_rpc_url != "http://127.0.0.1:18599"
