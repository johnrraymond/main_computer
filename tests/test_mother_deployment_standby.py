from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from tools import mother_deploy
from tools.mother.common.deployment_executor import execute_released_mutation
from tools.mother.common.deployment_standby import (
    MotherDeploymentStandbyError,
    run_deployment_standby_verification,
    verify_deployment_standby_evidence,
    write_deployment_standby_verification,
)
from tests.test_mother_deployment_executor import (
    TOKEN_A,
    TOKEN_C,
    _CoolifyOpener,
    _install,
    _operation,
    _release_artifact,
)


def _successful_execution(tmp_path: Path):
    _, paths, private_state = _install(tmp_path)
    release_path, release_digest = _release_artifact(
        paths,
        private_state,
        _CoolifyOpener(),
    )
    live = _CoolifyOpener()
    result = execute_released_mutation(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        opener=live,
        operation=_operation("standby-execution"),
    )
    return paths, private_state, live, Path(result["result_artifact"]["path"])


def test_live_standby_verification_binds_exact_created_uuids_and_uses_get_only(tmp_path: Path) -> None:
    paths, private_state, live, execution_path = _successful_execution(tmp_path)
    before = len(live.requests)

    verification = run_deployment_standby_verification(
        paths,
        private_state,
        execution_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        opener=live,
        observed_at="2026-07-31T19:00:00Z",
    )

    new_requests = live.requests[before:]
    assert new_requests
    assert {item["method"] for item in new_requests} == {"GET"}
    assert verification["summary"] == {
        "clean": True,
        "target_count": 2,
        "blocker_count": 0,
        "blocker_codes": [],
        "verified_environment_count": 2,
        "verified_service_count": 2,
        "next_phase": "install-reserved-identity",
    }
    assert verification["results"][0]["environment"]["uuid"] == "env-a"
    assert verification["results"][0]["service"]["uuid"] == "svc-mainneta-super1"
    assert verification["results"][1]["environment"]["uuid"] == "env-c"
    assert verification["results"][1]["service"]["uuid"] == "svc-mainnetc-super1"
    rendered = json.dumps(verification)
    assert TOKEN_A not in rendered
    assert TOKEN_C not in rendered
    assert "private_key" not in rendered


def test_missing_created_service_is_reported_without_mutation(tmp_path: Path) -> None:
    paths, private_state, live, execution_path = _successful_execution(tmp_path)
    live.services["coolify-c.invalid"] = []
    before = len(live.requests)

    verification = run_deployment_standby_verification(
        paths,
        private_state,
        execution_path,
        opener=live,
    )

    assert {item["method"] for item in live.requests[before:]} == {"GET"}
    assert verification["summary"]["clean"] is False
    assert verification["summary"]["blocker_codes"] == [
        "MOTHER_DEPLOY_STANDBY_SERVICE_MISMATCH"
    ]


def test_tampered_execution_is_rejected_before_network_access(tmp_path: Path) -> None:
    paths, private_state, live, execution_path = _successful_execution(tmp_path)
    document = json.loads(execution_path.read_text(encoding="utf-8"))
    document["mutation_receipts"][0]["response"]["bound_uuid"] = "forged-environment"
    execution_path.write_text(json.dumps(document), encoding="utf-8")
    before = len(live.requests)

    with pytest.raises(MotherDeploymentStandbyError) as caught:
        run_deployment_standby_verification(
            paths,
            private_state,
            execution_path,
            opener=live,
        )

    assert caught.value.code == "MOTHER_DEPLOY_STANDBY_INVALID"
    assert len(live.requests) == before


def test_persisted_standby_evidence_is_canonical_bound_and_fresh(tmp_path: Path) -> None:
    paths, private_state, live, execution_path = _successful_execution(tmp_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = now.isoformat().replace("+00:00", "Z")
    verification = run_deployment_standby_verification(
        paths,
        private_state,
        execution_path,
        opener=live,
        observed_at=stamp,
    )
    evidence_path, digest = write_deployment_standby_verification(
        paths,
        verification,
        operation=_operation("standby-evidence"),
    )

    assert evidence_path.read_bytes() == evidence_path.read_bytes()
    verified = verify_deployment_standby_evidence(
        paths,
        private_state,
        evidence_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        max_age_seconds=300,
        now=now + timedelta(seconds=10),
    )

    assert verified["clean"] is True
    assert verified["age_seconds"] == 10
    assert verified["evidence_sha256"] == digest
    assert verified["verified_environment_count"] == 2
    assert verified["verified_service_count"] == 2


def test_standby_evidence_expires(tmp_path: Path) -> None:
    paths, private_state, live, execution_path = _successful_execution(tmp_path)
    observed = datetime(2026, 7, 31, 19, 0, tzinfo=timezone.utc)
    verification = run_deployment_standby_verification(
        paths,
        private_state,
        execution_path,
        opener=live,
        observed_at="2026-07-31T19:00:00Z",
    )
    evidence_path, _ = write_deployment_standby_verification(
        paths,
        verification,
        operation=_operation("standby-stale"),
    )

    with pytest.raises(MotherDeploymentStandbyError) as caught:
        verify_deployment_standby_evidence(
            paths,
            private_state,
            evidence_path,
            max_age_seconds=300,
            now=observed + timedelta(seconds=301),
        )
    assert caught.value.code == "MOTHER_DEPLOY_STANDBY_EVIDENCE_STALE_TIME"


def test_cli_verify_standby_writes_and_verifies_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime, paths, private_state = _install(tmp_path)
    release_path, release_digest = _release_artifact(paths, private_state, _CoolifyOpener())
    live = _CoolifyOpener()
    execution = execute_released_mutation(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=live,
        operation=_operation("standby-cli-execution"),
    )
    monkeypatch.setattr(
        "tools.mother.common.deployment_standby._DEFAULT_OPENER",
        live,
    )
    monkeypatch.setattr(mother_deploy, "run_deployment_standby_verification", lambda *args, **kwargs: run_deployment_standby_verification(*args, **kwargs, opener=live))

    code = mother_deploy.main(
        [
            "verify-standby",
            "--runtime-state-root",
            str(runtime),
            "--execution",
            execution["result_artifact"]["path"],
            "--node",
            "mainneta-super1",
            "--node",
            "mainnetc-super1",
            "--write-evidence",
            "--require-clean",
        ]
    )
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    evidence_path = output["evidence"]["path"]

    code = mother_deploy.main(
        [
            "verify-standby-evidence",
            "--runtime-state-root",
            str(runtime),
            "--evidence",
            evidence_path,
            "--node",
            "mainneta-super1",
            "--node",
            "mainnetc-super1",
        ]
    )
    assert code == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["clean"] is True
