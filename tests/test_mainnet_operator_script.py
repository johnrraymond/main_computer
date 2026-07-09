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



def test_private_key_to_address_matches_default_anvil_deployer() -> None:
    operator = load_mainnet_operator()
    assert operator.private_key_to_address(operator.DEFAULT_DEV_PRIVATE_KEY) == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


def test_prepare_keys_defaults_to_repo_private_state_path() -> None:
    operator = load_mainnet_operator()
    assert operator.default_private_state_path() == operator.repo_root() / "runtime" / "state" / "main_computer.private.yaml"


def test_prepare_keys_populates_missing_mainnet_wallets_without_printing_private_keys(tmp_path, capsys) -> None:
    operator = load_mainnet_operator()
    state_path = tmp_path / "main_computer.private.yaml"
    summary_path = tmp_path / "mainnet-wallets.public.json"
    local_secrets_path = tmp_path / "local.secrets"
    state_path.write_text(
        """
schema_version: 1
networks:
  mainnet:
    kind: mainnet
    wallets:
      deployer:
        address: null
        private_key: null
      captain:
        address: null
        private_key: null
      o1:
        address: null
        private_key: null
      o2:
        address: null
        private_key: null
      o3:
        address: null
        private_key: null
      hub_admin:
        address: null
        private_key: null
      escrow_owner:
        address: null
        private_key: null
""".lstrip(),
        encoding="utf-8",
    )

    status = operator.main(
        [
            "prepare-keys",
            "--network",
            "mainnet",
            "--state",
            str(state_path),
            "--summary-path",
            str(summary_path),
            "--local-secrets-path",
            str(local_secrets_path),
        ]
    )

    assert status == 0
    output = capsys.readouterr().out
    assert "private keys were not printed" in output
    assert "0x" + "11" * 32 not in output

    import yaml

    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    wallets = state["networks"]["mainnet"]["wallets"]
    assert set(operator.DEFAULT_KEYGEN_ROLES) <= set(wallets)
    for role in operator.DEFAULT_KEYGEN_ROLES:
        entry = wallets[role]
        assert operator.is_private_key(entry["private_key"])
        assert operator.is_address(entry["address"])
        assert operator.private_key_to_address(entry["private_key"]).lower() == entry["address"].lower()
        assert entry["source"] == "mainnet-operator prepare-keys mainnet"
        assert entry["created_at"].endswith("Z")

    local_secret_values = {line.strip() for line in local_secrets_path.read_text(encoding="utf-8").splitlines()}
    assert {wallets[role]["private_key"] for role in operator.DEFAULT_KEYGEN_ROLES} <= local_secret_values
    assert "local.secrets updated" in output

    import json

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["network"] == "mainnet"
    assert summary["private_keys_included"] is False
    assert sorted(item["role"] for item in summary["generated_roles"]) == sorted(operator.DEFAULT_KEYGEN_ROLES)
    summary_text = summary_path.read_text(encoding="utf-8")
    assert "private_key\"" not in summary_text
    assert "private_keys_included" in summary_text


def test_prepare_keys_refuses_address_without_matching_private_key(tmp_path) -> None:
    operator = load_mainnet_operator()
    state_path = tmp_path / "main_computer.private.yaml"
    local_secrets_path = tmp_path / "local.secrets"
    state_path.write_text(
        """
networks:
  mainnet:
    wallets:
      deployer:
        address: "0x1111111111111111111111111111111111111111"
        private_key: null
""".lstrip(),
        encoding="utf-8",
    )

    status = operator.main(
        [
            "prepare-keys",
            "--network",
            "mainnet",
            "--state",
            str(state_path),
            "--roles",
            "deployer",
            "--summary-path",
            str(tmp_path / "summary.json"),
            "--local-secrets-path",
            str(local_secrets_path),
        ]
    )

    assert status == 1


def test_prepare_keys_verifies_existing_private_key_and_fills_address(tmp_path) -> None:
    operator = load_mainnet_operator()
    state_path = tmp_path / "main_computer.private.yaml"
    summary_path = tmp_path / "summary.json"
    local_secrets_path = tmp_path / "local.secrets"
    private_key = "0x" + "12" * 32
    state_path.write_text(
        f"""
networks:
  testnet:
    wallets:
      deployer:
        address: null
        private_key: "{private_key}"
""".lstrip(),
        encoding="utf-8",
    )

    status = operator.main(
        [
            "prepare-keys",
            "--network",
            "testnet",
            "--state",
            str(state_path),
            "--roles",
            "deployer",
            "--summary-path",
            str(summary_path),
            "--local-secrets-path",
            str(local_secrets_path),
        ]
    )

    assert status == 0

    import yaml

    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    entry = state["networks"]["testnet"]["wallets"]["deployer"]
    assert entry["private_key"] == private_key
    assert entry["address"] == operator.private_key_to_address(private_key)
    assert private_key in {line.strip() for line in local_secrets_path.read_text(encoding="utf-8").splitlines()}

    import json

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["generated_roles"] == []
    assert summary["existing_roles"] == [{"address": entry["address"], "role": "deployer"}]



def test_append_local_secrets_is_idempotent_and_preserves_existing_lines(tmp_path) -> None:
    operator = load_mainnet_operator()
    local_secrets_path = tmp_path / "local.secrets"
    existing = "already-secret"
    private_key = "0x" + "34" * 32
    local_secrets_path.write_text(existing, encoding="utf-8")

    appended = operator.append_local_secrets(local_secrets_path, [private_key, private_key])

    assert appended == 1
    lines = local_secrets_path.read_text(encoding="utf-8").splitlines()
    assert lines == [existing, private_key]

    appended_again = operator.append_local_secrets(local_secrets_path, [private_key])
    assert appended_again == 0
    assert local_secrets_path.read_text(encoding="utf-8").splitlines() == [existing, private_key]

def test_private_key_to_address_does_not_shell_out_to_openssl(monkeypatch) -> None:
    operator = load_mainnet_operator()

    def fail_subprocess_run(*_args, **_kwargs):
        raise FileNotFoundError(2, "The system cannot find the file specified", "openssl")

    monkeypatch.setattr(operator.subprocess, "run", fail_subprocess_run)

    assert operator.private_key_to_address(operator.DEFAULT_DEV_PRIVATE_KEY) == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


def test_prepare_keys_file_not_found_diagnostic_includes_missing_filename(monkeypatch, capsys, tmp_path) -> None:
    operator = load_mainnet_operator()
    state_path = tmp_path / "main_computer.private.yaml"
    local_secrets_path = tmp_path / "local.secrets"
    state_path.write_text("networks: {mainnet: {wallets: {}}}\n", encoding="utf-8")

    def fail_load_private_state(_path):
        raise FileNotFoundError(2, "The system cannot find the file specified", "missing-tool.exe")

    monkeypatch.setattr(operator, "load_private_state", fail_load_private_state)

    status = operator.main(
        [
            "prepare-keys",
            "--network",
            "mainnet",
            "--state",
            str(state_path),
            "--roles",
            "deployer",
            "--summary-path",
            str(tmp_path / "summary.json"),
            "--local-secrets-path",
            str(local_secrets_path),
        ]
    )

    assert status == 1
    captured = capsys.readouterr()
    assert "prepare-keys" in captured.err
    assert "missing-tool.exe" in captured.err
    assert "FileNotFoundError" in captured.err
    assert "errno" in captured.err
