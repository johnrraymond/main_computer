from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NON_DEFAULT_KEY = "0x" + "11" * 32
NON_DEFAULT_OFFICES = ",".join(
    [
        "0x1111111111111111111111111111111111111111",
        "0x2222222222222222222222222222222222222222",
        "0x3333333333333333333333333333333333333333",
        "0x4444444444444444444444444444444444444444",
    ]
)


def load_mainnet_operator():
    spec = importlib.util.spec_from_file_location("mainnet_operator", ROOT / "tools" / "mainnet-operator.py")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def parse_deploy_args(module, extra: list[str]):
    parser = module.build_parser()
    return parser.parse_args(["deploy-contracts", *extra])


def base_args(*, target: str = "dev", chain_id: int = 42424242, offices: str = NON_DEFAULT_OFFICES) -> list[str]:
    return [
        "--target-environment",
        target,
        "--rpc-url",
        "http://127.0.0.1:18545",
        "--external-docker-network",
        "main-computer-dev",
        "--chain-id",
        str(chain_id),
        "--offices",
        offices,
        "--private-key-env",
        "OPERATOR_DEPLOYER_PRIVATE_KEY",
        "--dry-run",
    ]


def test_dev_target_can_use_default_anvil_authority_with_explicit_opt_in(monkeypatch) -> None:
    operator = load_mainnet_operator()
    monkeypatch.setenv("OPERATOR_DEPLOYER_PRIVATE_KEY", operator.DEFAULT_DEV_PRIVATE_KEY)

    args = parse_deploy_args(
        operator,
        [
            *base_args(offices="default-anvil"),
            "--allow-dev-authority",
            "--run-id",
            "devnet-operator-practice",
        ],
    )

    operator.validate_deploy_args(args)
    command = operator.dev_chain_reset_command(args)

    assert "--environment" in command
    assert command[command.index("--environment") + 1] == "dev"
    assert command[command.index("--chain-id") + 1] == "42424242"
    assert command[command.index("--private-key-env") + 1] == "OPERATOR_DEPLOYER_PRIVATE_KEY"
    assert operator.DEFAULT_DEV_PRIVATE_KEY not in command
    assert command[command.index("--offices") + 1] == operator.DEFAULT_DEV_OFFICES_ARG


def test_dev_target_rejects_default_anvil_authority_without_opt_in(monkeypatch) -> None:
    operator = load_mainnet_operator()
    monkeypatch.setenv("OPERATOR_DEPLOYER_PRIVATE_KEY", operator.DEFAULT_DEV_PRIVATE_KEY)
    args = parse_deploy_args(operator, base_args(offices="default-anvil"))

    try:
        operator.validate_deploy_args(args)
    except ValueError as exc:
        assert "--allow-dev-authority" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected default dev authority to require explicit opt in")


def test_mainnet_rejects_default_anvil_deployer_even_with_non_default_offices(monkeypatch) -> None:
    operator = load_mainnet_operator()
    monkeypatch.setenv("OPERATOR_DEPLOYER_PRIVATE_KEY", operator.DEFAULT_DEV_PRIVATE_KEY)
    args = parse_deploy_args(operator, base_args(target="mainnet", chain_id=operator.MAINNET_CHAIN_ID))

    try:
        operator.validate_deploy_args(args)
    except ValueError as exc:
        assert "default Anvil" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected mainnet to reject the default Anvil deployer")


def test_mainnet_rejects_default_anvil_offices_even_with_non_default_deployer(monkeypatch) -> None:
    operator = load_mainnet_operator()
    monkeypatch.setenv("OPERATOR_DEPLOYER_PRIVATE_KEY", NON_DEFAULT_KEY)
    args = parse_deploy_args(
        operator,
        base_args(target="mainnet", chain_id=operator.MAINNET_CHAIN_ID, offices="default-anvil"),
    )

    try:
        operator.validate_deploy_args(args)
    except ValueError as exc:
        assert "default Anvil" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected mainnet to reject the default Anvil offices")


def test_non_mainnet_target_cannot_claim_mainnet_chain_id(monkeypatch) -> None:
    operator = load_mainnet_operator()
    monkeypatch.setenv("OPERATOR_DEPLOYER_PRIVATE_KEY", NON_DEFAULT_KEY)
    args = parse_deploy_args(operator, base_args(target="dev", chain_id=operator.MAINNET_CHAIN_ID))

    try:
        operator.validate_deploy_args(args)
    except ValueError as exc:
        assert "reserved" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected dev target to reject the mainnet chain id")


def test_mainnet_requires_mainnet_chain_id(monkeypatch) -> None:
    operator = load_mainnet_operator()
    monkeypatch.setenv("OPERATOR_DEPLOYER_PRIVATE_KEY", NON_DEFAULT_KEY)
    args = parse_deploy_args(operator, base_args(target="mainnet", chain_id=42424242))

    try:
        operator.validate_deploy_args(args)
    except ValueError as exc:
        assert "mainnet target requires" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected mainnet target to require the mainnet chain id")


def test_dry_run_prints_delegated_command_without_executing(monkeypatch, capsys) -> None:
    operator = load_mainnet_operator()
    monkeypatch.setenv("OPERATOR_DEPLOYER_PRIVATE_KEY", operator.DEFAULT_DEV_PRIVATE_KEY)

    def fail_run(_command):
        raise AssertionError("dry run must not execute the delegated command")

    monkeypatch.setattr(operator, "run_command", fail_run)

    status = operator.main(
        [
            "deploy-contracts",
            *base_args(offices="default-anvil"),
            "--allow-dev-authority",
            "--run-id",
            "dry-run-only",
        ]
    )

    assert status == 0
    output = capsys.readouterr().out
    assert "target_environment=dev chain_id=42424242" in output
    assert "dev-chain-reset.py" in output
    assert "--private-key-env OPERATOR_DEPLOYER_PRIVATE_KEY" in output
    assert operator.DEFAULT_DEV_PRIVATE_KEY not in output
