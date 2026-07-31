from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import socket

import pytest
import yaml

from tools import mother_deploy
from tools.mother.common.deployment_genesis import (
    MotherDeploymentGenesisError,
    build_deployment_genesis_transaction,
    verify_deployment_genesis_transaction,
    write_deployment_genesis_transaction,
)
from tools.mother.common.deployment_identity_executor import execute_released_identity
from tests.test_mother_deployment_executor import _operation
from tests.test_mother_deployment_identity_executor import _IdentityOpener, _identity_release


def _identity_execution(tmp_path: Path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, private_state, _, _, release_path, release_digest = _identity_release(tmp_path, now=now)
    result = execute_released_identity(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        opener=_IdentityOpener(),
        operation=_operation("genesis-identity-execution"),
    )
    assert result["status"] == "pass"
    return paths, private_state, Path(result["result_artifact"]["path"])


def test_genesis_compiler_builds_one_initial_validator_and_one_soft_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, private_state, execution_path = _identity_execution(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("genesis staging must not perform network access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    transaction = build_deployment_genesis_transaction(
        paths,
        private_state,
        execution_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        created_at="2026-07-31T20:34:00Z",
    )

    state = yaml.safe_load(private_state.document_bytes)
    network = state["networks"]["mainnet"]
    validator_a = network["validators"]["mainneta-super1"]["address"].lower()
    validator_c = network["validators"]["mainnetc-super1"]["address"].lower()
    captain = network["wallets"]["captain"]["address"].lower()
    genesis = transaction["genesis"]["canonical_json"]

    assert transaction["staged_scope"] == "compile-first-genesis-and-replica-admission"
    assert transaction["genesis"]["chain_id"] == 42424240
    assert transaction["genesis"]["initial_node"] == "mainneta-super1"
    assert transaction["genesis"]["validator_set"] == [validator_a]
    assert validator_a[2:] in genesis["extraData"]
    assert validator_c[2:] not in genesis["extraData"]
    assert set(genesis["alloc"]) == {captain[2:]}
    assert genesis["config"]["berlinBlock"] == 0
    assert genesis["config"]["londonBlock"] == 0
    assert genesis["config"]["shanghaiTime"] == 0
    assert genesis["config"]["qbft"] == {
        "blockperiodseconds": 2,
        "epochlength": 30000,
        "requesttimeoutseconds": 4,
    }
    assert transaction["replica_admissions"] == [
        {
            "node": "mainnetc-super1",
            "mode": "soft",
            "validator_address": validator_c,
            "current_validator_set": [validator_a],
            "desired_validator_set": [validator_a, validator_c],
            "requires_initial_chain_proof": True,
            "live_vote_authorized": False,
        }
    ]
    assert transaction["summary"]["identity_commitment_count"] == 4
    assert transaction["summary"]["persisted_secret_value_count"] == 0
    assert transaction["policy"]["network_access_performed"] is False
    assert transaction["policy"]["service_deploy_or_start_performed"] is False

    rendered = json.dumps(transaction, sort_keys=True)
    for node in ("mainneta-super1", "mainnetc-super1"):
        assert network["validators"][node]["private_key"] not in rendered
        assert network["node_seed_material"][node]["wallets"]["hub_admin"]["private_key"] not in rendered


def test_genesis_transaction_persists_canonically_and_verifies(tmp_path: Path) -> None:
    paths, private_state, execution_path = _identity_execution(tmp_path)
    transaction = build_deployment_genesis_transaction(
        paths,
        private_state,
        execution_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        created_at="2026-07-31T20:34:00Z",
    )
    transaction_path, digest = write_deployment_genesis_transaction(
        paths,
        transaction,
        operation=_operation("genesis-transaction-write"),
    )

    verified = verify_deployment_genesis_transaction(
        paths,
        private_state,
        transaction_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
    )
    assert verified["clean"] is True
    assert verified["genesis_transaction_sha256"] == digest
    assert verified["initial_node"] == "mainneta-super1"
    assert verified["initial_validator_count"] == 1
    assert verified["replica_admission_count"] == 1
    assert verified["identity_commitment_count"] == 4
    assert verified["persisted_secret_value_count"] == 0


def test_genesis_transaction_tamper_is_rejected(tmp_path: Path) -> None:
    paths, private_state, execution_path = _identity_execution(tmp_path)
    transaction = build_deployment_genesis_transaction(
        paths,
        private_state,
        execution_path,
        created_at="2026-07-31T20:34:00Z",
    )
    transaction_path, _ = write_deployment_genesis_transaction(
        paths,
        transaction,
        operation=_operation("genesis-transaction-tamper"),
    )
    document = json.loads(transaction_path.read_text(encoding="utf-8"))
    document["genesis"]["canonical_json"]["config"]["chainId"] += 1
    transaction_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(MotherDeploymentGenesisError) as caught:
        verify_deployment_genesis_transaction(paths, private_state, transaction_path)
    assert caught.value.code == "MOTHER_DEPLOY_GENESIS_TRANSACTION_INVALID"


def test_genesis_compiler_rejects_incomplete_identity_execution(tmp_path: Path) -> None:
    paths, private_state, execution_path = _identity_execution(tmp_path)
    execution = json.loads(execution_path.read_text(encoding="utf-8"))
    execution["summary"]["commitment_verified_count"] = 3
    from tools.mother.common.canonical import canonical_json

    execution_path.write_bytes(canonical_json(execution))
    with pytest.raises(MotherDeploymentGenesisError) as caught:
        build_deployment_genesis_transaction(paths, private_state, execution_path)
    assert caught.value.code == "MOTHER_DEPLOY_GENESIS_IDENTITY_EXECUTION_INVALID"


def test_cli_stages_and_verifies_genesis_transaction(tmp_path: Path, capsys) -> None:
    paths, _, execution_path = _identity_execution(tmp_path)
    runtime_root = paths.root.parent
    code = mother_deploy.main(
        [
            "stage-genesis",
            "--runtime-state-root",
            str(runtime_root),
            "--identity-execution",
            str(execution_path),
            "--node",
            "mainneta-super1",
            "--node",
            "mainnetc-super1",
            "--write-transaction",
        ]
    )
    assert code == 0
    staged = json.loads(capsys.readouterr().out)
    transaction_path = staged["transaction_artifact"]["path"]
    assert staged["summary"]["genesis_count"] == 1
    assert staged["summary"]["replica_admission_count"] == 1

    code = mother_deploy.main(
        [
            "verify-genesis-transaction",
            "--runtime-state-root",
            str(runtime_root),
            "--transaction",
            transaction_path,
            "--node",
            "mainneta-super1",
            "--node",
            "mainnetc-super1",
        ]
    )
    assert code == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["clean"] is True
    assert verified["staged_scope"] == "compile-first-genesis-and-replica-admission"
