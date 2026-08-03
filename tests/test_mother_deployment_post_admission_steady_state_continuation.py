from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import mother_deploy
from tools.mother.common import deployment_post_admission_steady_state as steady_state
from tools.mother.common.deployment_post_admission_steady_state import (
    execute_post_admission_steady_state_release,
    reconcile_post_admission_steady_state,
)
from tools.mother.common.deployment_post_admission_steady_state_continuation import (
    MotherDeploymentPostAdmissionSteadyStateContinuationError,
    build_post_admission_steady_state_continuation_release,
    build_post_admission_steady_state_continuation_transaction,
    execute_post_admission_steady_state_continuation_release,
    inspect_post_admission_steady_state_continuation_release,
    verify_post_admission_steady_state_continuation_evidence,
    verify_post_admission_steady_state_continuation_release,
    verify_post_admission_steady_state_continuation_transaction,
    write_post_admission_steady_state_continuation_release,
    write_post_admission_steady_state_continuation_transaction,
)
from tests.test_mother_deployment_executor import _operation
from tests.test_mother_deployment_post_admission_steady_state import (
    _SteadyStateOpener,
    _fixture,
    _install_fake_clock,
)


def _continuation_fixture(tmp_path: Path):
    (
        paths,
        private_state,
        _,
        _,
        _,
        _,
        source_release,
        source_release_path,
        source_release_digest,
    ) = _fixture(tmp_path)
    opener = _SteadyStateOpener(
        source_release,
        keep_obsolete_c=True,
        degraded_aggregate_with_obsolete_c=True,
    )
    failed = execute_post_admission_steady_state_release(
        paths,
        private_state,
        source_release_path,
        acknowledged_release_sha256=source_release_digest,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("continuation-failed-source"),
    )
    assert failed["status"] == "failed"
    assert [item["mutation_id"] for item in failed["mutation_receipts"]] == [
        "mainnetc-super1.install-post-admission-steady-state",
        "mainnetc-super1.deploy-post-admission-steady-state",
    ]
    reconciled = reconcile_post_admission_steady_state(
        paths,
        private_state,
        Path(failed["evidence"]["path"]),
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
        opener=opener,
        operation=_operation("continuation-source-reconciliation"),
    )
    assert reconciled["status"] == "pass"
    reconciliation_path = Path(reconciled["reconciliation_artifact"]["path"])

    transaction = build_post_admission_steady_state_continuation_transaction(
        paths,
        private_state,
        reconciliation_path,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
    )
    transaction_path, transaction_digest = (
        write_post_admission_steady_state_continuation_transaction(
            paths,
            transaction,
            operation=_operation("continuation-transaction"),
        )
    )
    release = build_post_admission_steady_state_continuation_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_transaction_sha256=transaction_digest,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
    )
    release_path, release_digest = (
        write_post_admission_steady_state_continuation_release(
            paths,
            release,
            operation=_operation("continuation-release"),
        )
    )
    return (
        paths,
        private_state,
        opener,
        reconciliation_path,
        transaction,
        transaction_path,
        transaction_digest,
        release,
        release_path,
        release_digest,
    )


def test_continuation_transaction_is_exactly_A_only(tmp_path: Path) -> None:
    (
        paths,
        private_state,
        _,
        _,
        transaction,
        transaction_path,
        _,
        release,
        release_path,
        _,
    ) = _continuation_fixture(tmp_path)

    mutations = transaction["execution_plan"]["mutations"]
    assert [item["mutation_id"] for item in mutations] == [
        "mainneta-super1.install-post-admission-steady-state-continuation",
        "mainneta-super1.deploy-post-admission-steady-state-continuation",
    ]
    assert [item["controller_id"] for item in mutations] == [
        "coolify-a",
        "coolify-a",
    ]
    assert transaction["policy"]["C_mutation_authorized"] is False
    assert transaction["summary"]["C_mutation_count"] == 0

    verified_transaction = (
        verify_post_admission_steady_state_continuation_transaction(
            paths,
            private_state,
            transaction_path,
        )
    )
    assert verified_transaction["clean"] is True
    assert verified_transaction["mutation_count"] == 2
    assert verified_transaction["C_mutation_count"] == 0

    verified_release = verify_post_admission_steady_state_continuation_release(
        paths,
        private_state,
        release_path,
    )
    assert verified_release["clean"] is True
    assert verified_release["C_mutation_count"] == 0
    assert release["authority"]["C_mutation_authorized"] is False


