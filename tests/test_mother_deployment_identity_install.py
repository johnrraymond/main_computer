from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket

import pytest
import yaml

from tools import mother_deploy
from tools.mother.common.deployment_identity_install import (
    MotherDeploymentIdentityInstallError,
    build_deployment_identity_install_transaction,
    verify_deployment_identity_install_transaction,
    write_deployment_identity_install_transaction,
)
from tools.mother.common.deployment_standby import (
    run_deployment_standby_verification,
    write_deployment_standby_verification,
)
from tests.test_mother_deployment_standby import _successful_execution
from tests.test_mother_deployment_executor import _operation


def _evidence(tmp_path: Path, *, observed_at: str = "2026-07-31T19:11:00Z"):
    paths, private_state, live, execution_path = _successful_execution(tmp_path)
    verification = run_deployment_standby_verification(
        paths,
        private_state,
        execution_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        opener=live,
        observed_at=observed_at,
    )
    evidence_path, digest = write_deployment_standby_verification(
        paths,
        verification,
        operation=_operation("identity-install-evidence"),
    )
    return paths, private_state, evidence_path, digest


def test_stage_identity_commits_exact_secret_hashes_without_persisting_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, private_state, evidence_path, digest = _evidence(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("identity staging must not perform network access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    transaction = build_deployment_identity_install_transaction(
        paths,
        private_state,
        evidence_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        created_at="2026-07-31T19:11:05Z",
        now=datetime(2026, 7, 31, 19, 11, 5, tzinfo=timezone.utc),
    )

    assert transaction["staged_scope"] == "install-reserved-identity"
    assert transaction["standby_evidence"]["sha256"] == digest
    assert transaction["summary"]["mutation_count"] == 4
    assert transaction["summary"]["persisted_secret_value_count"] == 0
    assert transaction["policy"]["secret_values_persisted"] is False
    assert [item["method"] for item in transaction["mutations"]] == ["POST"] * 4
    assert [item["canonical_request_body_template"]["key"] for item in transaction["mutations"]] == [
        "MC_MOTHER_VALIDATOR_PRIVATE_KEY",
        "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY",
        "MC_MOTHER_VALIDATOR_PRIVATE_KEY",
        "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY",
    ]

    state = yaml.safe_load(private_state.document_bytes)
    secret_values = [
        state["networks"]["mainnet"]["validators"]["mainneta-super1"]["private_key"],
        state["networks"]["mainnet"]["node_seed_material"]["mainneta-super1"]["wallets"]["hub_admin"]["private_key"],
        state["networks"]["mainnet"]["validators"]["mainnetc-super1"]["private_key"],
        state["networks"]["mainnet"]["node_seed_material"]["mainnetc-super1"]["wallets"]["hub_admin"]["private_key"],
    ]
    rendered = json.dumps(transaction, sort_keys=True)
    for value in secret_values:
        assert value not in rendered
    assert all(item["value_bytes"] == 66 for item in transaction["mutations"])
    assert all(len(item["value_sha256"]) == 64 for item in transaction["mutations"])
    assert all(len(item["materialized_body_sha256"]) == 64 for item in transaction["mutations"])


def test_identity_transaction_persists_canonically_and_verifies(tmp_path: Path) -> None:
    paths, private_state, evidence_path, _ = _evidence(tmp_path)
    now = datetime(2026, 7, 31, 19, 11, 10, tzinfo=timezone.utc)
    transaction = build_deployment_identity_install_transaction(
        paths,
        private_state,
        evidence_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        created_at="2026-07-31T19:11:10Z",
        now=now,
    )
    transaction_path, digest = write_deployment_identity_install_transaction(
        paths,
        transaction,
        operation=_operation("identity-install-transaction"),
    )

    verified = verify_deployment_identity_install_transaction(
        paths,
        private_state,
        transaction_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        max_age_seconds=300,
        now=now + timedelta(seconds=5),
    )
    assert verified["clean"] is True
    assert verified["identity_transaction_sha256"] == digest
    assert verified["mutation_count"] == 4
    assert verified["secret_reference_count"] == 4
    assert verified["persisted_secret_value_count"] == 0
    assert verified["transaction_apply_authorized"] is False


def test_tampered_identity_transaction_is_rejected(tmp_path: Path) -> None:
    paths, private_state, evidence_path, _ = _evidence(tmp_path)
    now = datetime(2026, 7, 31, 19, 11, 10, tzinfo=timezone.utc)
    transaction = build_deployment_identity_install_transaction(
        paths,
        private_state,
        evidence_path,
        created_at="2026-07-31T19:11:10Z",
        now=now,
    )
    transaction_path, _ = write_deployment_identity_install_transaction(
        paths,
        transaction,
        operation=_operation("identity-install-tamper"),
    )
    document = json.loads(transaction_path.read_text(encoding="utf-8"))
    document["mutations"][0]["value_sha256"] = "0" * 64
    transaction_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(MotherDeploymentIdentityInstallError) as caught:
        verify_deployment_identity_install_transaction(
            paths,
            private_state,
            transaction_path,
            now=now + timedelta(seconds=1),
        )
    assert caught.value.code == "MOTHER_DEPLOY_IDENTITY_TRANSACTION_INVALID"


def test_stale_standby_evidence_is_rejected(tmp_path: Path) -> None:
    paths, private_state, evidence_path, _ = _evidence(tmp_path)
    with pytest.raises(Exception) as caught:
        build_deployment_identity_install_transaction(
            paths,
            private_state,
            evidence_path,
            now=datetime(2026, 7, 31, 19, 16, 1, tzinfo=timezone.utc),
        )
    assert getattr(caught.value, "code", "") == "MOTHER_DEPLOY_STANDBY_EVIDENCE_STALE_TIME"


def test_cli_stages_and_verifies_identity_transaction(tmp_path: Path, capsys) -> None:
    paths, _, evidence_path, _ = _evidence(
        tmp_path,
        observed_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    runtime_root = paths.root.parent
    code = mother_deploy.main(
        [
            "stage-identity",
            "--runtime-state-root",
            str(runtime_root),
            "--standby-evidence",
            str(evidence_path),
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
    assert staged["summary"]["mutation_count"] == 4

    code = mother_deploy.main(
        [
            "verify-identity-transaction",
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
    assert verified["staged_scope"] == "install-reserved-identity"
