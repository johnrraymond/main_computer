from __future__ import annotations

from datetime import datetime, timezone
import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import pytest
import yaml

from tools import mother_deploy
from tools.mother.common.deployment_c2_replica_standby import (
    build_c2_replica_standby_release,
    execute_c2_replica_standby_release,
    stage_c2_replica_standby_transaction,
    verify_c2_replica_standby,
    verify_c2_replica_standby_transaction,
    write_c2_replica_standby_release,
    write_c2_replica_standby_transaction,
)
from tools.mother.common.deployment_c2_replica_sync import (
    build_c2_replica_sync_release,
    execute_c2_replica_sync_release,
    inspect_c2_replica_sync_release,
    verify_c2_replica_sync_evidence,
    verify_c2_replica_sync_release,
    write_c2_replica_sync_release,
)
from tests.test_mother_deployment_c2_replica_standby import (
    _ReplicaStandbyOpener,
    _genesis_birth_lineage,
    _identity_and_rollback,
    _operation,
    _stamp,
)
from tests.test_mother_deployment_c2_standby import _install_c2
from tests.test_mother_deployment_executor import TOKEN_A, TOKEN_C


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


class _C2ReplicaSyncOpener:
    def __init__(self, *, service_uuid: str, standby_compose: str, a_service_uuid: str) -> None:
        self.service_uuid = service_uuid
        self.a_service_uuid = a_service_uuid
        self.compose_raw = standby_compose
        self.status = "exited"
        self.requests: list[dict] = []

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        path = parsed.path
        method = request.get_method()
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append({"method": method, "host": host, "path": path, "query": parsed.query, "body": body})
        assert timeout > 0

        if host == "coolify-a.invalid":
            assert request.headers.get("Authorization") == f"Bearer {TOKEN_A}"
            assert method == "GET"
            assert path == "/api/v1/services"
            return _Response({"services": [{"uuid": self.a_service_uuid, "name": "mainneta-super1", "status": "running:healthy"}]})

        assert host == "coolify-c.invalid"
        assert request.headers.get("Authorization") == f"Bearer {TOKEN_C}"

        if method == "GET" and path == f"/api/v1/services/{self.service_uuid}":
            return _Response(
                {
                    "service": {
                        "uuid": self.service_uuid,
                        "name": "mainnetc-super2",
                        "status": self.status,
                        "docker_compose_raw": self.compose_raw,
                    }
                }
            )

        if method == "PATCH" and path == f"/api/v1/services/{self.service_uuid}":
            self.compose_raw = base64.b64decode(body["docker_compose_raw"]).decode("utf-8")
            return _Response({"uuid": self.service_uuid, "name": "mainnetc-super2", "docker_compose_raw": self.compose_raw}, status=200)

        if method == "GET" and path == "/api/v1/deploy":
            query = parse_qs(parsed.query)
            assert query.get("uuid") == [self.service_uuid]
            assert query.get("force") == ["true"]
            self.status = "running:healthy"
            return _Response({"uuid": self.service_uuid, "status": "accepted"}, status=200)

        if method == "GET" and path == "/api/v1/services":
            return _Response({"services": [{"uuid": self.service_uuid, "name": "mainnetc-super2", "status": self.status}]})

        raise AssertionError(f"unexpected {method} {host} {path}?{parsed.query}")


def _standby_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
        operation=_operation("stage-c2-replica-standby-for-sync"),
    )
    tx_path, tx_sha = write_c2_replica_standby_transaction(paths, tx, operation=_operation("write-c2-replica-standby-for-sync"))
    assert verify_c2_replica_standby_transaction(paths, private_state, tx_path, now=now, operation=_operation("verify-c2-replica-standby-for-sync"))["clean"] is True
    release = build_c2_replica_standby_release(
        paths,
        private_state,
        tx_path,
        acknowledged_transaction_sha256=tx_sha,
        created_at=_stamp(now),
        now=now,
        operation=_operation("release-c2-replica-standby-for-sync"),
    )
    release_path, release_sha = write_c2_replica_standby_release(paths, release, operation=_operation("write-c2-replica-standby-release-for-sync"))
    opener = _ReplicaStandbyOpener()
    result = execute_c2_replica_standby_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=999999999,
        now=now,
        opener=opener,
        operation=_operation("execute-c2-replica-standby-for-sync"),
    )
    assert result["status"] == "pass"
    opener.compose_raw = yaml.safe_dump(yaml.safe_load(opener.compose_raw), sort_keys=False)
    evidence = verify_c2_replica_standby(
        paths,
        private_state,
        Path(result["result_artifact"]["path"]),
        opener=opener,
        observed_at=_stamp(now),
        write_evidence=True,
        operation=_operation("verify-c2-replica-standby-for-sync"),
    )
    assert evidence["summary"]["clean"] is True
    birth_doc = json.loads(Path(birth_evidence).read_text(encoding="utf-8"))
    return paths, private_state, evidence, opener.compose_raw, birth_doc["service_uuid"], now


