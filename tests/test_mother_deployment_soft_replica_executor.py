from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from tools import mother_deploy
from tools.mother.common.deployment_soft_replica import build_soft_replica_transaction, write_soft_replica_transaction
from tools.mother.common.deployment_soft_replica_executor import (
    MotherDeploymentSoftReplicaExecutorError,
    execute_released_soft_replica,
    inspect_released_soft_replica,
)
from tools.mother.common.deployment_soft_replica_release import (
    MotherDeploymentSoftReplicaReleaseError,
    build_soft_replica_release,
    verify_soft_replica_release,
    write_soft_replica_release,
)
from tests.test_mother_deployment_executor import TOKEN_A, TOKEN_C, _operation
from tests.test_mother_deployment_soft_replica import _birth_evidence


class _Response:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self.status = status
        self.headers = {"Content-Type": "application/json"}
        self._body = json.dumps(payload).encode("utf-8")

    def getcode(self) -> int:
        return self.status

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]

    def close(self) -> None:
        return None


def _fixture(tmp_path: Path):
    paths, private_state, evidence_path = _birth_evidence(tmp_path)
    transaction = build_soft_replica_transaction(
        paths, private_state, evidence_path, selected_nodes=("mainnetc-super1",),
    )
    transaction_path, transaction_digest = write_soft_replica_transaction(
        paths, transaction, operation=_operation("replica-transaction-fixture"),
    )
    release = build_soft_replica_release(
        paths, private_state, transaction_path,
        acknowledged_transaction_sha256=transaction_digest,
        selected_nodes=("mainnetc-super1",),
    )
    release_path, release_digest = write_soft_replica_release(
        paths, release, operation=_operation("replica-release-fixture"),
    )
    return paths, private_state, transaction, transaction_path, transaction_digest, release, release_path, release_digest


class _ReplicaOpener:
    def __init__(self, private_state, release, *, a_healthy: bool = True, fail_patch: bool = False) -> None:
        state = json.loads(private_state.canonical_object_bytes)
        self.a_healthy = a_healthy
        self.fail_patch = fail_patch
        self.release = release
        self.requests: list[tuple[str, str, str]] = []
        self.patched = False
        self.values = {
            "MC_MOTHER_VALIDATOR_PRIVATE_KEY": state["networks"]["mainnet"]["validators"]["mainnetc-super1"]["private_key"],
            "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY": state["networks"]["mainnet"]["node_seed_material"]["mainnetc-super1"]["wallets"]["hub_admin"]["private_key"],
        }

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host, method, path = parsed.hostname, request.get_method(), parsed.path
        self.requests.append((host or "", method, path))
        assert timeout > 0
        expected_token = TOKEN_A if host == "coolify-a.invalid" else TOKEN_C
        assert request.headers.get("Authorization") == f"Bearer {expected_token}"
        if host == "coolify-a.invalid":
            assert method == "GET"
            if path == "/api/v1/services":
                return _Response([{
                    "uuid": "svc-mainneta-super1",
                    "name": "mainneta-super1",
                    "status": "running:healthy" if self.a_healthy else "running:unhealthy",
                }])
            assert path == "/api/v1/services/svc-mainneta-super1"
            proof = self.release["initial_chain_precondition"]["proof_compose"]["canonical_text"]
            return _Response({
                "service": {
                    "uuid": "svc-mainneta-super1",
                    "name": "mainneta-super1",
                    "docker_compose_raw": proof,
                }
            })
        assert host == "coolify-c.invalid"
        if method == "GET" and path == "/api/v1/services/svc-mainnetc-super1":
            return _Response({"uuid": "svc-mainnetc-super1", "name": "mainnetc-super1", "status": "exited"})
        if method == "GET" and path == "/api/v1/services/svc-mainnetc-super1/envs":
            commitments = self.release["execution_plan"]["identity_commitments"]
            return _Response([
                {
                    "uuid": commitments[key]["environment_variable_uuid"],
                    "key": key,
                    "value": self.values[key],
                }
                for key in sorted(commitments)
            ])
        if method == "PATCH" and path == "/api/v1/services/svc-mainnetc-super1":
            if self.fail_patch:
                return _Response({"message": "rejected"}, status=500)
            body = json.loads(request.data.decode("utf-8"))
            compose = base64.b64decode(body["docker_compose_raw"]).decode("utf-8")
            assert compose == self.release["execution_plan"]["compose"]["canonical_text"]
            self.patched = True
            return _Response({"uuid": "svc-mainnetc-super1"}, status=200)
        if method == "GET" and path == "/api/v1/deploy":
            assert self.patched
            return _Response({"deployment_uuid": "replica-deploy"}, status=200)
        raise AssertionError(f"unexpected request {method} {request.full_url}")


def test_soft_replica_release_is_exact_c_only_and_vote_blocked(tmp_path: Path) -> None:
    paths, private_state, transaction, _, transaction_digest, release, release_path, release_digest = _fixture(tmp_path)
    assert release["transaction"]["sha256"] == transaction_digest
    assert release["execution_plan"]["replica_node"] == "mainnetc-super1"
    assert release["execution_plan"]["controller_id"] == "coolify-c"
    assert release["authority"]["configuration_apply_authorized"] is True
    assert release["authority"]["replica_start_authorized"] is True
    assert release["authority"]["validator_vote_authorized"] is False
    assert [m["controller_id"] for m in release["execution_plan"]["mutations"]] == ["coolify-c", "coolify-c"]
    assert release["execution_plan"]["compose"]["sha256"] == transaction["replica"]["compose"]["sha256"]
    verified = verify_soft_replica_release(paths, private_state, release_path, selected_nodes=("mainnetc-super1",))
    assert verified["clean"] is True
    assert verified["soft_replica_release_sha256"] == release_digest
    assert verified["validator_vote_authorized"] is False


