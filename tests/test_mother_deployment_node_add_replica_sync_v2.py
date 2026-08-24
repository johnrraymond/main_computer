from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from tools import mother_deploy
from tools.mother.common.deployment_node_add_replica_sync import (
    build_node_add_replica_sync_release,
    verify_node_add_replica_sync_evidence,
    write_node_add_replica_sync_release,
)
from tools.mother.common.deployment_node_add_replica_sync_v2 import (
    execute_node_add_replica_sync_release,
)
from tests.test_mother_deployment_executor import _operation
from tests.test_mother_deployment_node_add_prep import (
    A_NODE,
    _AddNodeReplicaSyncOpener,
    _write_verified_add_node_identity_evidence,
)


_NOW = datetime(2026, 8, 11, 21, 40, 1, tzinfo=timezone.utc)


def _release(tmp_path: Path):
    paths, private_state, identity_evidence_path, identity_evidence_sha = (
        _write_verified_add_node_identity_evidence(tmp_path)
    )
    release = build_node_add_replica_sync_release(
        paths,
        private_state,
        identity_evidence_path,
        acknowledged_add_node_identity_evidence_sha256=identity_evidence_sha,
        created_at="2026-08-11T21:40:00Z",
        now=_NOW,
    )
    release_path, release_sha = write_node_add_replica_sync_release(
        paths,
        release,
        operation=_operation("write-add-replica-sync-release-v2"),
    )
    return paths, private_state, release_path, release_sha


def _execute(paths, private_state, release_path, release_sha, opener):
    return execute_node_add_replica_sync_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=900,
        identity_max_age_seconds=86400,
        identity_release_max_age_seconds=86400,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        timeout=1.0,
        max_wait_seconds=0.05,
        poll_interval_seconds=0.001,
        opener=opener,
        now=_NOW,
        operation=_operation("execute-add-replica-sync-release-v2"),
    )


class _DelayedNodeReadyOpener(_AddNodeReplicaSyncOpener):
    def __init__(self, *, private_state, guardian_ready_with_node: bool = False) -> None:  # noqa: ANN001
        super().__init__(
            private_state=private_state,
            node_after_start_status="exited",
        )
        self.guardian_ready_with_node = guardian_ready_with_node
        self._start_seen = False
        self.post_start_target_gets = 0
        self.helper_created_after_target_gets: int | None = None

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path

        if method == "POST" and path == "/api/v1/services/svc-a1/start":
            response = super().open(request, timeout)
            self._start_seen = True
            return response

        if (
            self._start_seen
            and method == "GET"
            and path == "/api/v1/services/svc-a1"
        ):
            self.post_start_target_gets += 1
            # GET #1 is the existing pre-helper diagnostic; GET #2 observes the
            # still-exited node in the v2 readiness gate.  The next observation
            # simulates Coolify completing its asynchronous node start.
            if self.post_start_target_gets >= 3:
                self.node_after_start_status = "running:healthy"
                for application in self.service.get("applications", []):
                    if application.get("name") == A_NODE:
                        application["status"] = self.node_after_start_status
                    if (
                        self.guardian_ready_with_node
                        and application.get("name") == "mother-replica-sync-guardian"
                    ):
                        application["status"] = "running:healthy"

        if method == "POST" and path == "/api/v1/services":
            self.helper_created_after_target_gets = self.post_start_target_gets

        return super().open(request, timeout)


def test_mother_deploy_selects_replica_sync_v2_executor() -> None:
    assert mother_deploy.execute_node_add_replica_sync_release is execute_node_add_replica_sync_release
    assert mother_deploy.execute_node_add_replica_sync_release.__module__.endswith(
        "deployment_node_add_replica_sync_v2"
    )