def test_continuation_refreshes_C_then_mutates_only_A(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        opener,
        _,
        _,
        _,
        _,
        _,
        release_path,
        release_digest,
    ) = _continuation_fixture(tmp_path)
    opener.require_guardian_refresh = True

    def _refresh(seconds: float) -> None:
        if seconds >= 50:
            opener.guardian_refreshed = True

    clock = _install_fake_clock(monkeypatch, on_sleep=_refresh)
    result = execute_post_admission_steady_state_continuation_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1", "mainneta-super1"),
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("continuation-execute"),
    )

    assert result["status"] == "pass"
    assert result["summary"]["complete"] is True
    assert result["summary"]["C_mutation_count"] == 0
    assert result["summary"]["A_mutation_count"] == 2
    assert (
        result["summary"]["C_guardian_refresh_verified_before_A_restart"]
        is True
    )
    assert result["summary"]["component_scoped_steady_state_verified"] is True
    assert result["summary"]["strict_aggregate_cleanup_complete"] is False
    assert result["summary"]["platform_stale_component_records_present"] is True
    assert clock.sleeps == [50]
    assert opener.a_patch_after_guardian_refresh is True

    writes = [
        (host, method, path)
        for host, method, path in opener.requests
        if method == "PATCH" or path == "/api/v1/deploy"
    ]
    continuation_writes = writes[-2:]
    assert continuation_writes == [
        ("coolify-a.invalid", "PATCH", f"/api/v1/services/{result['precondition_receipts'][1]['service_uuid']}"),
        ("coolify-a.invalid", "GET", "/api/v1/deploy"),
    ]
    assert all(host != "coolify-c.invalid" for host, _, _ in continuation_writes)

    verified = verify_post_admission_steady_state_continuation_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
    )
    assert verified["clean"] is True
    assert verified["A_steady_state_installed"] is True
    assert verified["C_steady_state_preserved"] is True
    assert verified["C_mutation_count"] == 0
    assert verified["strict_aggregate_cleanup_complete"] is False


def test_continuation_release_is_one_use(tmp_path: Path) -> None:
    (
        paths,
        private_state,
        _,
        _,
        _,
        _,
        _,
        _,
        release_path,
        release_digest,
    ) = _continuation_fixture(tmp_path)
    inspected = inspect_post_admission_steady_state_continuation_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
    )
    assert inspected["release_already_claimed"] is False

    claim_root = (
        paths.root
        / "actions"
        / "deployment-post-admission-steady-state-continuation-execution-claims"
    )
    claim_root.mkdir(parents=True, exist_ok=True)
    (claim_root / f"{release_digest}.json").write_text("{}", encoding="utf-8")
    inspected_again = inspect_post_admission_steady_state_continuation_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
    )
    assert inspected_again["release_already_claimed"] is True


def test_continuation_evidence_rejects_C_mutation_tampering(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        paths,
        private_state,
        opener,
        _,
        _,
        _,
        _,
        _,
        release_path,
        release_digest,
    ) = _continuation_fixture(tmp_path)
    opener.require_guardian_refresh = True
    _install_fake_clock(
        monkeypatch,
        on_sleep=lambda seconds: setattr(
            opener,
            "guardian_refreshed",
            seconds >= 50,
        ),
    )
    result = execute_post_admission_steady_state_continuation_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=opener,
        poll_interval_seconds=0,
        max_wait_seconds=0,
        operation=_operation("continuation-tamper-source"),
    )
    evidence_path = Path(result["evidence"]["path"])
    document = json.loads(evidence_path.read_text(encoding="utf-8"))
    document["mutation_receipts"][0]["controller_id"] = "coolify-c"
    from tools.mother.common.canonical import canonical_json

    evidence_path.write_bytes(canonical_json(document))
    with pytest.raises(
        MotherDeploymentPostAdmissionSteadyStateContinuationError
    ) as caught:
        verify_post_admission_steady_state_continuation_evidence(
            paths,
            private_state,
            evidence_path,
        )
    assert (
        caught.value.code
        == "MOTHER_DEPLOY_POST_ADMISSION_STEADY_STATE_CONTINUATION_EVIDENCE_INVALID"
    )


def test_cli_exposes_continuation_commands() -> None:
    parser = mother_deploy._parser()
    commands = parser._subparsers._group_actions[0].choices
    assert {
        "stage-post-admission-steady-state-continuation",
        "verify-post-admission-steady-state-continuation-transaction",
        "release-post-admission-steady-state-continuation",
        "verify-post-admission-steady-state-continuation-release",
        "apply-post-admission-steady-state-continuation",
        "verify-post-admission-steady-state-continuation-evidence",
    }.issubset(commands)