def test_soft_replica_release_rejects_wrong_transaction_digest(tmp_path: Path) -> None:
    paths, private_state, _, transaction_path, _, _, _, _ = _fixture(tmp_path)
    with pytest.raises(MotherDeploymentSoftReplicaReleaseError) as caught:
        build_soft_replica_release(
            paths, private_state, transaction_path,
            acknowledged_transaction_sha256="0" * 64,
            selected_nodes=("mainnetc-super1",),
        )
    assert caught.value.code == "MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_ACKNOWLEDGEMENT_MISMATCH"


def test_soft_replica_inspection_is_network_free(tmp_path: Path) -> None:
    paths, private_state, _, _, _, _, release_path, release_digest = _fixture(tmp_path)
    result = inspect_released_soft_replica(
        paths, private_state, release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
    )
    assert result["clean"] is True
    assert result["network_access_performed"] is False
    assert result["initial_node_read_only"] is True
    assert result["validator_vote_authorized"] is False
    assert result["release_already_claimed"] is False


def test_soft_replica_executor_rechecks_a_and_starts_only_c(tmp_path: Path) -> None:
    paths, private_state, _, _, _, release, release_path, release_digest = _fixture(tmp_path)
    opener = _ReplicaOpener(private_state, release)
    result = execute_released_soft_replica(
        paths, private_state, release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        operation=_operation("replica-live"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["initial_chain_reverified"] is True
    assert result["summary"]["replica_compose_update_succeeded"] is True
    assert result["summary"]["replica_deployment_requested"] is True
    assert result["summary"]["replica_synchronized"] is False
    assert result["summary"]["validator_vote_authorized"] is False
    assert result["summary"]["next_phase"] == "prove-soft-replica-synchronization-before-validator-admission"
    assert [(h, m) for h, m, _ in opener.requests if h == "coolify-a.invalid"] == [("coolify-a.invalid", "GET"), ("coolify-a.invalid", "GET")]
    assert all(h != "coolify-a.invalid" or m == "GET" for h, m, _ in opener.requests)
    rendered = json.dumps(result, sort_keys=True)
    for value in opener.values.values():
        assert value not in rendered


def test_soft_replica_executor_fails_before_mutation_when_a_unhealthy_and_consumes_release(tmp_path: Path) -> None:
    paths, private_state, _, _, _, release, release_path, release_digest = _fixture(tmp_path)
    opener = _ReplicaOpener(private_state, release, a_healthy=False)
    result = execute_released_soft_replica(
        paths, private_state, release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        operation=_operation("replica-unhealthy"),
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_INITIAL_CHAIN_UNHEALTHY"
    assert result["summary"]["live_mutation_performed"] is False
    assert all(host != "coolify-c.invalid" for host, _, _ in opener.requests)
    with pytest.raises(MotherDeploymentSoftReplicaExecutorError) as caught:
        execute_released_soft_replica(
            paths, private_state, release_path,
            acknowledged_release_sha256=release_digest,
            selected_nodes=("mainnetc-super1",),
            opener=opener,
            operation=_operation("replica-replay"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_ALREADY_CONSUMED"


def test_soft_replica_executor_writes_secret_free_partial_failure_receipt(tmp_path: Path) -> None:
    paths, private_state, _, _, _, release, release_path, release_digest = _fixture(tmp_path)
    opener = _ReplicaOpener(private_state, release, fail_patch=True)
    result = execute_released_soft_replica(
        paths, private_state, release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
        operation=_operation("replica-patch-failure"),
    )
    assert result["status"] == "failed"
    assert result["summary"]["attempted_mutation_count"] == 1
    assert result["summary"]["replica_deployment_requested"] is False
    artifact = json.loads(Path(result["result_artifact"]["path"]).read_text(encoding="utf-8"))
    rendered = json.dumps(artifact, sort_keys=True)
    for value in opener.values.values():
        assert value not in rendered


def test_cli_releases_inspects_and_verifies_soft_replica(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths, _, _, transaction_path, transaction_digest, _, _, _ = _fixture(tmp_path)
    runtime_root = paths.root.parent
    code = mother_deploy.main([
        "release-soft-replica", "--runtime-state-root", str(runtime_root),
        "--transaction", str(transaction_path),
        "--acknowledge-soft-replica-transaction-sha256", transaction_digest,
        "--node", "mainnetc-super1", "--write-release",
    ])
    assert code == 0
    staged = json.loads(capsys.readouterr().out)
    release_path = staged["release_artifact"]["path"]
    release_digest = staged["release_artifact"]["sha256"]
    code = mother_deploy.main([
        "verify-soft-replica-release", "--runtime-state-root", str(runtime_root),
        "--release", release_path, "--node", "mainnetc-super1",
    ])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["clean"] is True
    code = mother_deploy.main([
        "apply-soft-replica", "--runtime-state-root", str(runtime_root),
        "--release", release_path, "--acknowledge-release-sha256", release_digest,
        "--node", "mainnetc-super1",
    ])
    assert code == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["execute_requested"] is False
    assert inspected["validator_vote_authorized"] is False
