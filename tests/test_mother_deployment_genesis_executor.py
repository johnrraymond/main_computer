from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from tools import mother_deploy
from tools.mother.common.deployment_genesis import (
    build_deployment_genesis_transaction,
    write_deployment_genesis_transaction,
)
from tools.mother.common.deployment_genesis_executor import (
    MotherDeploymentGenesisExecutorError,
    execute_released_genesis,
    inspect_released_genesis,
)
from tools.mother.common.deployment_genesis_release import (
    MotherDeploymentGenesisReleaseError,
    build_deployment_genesis_release,
    verify_deployment_genesis_release,
    write_deployment_genesis_release,
)
from tools.mother.common.deployment_genesis_rollback import (
    execute_genesis_mutation_rollback,
    inspect_genesis_mutation_rollback,
    verify_genesis_mutation_rollback,
)
from tests.test_mother_deployment_executor import TOKEN_A, _operation
from tests.test_mother_deployment_genesis import _identity_execution


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _genesis_transaction(tmp_path: Path, *, now: datetime):
    paths, private_state, identity_execution, rollback_verification = _identity_execution(tmp_path)
    transaction = build_deployment_genesis_transaction(
        paths,
        private_state,
        identity_execution,
        identity_rollback_verification_path=rollback_verification,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        created_at=_stamp(now),
    )
    transaction_path, transaction_digest = write_deployment_genesis_transaction(
        paths,
        transaction,
        operation=_operation("genesis-executor-transaction"),
    )
    return paths, private_state, transaction_path, transaction_digest, transaction


def _genesis_release(tmp_path: Path, *, now: datetime):
    paths, private_state, transaction_path, transaction_digest, transaction = _genesis_transaction(tmp_path, now=now)
    release = build_deployment_genesis_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_genesis_transaction_sha256=transaction_digest,
        selected_nodes=("mainneta-super1",),
        created_at=_stamp(now),
        now=now,
    )
    release_path, release_digest = write_deployment_genesis_release(
        paths,
        release,
        operation=_operation("genesis-executor-release"),
    )
    return paths, private_state, transaction_path, transaction_digest, transaction, release_path, release_digest, release


class _Response:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self.status = status
        self.headers = {"Content-Type": "application/json"}
        self._body = json.dumps(payload).encode("utf-8") if not isinstance(payload, str) else payload.encode("utf-8")

    def getcode(self) -> int:
        return self.status

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]

    def close(self) -> None:
        return None


def _standby_compose() -> str:
    return "\n".join(
        [
            "name: mainneta-super1",
            "",
            "services:",
            "  mainneta-super1:",
            "    image: alpine:3.20",
            '    restart: "no"',
            "    command:",
            "      - sh",
            "      - -lc",
            "      - exec tail -f /dev/null",
            "    labels:",
            "      main_computer.mother.stage: standby",
            "      main_computer.mother.node: mainneta-super1",
            "",
        ]
    )


class _GenesisOpener:
    def __init__(self, *, fail_deploy: bool = False, wrong_service: bool = False) -> None:
        self.fail_deploy = fail_deploy
        self.wrong_service = wrong_service
        self.requests: list[dict[str, Any]] = []
        self.current_compose = _standby_compose()
        self.status = "exited"

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        path = parsed.path
        method = request.get_method()
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append({
            "method": method,
            "host": host,
            "path": path,
            "query": parse_qs(parsed.query),
            "body": body,
        })
        assert timeout > 0
        assert host == "coolify-a.invalid"
        assert request.headers.get("Authorization") == f"Bearer {TOKEN_A}"
        if method == "GET" and path == "/api/v1/services":
            name = "wrong-name" if self.wrong_service else "mainneta-super1"
            return _Response([{
                "uuid": "svc-mainneta-super1",
                "name": name,
                "status": self.status,
                "docker_compose_raw": self.current_compose,
            }])
        if method == "GET" and path == "/api/v1/services/svc-mainneta-super1":
            return _Response({
                "uuid": "svc-mainneta-super1",
                "name": "mainneta-super1",
                "status": self.status,
                "docker_compose_raw": self.current_compose,
            })
        if method == "GET" and path == "/api/v1/services/svc-mainneta-super1/envs":
            return _Response([
                {"uuid": "env-a-1", "key": "MC_MOTHER_VALIDATOR_PRIVATE_KEY", "value": "<redacted>"},
                {"uuid": "env-a-2", "key": "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY", "value": "<redacted>"},
            ])
        if method == "PATCH" and path == "/api/v1/services/svc-mainneta-super1":
            assert body is not None
            self.current_compose = base64.b64decode(body["docker_compose_raw"]).decode("utf-8")
            return _Response({"uuid": "svc-mainneta-super1", "message": "updated"}, status=200)
        if method == "GET" and path == "/api/v1/services/svc-mainneta-super1/stop":
            self.status = "exited"
            return _Response({"message": "Service stopping request queued."}, status=200)
        if method == "GET" and path == "/api/v1/deploy":
            assert self.requests[-1]["query"] == {"uuid": ["svc-mainneta-super1"], "force": ["true"]}
            if not self.fail_deploy:
                self.status = "running:healthy"
            return _Response({"deployment_uuid": "deploy-a"}, status=500 if self.fail_deploy else 200)
        raise AssertionError(f"unexpected request: {method} {request.full_url}")

