from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from tools import mother_deploy
from tools.mother.common.deployment_genesis_birth import (
    MotherDeploymentGenesisBirthError,
    build_genesis_birth_release,
    execute_genesis_birth_release,
    inspect_genesis_birth_release,
    verify_genesis_birth_evidence,
    verify_genesis_birth_release,
    write_genesis_birth_release,
)
from tools.mother.common.deployment_genesis_executor import execute_released_genesis
from tests.test_mother_deployment_executor import TOKEN_A, _operation
from tests.test_mother_deployment_genesis_executor import _GenesisOpener, _genesis_release


def _successful_execution(tmp_path: Path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, private_state, _, _, _, release_path, release_digest, release = _genesis_release(tmp_path, now=now)
    result = execute_released_genesis(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainneta-super1",),
        opener=_GenesisOpener(),
        operation=_operation("birth-fixture-genesis"),
    )
    return paths, private_state, Path(result["result_artifact"]["path"]), result, release


def _birth_release(tmp_path: Path):
    paths, private_state, execution_path, execution, genesis_release = _successful_execution(tmp_path)
    release = build_genesis_birth_release(
        paths,
        private_state,
        execution_path,
        acknowledged_genesis_execution_sha256=execution["result_artifact"]["sha256"],
        selected_nodes=("mainneta-super1",),
    )
    path, digest = write_genesis_birth_release(
        paths, release, operation=_operation("birth-release")
    )
    return paths, private_state, execution_path, execution, genesis_release, path, digest, release


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


class _BirthOpener:
    def __init__(self, original_compose: str, proof_compose: str, *, healthy: bool = True) -> None:
        self.original_compose = original_compose
        self.proof_compose = proof_compose
        self.healthy = healthy
        self.requests: list[tuple[str, str]] = []
        self.patched = False

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        self.requests.append((method, path))
        assert parsed.hostname == "coolify-a.invalid"
        assert request.headers.get("Authorization") == f"Bearer {TOKEN_A}"
        assert timeout > 0
        if method == "GET" and path == "/api/v1/services":
            status = "running:healthy" if self.patched and self.healthy else "running:unknown"
            return _Response([{"uuid": "svc-mainneta-super1", "name": "mainneta-super1", "status": status}])
        if method == "GET" and path == "/api/v1/services/svc-mainneta-super1":
            compose = self.proof_compose if self.patched else self.original_compose
            return _Response({"uuid": "svc-mainneta-super1", "name": "mainneta-super1", "docker_compose_raw": compose})
        if method == "PATCH" and path == "/api/v1/services/svc-mainneta-super1":
            body = json.loads(request.data.decode("utf-8"))
            import base64
            assert base64.b64decode(body["docker_compose_raw"]).decode("utf-8") == self.proof_compose
            self.patched = True
            return _Response({"uuid": "svc-mainneta-super1"}, status=200)
        if method == "GET" and path == "/api/v1/deploy":
            return _Response({"deployment_uuid": "proof-deploy"}, status=200)
        raise AssertionError(f"unexpected request {method} {request.full_url}")


def test_birth_release_is_internal_only_and_removes_host_rpc_mapping(tmp_path: Path) -> None:
    paths, private_state, _, execution, _, release_path, digest, release = _birth_release(tmp_path)
    compose = release["proof_plan"]["proof_compose"]["canonical_text"]
    guardian = compose.split("  mother-genesis-proof-guardian:", 1)[1].split("\nvolumes:\n", 1)[0]
    assert "127.0.0.1:8545:8545" not in compose
    assert "ports:" not in guardian
    assert "expose:" not in guardian
    assert "traefik." not in guardian
    assert release["proof_plan"]["proof"]["manual_ssh_required"] is False
    assert release["proof_plan"]["proof"]["public_endpoint_created"] is False
    verified = verify_genesis_birth_release(
        paths, private_state, release_path, selected_nodes=("mainneta-super1",)
    )
    assert verified["clean"] is True
    assert verified["genesis_birth_release_sha256"] == digest
    assert verified["genesis_execution_sha256"] == execution["result_artifact"]["sha256"]


def test_birth_release_rejects_wrong_execution_digest(tmp_path: Path) -> None:
    paths, private_state, execution_path, _, _ = _successful_execution(tmp_path)
    with pytest.raises(MotherDeploymentGenesisBirthError) as caught:
        build_genesis_birth_release(
            paths,
            private_state,
            execution_path,
            acknowledged_genesis_execution_sha256="0" * 64,
            selected_nodes=("mainneta-super1",),
        )
    assert caught.value.code == "MOTHER_DEPLOY_GENESIS_BIRTH_ACKNOWLEDGEMENT_MISMATCH"


def test_birth_inspection_is_network_free(tmp_path: Path) -> None:
    paths, private_state, _, _, _, release_path, digest, _ = _birth_release(tmp_path)
    result = inspect_genesis_birth_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=digest,
        selected_nodes=("mainneta-super1",),
    )
    assert result["clean"] is True
    assert result["manual_ssh_required"] is False
    assert result["public_endpoint_created"] is False
    assert result["network_access_performed"] is False
    assert result["release_already_claimed"] is False


