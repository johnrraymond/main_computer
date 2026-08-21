from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

from tools.mother.common.deployment_node_remove_do import _find_conflicting_add_node_voters
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import (
    install_verified_private_state,
    prepare_private_state_bootstrap,
    read_private_state,
)
from tools.mother_admission_voter_cleanup import (
    MotherAdmissionVoterCleanupError,
    TARGET_PREFIX,
    execute_admission_voter_cleanup,
    inspect_admission_voter_cleanup,
)
from tests.test_mother_deployment_executor import _operation, _starter_document


SERVICE_UUID = "j1445405xyjkbeld0se5j8i8"
NODE = "mainnetc-super1"
TARGET_NAME = f"{TARGET_PREFIX}{NODE}"


class _Response:
    def __init__(self, payload, status: int = 200) -> None:  # noqa: ANN001
        self.status = status
        self.headers = {"Content-Type": "application/json"}
        self._body = json.dumps(payload).encode("utf-8")

    def getcode(self) -> int:
        return self.status

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]

    def close(self) -> None:
        return None


def _install(tmp_path: Path):
    runtime = tmp_path / "runtime" / "state"
    paths = MotherPaths(runtime_state_root=runtime).resolve_private_state_paths()
    operation = _operation("admission-voter-cleanup-install")
    closure = prepare_private_state_bootstrap(
        paths,
        _starter_document(),
        updated_at="2026-08-18T01:00:00Z",
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    install_verified_private_state(paths, closure, None, operation=operation)
    private_state = read_private_state(paths, operation=_operation("admission-voter-cleanup-read"))
    return runtime, private_state


def _admission_evidence(runtime: Path, *, node: str = NODE, status: str = "pass") -> tuple[Path, str]:
    root = runtime / "mother" / "evidence" / "deployment-node-add-validator-admission"
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": "main_computer.mother.deployment_node_add_validator_admission_evidence.v1",
        "network": "mainnet",
        "status": status,
        "next_phase": "add-node-post-admission-observe-mainnet",
        "transient_voter_guardian_nodes": [node],
        "summary": {
            "complete": True,
            "final_validator_set_verified": True,
            "target_node": "mainneta-super1",
        },
    }
    path = root / "20260818T235113Z-mainneta-super1-472e774d2ae34487.json"
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _application(
    name: str,
    uuid: str,
    status: str,
    image: str = "python:3.12-alpine",
    *,
    exclude_from_status: bool = False,
    labels: dict[str, str] | None = None,
) -> dict[str, object]:
    item: dict[str, object] = {
        "name": name,
        "uuid": uuid,
        "status": status,
        "image": image,
        "exclude_from_status": exclude_from_status,
    }
    if labels is not None:
        item["labels"] = labels
    return item