def test_replica_sync_v2_waits_for_node_before_creating_guardian_helper(tmp_path: Path) -> None:
    paths, private_state, release_path, release_sha = _release(tmp_path)
    opener = _DelayedNodeReadyOpener(private_state=private_state)

    result = _execute(paths, private_state, release_path, release_sha, opener)

    assert result["status"] == "pass"
    assert result["implementation_version"] == 2
    readiness = result["replica_sync_node_readiness"]
    assert readiness["healthy"] is True
    assert readiness["reason"] == "replica-sync-node-running-healthy"
    assert readiness["observed_statuses"][0]["node_status"] == "exited"
    assert readiness["observed_statuses"][-1]["node_status"] == "running:healthy"
    assert readiness["observation_count"] >= 2
    assert opener.helper_created_after_target_gets is not None
    assert opener.helper_created_after_target_gets >= 3
    assert result["replica_sync_guardian_start"]["status"] == "pass"

    # Downstream validator-admission code still imports the v1 verifier.  Prove
    # that v2 evidence remains contract-compatible with that unchanged verifier.
    verified = verify_node_add_replica_sync_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        max_age_seconds=86400,
        release_max_age_seconds=86400,
        identity_max_age_seconds=86400,
        identity_release_max_age_seconds=86400,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        now=_NOW,
    )
    assert verified["clean"] is True
    assert verified["replica_sync_proven"] is True



def test_replica_sync_v2_skips_helper_when_guardian_is_already_healthy(tmp_path: Path) -> None:
    paths, private_state, release_path, release_sha = _release(tmp_path)
    opener = _DelayedNodeReadyOpener(
        private_state=private_state,
        guardian_ready_with_node=True,
    )

    result = _execute(paths, private_state, release_path, release_sha, opener)

    assert result["status"] == "pass"
    readiness = result["replica_sync_node_readiness"]
    assert readiness["healthy"] is True
    assert readiness["component_summary"]["node_running_healthy"] is True
    assert readiness["component_summary"]["guardian_running_healthy"] is True
    guardian_start = result["replica_sync_guardian_start"]
    assert guardian_start["status"] == "pass"
    assert guardian_start["reason"] == "guardian-already-running-healthy"
    assert guardian_start["skipped"] is True
    assert guardian_start["temporary_service_created"] is False
    assert opener.helper_created_after_target_gets is None
    assert not any(
        request["method"] == "POST" and request["path"] == "/api/v1/services"
        for request in opener.requests
    )

    verified = verify_node_add_replica_sync_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        max_age_seconds=86400,
        release_max_age_seconds=86400,
        identity_max_age_seconds=86400,
        identity_release_max_age_seconds=86400,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        now=_NOW,
    )
    assert verified["clean"] is True
    assert verified["replica_sync_proven"] is True

def test_replica_sync_v2_timeout_does_not_create_guardian_helper(tmp_path: Path) -> None:
    paths, private_state, release_path, release_sha = _release(tmp_path)
    opener = _AddNodeReplicaSyncOpener(
        private_state=private_state,
        node_after_start_status="exited",
    )

    # Zero wait makes this a single read-only readiness observation and proves
    # the failure boundary without creating the temporary helper.
    result = execute_node_add_replica_sync_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=900,
        identity_max_age_seconds=86400,
        identity_release_max_age_seconds=86400,
        add_do_max_age_seconds=86400,
        add_do_release_max_age_seconds=86400,
        transaction_max_age_seconds=86400,
        baseline_max_age_seconds=86400,
        timeout=1.0,
        max_wait_seconds=0.0,
        poll_interval_seconds=0.0,
        opener=opener,
        now=_NOW,
        operation=_operation("execute-add-replica-sync-release-v2-timeout"),
    )

    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_NODE_ADD_REPLICA_SYNC_NODE_NOT_READY"
    assert result["replica_sync_node_readiness"]["healthy"] is False
    assert result["replica_sync_node_readiness"]["component_summary"]["node_status"] == "exited"
    assert result["replica_sync_guardian_start"] is None
    assert not any(
        request["method"] == "POST" and request["path"] == "/api/v1/services"
        for request in opener.requests
    )
