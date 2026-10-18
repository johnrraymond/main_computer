"""Mother-verified Hub contract-deployer boundary regression tests."""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from tools import mother_contract_deploy as deployer
from tools.mother.common.ethereum_identity import private_key_to_address
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import prepare_private_state_bootstrap, install_verified_private_state


def _operation() -> OperationIdentity:
    return OperationIdentity(operation_id="mother-contract-fixture", request_id="mother-contract-fixture",
                             network="mainnet", operation_kind="MOTHER-OP-DIAGNOSE")


def _state(tmp_path: Path, *, chain_id: int = 42424240, wrong_key: bool = False) -> tuple[Path, Path, str]:
    root = tmp_path / "runtime" / "state"
    paths = MotherPaths(runtime_state_root=root).resolve_private_state_paths()
    roles = ("deployer", "captain", "o1", "o2", "o3")
    keys = ["0x" + f"{index:064x}" for index in range(1, 6)]
    wallets = {
        role: {"private_key": key, "address": private_key_to_address(key)}
        for role, key in zip(roles, keys)
    }
    if wrong_key:
        wallets["deployer"]["address"] = wallets["captain"]["address"]
    document = {"kind": "main_computer.mother.private_state.v1", "schema_version": 1,
                "networks": {"mainnet": {"chain_id": chain_id, "wallets": wallets}}}
    initial = prepare_private_state_bootstrap(paths, document,
        updated_at="2026-10-17T00:00:00Z", updated_by_action_id="mother-contract-fixture", operation=_operation())
    install_verified_private_state(paths, initial, None, operation=_operation())
    return root, paths.identity_file, keys[0]


def _args(root: Path, *, extra: tuple[str, ...] = ()) -> list[str]:
    return ["--network", "mainnet", "--chain-id", "42424240",
            "--rpc-url", "https://mainnet-rpc.example.invalid",
            "--runtime-state-root", str(root), *extra]


def test_verified_mother_delegates_to_existing_operator_without_leaking_key(tmp_path, monkeypatch, capsys):
    root, _, secret = _state(tmp_path)
    seen = {}

    def fake_run(command, *, cwd, env, check):
        seen.update(command=command, cwd=cwd, env=env, check=check)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(deployer.subprocess, "run", fake_run)
    assert deployer.main(_args(root, extra=("--yes",))) == 0
    command = seen["command"]
    assert command[1].endswith("mainnet-operator.py")
    assert "deploy-contracts" in command
    assert command[command.index("--target-environment") + 1] == "mainnet"
    assert command[command.index("--offices") + 1].count(",") == 3
    assert "--yes" in command
    assert seen["env"]["MAINNET_DEPLOYER_PRIVATE_KEY"] == secret
    assert secret not in str(command)
    assert secret not in capsys.readouterr().out
    assert "runtime/state/main_computer.private.yaml" not in str(command)


def test_dry_run_preserves_operator_preview_semantics(tmp_path, monkeypatch):
    root, _, _ = _state(tmp_path)
    observed = []
    monkeypatch.setattr(deployer.subprocess, "run", lambda command, **kw: (observed.append(command) or subprocess.CompletedProcess(command, 0)))
    assert deployer.main(_args(root, extra=("--dry-run",))) == 0
    assert "--dry-run" in observed[0] and "--yes" not in observed[0]


def test_missing_or_unverified_mother_is_fail_closed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(deployer.subprocess, "run", lambda *a, **k: pytest.fail("operator must not run"))
    assert deployer.main(_args(tmp_path, extra=("--yes",))) == 1
    assert "verified Mother identity unavailable" in capsys.readouterr().err

    root, identity, secret = _state(tmp_path / "installed")
    identity.write_text(identity.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    assert deployer.main(_args(root, extra=("--yes",))) == 1
    output = capsys.readouterr().err
    assert "verified Mother identity unavailable" in output
    assert secret not in output


def test_chain_id_mismatch_rejects_before_subprocess(tmp_path, monkeypatch, capsys):
    root, _, _ = _state(tmp_path)
    monkeypatch.setattr(deployer.subprocess, "run", lambda *a, **k: pytest.fail("operator must not run"))
    args = _args(root, extra=("--yes",))
    args[args.index("--chain-id") + 1] = "42424241"
    assert deployer.main(args) == 1
    assert "differs from requested chain ID" in capsys.readouterr().err


def test_mother_deployer_key_address_mismatch_rejects(tmp_path, monkeypatch, capsys):
    root, _, secret = _state(tmp_path, wrong_key=True)
    monkeypatch.setattr(deployer.subprocess, "run", lambda *a, **k: pytest.fail("operator must not run"))
    assert deployer.main(_args(root, extra=("--yes",))) == 1
    error = capsys.readouterr().err
    assert "key/address mismatch" in error
    assert secret not in error


def test_operator_failure_status_is_propagated(tmp_path, monkeypatch):
    root, _, _ = _state(tmp_path)
    monkeypatch.setattr(deployer.subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(command, 17))
    assert deployer.main(_args(root, extra=("--yes",))) == 17


def test_wrapper_rejects_embedded_url_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(deployer.subprocess, "run", lambda *a, **k: pytest.fail("operator must not run"))
    args = _args(tmp_path, extra=("--yes",))
    args[args.index("--rpc-url") + 1] = "https://user:password@rpc.example.invalid/"
    assert deployer.main(args) == 1
    assert "without embedded credentials" in capsys.readouterr().err


def test_missing_testnet_identity_rejects_before_subprocess(tmp_path, monkeypatch, capsys):
    root, _, _ = _state(tmp_path)
    monkeypatch.setattr(deployer.subprocess, "run", lambda *a, **k: pytest.fail("operator must not run"))
    args = _args(root, extra=("--yes",))
    args[args.index("--network") + 1] = "testnet"
    assert deployer.main(args) == 1
    assert "no verified testnet network" in capsys.readouterr().err


def test_deploy_wrapper_does_not_reference_retired_private_yaml():
    source = Path(deployer.__file__).read_text(encoding="utf-8")
    assert "main_computer.private.yaml" not in source
    assert "open(" not in source
    assert "shell=True" not in source
