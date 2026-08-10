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
from tools.mother.common.canonical import canonical_json
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
    _digest_without,
    build_c2_replica_sync_release,
    build_c2_replica_sync_resume_release,
    diagnose_c2_replica_sync_materialization,
    execute_c2_replica_sync_release,
    execute_c2_replica_sync_resume_release,
    inspect_c2_replica_sync_release,
    inspect_c2_replica_sync_resume_release,
    verify_c2_replica_sync_evidence,
    verify_c2_replica_sync_release,
    verify_c2_replica_sync_resume_release,
    write_c2_replica_sync_release,
    write_c2_replica_sync_resume_release,
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



class _C2ReplicaSyncNoMaterializationOpener(_C2ReplicaSyncOpener):
    def __init__(self, *, service_uuid: str, standby_compose: str, a_service_uuid: str, child_uuid: str = "gtctxib7h2jdh0pgl4gcvoww") -> None:
        super().__init__(service_uuid=service_uuid, standby_compose=standby_compose, a_service_uuid=a_service_uuid)
        self.child_uuid = child_uuid

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        path = parsed.path
        method = request.get_method()
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append({"method": method, "host": host, "path": path, "query": parsed.query, "body": body})
        assert timeout > 0

        if host == "coolify-a.invalid":
            assert method == "GET"
            assert path == "/api/v1/services"
            return _Response({"services": [{"uuid": self.a_service_uuid, "name": "mainneta-super1", "status": "running:healthy"}]})

        assert host == "coolify-c.invalid"

        if method == "GET" and path == f"/api/v1/services/{self.service_uuid}":
            return _Response(
                {
                    "service": {
                        "uuid": self.service_uuid,
                        "name": "mainnetc-super2",
                        "status": self.status,
                        "docker_compose_raw": self.compose_raw,
                        "applications": [
                            {
                                "uuid": self.child_uuid,
                                "id": 56,
                                "name": "mainnetc-super2",
                                "service_id": 56,
                                "status": self.status,
                            }
                        ],
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
            self.status = "starting:unknown"
            return _Response({"uuid": self.service_uuid, "status": "accepted"}, status=200)

        if method == "GET" and path == "/api/v1/services":
            return _Response({"services": [{"uuid": self.service_uuid, "name": "mainnetc-super2", "status": self.status}]})

        if method == "GET" and path == "/api/v1/resources":
            return _Response({"resources": [{"uuid": self.service_uuid, "name": "mainnetc-super2", "status": self.status}]})

        if method == "GET" and path == "/api/v1/applications":
            return _Response({"applications": [{"uuid": self.child_uuid, "name": "mainnetc-super2", "service_id": 56, "status": self.status}]})

        if method == "GET" and (path == "/api/v1/deployments" or path == f"/api/v1/services/{self.service_uuid}/deployments"):
            return _Response({"deployments": []})

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
    assert "30303:30303/tcp" not in release["proof_plan"]["proof_compose"]["canonical_text"]
    assert "30303:30303/udp" not in release["proof_plan"]["proof_compose"]["canonical_text"]
    assert "--p2p-port=30303" in release["proof_plan"]["proof_compose"]["canonical_text"]
    assert release["proof_plan"]["proof_compose"]["host_p2p_mapping_present"] is False

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



def test_c2_replica_sync_materialization_diagnosis_authorizes_safe_retry_after_no_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, private_state, standby_evidence, standby_compose, a_service_uuid, now = _standby_gate(tmp_path, monkeypatch)

    release = build_c2_replica_sync_release(
        paths,
        private_state,
        Path(standby_evidence["evidence"]["path"]),
        acknowledged_c2_replica_standby_evidence_sha256=standby_evidence["evidence"]["sha256"],
        created_at=_stamp(now),
        now=now,
    )
    release_path, release_sha = write_c2_replica_sync_release(paths, release, operation=_operation("write-c2-replica-sync-release-no-materialization"))
    opener = _C2ReplicaSyncNoMaterializationOpener(
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
        operation=_operation("execute-c2-replica-sync-no-materialization"),
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_C2_REPLICA_SYNC_NOT_HEALTHY"
    assert result["chain_mutation_count"] == 0
    assert result["validator_vote_performed"] is False

    diagnostic = diagnose_c2_replica_sync_materialization(
        paths,
        private_state,
        Path(result["evidence"]["path"]),
        failed_evidence_max_age_seconds=999999999,
        operator_confirm_no_docker_materialization=True,
        opener=opener,
        now=None,
        write_evidence=True,
        operation=_operation("diagnose-c2-replica-sync-no-materialization"),
    )
    assert diagnostic["summary"]["classification"] == "no-docker-materialization"
    assert diagnostic["summary"]["retry_authorized"] is True
    assert diagnostic["summary"]["sync_proof_compose_current"] is True
    assert diagnostic["summary"]["chain_mutation_count"] == 0
    assert diagnostic["summary"]["validator_vote_performed"] is False
    assert diagnostic["policy"]["allowed_http_methods"] == ["GET"]
    assert diagnostic["policy"]["live_mutation_performed"] is False
    assert diagnostic["next_phase"] == "mint-fresh-c2-replica-sync-release"
    assert Path(diagnostic["evidence"]["path"]).exists()




def test_c2_replica_sync_resume_deploys_without_repatching_after_materialization_diagnostic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, private_state, standby_evidence, standby_compose, a_service_uuid, now = _standby_gate(tmp_path, monkeypatch)

    release = build_c2_replica_sync_release(
        paths,
        private_state,
        Path(standby_evidence["evidence"]["path"]),
        acknowledged_c2_replica_standby_evidence_sha256=standby_evidence["evidence"]["sha256"],
        created_at=_stamp(now),
        now=now,
    )
    release_path, release_sha = write_c2_replica_sync_release(paths, release, operation=_operation("write-c2-replica-sync-release-for-resume"))
    opener = _C2ReplicaSyncNoMaterializationOpener(
        service_uuid=standby_evidence["service_uuid"],
        standby_compose=standby_compose,
        a_service_uuid=a_service_uuid,
    )

    first = execute_c2_replica_sync_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=999999999,
        now=now,
        opener=opener,
        max_wait_seconds=1,
        poll_interval_seconds=0,
        operation=_operation("execute-c2-replica-sync-before-resume"),
    )
    assert first["status"] == "failed"
    assert first["failure"]["code"] == "MOTHER_DEPLOY_C2_REPLICA_SYNC_NOT_HEALTHY"
    assert first["service_mutation_count"] == 2
    assert [item["method"] for item in first["mutation_receipts"]] == ["PATCH", "GET"]

    diagnostic = diagnose_c2_replica_sync_materialization(
        paths,
        private_state,
        Path(first["evidence"]["path"]),
        failed_evidence_max_age_seconds=999999999,
        operator_confirm_no_docker_materialization=True,
        opener=opener,
        now=None,
        write_evidence=True,
        operation=_operation("diagnose-c2-replica-sync-before-resume"),
    )
    assert diagnostic["summary"]["retry_authorized"] is True
    assert diagnostic["summary"]["sync_proof_compose_current"] is True

    resume_release = build_c2_replica_sync_resume_release(
        paths,
        private_state,
        Path(diagnostic["evidence"]["path"]),
        acknowledged_c2_replica_sync_diagnostic_sha256=diagnostic["evidence"]["sha256"],
    )
    assert resume_release["summary"]["mutation_count"] == 1
    assert resume_release["authority"]["compose_patch_authorized"] is False
    assert resume_release["policy"]["allowed_http_methods"] == ["GET"]

    resume_path, resume_sha = write_c2_replica_sync_resume_release(paths, resume_release, operation=_operation("write-c2-replica-sync-resume-release"))
    verified = verify_c2_replica_sync_resume_release(paths, private_state, resume_path, max_age_seconds=999999999)
    assert verified["clean"] is True
    assert verified["compose_patch_authorized"] is False
    inspection = inspect_c2_replica_sync_resume_release(
        paths,
        private_state,
        resume_path,
        acknowledged_release_sha256=resume_sha,
        max_age_seconds=999999999,
    )
    assert inspection["execute_requested"] is False
    assert inspection["release_already_claimed"] is False
    assert inspection["compose_patch_performed"] is False

    resume_opener = _C2ReplicaSyncOpener(
        service_uuid=standby_evidence["service_uuid"],
        standby_compose=opener.compose_raw,
        a_service_uuid=a_service_uuid,
    )
    resumed = execute_c2_replica_sync_resume_release(
        paths,
        private_state,
        resume_path,
        acknowledged_release_sha256=resume_sha,
        max_age_seconds=999999999,
        opener=resume_opener,
        max_wait_seconds=1,
        poll_interval_seconds=0,
        operation=_operation("execute-c2-replica-sync-resume"),
    )
    assert resumed["status"] == "pass"
    assert resumed["summary"]["resume_from_applied_sync_proof_compose"] is True
    assert resumed["summary"]["compose_patch_performed"] is False
    assert resumed["summary"]["replica_synchronized"] is True
    assert [item["method"] for item in resumed["mutation_receipts"]] == ["GET"]
    assert all(request["method"] != "PATCH" for request in resume_opener.requests)
    assert resumed["service_mutation_count"] == 1
    assert resumed["validator_vote_performed"] is False
    assert verify_c2_replica_sync_evidence(
        paths,
        private_state,
        Path(resumed["evidence"]["path"]),
        max_age_seconds=999999999,
    )["clean"] is True



def test_c2_replica_sync_materialization_diagnosis_blocks_blind_retry_after_resume_ack_no_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, private_state, standby_evidence, standby_compose, a_service_uuid, now = _standby_gate(tmp_path, monkeypatch)

    release = build_c2_replica_sync_release(
        paths,
        private_state,
        Path(standby_evidence["evidence"]["path"]),
        acknowledged_c2_replica_standby_evidence_sha256=standby_evidence["evidence"]["sha256"],
        created_at=_stamp(now),
        now=now,
    )
    release_path, release_sha = write_c2_replica_sync_release(paths, release, operation=_operation("write-c2-replica-sync-release-for-v51-diagnostic"))
    opener = _C2ReplicaSyncNoMaterializationOpener(
        service_uuid=standby_evidence["service_uuid"],
        standby_compose=standby_compose,
        a_service_uuid=a_service_uuid,
    )

    first = execute_c2_replica_sync_release(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_sha,
        max_age_seconds=999999999,
        now=now,
        opener=opener,
        max_wait_seconds=1,
        poll_interval_seconds=0,
        operation=_operation("execute-c2-replica-sync-before-v51-diagnostic"),
    )
    assert first["status"] == "failed"

    diagnostic = diagnose_c2_replica_sync_materialization(
        paths,
        private_state,
        Path(first["evidence"]["path"]),
        failed_evidence_max_age_seconds=999999999,
        operator_confirm_no_docker_materialization=True,
        opener=opener,
        now=None,
        write_evidence=True,
        operation=_operation("diagnose-c2-replica-sync-before-v51-diagnostic"),
    )
    assert diagnostic["summary"]["retry_authorized"] is True

    resume_release = build_c2_replica_sync_resume_release(
        paths,
        private_state,
        Path(diagnostic["evidence"]["path"]),
        acknowledged_c2_replica_sync_diagnostic_sha256=diagnostic["evidence"]["sha256"],
    )
    resume_path, resume_sha = write_c2_replica_sync_resume_release(paths, resume_release, operation=_operation("write-c2-replica-sync-resume-release-for-v51-diagnostic"))

    resumed = execute_c2_replica_sync_resume_release(
        paths,
        private_state,
        resume_path,
        acknowledged_release_sha256=resume_sha,
        max_age_seconds=999999999,
        opener=opener,
        max_wait_seconds=1,
        poll_interval_seconds=0,
        operation=_operation("execute-c2-replica-sync-resume-for-v51-diagnostic"),
    )
    assert resumed["status"] == "failed"
    assert resumed["failure"]["code"] == "MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_NOT_HEALTHY"
    assert resumed["mutation_receipts"][0]["response"]["safe_text_available"] is True
    assert "accepted" in resumed["mutation_receipts"][0]["response"]["safe_text"]

    resume_diagnostic = diagnose_c2_replica_sync_materialization(
        paths,
        private_state,
        Path(resumed["evidence"]["path"]),
        failed_evidence_max_age_seconds=999999999,
        operator_confirm_no_docker_materialization=True,
        opener=opener,
        now=None,
        write_evidence=True,
        operation=_operation("diagnose-c2-replica-sync-resume-v51-no-docker"),
    )
    assert resume_diagnostic["summary"]["classification"] == "deploy-request-acknowledged-no-docker-materialization"
    assert resume_diagnostic["summary"]["previous_release_type"] == "resume-sync-release"
    assert resume_diagnostic["summary"]["previous_deploy_request_acknowledged"] is True
    assert resume_diagnostic["summary"]["deploy_response_safe_text_available"] is True
    assert resume_diagnostic["summary"]["queue_or_job_result_identified"] is False
    assert resume_diagnostic["summary"]["retry_authorized"] is False
    assert resume_diagnostic["deploy_materialization"]["http_200_is_not_materialization_proof"] is True
    assert resume_diagnostic["next_phase"] == "manual-review-required"
    assert resume_diagnostic["summary"]["validator_vote_performed"] is False





def _legacy_host_p2p_release(release: dict) -> dict:
    document = json.loads(json.dumps(release))
    compose = document["proof_plan"]["proof_compose"]["canonical_text"]
    marker = "    volumes:\n      - mother-config:/config:ro\n      - mother-data:/var/lib/besu\n"
    host_ports = '    ports:\n      - "30303:30303/tcp"\n      - "30303:30303/udp"\n'
    assert marker in compose
    legacy_compose = compose.replace(marker, host_ports + marker, 1)
    proof_bytes = legacy_compose.encode("utf-8")
    document["proof_plan"]["proof_compose"].update({
        "canonical_text": legacy_compose,
        "sha256": hashlib.sha256(proof_bytes).hexdigest(),
        "semantic_sha256": hashlib.sha256(canonical_json(yaml.safe_load(legacy_compose))).hexdigest(),
        "byte_length": len(proof_bytes),
        "host_p2p_mapping_present": True,
        "host_p2p_publication_authorized": True,
        "p2p_publication_mode": "legacy-host-bound-30303",
    })
    body = {
        "name": "mainnetc-super2",
        "docker_compose_raw": base64.b64encode(proof_bytes).decode("ascii"),
    }
    document["proof_plan"]["mutations"][0]["canonical_request_body"] = body
    document["proof_plan"]["mutations"][0]["body_sha256"] = hashlib.sha256(canonical_json(body)).hexdigest()
    document["c2_replica_sync_release_sha256"] = None
    document["c2_replica_sync_release_sha256"] = _digest_without(document, "c2_replica_sync_release_sha256")
    return document


def test_c2_replica_sync_resume_isolates_host_p2p_after_port_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, private_state, standby_evidence, standby_compose, a_service_uuid, now = _standby_gate(tmp_path, monkeypatch)

    release = build_c2_replica_sync_release(
        paths,
        private_state,
        Path(standby_evidence["evidence"]["path"]),
        acknowledged_c2_replica_standby_evidence_sha256=standby_evidence["evidence"]["sha256"],
        created_at=_stamp(now),
        now=now,
    )
    legacy_release = _legacy_host_p2p_release(release)
    legacy_path, legacy_sha = write_c2_replica_sync_release(paths, legacy_release, operation=_operation("write-c2-replica-sync-release-host-p2p-conflict"))

    opener = _C2ReplicaSyncNoMaterializationOpener(
        service_uuid=standby_evidence["service_uuid"],
        standby_compose=standby_compose,
        a_service_uuid=a_service_uuid,
    )
    opener.compose_raw = legacy_release["proof_plan"]["proof_compose"]["canonical_text"]
    assert "30303:30303/tcp" in opener.compose_raw

    claim_root = paths.root / "actions" / "deployment-c2-replica-sync-claims"
    claim_root.mkdir(parents=True, exist_ok=True)
    claim_path = claim_root / f"{legacy_sha}.json"
    claim_doc = {
        "kind": "main_computer.mother.deployment_c2_replica_sync_execution_claim.v1",
        "schema_version": 1,
        "claimed_at": _stamp(now),
        "release": {"locator": f"actions/deployment-c2-replica-sync-releases/{legacy_path.name}", "sha256": legacy_sha},
        "c2_replica_standby_evidence_sha256": standby_evidence["evidence"]["sha256"],
        "node": "mainnetc-super2",
        "requested_use_limit": 1,
        "operation_id": "test-host-p2p-conflict",
    }
    claim_path.write_bytes(canonical_json(claim_doc))

    evidence_root = paths.root / "evidence" / "deployment-c2-replica-sync"
    evidence_root.mkdir(parents=True, exist_ok=True)
    failed_evidence_path = evidence_root / "host-p2p-conflict.json"
    failed_doc = {
        "kind": "main_computer.mother.deployment_c2_replica_sync_evidence.v1",
        "schema_version": 1,
        "started_at": _stamp(now),
        "completed_at": _stamp(now),
        "status": "failed",
        "mother_binding": legacy_release["mother_binding"],
        "network": "mainnet",
        "node": "mainnetc-super2",
        "nodes": ["mainnetc-super2"],
        "service_uuid": standby_evidence["service_uuid"],
        "release": {"locator": f"actions/deployment-c2-replica-sync-releases/{legacy_path.name}", "sha256": legacy_sha},
        "execution_claim": {"locator": f"actions/deployment-c2-replica-sync-claims/{claim_path.name}"},
        "proof": {"service_status": "starting:unknown"},
        "failure": {"code": "MOTHER_DEPLOY_C2_REPLICA_SYNC_NOT_HEALTHY", "message": "host port 30303 already allocated"},
        "mutation_receipts": [
            {"ordinal": 1, "mutation_id": "mainnetc-super2.install-sync-proof-compose", "method": "PATCH", "endpoint": f"/api/v1/services/{standby_evidence['service_uuid']}", "status": "succeeded", "live_write_acknowledged": True, "response": {"status": 200}},
            {"ordinal": 2, "mutation_id": "mainnetc-super2.deploy-sync-proof-compose", "method": "GET", "endpoint": f"/api/v1/deploy?uuid={standby_evidence['service_uuid']}&force=true", "status": "succeeded", "live_write_acknowledged": True, "response": {"status": 200, "safe_text_available": True, "safe_text": "accepted"}},
        ],
        "policy": {
            "validator_activation_performed": False,
            "validator_vote_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
        },
        "identity_mutation_count": 0,
        "chain_mutation_count": 0,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "validator_vote_performed": False,
    }
    failed_evidence_path.write_bytes(canonical_json(failed_doc))

    diagnostic = diagnose_c2_replica_sync_materialization(
        paths,
        private_state,
        failed_evidence_path,
        failed_evidence_max_age_seconds=999999999,
        operator_confirm_host_p2p_port_conflict=True,
        opener=opener,
        now=None,
        write_evidence=True,
        operation=_operation("diagnose-c2-replica-sync-host-p2p-conflict"),
    )
    assert diagnostic["summary"]["classification"] == "docker-created-host-p2p-port-conflict"
    assert diagnostic["summary"]["retry_authorized"] is True
    assert diagnostic["summary"]["docker_materialization_observed"] is True

    resume_release = build_c2_replica_sync_resume_release(
        paths,
        private_state,
        Path(diagnostic["evidence"]["path"]),
        acknowledged_c2_replica_sync_diagnostic_sha256=diagnostic["evidence"]["sha256"],
    )
    assert resume_release["summary"]["compose_patch_authorized"] is True
    assert resume_release["summary"]["host_p2p_isolation_authorized"] is True
    assert resume_release["summary"]["mutation_count"] == 2
    corrected = resume_release["proof_plan"]["proof_compose"]["canonical_text"]
    assert "30303:30303/tcp" not in corrected
    assert "30303:30303/udp" not in corrected
    assert "--p2p-port=30303" in corrected

    resume_path, resume_sha = write_c2_replica_sync_resume_release(paths, resume_release, operation=_operation("write-c2-replica-sync-resume-host-p2p-isolated"))
    verified = verify_c2_replica_sync_resume_release(paths, private_state, resume_path, max_age_seconds=999999999)
    assert verified["compose_patch_authorized"] is True
    assert verified["service_mutation_count"] == 2

    resume_opener = _C2ReplicaSyncOpener(
        service_uuid=standby_evidence["service_uuid"],
        standby_compose=opener.compose_raw,
        a_service_uuid=a_service_uuid,
    )
    resumed = execute_c2_replica_sync_resume_release(
        paths,
        private_state,
        resume_path,
        acknowledged_release_sha256=resume_sha,
        max_age_seconds=999999999,
        opener=resume_opener,
        max_wait_seconds=1,
        poll_interval_seconds=0,
        operation=_operation("execute-c2-replica-sync-resume-host-p2p-isolated"),
    )
    assert resumed["status"] == "pass"
    assert resumed["summary"]["compose_patch_performed"] is True
    assert resumed["summary"]["host_p2p_isolation_performed"] is True
    assert [item["method"] for item in resumed["mutation_receipts"]] == ["PATCH", "GET"]
    assert "30303:30303/tcp" not in resume_opener.compose_raw
    assert resumed["validator_vote_performed"] is False



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
        "diagnose-c2-replica-sync-materialization": ["--failed-evidence", "failed.json", "--operator-confirm-host-p2p-port-conflict"],
        "release-c2-replica-sync-resume": [
            "--diagnostic-evidence", "diagnostic.json",
            "--acknowledge-c2-replica-sync-diagnostic-sha256", "c" * 64,
        ],
        "verify-c2-replica-sync-resume-release": ["--release", "resume-release.json"],
        "apply-c2-replica-sync-resume": ["--release", "resume-release.json", "--acknowledge-release-sha256", "d" * 64],
    }
    for command, extra in commands.items():
        args = parser.parse_args([command, "--node", "mainnetc-super2", *extra])
        assert args.command == command
