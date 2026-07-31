from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket

import pytest

from tools import mother_deploy
from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_genesis_birth import execute_genesis_birth_release
from tools.mother.common.deployment_soft_replica import (
    MotherDeploymentSoftReplicaError,
    build_soft_replica_transaction,
    verify_soft_replica_transaction,
    write_soft_replica_transaction,
)
from tests.test_mother_deployment_executor import _operation
from tests.test_mother_deployment_genesis_birth import _BirthOpener, _birth_release


def _birth_evidence(tmp_path: Path):
    paths, private_state, _, _, genesis_release, release_path, digest, release = _birth_release(tmp_path)
    opener = _BirthOpener(
        genesis_release["execution_plan"]["compose"]["canonical_text"],
        release["proof_plan"]["proof_compose"]["canonical_text"],
        normalized_readback=True,
        wrapped_readback=True,
    )
    result = execute_genesis_birth_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=digest,
        selected_nodes=("mainneta-super1",),
        opener=opener,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        operation=_operation("soft-replica-birth-proof"),
    )
    assert result["status"] == "pass"
    return paths, private_state, Path(result["evidence"]["path"])


def test_soft_replica_staging_is_offline_secret_free_and_vote_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, private_state, evidence_path = _birth_evidence(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("soft replica staging must not perform network access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    transaction = build_soft_replica_transaction(
        paths,
        private_state,
        evidence_path,
        selected_nodes=("mainnetc-super1",),
    )

    assert transaction["staged_scope"] == "configure-soft-replica-without-validator-admission"
    assert transaction["initial_chain"]["node"] == "mainneta-super1"
    assert transaction["replica"]["node"] == "mainnetc-super1"
    assert transaction["replica"]["role_before_admission"] == "non-validator-replica"
    assert transaction["authority"] == {
        "configuration_apply_authorized": False,
        "replica_start_authorized": False,
        "validator_vote_authorized": False,
        "validator_activation_authorized": False,
    }
    assert transaction["policy"]["network_access_performed"] is False
    assert transaction["policy"]["manual_ssh_required"] is False
    assert transaction["policy"]["public_http_endpoint_created"] is False
    assert transaction["summary"]["persisted_secret_value_count"] == 0
    assert [item["controller_id"] for item in transaction["future_write_set"]] == ["coolify-c", "coolify-c"]
    assert [item["method"] for item in transaction["future_write_set"]] == ["PATCH", "GET"]

    compose = transaction["replica"]["compose"]["canonical_text"]
    assert "--bootnodes=enode://" in compose
    assert "@coolify-a.invalid:30303" in compose
    assert '"30303:30303/tcp"' in compose
    assert '"30303:30303/udp"' in compose
    assert "8545:8545" not in compose
    assert "traefik." not in compose
    assert "main_computer.mother.validator-activation: blocked" in compose

    state = json.loads(private_state.canonical_object_bytes)
    rendered = json.dumps(transaction, sort_keys=True)
    for node in ("mainneta-super1", "mainnetc-super1"):
        assert state["networks"]["mainnet"]["validators"][node]["private_key"] not in rendered
        assert state["networks"]["mainnet"]["node_seed_material"][node]["wallets"]["hub_admin"]["private_key"] not in rendered


def test_soft_replica_transaction_persists_and_verifies(tmp_path: Path) -> None:
    paths, private_state, evidence_path = _birth_evidence(tmp_path)
    transaction = build_soft_replica_transaction(
        paths,
        private_state,
        evidence_path,
        selected_nodes=("mainnetc-super1",),
    )
    path, digest = write_soft_replica_transaction(
        paths,
        transaction,
        operation=_operation("soft-replica-write"),
    )
    verified = verify_soft_replica_transaction(
        paths,
        private_state,
        path,
        selected_nodes=("mainnetc-super1",),
    )
    assert verified["clean"] is True
    assert verified["soft_replica_transaction_sha256"] == digest
    assert verified["replica_node"] == "mainnetc-super1"
    assert verified["initial_node"] == "mainneta-super1"
    assert verified["future_mutation_count"] == 2
    assert verified["validator_vote_authorized"] is False
    assert verified["persisted_secret_value_count"] == 0


def test_soft_replica_transaction_tamper_is_rejected(tmp_path: Path) -> None:
    paths, private_state, evidence_path = _birth_evidence(tmp_path)
    transaction = build_soft_replica_transaction(paths, private_state, evidence_path)
    path, _ = write_soft_replica_transaction(
        paths,
        transaction,
        operation=_operation("soft-replica-tamper"),
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    document["replica"]["compose"]["canonical_text"] += "# tampered\n"
    path.write_bytes(canonical_json(document))

    with pytest.raises(MotherDeploymentSoftReplicaError) as caught:
        verify_soft_replica_transaction(paths, private_state, path)
    assert caught.value.code == "MOTHER_DEPLOY_SOFT_REPLICA_TRANSACTION_INVALID"


def test_soft_replica_staging_rejects_stale_birth_evidence(tmp_path: Path) -> None:
    paths, private_state, evidence_path = _birth_evidence(tmp_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["completed_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    evidence_path.write_bytes(canonical_json(evidence))

    with pytest.raises(Exception) as caught:
        build_soft_replica_transaction(
            paths,
            private_state,
            evidence_path,
            max_age_seconds=300,
        )
    assert getattr(caught.value, "code", None) == "MOTHER_DEPLOY_GENESIS_BIRTH_EVIDENCE_STALE"


def test_soft_replica_staging_rejects_a_or_multi_node_selection(tmp_path: Path) -> None:
    paths, private_state, evidence_path = _birth_evidence(tmp_path)
    for selected in (("mainneta-super1",), ("mainneta-super1", "mainnetc-super1")):
        with pytest.raises(MotherDeploymentSoftReplicaError) as caught:
            build_soft_replica_transaction(
                paths,
                private_state,
                evidence_path,
                selected_nodes=selected,
            )
        assert caught.value.code == "MOTHER_DEPLOY_SOFT_REPLICA_SELECTION_MISMATCH"


def test_cli_stages_and_verifies_soft_replica_transaction(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths, _, evidence_path = _birth_evidence(tmp_path)
    runtime_root = paths.root.parent
    code = mother_deploy.main([
        "stage-soft-replica",
        "--runtime-state-root", str(runtime_root),
        "--birth-evidence", str(evidence_path),
        "--node", "mainnetc-super1",
        "--write-transaction",
    ])
    assert code == 0
    staged = json.loads(capsys.readouterr().out)
    assert staged["summary"]["future_mutation_count"] == 2
    assert staged["summary"]["validator_vote_authorized"] is False
    transaction_path = staged["transaction_artifact"]["path"]

    code = mother_deploy.main([
        "verify-soft-replica-transaction",
        "--runtime-state-root", str(runtime_root),
        "--transaction", transaction_path,
        "--node", "mainnetc-super1",
    ])
    assert code == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["clean"] is True
    assert verified["next_phase"] == "release-and-apply-soft-replica-configuration"
    assert verified["live_mutation_performed"] is False