def test_genesis_release_binds_exact_a_only_compose_and_is_secret_free(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, private_state, _, transaction_digest, transaction, release_path, release_digest, release = _genesis_release(
        tmp_path, now=now
    )
    plan = release["execution_plan"]
    assert plan["initial_node"] == "mainneta-super1"
    assert plan["controller_id"] == "coolify-a"
    assert plan["service_uuid"] == "svc-mainneta-super1"
    assert [item["method"] for item in plan["mutations"]] == ["PATCH", "GET"]
    assert plan["excluded_targets"] == [
        {
            "node": "mainnetc-super1",
            "controller_id": "coolify-c",
            "service_uuid": "svc-mainnetc-super1",
            "reason": "soft replica admission requires an independently proven initial chain",
        }
    ]
    compose = plan["compose"]["canonical_text"]
    assert "hyperledger/besu:latest" in compose
    assert transaction["genesis"]["canonical_json_sha256"] == plan["genesis_sha256"]
    assert "mainnetc-super1" not in compose
    rendered = json.dumps(release, sort_keys=True)
    assert TOKEN_A not in rendered
    state = json.loads(private_state.canonical_object_bytes)
    assert state["networks"]["mainnet"]["validators"]["mainneta-super1"]["private_key"] not in rendered

    verified = verify_deployment_genesis_release(
        paths,
        private_state,
        release_path,
        selected_nodes=("mainneta-super1",),
        now=now + timedelta(seconds=1),
    )
    assert verified["clean"] is True
    assert verified["genesis_release_sha256"] == release_digest
    assert verified["genesis_transaction_sha256"] == transaction_digest


def test_genesis_release_rejects_wrong_digest_and_soft_node_selection(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, private_state, transaction_path, transaction_digest, _ = _genesis_transaction(tmp_path, now=now)
    with pytest.raises(MotherDeploymentGenesisReleaseError) as caught:
        build_deployment_genesis_release(
            paths,
            private_state,
            transaction_path,
            acknowledged_genesis_transaction_sha256="0" * 64,
            selected_nodes=("mainneta-super1",),
            created_at=_stamp(now),
            now=now,
        )
    assert caught.value.code == "MOTHER_DEPLOY_GENESIS_RELEASE_ACKNOWLEDGEMENT_MISMATCH"

    with pytest.raises(MotherDeploymentGenesisReleaseError) as caught:
        build_deployment_genesis_release(
            paths,
            private_state,
            transaction_path,
            acknowledged_genesis_transaction_sha256=transaction_digest,
            selected_nodes=("mainneta-super1", "mainnetc-super1"),
            created_at=_stamp(now),
            now=now,
        )
    assert caught.value.code == "MOTHER_DEPLOY_GENESIS_RELEASE_SELECTION_MISMATCH"


def test_genesis_release_expiry_is_enforced(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, private_state, _, _, _, release_path, _, _ = _genesis_release(tmp_path, now=now)
    with pytest.raises(MotherDeploymentGenesisReleaseError) as caught:
        verify_deployment_genesis_release(
            paths,
            private_state,
            release_path,
            selected_nodes=("mainneta-super1",),
            now=now + timedelta(seconds=301),
        )
    assert caught.value.code == "MOTHER_DEPLOY_GENESIS_RELEASE_EXPIRED"


def test_genesis_executor_dry_run_is_network_free_and_resolves_blocker(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, private_state, _, _, _, release_path, release_digest, _ = _genesis_release(tmp_path, now=now)
    result = inspect_released_genesis(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainneta-super1",),
        now=now + timedelta(seconds=1),
    )
    assert result["clean"] is True
    assert result["executor_implemented"] is True
    assert result["release_already_claimed"] is False
    assert result["live_execution_authorized"] is True
    assert result["soft_replica_untouched"] is True
    assert result["remaining_blocker_codes"] == []


def test_genesis_executor_updates_and_deploys_only_a(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, private_state, _, _, transaction, release_path, release_digest, release = _genesis_release(tmp_path, now=now)
    live = _GenesisOpener()
    result = execute_released_genesis(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainneta-super1",),
        opener=live,
        operation=_operation("genesis-executor-live"),
    )
    assert [(item["method"], item["path"]) for item in live.requests] == [
        ("GET", "/api/v1/services"),
        ("GET", "/api/v1/services/svc-mainneta-super1/envs"),
        ("GET", "/api/v1/services/svc-mainneta-super1"),
        ("PATCH", "/api/v1/services/svc-mainneta-super1"),
        ("GET", "/api/v1/deploy"),
    ]
    update_body = live.requests[3]["body"]
    compose = base64.b64decode(update_body["docker_compose_raw"]).decode("utf-8")
    assert "mainneta-super1" in compose
    assert "mainnetc-super1" not in compose
    assert result["status"] == "pass"
    assert result["summary"]["compose_update_succeeded"] is True
    assert result["summary"]["deployment_requested"] is True
    assert result["summary"]["soft_replica_untouched"] is True
    assert result["summary"]["initial_chain_proven"] is False
    assert result["summary"]["next_phase"] == "prove-genesis-rollback-cycle-before-birth"
    assert result["summary"]["rollback_available"] is True
    assert result["summary"]["genesis_birth_blocked_pending_genesis_rollback_cycle"] is True
    assert result["rollback_journal"]["sha256"]
    assert result["genesis_sha256"] == transaction["genesis"]["canonical_json_sha256"]
    assert result["compose_sha256"] == release["execution_plan"]["compose"]["sha256"]
    assert Path(result["result_artifact"]["path"]).is_file()


def test_genesis_executor_fails_closed_before_mutation_on_service_mismatch(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, private_state, _, _, _, release_path, release_digest, _ = _genesis_release(tmp_path, now=now)
    live = _GenesisOpener(wrong_service=True)
    result = execute_released_genesis(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=live,
        operation=_operation("genesis-executor-precondition"),
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_GENESIS_EXECUTOR_SERVICE_MISMATCH"
    assert not [item for item in live.requests if item["method"] in {"PATCH", "POST", "PUT", "DELETE"}]
    assert result["summary"]["live_mutation_performed"] is False


def test_genesis_executor_is_one_shot_and_records_deploy_failure(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, private_state, _, _, _, release_path, release_digest, _ = _genesis_release(tmp_path, now=now)
    live = _GenesisOpener(fail_deploy=True)
    result = execute_released_genesis(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=live,
        operation=_operation("genesis-executor-partial"),
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_GENESIS_EXECUTOR_MUTATION_FAILED"
    assert result["summary"]["compose_update_succeeded"] is True
    assert result["summary"]["deployment_requested"] is False
    assert result["summary"]["live_mutation_performed"] is True
    assert result["summary"]["automatic_rollback_complete"] is True
    assert result["summary"]["next_phase"] == "failure-compensated"
    assert result["automatic_rollback"]["status"] == "pass"
    assert live.current_compose == _standby_compose()
    assert live.status == "exited"

    with pytest.raises(MotherDeploymentGenesisExecutorError) as caught:
        execute_released_genesis(
            paths,
            private_state,
            release_path,
            acknowledged_release_sha256=release_digest,
            opener=_GenesisOpener(),
            operation=_operation("genesis-executor-replay"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_GENESIS_RELEASE_ALREADY_CONSUMED"


def test_genesis_explicit_rollback_restores_standby_and_preserves_identity(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, private_state, _, _, _, release_path, release_digest, _ = _genesis_release(tmp_path, now=now)
    live = _GenesisOpener()
    execution = execute_released_genesis(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=live,
        operation=_operation("genesis-explicit-rollback-execution"),
    )
    assert execution["status"] == "pass"
    assert live.status == "running:healthy"
    execution_path = Path(execution["result_artifact"]["path"])
    execution_sha = execution["result_artifact"]["sha256"]

    inspected = inspect_genesis_mutation_rollback(
        paths,
        private_state,
        execution_path,
        acknowledged_execution_sha256=execution_sha,
    )
    assert inspected["clean"] is True
    assert inspected["rollback_boundary_open"] is True
    assert inspected["rollback_operation_count"] == 2
    assert inspected["persistent_volume_cleanup_performed"] is False

    rolled_back = execute_genesis_mutation_rollback(
        paths,
        private_state,
        execution_path,
        acknowledged_execution_sha256=execution_sha,
        opener=live,
        max_wait_seconds=1.0,
        poll_interval_seconds=0.0,
        operation=_operation("genesis-explicit-rollback"),
    )
    assert rolled_back["status"] == "pass"
    assert rolled_back["summary"]["complete"] is True
    assert rolled_back["summary"]["service_stopped"] is True
    assert rolled_back["summary"]["standby_compose_restored"] is True
    assert rolled_back["summary"]["identity_keys_preserved"] is True
    assert rolled_back["summary"]["persistent_volume_cleanup_performed"] is False
    assert live.status == "exited"
    assert live.current_compose == _standby_compose()

    retried = execute_genesis_mutation_rollback(
        paths,
        private_state,
        execution_path,
        acknowledged_execution_sha256=execution_sha,
        opener=live,
        max_wait_seconds=1.0,
        poll_interval_seconds=0.0,
        operation=_operation("genesis-explicit-rollback-retry"),
    )
    assert retried["status"] == "pass"
    assert retried["summary"]["complete"] is True
    assert retried["rollback_receipts"][0]["status"] == "already-restored"
    assert retried["summary"]["persistent_volume_cleanup_performed"] is False

    verified = verify_genesis_mutation_rollback(
        paths,
        private_state,
        Path(rolled_back["result_artifact"]["path"]),
        opener=live,
    )
    assert verified["clean"] is True
    assert verified["checks"]["service_stopped"] is True
    assert verified["checks"]["standby_compose_restored"] is True
    assert verified["checks"]["identity_keys_preserved"] is True
    assert verified["checks"]["persistent_volume_cleanup_performed"] is False


def test_genesis_release_and_dry_run_cli(tmp_path: Path, capsys) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    paths, _, transaction_path, transaction_digest, _ = _genesis_transaction(tmp_path, now=now)
    runtime_root = paths.root.parent
    code = mother_deploy.main([
        "release-genesis",
        "--runtime-state-root", str(runtime_root),
        "--transaction", str(transaction_path),
        "--acknowledge-genesis-transaction-sha256", transaction_digest,
        "--node", "mainneta-super1",
        "--write-release",
    ])
    assert code == 0
    release = json.loads(capsys.readouterr().out)
    release_path = release["release_artifact"]["path"]
    release_digest = release["release_artifact"]["sha256"]

    code = mother_deploy.main([
        "verify-genesis-release",
        "--runtime-state-root", str(runtime_root),
        "--release", release_path,
        "--node", "mainneta-super1",
    ])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["clean"] is True

    code = mother_deploy.main([
        "apply-genesis",
        "--runtime-state-root", str(runtime_root),
        "--release", release_path,
        "--acknowledge-release-sha256", release_digest,
        "--node", "mainneta-super1",
    ])
    assert code == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["execute_requested"] is False
    assert inspected["live_execution_authorized"] is True
