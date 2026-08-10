from __future__ import annotations

from datetime import datetime, timezone
import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from tools import mother_deploy
from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_c2_replica_standby import (
    build_c2_replica_standby_release,
    execute_c2_replica_standby_release,
    inspect_c2_replica_standby_release,
    stage_c2_replica_standby_transaction,
    verify_c2_replica_standby,
    verify_c2_replica_standby_release,
    verify_c2_replica_standby_transaction,
    write_c2_replica_standby_release,
    write_c2_replica_standby_transaction,
)
from tools.mother.common.models import OperationIdentity
from tests.test_mother_deployment_c2_standby import _install_c2
from tests.test_mother_deployment_executor import TOKEN_C


def _operation(name: str) -> OperationIdentity:
    return OperationIdentity(
        operation_id=name,
        request_id=f"{name}-request",
        network="mainnet",
        operation_kind="MOTHER-OP-ADD-NODE",
    )


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_json(path: Path, document: dict) -> tuple[Path, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json(document)
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def _with_digest(document: dict, field: str) -> dict:
    result = dict(document)
    result[field] = hashlib.sha256(canonical_json(result)).hexdigest()
    return result


def _genesis_birth_lineage(paths, predecessor_binding: dict) -> Path:
    genesis = {
        "config": {"chainId": 42424240, "qbft": {"blockperiodseconds": 2, "epochlength": 30000}},
        "alloc": {},
        "extraData": "0x00",
        "mixHash": "0x" + "12" * 32,
        "nonce": "0x0",
        "timestamp": "0x0",
    }
    genesis_sha = hashlib.sha256(canonical_json(genesis)).hexdigest()
    tx = _with_digest(
        {
            "kind": "main_computer.mother.deployment_genesis_transaction.v1",
            "schema_version": 1,
            "created_at": "2026-08-10T11:59:00Z",
            "network": "mainnet",
            "mother_binding": predecessor_binding,
            "genesis": {
                "chain_id": 42424240,
                "initial_node": "mainneta-super1",
                "canonical_json": genesis,
                "canonical_json_sha256": genesis_sha,
            },
            "summary": {"transaction_valid": True},
        },
        "genesis_transaction_sha256",
    )
    tx_path, tx_byte_sha = _write_json(
        paths.root / "actions" / "deployment-genesis-transactions" / "genesis.json",
        tx,
    )

    release = _with_digest(
        {
            "kind": "main_computer.mother.deployment_genesis_release.v1",
            "schema_version": 1,
            "created_at": "2026-08-10T11:59:10Z",
            "network": "mainnet",
            "mother_binding": predecessor_binding,
            "genesis_transaction": {
                "locator": tx_path.relative_to(paths.root).as_posix(),
                "sha256": tx["genesis_transaction_sha256"],
                "byte_sha256": tx_byte_sha,
            },
        },
        "genesis_release_sha256",
    )
    release_path, release_byte_sha = _write_json(
        paths.root / "actions" / "deployment-genesis-releases" / "release.json",
        release,
    )

    execution = {
        "kind": "main_computer.mother.deployment_genesis_execution_result.v1",
        "schema_version": 1,
        "status": "pass",
        "network": "mainnet",
        "mother_binding": predecessor_binding,
        "release": {
            "locator": release_path.relative_to(paths.root).as_posix(),
            "sha256": release["genesis_release_sha256"],
        },
        "summary": {"complete": True},
    }
    execution_path, execution_byte_sha = _write_json(
        paths.root / "actions" / "deployment-genesis-executions" / "execution.json",
        execution,
    )

    birth_release = _with_digest(
        {
            "kind": "main_computer.mother.deployment_genesis_birth_release.v1",
            "schema_version": 1,
            "created_at": "2026-08-10T11:59:20Z",
            "network": "mainnet",
            "mother_binding": predecessor_binding,
            "genesis_execution": {
                "locator": execution_path.relative_to(paths.root).as_posix(),
                "sha256": execution_byte_sha,
            },
        },
        "release_sha256",
    )
    birth_release_path, _ = _write_json(
        paths.root / "actions" / "deployment-genesis-birth-releases" / "birth-release.json",
        birth_release,
    )

    birth_evidence = {
        "kind": "main_computer.mother.deployment_genesis_birth_evidence.v1",
        "schema_version": 1,
        "started_at": "2026-08-10T11:59:30Z",
        "completed_at": "2026-08-10T11:59:40Z",
        "status": "pass",
        "network": "mainnet",
        "nodes": ["mainneta-super1"],
        "initial_node": "mainneta-super1",
        "controller_id": "coolify-a",
        "service_uuid": "svc-mainneta-super1",
        "mother_binding": predecessor_binding,
        "genesis_sha256": genesis_sha,
        "genesis_execution_sha256": execution_byte_sha,
        "release": {
            "locator": birth_release_path.relative_to(paths.root).as_posix(),
            "sha256": birth_release["release_sha256"],
        },
        "proof": {
            "chain_id": 42424240,
            "service_status": "running:healthy",
            "hub_local_rpc_verified": True,
            "hub_local_rpc_url": "http://mainneta-super1:8545",
            "hub_service": "mother-super-node-hub",
            "hub_internal_port": 8790,
            "validator_set": ["0x" + "11" * 20],
        },
        "summary": {
            "clean": True,
            "initial_chain_proven": True,
            "next_phase": "stage-soft-replica-configuration",
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "soft_replica_untouched": True,
        },
        "policy": {"secrets_in_output": False},
        "mutation_receipts": [],
        "precondition_receipts": [],
        "health_observations": [],
        "failure": None,
    }
    birth_path, _ = _write_json(
        paths.root / "evidence" / "deployment-genesis-birth" / "birth.json",
        birth_evidence,
    )
    return birth_path


def _identity_and_rollback(paths, private_state) -> tuple[Path, Path]:
    binding = {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }
    profile_sha = "44" * 32
    receipts = [
        {
            "node": "mainnetc-super2",
            "controller_id": "coolify-c",
            "endpoint": "/api/v1/services/svc-mainnetc-super2/envs",
            "environment_key": "MC_MOTHER_VALIDATOR_PRIVATE_KEY",
            "environment_variable_uuid": "env-validator",
            "value_sha256": hashlib.sha256(b"validator-secret").hexdigest(),
            "status": "succeeded",
            "live_write_acknowledged": True,
            "postcondition": {"commitment_verified": True, "key_unique": True},
        },
        {
            "node": "mainnetc-super2",
            "controller_id": "coolify-c",
            "endpoint": "/api/v1/services/svc-mainnetc-super2/envs",
            "environment_key": "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY",
            "environment_variable_uuid": "env-hub",
            "value_sha256": hashlib.sha256(b"hub-secret").hexdigest(),
            "status": "succeeded",
            "live_write_acknowledged": True,
            "postcondition": {"commitment_verified": True, "key_unique": True},
        },
    ]
    execution = {
        "kind": "main_computer.mother.deployment_identity_execution_result.v1",
        "schema_version": 1,
        "started_at": "2026-08-10T12:00:30Z",
        "completed_at": "2026-08-10T12:00:31Z",
        "status": "pass",
        "network": "mainnet",
        "node": "mainnetc-super2",
        "nodes": ["mainnetc-super2"],
        "controller_id": "coolify-c",
        "mother_binding": binding,
        "identity_profile_sha256": profile_sha,
        "mutation_receipts": receipts,
        "summary": {"complete": True, "planned_mutation_count": 2},
        "service_mutation_count": 0,
        "chain_mutation_count": 0,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "validator_vote_performed": False,
        "policy": {"secrets_in_output": False, "service_deploy_or_start_performed": False},
    }
    execution_path, execution_sha = _write_json(
        paths.root / "actions" / "deployment-identity-executions" / "identity-current.json",
        execution,
    )

    rollback = {
        "kind": "main_computer.mother.deployment_identity_rollback_verification.v1",
        "schema_version": 1,
        "observed_at": "2026-08-10T12:00:00Z",
        "clean": True,
        "mother_binding": binding,
        "network": "mainnet",
        "nodes": ["mainnetc-super2"],
        "staged_scope": "install-reserved-identity",
        "identity_profile_sha256": profile_sha,
        "rollback_result": {"locator": "actions/deployment-identity-rollbacks/old.json", "sha256": "55" * 32},
        "rolled_back_execution": {"sha256": "66" * 32},
        "checks": [
            {"node": "mainnetc-super2", "controller_id": "coolify-c", "environment_key": "MC_MOTHER_VALIDATOR_PRIVATE_KEY", "environment_variable_uuid": "env-old-a", "absent": True},
            {"node": "mainnetc-super2", "controller_id": "coolify-c", "environment_key": "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY", "environment_variable_uuid": "env-old-b", "absent": True},
        ],
        "summary": {
            "expected_absent_count": 2,
            "absent_count": 2,
            "network_access_performed": True,
            "live_mutation_performed": False,
            "clean": True,
        },
    }
    rollback = _with_digest(rollback, "identity_rollback_verification_sha256")
    rollback_path, _ = _write_json(
        paths.root / "evidence" / "deployment-identity-rollbacks" / "rollback.json",
        rollback,
    )

    identity_evidence = {
        "kind": "main_computer.mother.deployment_c2_standby_identity_verification.v1",
        "schema_version": 1,
        "observed_at": "2026-08-10T12:00:32Z",
        "network": "mainnet",
        "node": "mainnetc-super2",
        "nodes": ["mainnetc-super2"],
        "controller_id": "coolify-c",
        "mother_binding": binding,
        "execution": {
            "path": str(execution_path),
            "file_sha256": execution_sha,
            "completed_at": execution["completed_at"],
            "identity_profile_sha256": profile_sha,
        },
        "endpoint": {
            "ok": True,
            "path": "/api/v1/services/svc-mainnetc-super2/envs",
            "status": 200,
            "response_sha256": "77" * 32,
        },
        "identity_results": [
            {"environment_key": "MC_MOTHER_VALIDATOR_PRIVATE_KEY", "expected_environment_variable_uuid": "env-validator", "observed_environment_variable_uuid": "env-validator", "expected_value_sha256": hashlib.sha256(b"validator-secret").hexdigest(), "observed_value_sha256": hashlib.sha256(b"validator-secret").hexdigest(), "matches": 1, "uuid_verified": True, "commitment_verified": True},
            {"environment_key": "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY", "expected_environment_variable_uuid": "env-hub", "observed_environment_variable_uuid": "env-hub", "expected_value_sha256": hashlib.sha256(b"hub-secret").hexdigest(), "observed_value_sha256": hashlib.sha256(b"hub-secret").hexdigest(), "matches": 1, "uuid_verified": True, "commitment_verified": True},
        ],
        "blockers": [],
        "policy": {
            "allowed_http_method": "GET",
            "live_mutation_performed": False,
            "network_access_performed": True,
            "private_state_updated": False,
            "secrets_in_output": False,
            "service_deploy_or_start_performed": False,
            "replica_sync_performed": False,
            "validator_activation_performed": False,
            "validator_vote_performed": False,
        },
        "identity_mutation_count": 0,
        "service_mutation_count": 0,
        "chain_mutation_count": 0,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "validator_vote_performed": False,
        "summary": {
            "clean": True,
            "verified_identity_key_count": 2,
            "identity_mutation_count": 2,
            "service_mutation_count": 0,
            "chain_mutation_count": 0,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_performed": False,
            "next_phase": "prove-identity-rollback-cycle-before-genesis",
        },
        "next_phase": "prove-identity-rollback-cycle-before-genesis",
    }
    identity_evidence_path, _ = _write_json(
        paths.root / "evidence" / "deployment-c2-standby-identity" / "identity-evidence.json",
        identity_evidence,
    )
    return identity_evidence_path, rollback_path


class _Response:
    def __init__(self, payload, status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = {"Content-Type": "application/json"}

    def getcode(self) -> int:
        return self.status

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]

    def close(self) -> None:
        return None


class _ReplicaStandbyOpener:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.compose_raw = ""

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append({"method": method, "path": path, "body": body})
        assert parsed.hostname == "coolify-c.invalid"
        assert request.headers.get("Authorization") == f"Bearer {TOKEN_C}"
        if method == "GET" and path == "/api/v1/services/svc-mainnetc-super2/envs":
            return _Response(
                {
                    "envs": [
                        {"uuid": "env-validator", "key": "MC_MOTHER_VALIDATOR_PRIVATE_KEY", "value": "validator-secret"},
                        {"uuid": "env-hub", "key": "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY", "value": "hub-secret"},
                    ]
                }
            )
        if method == "GET" and path == "/api/v1/services/svc-mainnetc-super2":
            record = {"uuid": "svc-mainnetc-super2", "name": "mainnetc-super2", "status": "exited"}
            if self.compose_raw:
                record["docker_compose_raw"] = self.compose_raw
            return _Response({"service": record})
        if method == "PATCH" and path == "/api/v1/services/svc-mainnetc-super2":
            self.compose_raw = base64.b64decode(body["docker_compose_raw"]).decode("utf-8")
            return _Response({"uuid": "svc-mainnetc-super2", "name": "mainnetc-super2", "docker_compose_raw": self.compose_raw}, status=200)
        raise AssertionError(f"unexpected {method} {path}")


def test_c2_replica_standby_patches_compose_only_after_identity_rollback_cycle(tmp_path: Path, monkeypatch) -> None:
    paths, private_state, c2_evidence = _install_c2(tmp_path, monkeypatch)
    c2_doc = json.loads(Path(c2_evidence).read_text(encoding="utf-8"))
    birth_evidence = _genesis_birth_lineage(paths, c2_doc["predecessor_binding"])
    identity_evidence, rollback_evidence = _identity_and_rollback(paths, private_state)
    now = datetime(2026, 8, 10, 12, 1, tzinfo=timezone.utc)

    tx = stage_c2_replica_standby_transaction(
        paths,
        private_state,
        c2_state_extension_evidence=c2_evidence,
        identity_evidence=identity_evidence,
        identity_rollback_evidence=rollback_evidence,
        genesis_birth_evidence=birth_evidence,
        created_at=_stamp(now),
        now=now,
        operation=_operation("stage-c2-replica-standby"),
    )
    assert tx["node"] == "mainnetc-super2"
    assert tx["mutation"]["method"] == "PATCH"
    assert tx["summary"]["mutation_count"] == 1
    assert tx["authority"]["replica_sync_authorized"] is False
    assert tx["authority"]["validator_vote_authorized"] is False
    assert tx["policy"]["service_deploy_or_start_performed"] is False
    assert tx["genesis_birth_evidence"]["binding_source"] == "c2-predecessor-preserved"
    assert "deployment-identity-rollbacks/rollback.json" in tx["identity_rollback_cycle"]["locator"]
    compose = tx["replica"]["compose"]["canonical_text"]
    assert "main_computer.mother.replica-sync: blocked" in compose
    assert "main_computer.mother.validator-activation: blocked" in compose
    assert "--genesis-file=/config/genesis.json" in compose
    assert "--bootnodes=enode://" in compose
    assert '"30303:30303/tcp"' in compose
    assert "8545:8545" not in compose
    assert "validator-secret" not in json.dumps(tx)
    assert "hub-secret" not in json.dumps(tx)

    tx_path, tx_sha = write_c2_replica_standby_transaction(paths, tx, operation=_operation("write-c2-replica-tx"))
    verified_tx = verify_c2_replica_standby_transaction(
        paths,
        private_state,
        tx_path,
        now=now,
        operation=_operation("verify-c2-replica-tx"),
    )
    assert verified_tx["clean"] is True
    assert verified_tx["c2_replica_standby_transaction_sha256"] == tx_sha
    assert verified_tx["service_mutation_count"] == 1
    assert verified_tx["replica_start_authorized"] is False

    release = build_c2_replica_standby_release(
        paths,
        private_state,
        tx_path,
        acknowledged_transaction_sha256=tx_sha,
        created_at=_stamp(now),
        now=now,
        operation=_operation("release-c2-replica"),
    )
    release_path, release_sha = write_c2_replica_standby_release(paths, release, operation=_operation("write-c2-replica-release"))
    verified_release = verify_c2_replica_standby_release(
        paths,
        private_state,
        release_path,
        now=now,
        operation=_operation("verify-c2-replica-release"),
    )
    assert verified_release["transaction_apply_authorized"] is True
    assert verified_release["replica_sync_authorized"] is False
    inspection = inspect_c2_replica_standby_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        now=now,
        operation=_operation("inspect-c2-replica"),
    )
    assert inspection["release_already_claimed"] is False
    assert inspection["live_mutation_performed"] is False

    opener = _ReplicaStandbyOpener()
    result = execute_c2_replica_standby_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        now=now,
        opener=opener,
        operation=_operation("execute-c2-replica"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["planned_mutation_count"] == 1
    assert result["summary"]["succeeded_mutation_count"] == 1
    assert result["policy"]["service_deploy_or_start_performed"] is False
    assert result["policy"]["replica_sync_performed"] is False
    assert result["validator_vote_performed"] is False
    assert [item["method"] for item in opener.requests] == ["GET", "GET", "PATCH"]

    exact_compose_sha = hashlib.sha256(opener.compose_raw.encode("utf-8")).hexdigest()
    opener.compose_raw = yaml.safe_dump(yaml.safe_load(opener.compose_raw), sort_keys=False)
    assert hashlib.sha256(opener.compose_raw.encode("utf-8")).hexdigest() != exact_compose_sha

    evidence = verify_c2_replica_standby(
        paths,
        private_state,
        Path(result["result_artifact"]["path"]),
        opener=opener,
        observed_at=_stamp(now),
        write_evidence=True,
        operation=_operation("verify-c2-replica"),
    )
    assert evidence["summary"]["clean"] is True
    assert evidence["summary"]["standby_compose_verified"] is True
    assert evidence["summary"]["standby_compose_exact_match"] is False
    assert evidence["summary"]["coolify_normalized_compose_accepted"] is True
    assert evidence["summary"]["service_deploy_or_start_performed"] is False
    assert evidence["summary"]["replica_sync_performed"] is False
    assert evidence["summary"]["validator_vote_performed"] is False
    assert evidence["next_phase"] == "stage-c2-replica-sync"
    assert Path(evidence["evidence"]["path"]).exists()


def test_cli_registers_c2_replica_standby_commands() -> None:
    parser = mother_deploy._parser()
    commands = {
        "stage-c2-replica-standby": [
            "--c2-state-extension-evidence", "c2.json",
            "--identity-evidence", "identity.json",
            "--identity-rollback-evidence", "rollback.json",
            "--genesis-birth-evidence", "birth.json",
        ],
        "verify-c2-replica-standby-transaction": ["--transaction", "tx.json"],
        "release-c2-replica-standby": [
            "--transaction", "tx.json",
            "--acknowledge-c2-replica-standby-transaction-sha256", "a" * 64,
        ],
        "verify-c2-replica-standby-release": ["--release", "rel.json"],
        "apply-c2-replica-standby": ["--release", "rel.json", "--acknowledge-release-sha256", "b" * 64],
        "verify-c2-replica-standby": ["--execution", "exec.json"],
    }
    for command, extra in commands.items():
        args = parser.parse_args([command, "--node", "mainnetc-super2", *extra])
        assert args.command == command