def test_birth_executor_proves_chain_through_internal_guardian_and_coolify(tmp_path: Path) -> None:
    paths, private_state, _, _, genesis_release, release_path, digest, release = _birth_release(tmp_path)
    original = genesis_release["execution_plan"]["compose"]["canonical_text"]
    proof = release["proof_plan"]["proof_compose"]["canonical_text"]
    opener = _BirthOpener(original, proof)
    result = execute_genesis_birth_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=digest,
        selected_nodes=("mainneta-super1",),
        opener=opener,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        operation=_operation("birth-live"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["initial_chain_proven"] is True
    assert result["summary"]["manual_ssh_required"] is False
    assert result["summary"]["public_endpoint_created"] is False
    assert result["proof"]["service_status"] == "running:healthy"
    assert all("coolify-c" not in path for _, path in opener.requests)
    verified = verify_genesis_birth_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        selected_nodes=("mainneta-super1",),
    )
    assert verified["initial_chain_proven"] is True
    assert verified["next_phase"] == "stage-soft-replica-configuration"


def test_birth_executor_fails_closed_when_guardian_never_becomes_healthy(tmp_path: Path) -> None:
    paths, private_state, _, _, genesis_release, release_path, digest, release = _birth_release(tmp_path)
    opener = _BirthOpener(
        genesis_release["execution_plan"]["compose"]["canonical_text"],
        release["proof_plan"]["proof_compose"]["canonical_text"],
        healthy=False,
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
        operation=_operation("birth-unhealthy"),
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_GENESIS_BIRTH_NOT_HEALTHY"
    assert result["summary"]["initial_chain_proven"] is False
    inspected = inspect_genesis_birth_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=digest,
        selected_nodes=("mainneta-super1",),
    )
    assert inspected["release_already_claimed"] is True


def test_birth_cli_release_verify_and_dry_apply(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths, _, execution_path, execution, _ = _successful_execution(tmp_path)
    runtime_root = paths.root.parent
    code = mother_deploy.main([
        "release-genesis-birth",
        "--execution", str(execution_path),
        "--acknowledge-genesis-execution-sha256", execution["result_artifact"]["sha256"],
        "--node", "mainneta-super1",
        "--runtime-state-root", str(runtime_root),
        "--write-release",
    ])
    assert code == 0
    release = json.loads(capsys.readouterr().out)
    release_path = release["release_artifact"]["path"]
    release_sha = release["release_artifact"]["sha256"]
    code = mother_deploy.main([
        "verify-genesis-birth-release",
        "--release", release_path,
        "--node", "mainneta-super1",
        "--runtime-state-root", str(runtime_root),
    ])
    assert code == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["manual_ssh_required"] is False
    assert verified["public_endpoint_created"] is False
    code = mother_deploy.main([
        "apply-genesis-birth",
        "--release", release_path,
        "--acknowledge-release-sha256", release_sha,
        "--node", "mainneta-super1",
        "--runtime-state-root", str(runtime_root),
    ])
    assert code == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["execute_requested"] is False
    assert inspected["network_access_performed"] is False