class _AdmissionVoterShimOpener:
    def __init__(self) -> None:
        self.shim_installed = False
        self.requests: list[tuple[str, str]] = []
        self.patched_compose: dict[str, object] | None = None

    def _compose_raw(self) -> str:
        compose = """services:
  mainnetc-super1:
    image: hyperledger/besu:latest
  mother-super-node-fdb:
    image: foundationdb/foundationdb:7.4.6
  mother-super-node-hub:
    image: mainnetc-super1-hub:c56a20a0fd05
  mother-genesis-proof-guardian:
    image: alpine:3.20
  mother-add-node-validator-activation-guardian:
    image: alpine:3.20
"""
        compose_obj = yaml.safe_load(compose)
        if self.shim_installed:
            compose_obj["services"][TARGET_NAME] = {
                "image": "alpine:3.20",
                "container_name": f"{TARGET_NAME}-{SERVICE_UUID}",
                "environment": {
                    "MOTHER_RETIRED_ADMISSION_VOTER_SHIM": "true",
                    "CANDIDATE_VALIDATOR": "0x0000000000000000000000000000000000000000",
                },
                "labels": {
                    "main_computer.mother.post_work_shim": "true",
                    "main_computer.mother.retired_admission_voter_shim": "true",
                    "main_computer.mother.not_a_validator_voter": "true",
                },
                "healthcheck": {
                    "test": ["CMD", "sh", "-lc", "test -f /tmp/mother-retired-helper-shim"],
                },
            }
        else:
            compose_obj["services"][TARGET_NAME] = {
                "image": "python:3.12-alpine",
                "environment": {
                    "CANDIDATE_VALIDATOR": "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
                },
                "command": ["python", "-c", "qbft_proposeValidatorVote"],
            }
        return base64.b64encode(yaml.safe_dump(compose_obj, sort_keys=False).encode("utf-8")).decode("ascii")

    def _payload(self) -> dict[str, object]:
        applications = [
            _application("mainnetc-super1", "besu123", "running:healthy", "hyperledger/besu:latest"),
            _application("mother-super-node-fdb", "fdb123", "running:healthy", "foundationdb/foundationdb:7.4.6"),
            _application("mother-super-node-hub", "hub123", "running:healthy", "mainnetc-super1-hub:c56a20a0fd05"),
            _application("mother-genesis-proof-guardian", "genesisguardian123", "running:healthy", "alpine:3.20"),
            _application("mother-add-node-validator-activation-guardian", "activationguardian123", "running:healthy", "alpine:3.20"),
        ]
        if self.shim_installed:
            applications.append(
                _application(
                    TARGET_NAME,
                    "admissionvoter123",
                    "running:healthy",
                    "alpine:3.20",
                    labels={
                        "main_computer.mother.post_work_shim": "true",
                        "main_computer.mother.retired_admission_voter_shim": "true",
                        "main_computer.mother.not_a_validator_voter": "true",
                    },
                )
            )
        else:
            applications.append(
                _application(
                    TARGET_NAME,
                    "admissionvoter123",
                    "running:unhealthy:excluded",
                    exclude_from_status=True,
                )
            )
        return {
            "name": NODE,
            "uuid": SERVICE_UUID,
            "status": "running:healthy" if self.shim_installed else "degraded:unhealthy",
            "applications": applications,
            "docker_compose_raw": self._compose_raw(),
        }

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        self.requests.append((method, path))
        if method == "GET" and path == f"/api/v1/services/{SERVICE_UUID}":
            return _Response(self._payload())
        if method == "PATCH" and path == f"/api/v1/services/{SERVICE_UUID}":
            raw = json.loads(request.data.decode("utf-8"))
            decoded = base64.b64decode(raw["docker_compose_raw"]).decode("utf-8")
            compose = yaml.safe_load(decoded)
            self.patched_compose = compose
            service_def = compose["services"][TARGET_NAME]
            assert service_def["image"] == "alpine:3.20"
            assert service_def["container_name"] == f"{TARGET_NAME}-{SERVICE_UUID}"
            assert service_def["environment"]["MOTHER_RETIRED_ADMISSION_VOTER_SHIM"] == "true"
            assert service_def["environment"]["CANDIDATE_VALIDATOR"] == "0x0000000000000000000000000000000000000000"
            assert service_def["healthcheck"]["test"] == ["CMD", "sh", "-lc", "test -f /tmp/mother-retired-helper-shim"]
            assert service_def["labels"]["main_computer.mother.post_work_shim"] == "true"
            assert service_def["labels"]["main_computer.mother.retired_admission_voter_shim"] == "true"
            assert service_def["labels"]["main_computer.mother.not_a_validator_voter"] == "true"
            assert "ports" not in service_def
            assert "volumes" not in service_def
            assert "qbft_proposeValidatorVote" not in json.dumps(service_def, sort_keys=True)

            # Scope guard: this standalone script preserves every non-target service.
            services = compose["services"]
            assert "mainnetc-super1" in services
            assert "mother-super-node-fdb" in services
            assert "mother-super-node-hub" in services
            assert "mother-genesis-proof-guardian" in services
            assert "mother-add-node-validator-activation-guardian" in services

            self.shim_installed = True
            return _Response({"uuid": SERVICE_UUID, "updated": True})
        if method == "POST" and path == "/api/v1/applications/admissionvoter123/restart":
            return _Response({"ok": True, "uuid": "admissionvoter123", "action": "restart"})
        raise AssertionError(f"unexpected request: {method} {path}")


def test_inspect_reports_admission_voter_cleanup_required_without_mutation(tmp_path: Path) -> None:
    _, private_state = _install(tmp_path)
    opener = _AdmissionVoterShimOpener()

    result = inspect_admission_voter_cleanup(
        private_state,
        network="mainnet",
        controller_id="coolify-c",
        service_uuid=SERVICE_UUID,
        node=NODE,
        opener=opener.open,
    )

    assert result["status"] == "cleanup-required"
    assert result["target_name"] == TARGET_NAME
    assert result["mutation_performed"] is False
    assert result["docker_touched"] is False
    assert result["chain_touched"] is False
    assert result["summary"]["compose_target_is_retired_shim"] is False
    assert result["summary"]["target_record_is_retired_shim"] is False
    assert opener.requests == [("GET", f"/api/v1/services/{SERVICE_UUID}")]


