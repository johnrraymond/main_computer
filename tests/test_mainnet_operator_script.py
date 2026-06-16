from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "tools" / "mainnet-operator.py"

ROTATED_OFFICES = ",".join(
    [
        "0x1111111111111111111111111111111111111111",
        "0x2222222222222222222222222222222222222222",
        "0x3333333333333333333333333333333333333333",
        "0x4444444444444444444444444444444444444444",
    ]
)
ROTATED_PRIVATE_KEY = "0x" + ("11" * 32)


def load_mainnet_operator():
    spec = importlib.util.spec_from_file_location("mainnet_operator", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def parse_deploy_args(module, extra: list[str] | None = None):
    parser = module.build_parser()
    argv = [
        "deploy-contracts",
        "--rpc-url",
        "http://144.126.212.9:31010",
        "--offices",
        ROTATED_OFFICES,
        "--private-key-env",
        "MAINNET_DEPLOYER_PRIVATE_KEY",
        "--run-id",
        "unit-mainnet-run",
        "--dry-run",
    ]
    if extra:
        argv.extend(extra)
    return parser.parse_args(argv)


def test_deploy_contracts_builds_dev_chain_reset_delegation(monkeypatch) -> None:
    module = load_mainnet_operator()
    monkeypatch.setenv("MAINNET_DEPLOYER_PRIVATE_KEY", ROTATED_PRIVATE_KEY)
    args = parse_deploy_args(module)

    offices = module.validate_deploy_contracts_args(args)
    command = module.dev_chain_reset_argv(args, offices)

    assert command[0] == "--dry-run"
    assert "--environment" in command
    assert command[command.index("--environment") + 1] == "mainnet"
    assert "--source-kind" in command
    assert command[command.index("--source-kind") + 1] == "mainnet-operator-deploy"
    assert "--chain-id" in command
    assert command[command.index("--chain-id") + 1] == "42424240"
    assert "--offices" in command
    assert command[command.index("--offices") + 1] == ROTATED_OFFICES
    assert "--private-key-env" in command
    assert command[command.index("--private-key-env") + 1] == "MAINNET_DEPLOYER_PRIVATE_KEY"
    assert ROTATED_PRIVATE_KEY not in command


def test_deploy_contracts_dry_run_does_not_execute_delegated_script(monkeypatch, capsys) -> None:
    module = load_mainnet_operator()
    monkeypatch.setenv("MAINNET_DEPLOYER_PRIVATE_KEY", ROTATED_PRIVATE_KEY)

    def fail_load_dev_chain_reset_module():
        raise AssertionError("dry run should not load or execute dev-chain-reset.py")

    monkeypatch.setattr(module, "load_dev_chain_reset_module", fail_load_dev_chain_reset_module)

    rc = module.main(
        [
            "deploy-contracts",
            "--rpc-url",
            "http://144.126.212.9:31010",
            "--offices",
            ROTATED_OFFICES,
            "--private-key-env",
            "MAINNET_DEPLOYER_PRIVATE_KEY",
            "--run-id",
            "unit-mainnet-run",
            "--dry-run",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert "tools/dev-chain-reset.py" in captured.out
    assert "--private-key-env MAINNET_DEPLOYER_PRIVATE_KEY" in captured.out
    assert ROTATED_PRIVATE_KEY not in captured.out


def test_deploy_contracts_rejects_default_anvil_offices(monkeypatch) -> None:
    module = load_mainnet_operator()
    monkeypatch.setenv("MAINNET_DEPLOYER_PRIVATE_KEY", ROTATED_PRIVATE_KEY)
    args = parse_deploy_args(
        module,
        [
            "--offices",
            ",".join(
                [
                    "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
                    "0x2222222222222222222222222222222222222222",
                    "0x3333333333333333333333333333333333333333",
                    "0x4444444444444444444444444444444444444444",
                ]
            ),
        ],
    )

    with pytest.raises(ValueError, match="default Anvil office"):
        module.validate_deploy_contracts_args(args)


def test_deploy_contracts_rejects_default_anvil_deployer_key(monkeypatch) -> None:
    module = load_mainnet_operator()
    monkeypatch.setenv("MAINNET_DEPLOYER_PRIVATE_KEY", module.DEFAULT_DEV_PRIVATE_KEY)
    args = parse_deploy_args(module)

    with pytest.raises(ValueError, match="default Anvil deployer key"):
        module.validate_deploy_contracts_args(args)


def test_deploy_contracts_rejects_missing_private_key_env(monkeypatch) -> None:
    module = load_mainnet_operator()
    monkeypatch.delenv("MAINNET_DEPLOYER_PRIVATE_KEY", raising=False)
    args = parse_deploy_args(module)

    with pytest.raises(ValueError, match="not set or is empty"):
        module.validate_deploy_contracts_args(args)


def test_deploy_contracts_rejects_non_mainnet_chain_id(monkeypatch) -> None:
    module = load_mainnet_operator()
    monkeypatch.setenv("MAINNET_DEPLOYER_PRIVATE_KEY", ROTATED_PRIVATE_KEY)
    args = parse_deploy_args(module, ["--chain-id", "42424241"])

    with pytest.raises(ValueError, match="requires --chain-id 42424240"):
        module.validate_deploy_contracts_args(args)
