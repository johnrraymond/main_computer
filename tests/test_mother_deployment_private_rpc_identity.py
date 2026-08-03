from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from tools import mother_deploy
from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_private_rpc_identity import (
    MotherDeploymentPrivateRpcIdentityError,
    inspect_private_rpc_identity_reservation,
    reserve_private_rpc_identity,
    verify_private_rpc_identity,
)
from tools.mother.common.ethereum_identity import (
    private_key_to_address,
    private_key_to_node_id,
)
from tests.test_mother_deployment_executor import _operation
from tests.test_mother_deployment_private_rpc import _soak_fixture


PRIVATE_KEY_ONE = "0x" + "0" * 63 + "1"


def _identity_fixture(tmp_path: Path, monkeypatch):
    paths, private_state, soak_path, _ = _soak_fixture(tmp_path, monkeypatch)
    result = reserve_private_rpc_identity(
        paths,
        private_state,
        service_name="mainnet-rpc1",
        operation=_operation("private-rpc-identity"),
        key_factory=lambda: PRIVATE_KEY_ONE,
    )
    identity_path = paths.root / result["identity_locator"]
    return paths, private_state, soak_path, result, identity_path


def test_private_rpc_identity_is_reserved_once_and_secret_is_not_printed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, _, result, identity_path = _identity_fixture(
        tmp_path,
        monkeypatch,
    )
    assert result["status"] == "pass"
    assert result["identity_created"] is True
    assert result["private_key_present"] is True
    assert result["private_key_printed"] is False
    assert result["node_id"] == private_key_to_node_id(PRIVATE_KEY_ONE)
    assert result["address"] == private_key_to_address(PRIVATE_KEY_ONE).lower()
    assert PRIVATE_KEY_ONE not in json.dumps(result)

    document = json.loads(identity_path.read_text(encoding="utf-8"))
    assert document["private_key"] == PRIVATE_KEY_ONE
    assert document["node_id"] == result["node_id"]
    assert document["address"] == result["address"]
    if os.name != "nt":
        assert stat.S_IMODE(identity_path.stat().st_mode) == 0o600

    second = reserve_private_rpc_identity(
        paths,
        private_state,
        service_name="mainnet-rpc1",
        operation=_operation("private-rpc-identity-repeat"),
        key_factory=lambda: "0x" + "0" * 62 + "02",
    )
    assert second["identity_created"] is False
    assert second["write_performed"] is False
    assert second["node_id"] == result["node_id"]
    assert json.loads(identity_path.read_text(encoding="utf-8"))["private_key"] == PRIVATE_KEY_ONE


def test_private_rpc_identity_inspect_and_verify_are_offline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, _, result, identity_path = _identity_fixture(
        tmp_path,
        monkeypatch,
    )
    inspected = inspect_private_rpc_identity_reservation(
        paths,
        private_state,
        service_name="mainnet-rpc1",
        operation=_operation("private-rpc-identity-inspect"),
    )
    assert inspected["identity_exists"] is True
    assert inspected["write_performed"] is False
    assert inspected["network_access_performed"] is False
    assert inspected["live_mutation_performed"] is False

    verified = verify_private_rpc_identity(
        paths,
        private_state,
        identity_path,
        service_name="mainnet-rpc1",
        operation=_operation("private-rpc-identity-verify"),
    )
    assert verified["clean"] is True
    assert verified["identity_file_sha256"] == result["identity_file_sha256"]
    assert verified["private_key_printed"] is False
    assert verified["validator_identity"] is False


def test_private_rpc_identity_verifier_rejects_tamper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths, private_state, _, _, identity_path = _identity_fixture(
        tmp_path,
        monkeypatch,
    )
    document = json.loads(identity_path.read_text(encoding="utf-8"))
    document["node_id"] = "ab" * 64
    identity_path.write_bytes(canonical_json(document))

    with pytest.raises(MotherDeploymentPrivateRpcIdentityError) as caught:
        verify_private_rpc_identity(
            paths,
            private_state,
            identity_path,
            service_name="mainnet-rpc1",
            operation=_operation("private-rpc-identity-tampered"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_PRIVATE_RPC_IDENTITY_INVALID"


def test_private_rpc_stage_cli_consumes_protected_identity(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    paths, private_state, soak_path, result, _ = _identity_fixture(tmp_path, monkeypatch)
    runtime_state_root = paths.root.parent
    args = mother_deploy._parser().parse_args(
        [
            "stage-private-rpc-transaction",
            "--runtime-state-root",
            str(runtime_state_root),
            "--network",
            "mainnet",
            "--soak-evidence",
            str(soak_path),
            "--controller-id",
            "coolify-a",
            "--service-name",
            "mainnet-rpc1",
            "--identity",
            str(paths.root / result["identity_locator"]),
            "--write-transaction",
        ]
    )
    exit_code = mother_deploy._cmd_stage_private_rpc_transaction(args, private_state)
    assert exit_code == 0
    output = capsys.readouterr().out
    assert PRIVATE_KEY_ONE not in output
    staged = json.loads(output)
    assert staged["identity"]["expected_node_id"] == result["node_id"]
    assert staged["identity"]["expected_node_address"] == result["address"]
    assert staged["transaction_artifact"]["path"]


def test_private_rpc_identity_cli_help(capsys) -> None:
    with pytest.raises(SystemExit) as reserve_exit:
        mother_deploy._parser().parse_args(
            ["reserve-private-rpc-identity", "--help"]
        )
    assert reserve_exit.value.code == 0
    reserve_help = capsys.readouterr().out
    assert "--write-identity" in reserve_help
    assert "--service-name" in reserve_help

    with pytest.raises(SystemExit) as verify_exit:
        mother_deploy._parser().parse_args(
            ["verify-private-rpc-identity", "--help"]
        )
    assert verify_exit.value.code == 0
    assert "--identity" in capsys.readouterr().out

    with pytest.raises(SystemExit) as stage_exit:
        mother_deploy._parser().parse_args(
            ["stage-private-rpc-transaction", "--help"]
        )
    assert stage_exit.value.code == 0
    assert "--identity" in capsys.readouterr().out