def test_c2_replica_sync_release_starts_non_validator_replica_without_vote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, private_state, standby_evidence, standby_compose, a_service_uuid, now = _standby_gate(tmp_path, monkeypatch)

    release = build_c2_replica_sync_release(
        paths,
        private_state,
        Path(standby_evidence["evidence"]["path"]),
        acknowledged_c2_replica_standby_evidence_sha256=standby_evidence["evidence"]["sha256"],
        created_at=_stamp(now),
        now=now,
    )
    assert release["node"] == "mainnetc-super2"
    assert release["authority"]["replica_sync_authorized"] is True
    assert release["authority"]["validator_vote_authorized"] is False
    assert release["authority"]["validator_activation_authorized"] is False
    assert release["authority"]["routing_or_topology_publication_authorized"] is False
    assert release["summary"]["mutation_count"] == 2
    assert "mother-replica-sync-guardian" in release["proof_plan"]["proof_compose"]["canonical_text"]
    assert "qbft_proposeValidatorVote" not in release["proof_plan"]["proof_compose"]["canonical_text"]
    assert "8545:8545" not in release["proof_plan"]["proof_compose"]["canonical_text"]

    release_path, release_sha = write_c2_replica_sync_release(paths, release, operation=_operation("write-c2-replica-sync-release"))
    verified = verify_c2_replica_sync_release(paths, private_state, release_path, now=now)
    assert verified["clean"] is True
    assert verified["replica_sync_authorized"] is True
    assert verified["validator_vote_authorized"] is False

    inspection = inspect_c2_replica_sync_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=999999999,
        now=now,
    )
    assert inspection["execute_requested"] is False
    assert inspection["live_mutation_performed"] is False
    assert inspection["service_deploy_or_start_performed"] is False

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
        operation=_operation("execute-c2-replica-sync"),
    )
    assert result["status"] == "pass"
    assert result["summary"]["replica_synchronized"] is True
    assert result["summary"]["service_deploy_or_start_performed"] is True
    assert result["summary"]["validator_vote_authorized"] is False
    assert result["summary"]["validator_activation_authorized"] is False
    assert result["summary"]["routing_or_topology_publication_authorized"] is False
    assert result["validator_vote_performed"] is False
    assert [item["method"] for item in result["mutation_receipts"]] == ["PATCH", "GET"]

    verified_evidence = verify_c2_replica_sync_evidence(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        max_age_seconds=999999999,
    )
    assert verified_evidence["clean"] is True
    assert verified_evidence["replica_synchronized"] is True
    assert verified_evidence["validator_vote_authorized"] is False
    assert verified_evidence["next_phase"] == "stage-c2-validator-admission"


def test_cli_registers_c2_replica_sync_commands() -> None:
    parser = mother_deploy._parser()
    commands = {
        "release-c2-replica-sync": [
            "--standby-evidence", "standby.json",
            "--acknowledge-c2-replica-standby-evidence-sha256", "a" * 64,
        ],
        "verify-c2-replica-sync-release": ["--release", "release.json"],
        "apply-c2-replica-sync": ["--release", "release.json", "--acknowledge-release-sha256", "b" * 64],
        "verify-c2-replica-sync-evidence": ["--evidence", "evidence.json"],
    }
    for command, extra in commands.items():
        args = parser.parse_args([command, "--node", "mainnetc-super2", *extra])
        assert args.command == command