def test_execute_installs_only_minimal_retired_admission_voter_shim(tmp_path: Path) -> None:
    runtime, private_state = _install(tmp_path)
    evidence, evidence_sha = _admission_evidence(runtime)
    opener = _AdmissionVoterShimOpener()

    result = execute_admission_voter_cleanup(
        private_state,
        network="mainnet",
        controller_id="coolify-c",
        service_uuid=SERVICE_UUID,
        node=NODE,
        admission_evidence=evidence,
        acknowledged_admission_evidence_sha256=evidence_sha,
        acknowledged_service_uuid=SERVICE_UUID,
        allow_retired_admission_voter_shim=True,
        instant_deploy=False,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        opener=opener.open,
    )

    assert result["status"] == "pass"
    assert result["summary"]["clean"] is True
    assert result["summary"]["installed_retired_admission_voter_shim"] is True
    assert result["patch_receipt"]["retired_admission_voter_shim_votes"] is False
    assert result["patch_receipt"]["retired_admission_voter_shim_private_key_required"] is False
    assert result["accepted_admission_evidence"]["voter_nodes"] == [NODE]
    assert result["docker_touched"] is True
    assert result["chain_touched"] is False
    assert ("PATCH", f"/api/v1/services/{SERVICE_UUID}") in opener.requests
    assert ("POST", "/api/v1/applications/admissionvoter123/restart") in opener.requests
    assert result["helper_restart"]["cleanup_scope"] == "existing-helper-mimic-restart"
    assert result["summary"]["helper_restart_succeeded"] is True
    assert result["summary"]["post_restart_health_poll_performed"] is False
    assert opener.patched_compose is not None

    rewritten = yaml.safe_dump(opener.patched_compose, sort_keys=False)
    conflicts = _find_conflicting_add_node_voters(
        rewritten,
        target_validator="0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
    )
    assert conflicts == []


def test_execute_refuses_without_explicit_shim_flag(tmp_path: Path) -> None:
    runtime, private_state = _install(tmp_path)
    evidence, evidence_sha = _admission_evidence(runtime)
    opener = _AdmissionVoterShimOpener()

    with pytest.raises(MotherAdmissionVoterCleanupError) as exc:
        execute_admission_voter_cleanup(
            private_state,
            network="mainnet",
            controller_id="coolify-c",
            service_uuid=SERVICE_UUID,
            node=NODE,
            admission_evidence=evidence,
            acknowledged_admission_evidence_sha256=evidence_sha,
            acknowledged_service_uuid=SERVICE_UUID,
            allow_retired_admission_voter_shim=False,
            opener=opener.open,
        )

    assert exc.value.code == "MOTHER_ADMISSION_VOTER_CLEANUP_SHIM_FLAG_REQUIRED"
    assert opener.requests == []


def test_execute_refuses_wrong_admission_evidence_ack(tmp_path: Path) -> None:
    runtime, private_state = _install(tmp_path)
    evidence, _ = _admission_evidence(runtime)
    opener = _AdmissionVoterShimOpener()

    with pytest.raises(MotherAdmissionVoterCleanupError) as exc:
        execute_admission_voter_cleanup(
            private_state,
            network="mainnet",
            controller_id="coolify-c",
            service_uuid=SERVICE_UUID,
            node=NODE,
            admission_evidence=evidence,
            acknowledged_admission_evidence_sha256="0" * 64,
            acknowledged_service_uuid=SERVICE_UUID,
            allow_retired_admission_voter_shim=True,
            opener=opener.open,
        )

    assert exc.value.code == "MOTHER_ADMISSION_VOTER_CLEANUP_EVIDENCE_ACK_MISMATCH"
    assert opener.requests == []


def test_execute_refuses_evidence_where_node_was_not_voter(tmp_path: Path) -> None:
    runtime, private_state = _install(tmp_path)
    evidence, evidence_sha = _admission_evidence(runtime, node="mainnetx-super9")
    opener = _AdmissionVoterShimOpener()

    with pytest.raises(MotherAdmissionVoterCleanupError) as exc:
        execute_admission_voter_cleanup(
            private_state,
            network="mainnet",
            controller_id="coolify-c",
            service_uuid=SERVICE_UUID,
            node=NODE,
            admission_evidence=evidence,
            acknowledged_admission_evidence_sha256=evidence_sha,
            acknowledged_service_uuid=SERVICE_UUID,
            allow_retired_admission_voter_shim=True,
            opener=opener.open,
        )

    assert exc.value.code == "MOTHER_ADMISSION_VOTER_CLEANUP_EVIDENCE_NOT_ACCEPTED"
    assert opener.requests == []
