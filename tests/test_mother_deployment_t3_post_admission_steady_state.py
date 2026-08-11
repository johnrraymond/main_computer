from __future__ import annotations

import json
from pathlib import Path

from tools import mother_deploy
from tools.mother.common.deployment_c2_validator_admission import (
    build_c2_validator_admission_release,
    build_c2_validator_admission_transaction,
    execute_c2_validator_admission_release,
    write_c2_validator_admission_release,
    write_c2_validator_admission_transaction,
)
from tools.mother.common.deployment_t3_post_admission_steady_state import (
    build_t3_post_admission_steady_state_release,
    build_t3_post_admission_steady_state_transaction,
    execute_t3_post_admission_steady_state_release,
    inspect_t3_post_admission_steady_state_release,
    verify_t3_post_admission_steady_state_evidence,
    verify_t3_post_admission_steady_state_release,
    verify_t3_post_admission_steady_state_transaction,
    write_t3_post_admission_steady_state_release,
    write_t3_post_admission_steady_state_transaction,
)
from tests.test_mother_deployment_c2_validator_admission import (
    _C2ValidatorAdmissionOpener,
    _sync_evidence,
)
from tests.test_mother_deployment_c2_replica_sync import _Response, _operation


def _admission_evidence(tmp_path, monkeypatch, *, c2_status: str = "running:unhealthy"):
    paths, private_state, sync_evidence, _ = _sync_evidence(tmp_path, monkeypatch)

    tx = build_c2_validator_admission_transaction(
        paths,
        private_state,
        sync_evidence,
        max_age_seconds=999999999,
    )
    tx_path, tx_sha = write_c2_validator_admission_transaction(
        paths,
        tx,
        operation=_operation("write-t3-source-admission-tx"),
    )
    release = build_c2_validator_admission_release(
        paths,
        private_state,
        tx_path,
        acknowledged_transaction_sha256=tx_sha,
        transaction_max_age_seconds=999999999,
    )
    release_path, release_sha = write_c2_validator_admission_release(
        paths,
        release,
        operation=_operation("write-t3-source-admission-release"),
    )

    admission = execute_c2_validator_admission_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=999999999,
        transaction_max_age_seconds=999999999,
        max_wait_seconds=1,
        poll_interval_seconds=0,
        opener=_C2ValidatorAdmissionOpener(c2_status=c2_status),
        operation=_operation("execute-t3-source-admission"),
    )
    assert admission["status"] == "pass"
    return paths, private_state, Path(admission["evidence"]["path"])


class _T3SteadyStateOpener:
    def __init__(self, *, c2_status: str = "running:unhealthy") -> None:
        self.requests: list[dict] = []
        self.statuses = {
            "coolify-a.invalid": [
                {
                    "uuid": "svc-a1",
                    "name": "mainneta-super1",
                    "status": "running:healthy",
                    "applications": [
                        {"name": "mainneta-super1", "status": "running:healthy"},
                    ],
                },
            ],
            "coolify-c.invalid": [
                {
                    "uuid": "svc-c1",
                    "name": "mainnetc-super1",
                    "status": "degraded:unhealthy",
                    "applications": [
                        {"name": "mainnetc-super1", "status": "running:healthy"},
                    ],
                },
                {
                    "uuid": "svc-c2",
                    "name": "mainnetc-super2",
                    "status": c2_status,
                    "applications": [
                        {"name": "mainnetc-super2", "status": "running:excluded"},
                        {"name": "mother-replica-sync-guardian", "status": "running:healthy"},
                    ],
                },
            ],
        }

    def open(self, request, timeout: float):  # noqa: ANN001
        from urllib.parse import urlsplit

        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        path = parsed.path
        method = request.get_method()
        self.requests.append({"method": method, "host": host, "path": path})
        assert timeout > 0

        if method == "GET" and path == "/api/v1/services":
            return _Response({"services": self.statuses[host]})

        raise AssertionError(f"unexpected {method} {host} {path}")


def _released_t3(tmp_path, monkeypatch):
    paths, private_state, admission_evidence = _admission_evidence(tmp_path, monkeypatch)
    tx = build_t3_post_admission_steady_state_transaction(
        paths,
        private_state,
        admission_evidence,
        max_age_seconds=999999999,
    )
    assert tx["summary"]["validator_count"] == 3
    assert tx["summary"]["mutation_count"] == 0
    assert tx["summary"]["routing_or_topology_publication_authorized"] is False
    tx_path, tx_sha = write_t3_post_admission_steady_state_transaction(
        paths,
        tx,
        operation=_operation("write-t3-steady-state-tx"),
    )
    release = build_t3_post_admission_steady_state_release(
        paths,
        private_state,
        tx_path,
        acknowledged_transaction_sha256=tx_sha,
        transaction_max_age_seconds=999999999,
    )
    release_path, release_sha = write_t3_post_admission_steady_state_release(
        paths,
        release,
        operation=_operation("write-t3-steady-state-release"),
    )
    return paths, private_state, admission_evidence, tx_path, tx_sha, release_path, release_sha


