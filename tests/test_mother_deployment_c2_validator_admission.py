from __future__ import annotations

import json
from pathlib import Path

from tools import mother_deploy
from tools.mother.common.deployment_c2_replica_sync import (
    build_c2_replica_sync_release,
    execute_c2_replica_sync_release,
    write_c2_replica_sync_release,
)
from tools.mother.common.deployment_c2_validator_admission import (
    build_c2_validator_admission_release,
    build_c2_validator_admission_transaction,
    inspect_c2_validator_admission_release,
    verify_c2_validator_admission_release,
    verify_c2_validator_admission_transaction,
    write_c2_validator_admission_release,
    write_c2_validator_admission_transaction,
)
from tests.test_mother_deployment_c2_replica_sync import (
    _C2ReplicaSyncOpener,
    _operation,
    _standby_gate,
    _stamp,
)


def _sync_evidence(tmp_path, monkeypatch):
    paths, private_state, standby_evidence, standby_compose, a_service_uuid, now = _standby_gate(tmp_path, monkeypatch)
    release = build_c2_replica_sync_release(
        paths,
        private_state,
        Path(standby_evidence["evidence"]["path"]),
        acknowledged_c2_replica_standby_evidence_sha256=standby_evidence["evidence"]["sha256"],
        created_at=_stamp(now),
        now=now,
    )
    release_path, release_sha = write_c2_replica_sync_release(
        paths,
        release,
        operation=_operation("write-c2-sync-for-admission"),
    )
    opener = _C2ReplicaSyncOpener(
        service_uuid=standby_evidence["service_uuid"],
        standby_compose=standby_compose,
        a_service_uuid=a_service_uuid,
    )
    result = execute_c2_replica_sync_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=999999999,
        now=now,
        opener=opener,
        max_wait_seconds=1,
        poll_interval_seconds=0,
        operation=_operation("execute-c2-sync-for-admission"),
    )
    assert result["status"] == "pass"
    return paths, private_state, Path(result["evidence"]["path"]), now


def test_c2_validator_admission_transaction_requires_two_existing_votes(tmp_path, monkeypatch) -> None:
    paths, private_state, sync_evidence, now = _sync_evidence(tmp_path, monkeypatch)

    tx = build_c2_validator_admission_transaction(
        paths,
        private_state,
        sync_evidence,
        max_age_seconds=999999999,
    )

    assert tx["candidate"]["node"] == "mainnetc-super2"
    assert tx["summary"]["current_validator_count"] == 2
    assert tx["summary"]["desired_validator_count"] == 3
    assert tx["summary"]["logical_vote_count"] == 2
    assert tx["summary"]["all_existing_validators_must_vote"] is True
    assert tx["authority"]["validator_vote_authorized"] is False
    assert tx["authority"]["routing_or_topology_publication_authorized"] is False
    assert tx["admission"]["voter_nodes"] == ["mainneta-super1", "mainnetc-super1"]
    assert [item["rpc_request"]["method"] for item in tx["admission"]["rpc_requests"]] == [
        "qbft_proposeValidatorVote",
        "qbft_proposeValidatorVote",
    ]
    rendered = json.dumps(tx, sort_keys=True)
    assert "0x0000000000000000000000000000000000000000000000000000000000000006" not in rendered
    assert "0xa1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1" not in rendered
    assert "THISISASECRETTOKENVALUE" not in rendered


def test_c2_validator_admission_release_inspects_without_live_mutation(tmp_path, monkeypatch) -> None:
    paths, private_state, sync_evidence, now = _sync_evidence(tmp_path, monkeypatch)

    tx = build_c2_validator_admission_transaction(
        paths,
        private_state,
        sync_evidence,
        max_age_seconds=999999999,
    )
    tx_path, tx_sha = write_c2_validator_admission_transaction(
        paths,
        tx,
        operation=_operation("write-c2-validator-admission-tx"),
    )
    verified_tx = verify_c2_validator_admission_transaction(
        paths,
        private_state,
        tx_path,
        max_age_seconds=999999999,
    )
    assert verified_tx["clean"] is True
    assert verified_tx["validator_vote_authorized"] is False

    release = build_c2_validator_admission_release(
        paths,
        private_state,
        tx_path,
        acknowledged_transaction_sha256=tx_sha,
        transaction_max_age_seconds=999999999,
    )
    assert release["summary"]["validator_vote_authorized"] is True
    assert release["summary"]["executor_implemented"] is False
    assert release["summary"]["routing_or_topology_publication_authorized"] is False

    release_path, release_sha = write_c2_validator_admission_release(
        paths,
        release,
        operation=_operation("write-c2-validator-admission-release"),
    )
    verified_release = verify_c2_validator_admission_release(
        paths,
        private_state,
        release_path,
        max_age_seconds=999999999,
        transaction_max_age_seconds=999999999,
    )
    assert verified_release["clean"] is True
    assert verified_release["validator_vote_authorized"] is True
    assert verified_release["executor_implemented"] is False
    assert verified_release["next_phase"] == "implement-c2-validator-admission-executor"

    inspection = inspect_c2_validator_admission_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=999999999,
        transaction_max_age_seconds=999999999,
    )
    assert inspection["summary"]["clean"] is True
    assert inspection["summary"]["executor_implemented"] is False
    assert inspection["live_mutation_performed"] is False
    assert inspection["validator_vote_performed"] is False
    assert inspection["routing_or_topology_published"] is False


def test_cli_stages_releases_and_inspects_c2_validator_admission(tmp_path, monkeypatch, capsys) -> None:
    paths, _, sync_evidence, _ = _sync_evidence(tmp_path, monkeypatch)
    runtime_root = paths.root.parent

    code = mother_deploy.main([
        "stage-c2-validator-admission",
        "--runtime-state-root", str(runtime_root),
        "--node", "mainnetc-super2",
        "--sync-evidence", str(sync_evidence),
        "--max-age-seconds", "999999999",
        "--write-transaction",
    ])
    assert code == 0
    staged = json.loads(capsys.readouterr().out)
    assert staged["summary"]["logical_vote_count"] == 2
    tx_path = staged["transaction_artifact"]["path"]
    tx_sha = staged["transaction_artifact"]["sha256"]

    code = mother_deploy.main([
        "verify-c2-validator-admission-transaction",
        "--runtime-state-root", str(runtime_root),
        "--node", "mainnetc-super2",
        "--transaction", tx_path,
        "--max-age-seconds", "999999999",
    ])
    assert code == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["clean"] is True

    code = mother_deploy.main([
        "release-c2-validator-admission",
        "--runtime-state-root", str(runtime_root),
        "--node", "mainnetc-super2",
        "--transaction", tx_path,
        "--acknowledge-c2-validator-admission-transaction-sha256", tx_sha,
        "--transaction-max-age-seconds", "999999999",
        "--expires-in-seconds", "900",
        "--write-release",
    ])
    assert code == 0
    released = json.loads(capsys.readouterr().out)
    assert released["summary"]["executor_implemented"] is False
    release_path = released["release_artifact"]["path"]
    release_sha = released["release_artifact"]["sha256"]

    code = mother_deploy.main([
        "apply-c2-validator-admission",
        "--runtime-state-root", str(runtime_root),
        "--node", "mainnetc-super2",
        "--release", release_path,
        "--acknowledge-release-sha256", release_sha,
        "--max-age-seconds", "999999999",
        "--transaction-max-age-seconds", "999999999",
    ])
    assert code == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["summary"]["executor_implemented"] is False
    assert inspected["summary"]["next_phase"] == "implement-c2-validator-admission-executor"
