from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from tools import mother_deploy
from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_private_rpc_release import (
    MotherDeploymentPrivateRpcReleaseError,
    build_private_rpc_release,
    verify_private_rpc_release,
    write_private_rpc_release,
)
from tests.test_mother_deployment_executor import _operation
from tests.test_mother_deployment_private_rpc import _transaction


def _release(tmp_path: Path, monkeypatch):
    (
        paths,
        private_state,
        _,
        _,
        transaction,
        transaction_path,
        transaction_digest,
    ) = _transaction(tmp_path, monkeypatch)
    release = build_private_rpc_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_transaction_sha256=transaction_digest,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
    )
    release_path, release_digest = write_private_rpc_release(
        paths,
        release,
        operation=_operation("private-rpc-release"),
    )
    return (
        paths,
        private_state,
        transaction,
        transaction_path,
        transaction_digest,
        release,
        release_path,
        release_digest,
    )


def test_private_rpc_release_is_exact_expiring_and_non_validator(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        transaction,
        _,
        transaction_digest,
        release,
        release_path,
        release_digest,
    ) = _release(tmp_path, monkeypatch)

    assert release["private_rpc_release_sha256"] == release_digest
    assert release["transaction"]["sha256"] == transaction_digest
    assert release["staged_scope"] == "release-private-non-validator-rpc"
    assert release["execution_plan"] == transaction["execution_plan"]
    assert release["compose"] == transaction["compose"]
    assert release["required_secret_bindings"] == transaction[
        "required_secret_bindings"
    ]
    assert release["authority"] == {
        "private_rpc_service_create_authorized": True,
        "private_rpc_service_deploy_authorized": True,
        "secret_value_materialization_authorized": False,
        "validator_vote_authorized": False,
        "validator_identity_authorized": False,
        "validator_mutation_authorized": False,
        "public_endpoint_authorized": False,
        "host_port_authorized": False,
        "ssh_authorized": False,
        "requested_use_limit": 1,
    }
    assert release["policy"]["existing_validators_read_only"] is True
    assert release["policy"]["private_rpc_only"] is True
    assert release["policy"]["private_key_material_in_release"] is False
    assert release["policy"]["live_mutation_performed"] is False
    assert release["summary"]["mutation_count"] == 2
    assert release["summary"]["validator_mutation_count"] == 0

    verified = verify_private_rpc_release(
        paths,
        private_state,
        release_path,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
    )
    assert verified["clean"] is True
    assert verified["private_rpc_release_sha256"] == release_digest
    assert verified["transaction_sha256"] == transaction_digest
    assert verified["service_name"] == "mainnet-rpc1"
    assert verified["controller_id"] == "coolify-a"
    assert verified["mutation_count"] == 2
    assert verified["validator_mutation_count"] == 0
    assert verified["public_endpoint_count"] == 0
    assert verified["host_port_count"] == 0
    assert verified["live_mutation_performed"] is False
    assert verified["live_execution_available"] is False
    assert verified["next_phase"] == "private-rpc-executor-not-yet-implemented"


def test_private_rpc_release_rejects_acknowledgement_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        _,
        _,
        _,
        transaction_path,
        _,
    ) = _transaction(tmp_path, monkeypatch)

    with pytest.raises(MotherDeploymentPrivateRpcReleaseError) as caught:
        build_private_rpc_release(
            paths,
            private_state,
            transaction_path,
            acknowledged_transaction_sha256="00" * 32,
        )
    assert (
        caught.value.code
        == "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_ACKNOWLEDGEMENT_MISMATCH"
    )


def test_private_rpc_release_rejects_invalid_ttl(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        _,
        _,
        _,
        transaction_path,
        transaction_digest,
    ) = _transaction(tmp_path, monkeypatch)

    with pytest.raises(MotherDeploymentPrivateRpcReleaseError) as caught:
        build_private_rpc_release(
            paths,
            private_state,
            transaction_path,
            acknowledged_transaction_sha256=transaction_digest,
            expires_in_seconds=29,
        )
    assert caught.value.code == "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_TTL_INVALID"


def test_private_rpc_release_verifier_rejects_tamper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        _,
        _,
        _,
        _,
        release_path,
        _,
    ) = _release(tmp_path, monkeypatch)

    document = json.loads(release_path.read_text(encoding="utf-8"))
    document["execution_plan"]["mutations"][0][
        "canonical_request_body"
    ]["urls"] = ["https://rpc.invalid"]
    without = {
        key: value
        for key, value in document.items()
        if key != "private_rpc_release_sha256"
    }
    document["private_rpc_release_sha256"] = hashlib.sha256(
        canonical_json(without)
    ).hexdigest()
    release_path.write_bytes(canonical_json(document))

    with pytest.raises(MotherDeploymentPrivateRpcReleaseError) as caught:
        verify_private_rpc_release(
            paths,
            private_state,
            release_path,
            selected_nodes=("mainnetc-super1", "mainneta-super1"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_INVALID"


def test_private_rpc_release_verifier_rejects_expired_release(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        _,
        transaction_path,
        transaction_digest,
        _,
        _,
        _,
    ) = _release(tmp_path, monkeypatch)
    created = datetime.now(timezone.utc).replace(microsecond=0)
    release = build_private_rpc_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_transaction_sha256=transaction_digest,
        created_at=created.isoformat().replace("+00:00", "Z"),
        expires_in_seconds=30,
    )
    release_path, _ = write_private_rpc_release(
        paths,
        release,
        operation=_operation("private-rpc-expired-release"),
    )

    with pytest.raises(MotherDeploymentPrivateRpcReleaseError) as caught:
        verify_private_rpc_release(
            paths,
            private_state,
            release_path,
            now=created + timedelta(seconds=31),
        )
    assert caught.value.code == "MOTHER_DEPLOY_PRIVATE_RPC_RELEASE_EXPIRED"


def test_private_rpc_cli_exposes_release_and_verify(capsys) -> None:
    with pytest.raises(SystemExit) as release_exit:
        mother_deploy._parser().parse_args(["release-private-rpc", "--help"])
    assert release_exit.value.code == 0
    release_help = capsys.readouterr().out
    assert "--transaction" in release_help
    assert "--acknowledge-private-rpc-transaction-sha256" in release_help
    assert "--write-release" in release_help
    assert "--expires-in-seconds" in release_help

    with pytest.raises(SystemExit) as verify_exit:
        mother_deploy._parser().parse_args(
            ["verify-private-rpc-release", "--help"]
        )
    assert verify_exit.value.code == 0
    verify_help = capsys.readouterr().out
    assert "--release" in verify_help
    assert "--transaction-max-age-seconds" in verify_help
    assert "--soak-max-age-seconds" in verify_help