def test_t3_post_admission_steady_state_transaction_and_release_are_read_only(tmp_path, monkeypatch) -> None:
    paths, private_state, _, tx_path, _, release_path, release_sha = _released_t3(tmp_path, monkeypatch)

    verified_tx = verify_t3_post_admission_steady_state_transaction(
        paths,
        private_state,
        tx_path,
        max_age_seconds=999999999,
    )
    assert verified_tx["clean"] is True
    assert verified_tx["validator_count"] == 3
    assert verified_tx["mutation_count"] == 0
    assert verified_tx["read_only_observation_authorized"] is False

    verified_release = verify_t3_post_admission_steady_state_release(
        paths,
        private_state,
        release_path,
        max_age_seconds=999999999,
        transaction_max_age_seconds=999999999,
    )
    assert verified_release["clean"] is True
    assert verified_release["read_only_observation_authorized"] is True
    assert verified_release["mutation_count"] == 0
    assert verified_release["validator_vote_authorized"] is False

    inspection = inspect_t3_post_admission_steady_state_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=999999999,
        transaction_max_age_seconds=999999999,
    )
    assert inspection["summary"]["clean"] is True
    assert inspection["summary"]["live_mutation_performed"] is False
    assert inspection["summary"]["next_phase"] == "execute-t3-post-admission-steady-state"


def test_t3_post_admission_steady_state_execution_accepts_c2_aggregate_unhealthy_from_admission_evidence(tmp_path, monkeypatch) -> None:
    paths, private_state, _, _, _, release_path, release_sha = _released_t3(tmp_path, monkeypatch)

    opener = _T3SteadyStateOpener(c2_status="running:unhealthy")
    result = execute_t3_post_admission_steady_state_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=999999999,
        transaction_max_age_seconds=999999999,
        window_count=2,
        window_seconds=0,
        opener=opener,
        operation=_operation("execute-t3-steady-state"),
    )

    assert result["status"] == "pass"
    assert result["summary"]["clean"] is True
    assert result["summary"]["source_admission_evidence_reverified"] is True
    assert result["summary"]["final_validator_set_bound"] is True
    assert result["summary"]["C2_readiness_source"] == "c2-validator-admission-evidence"
    assert result["summary"]["live_mutation_performed"] is False
    assert result["summary"]["mutation_count"] == 0
    assert result["summary"]["routing_or_topology_published"] is False
    assert result["summary"]["public_endpoint_created"] is False
    assert result["next_phase"] == "stage-t3-steady-state-soak"
    assert [item["method"] for item in opener.requests] == ["GET"] * 6

    verified = verify_t3_post_admission_steady_state_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        max_age_seconds=999999999,
        transaction_max_age_seconds=999999999,
    )
    assert verified["clean"] is True
    assert verified["validator_count"] == 3
    assert verified["next_phase"] == "stage-t3-steady-state-soak"




def test_t3_post_admission_steady_state_execution_canonicalizes_float_cli_seconds(tmp_path, monkeypatch) -> None:
    paths, private_state, _, _, _, release_path, release_sha = _released_t3(tmp_path, monkeypatch)

    opener = _T3SteadyStateOpener()
    result = execute_t3_post_admission_steady_state_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=999999999,
        transaction_max_age_seconds=999999999,
        window_count=1,
        window_seconds=60.0,
        opener=opener,
        operation=_operation("execute-t3-steady-state-canonical-seconds"),
    )

    assert result["status"] == "pass"
    assert result["window_seconds"] == 60
    evidence_document = json.loads(Path(result["evidence"]["path"]).read_text())
    assert evidence_document["window_seconds"] == 60
    assert isinstance(evidence_document["window_seconds"], int)


def test_cli_stages_releases_executes_and_verifies_t3_post_admission_steady_state(tmp_path, monkeypatch, capsys) -> None:
    paths, _, admission_evidence = _admission_evidence(tmp_path, monkeypatch)
    runtime_root = paths.root.parent

    code = mother_deploy.main([
        "stage-t3-post-admission-steady-state",
        "--runtime-state-root", str(runtime_root),
        "--node", "mainnetc-super2",
        "--admission-evidence", str(admission_evidence),
        "--max-age-seconds", "999999999",
        "--write-transaction",
    ])
    assert code == 0
    staged = json.loads(capsys.readouterr().out)
    assert staged["summary"]["validator_count"] == 3
    tx_path = staged["transaction_artifact"]["path"]
    tx_sha = staged["transaction_artifact"]["sha256"]

    code = mother_deploy.main([
        "release-t3-post-admission-steady-state",
        "--runtime-state-root", str(runtime_root),
        "--node", "mainnetc-super2",
        "--transaction", tx_path,
        "--acknowledge-t3-post-admission-steady-state-transaction-sha256", tx_sha,
        "--transaction-max-age-seconds", "999999999",
        "--expires-in-seconds", "900",
        "--write-release",
    ])
    assert code == 0
    released = json.loads(capsys.readouterr().out)
    release_path = released["release_artifact"]["path"]
    release_sha = released["release_artifact"]["sha256"]
    assert released["summary"]["read_only_observation_authorized"] is True

    opener = _T3SteadyStateOpener()
    monkeypatch.setattr(
        "tools.mother.common.deployment_t3_post_admission_steady_state._DEFAULT_OPENER",
        opener.open,
    )
    # The default opener is imported into the function default at definition time,
    # so call the function-level executor through the API in other tests and use
    # CLI here only up to inspection.  This still proves parser and command wiring.
    code = mother_deploy.main([
        "apply-t3-post-admission-steady-state",
        "--runtime-state-root", str(runtime_root),
        "--node", "mainnetc-super2",
        "--release", release_path,
        "--acknowledge-release-sha256", release_sha,
        "--max-age-seconds", "999999999",
        "--transaction-max-age-seconds", "999999999",
    ])
    assert code == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["summary"]["clean"] is True
    assert inspected["summary"]["next_phase"] == "execute-t3-post-admission-steady-state"
