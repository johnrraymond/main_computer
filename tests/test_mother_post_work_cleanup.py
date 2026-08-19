from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

import tools.mother_post_work_cleanup as cleanup_cli

from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import (
    install_verified_private_state,
    prepare_private_state_bootstrap,
    read_private_state,
)
from tools.mother_post_work_cleanup import (
    MotherPostWorkCleanupError,
    classify_post_work_helpers,
    execute_post_work_completed_helper_cleanup,
    inspect_post_work_completed_helper_cleanup,
    is_post_work_helper_name,
    validate_completion_evidence,
)
from tests.test_mother_deployment_executor import _operation, _starter_document


SERVICE_UUID = "j1445405xyjkbeld0se5j8i8"


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
    operation = _operation("post-work-cleanup-install")
    closure = prepare_private_state_bootstrap(
        paths,
        _starter_document(),
        updated_at="2026-08-18T01:00:00Z",
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    install_verified_private_state(paths, closure, None, operation=operation)
    private_state = read_private_state(paths, operation=_operation("post-work-cleanup-read"))
    return runtime, private_state


def _application(
    name: str,
    uuid: str,
    status: str,
    image: str = "python:3.12-alpine",
    *,
    exclude_from_status: bool = False,
) -> dict[str, object]:
    return {
        "name": name,
        "uuid": uuid,
        "status": status,
        "image": image,
        "exclude_from_status": exclude_from_status,
    }


def _clean_add_evidence(service_uuid: str = SERVICE_UUID) -> dict[str, object]:
    return {
        "kind": "main_computer.mother.add_node_post_admission_topology_evidence.v1",
        "schema_version": 1,
        "status": "pass",
        "failure": None,
        "network": "mainnet",
        "next_phase": "add-node-prep-mainnet",
        "summary": {
            "clean": True,
            "complete": True,
            "topology_current": True,
            "topology_stale": False,
            "next_phase": "add-node-prep-mainnet",
            "final_validator_count": 2,
            "final_nodes": ["mainnetc-super1", "mainneta-super1"],
        },
        "final_topology": {
            "nodes": ["mainnetc-super1", "mainneta-super1"],
            "services": {
                "mainnetc-super1": {
                    "service_uuid": service_uuid,
                    "controller_id": "coolify-c",
                },
                "mainneta-super1": {
                    "service_uuid": "mzg0ttmqt4yqsurwjkv5un4a",
                    "controller_id": "coolify-a",
                },
            },
        },
    }


def _write_evidence(tmp_path: Path, document: dict[str, object]) -> tuple[Path, str]:
    path = tmp_path / "completion.json"
    raw = json.dumps(document, sort_keys=True).encode("utf-8")
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def test_post_work_helper_name_set_includes_add_and_remove_phase_helpers() -> None:
    assert is_post_work_helper_name("mother-genesis-proof-guardian")
    assert is_post_work_helper_name("mother-add-node-validator-activation-guardian")
    assert is_post_work_helper_name("mother-add-node-validator-admission-voter-mainnetc-super1")
    assert is_post_work_helper_name("mother-node-remove-voter-mainneta_super1")
    assert not is_post_work_helper_name("mother-super-node-hub")
    assert not is_post_work_helper_name("mainnetc-super1")


def test_post_work_cleanup_classifies_running_unhealthy_known_helpers_after_clean_work() -> None:
    summary = classify_post_work_helpers(
        payload={
            "name": "mainnetc-super1",
            "uuid": SERVICE_UUID,
            "status": "running:unhealthy",
            "applications": [
                _application("mainnetc-super1", "core", "running:healthy", "hyperledger/besu:latest"),
                _application("mother-super-node-fdb", "fdb", "running:healthy", "foundationdb/foundationdb:7.4.6"),
                _application("mother-super-node-hub", "hub", "running:healthy", "mainnetc-super1-hub:c56a20a0fd05"),
                _application("mother-genesis-proof-guardian", "genesis", "running:unhealthy"),
                _application("mother-add-node-validator-admission-voter-mainnetc-super1", "voter", "running:unhealthy"),
                _application("mother-validator-quorum-recovery-initial-guardian", "preserve", "running:healthy"),
            ],
        },
        node="mainnetc-super1",
    )

    assert summary["summary"]["core_required_components_healthy"] is True
    assert summary["summary"]["post_work_helper_candidate_count"] == 2
    assert [item["name"] for item in summary["post_work_helper_candidates"]] == [
        "mother-genesis-proof-guardian",
        "mother-add-node-validator-admission-voter-mainnetc-super1",
    ]
    assert [item["name"] for item in summary["preserved_helpers"]] == [
        "mother-validator-quorum-recovery-initial-guardian"
    ]
    assert summary["summary"]["manual_review_required"] is False
    assert summary["summary"]["clean"] is False


def test_post_work_cleanup_rejects_unclean_completion_evidence() -> None:
    document = _clean_add_evidence()
    document["summary"]["clean"] = False

    with pytest.raises(MotherPostWorkCleanupError) as exc:
        validate_completion_evidence(
            document,
            workflow="add-node",
            node="mainnetc-super1",
            service_uuid=SERVICE_UUID,
        )

    assert exc.value.code == "MOTHER_POST_WORK_CLEANUP_COMPLETION_EVIDENCE_UNTRUSTED"
    assert "summary.clean is not true" in str(exc.value)


def test_add_node_single_node_finalized_evidence_allows_cleanup_gate() -> None:
    document = {
        "kind": "main_computer.mother.deployment_node_add_single_node_chain_and_hub_proof_evidence.v1",
        "schema_version": 1,
        "status": "pass",
        "failure": None,
        "network": "mainnet",
        "next_phase": "add-node-single-node-finalized-mainnet",
        "summary": {
            "clean": True,
            "complete": True,
            "current_topology_marked_by_evidence": True,
            "topology_stale": False,
            "final_nodes": ["mainnetc-super1"],
        },
        "final_topology": {
            "nodes": ["mainnetc-super1"],
            "services": {
                "mainnetc-super1": {
                    "service_uuid": SERVICE_UUID,
                    "controller_id": "coolify-c",
                },
            },
        },
    }

    result = validate_completion_evidence(
        document,
        workflow="add-node",
        node="mainnetc-super1",
        service_uuid=SERVICE_UUID,
    )

    assert result["accepted"] is True
    assert result["summary_topology_current"] is True
    assert result["next_phase"] == "add-node-single-node-finalized-mainnet"


class _PostWorkCleanupOpener:
    def __init__(self) -> None:
        self.deleted: set[str] = set()
        self.compose_cleaned = False
        self.requests: list[tuple[str, str]] = []
        self.helper_uuids = {
            "mother-genesis-proof-guardian": "genesisguardian123",
            "mother-add-node-validator-admission-voter-mainnetc-super1": "admissionvoter123",
        }

    def _payload(self) -> dict[str, object]:
        applications = [
            _application("mainnetc-super1", "besu123", "running:healthy", "hyperledger/besu:latest", exclude_from_status=True),
            _application("mother-super-node-fdb", "fdb123", "running:healthy", "foundationdb/foundationdb:7.4.6"),
            _application("mother-super-node-hub", "hub123", "running:healthy", "mainnetc-super1-hub:c56a20a0fd05"),
        ]
        for name, uuid in self.helper_uuids.items():
            if uuid not in self.deleted:
                applications.append(_application(name, uuid, "running:unhealthy"))
        return {
            "name": "mainnetc-super1",
            "uuid": SERVICE_UUID,
            "status": "running:healthy" if len(self.deleted) == len(self.helper_uuids) else "running:unhealthy",
            "applications": applications,
            "docker_compose_raw": base64.b64encode(
                (
                    b"""services:
  mainnetc-super1:
    image: hyperledger/besu:latest
  mother-super-node-fdb:
    image: foundationdb/foundationdb:7.4.6
  mother-super-node-hub:
    image: mainnetc-super1-hub:c56a20a0fd05
"""
                    if self.compose_cleaned
                    else b"""services:
  mainnetc-super1:
    image: hyperledger/besu:latest
  mother-super-node-fdb:
    image: foundationdb/foundationdb:7.4.6
  mother-super-node-hub:
    image: mainnetc-super1-hub:c56a20a0fd05
  mother-genesis-proof-guardian:
    image: python:3.12-alpine
  mother-add-node-validator-admission-voter-mainnetc-super1:
    image: python:3.12-alpine
"""
                )
            ).decode("ascii"),
        }

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        self.requests.append((method, path))
        if method == "GET" and path == f"/api/v1/services/{SERVICE_UUID}":
            return _Response(self._payload())
        if method == "DELETE" and path.startswith("/api/v1/applications/"):
            app_uuid = path.rsplit("/", 1)[-1]
            assert app_uuid in self.helper_uuids.values()
            self.deleted.add(app_uuid)
            return _Response({"uuid": app_uuid, "deleted": True})
        if method == "DELETE" and path == "/api/v1/applications/admissionvoter123":
            self.deleted.add("admissionvoter123")
            return _Response({"uuid": "admissionvoter123", "deleted": True})
        if method == "PATCH" and path == f"/api/v1/services/{SERVICE_UUID}":
            raw = json.loads(request.data.decode("utf-8"))
            decoded = base64.b64decode(raw["docker_compose_raw"]).decode("utf-8")
            assert "mother-genesis-proof-guardian" not in decoded
            assert "mother-add-node-validator-admission-voter-mainnetc-super1" not in decoded
            assert "mainnetc-super1" in decoded
            self.compose_cleaned = True
            return _Response({"uuid": SERVICE_UUID, "updated": True})
        raise AssertionError(f"unexpected request: {method} {path}")


def test_post_work_cleanup_execute_deletes_running_unhealthy_helpers_after_clean_add_evidence(tmp_path: Path) -> None:
    _, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_evidence(tmp_path, _clean_add_evidence())
    opener = _PostWorkCleanupOpener()

    result = execute_post_work_completed_helper_cleanup(
        private_state,
        network="mainnet",
        controller_id="coolify-c",
        service_uuid=SERVICE_UUID,
        node="mainnetc-super1",
        acknowledged_service_uuid=SERVICE_UUID,
        completion_evidence_path=evidence_path,
        completion_evidence_sha256=evidence_sha,
        workflow="add-node",
        allow_compose_rewrite=True,
        instant_deploy_compose_rewrite=True,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
        operation=_operation("post-work-cleanup-execute"),
    )

    assert result["status"] == "manual-review-required"
    assert result["summary"]["clean"] is True
    assert result["summary"]["initial_post_work_helper_candidate_count"] == 2
    assert result["summary"]["operator_host_cleanup_required"] is True
    assert "mother-add-node-validator-admission-voter-mainnetc-super1-" in result["operator_host_cleanup_commands"][0]["command"]
    assert result["summary"]["application_delete_count"] == 2
    assert result["summary"]["application_delete_succeeded"] is True
    assert result["summary"]["compose_rewrite_performed"] is True
    assert result["summary"]["compose_rewrite_succeeded"] is True
    assert opener.deleted == set(opener.helper_uuids.values())
    assert ("DELETE", "/api/v1/applications/genesisguardian123") in opener.requests
    assert ("DELETE", "/api/v1/applications/admissionvoter123") in opener.requests
    assert ("PATCH", f"/api/v1/services/{SERVICE_UUID}") in opener.requests


def test_post_work_cleanup_execute_emits_progress_before_mutating_and_polling(tmp_path: Path) -> None:
    _, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_evidence(tmp_path, _clean_add_evidence())
    opener = _PostWorkCleanupOpener()
    events: list[tuple[str, str, dict[str, object]]] = []

    result = execute_post_work_completed_helper_cleanup(
        private_state,
        network="mainnet",
        controller_id="coolify-c",
        service_uuid=SERVICE_UUID,
        node="mainnetc-super1",
        acknowledged_service_uuid=SERVICE_UUID,
        completion_evidence_path=evidence_path,
        completion_evidence_sha256=evidence_sha,
        workflow="add-node",
        allow_compose_rewrite=True,
        instant_deploy_compose_rewrite=True,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
        operation=_operation("post-work-cleanup-progress"),
        progress=lambda phase, message, details: events.append((phase, message, dict(details))),
    )

    assert result["status"] == "manual-review-required"
    assert events[0][:2] == ("execute", "validating completion evidence")
    assert any(phase == "execute.delete" and "deleting" in message for phase, message, _ in events)
    assert any(phase == "execute.compose" and message == "rewriting service Compose" for phase, message, _ in events)
    assert any(phase == "execute.poll" and message == "fetching service detail for cleanup verification" for phase, message, _ in events)
    assert events[-1][0] == "execute"
    assert events[-1][1] == "cleanup execution complete"
    assert events[-1][2]["status"] == "manual-review-required"


def test_post_work_cleanup_inspect_reports_cleanup_required_without_mutation(tmp_path: Path) -> None:
    _, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_evidence(tmp_path, _clean_add_evidence())
    opener = _PostWorkCleanupOpener()

    result = inspect_post_work_completed_helper_cleanup(
        private_state,
        network="mainnet",
        controller_id="coolify-c",
        service_uuid=SERVICE_UUID,
        node="mainnetc-super1",
        completion_evidence_path=evidence_path,
        completion_evidence_sha256=evidence_sha,
        workflow="add-node",
        opener=opener,
        operation=_operation("post-work-cleanup-inspect"),
    )

    assert result["status"] == "cleanup-required"
    assert result["summary"]["live_mutation_performed"] is False
    assert result["summary"]["post_work_helper_candidate_count"] == 2
    assert opener.deleted == set()
    assert all(method == "GET" for method, _ in opener.requests)


def test_retired_genesis_proof_guardian_shim_is_accepted_only_as_health_shim() -> None:
    summary = classify_post_work_helpers(
        payload={
            "name": "mainnetc-super1",
            "uuid": SERVICE_UUID,
            "status": "running:healthy",
            "applications": [
                _application("mainnetc-super1", "core", "running:healthy", "hyperledger/besu:latest"),
                _application("mother-super-node-fdb", "fdb", "running:healthy", "foundationdb/foundationdb:7.4.6"),
                _application("mother-super-node-hub", "hub", "running:healthy", "mainnetc-super1-hub:c56a20a0fd05"),
                {
                    **_application("mother-genesis-proof-guardian", "genesis", "running:healthy", "alpine:3.20"),
                    "labels": {
                        "main_computer.mother.post_work_shim": "true",
                        "main_computer.mother.not_a_proof_guardian": "true",
                    },
                },
            ],
        },
        node="mainnetc-super1",
    )

    assert summary["summary"]["clean"] is True
    assert summary["summary"]["retired_post_work_shim_count"] == 1
    assert summary["summary"]["post_work_helper_candidate_count"] == 0
    assert summary["retired_post_work_shims"][0]["name"] == "mother-genesis-proof-guardian"


class _PostWorkShimOpener:
    def __init__(self) -> None:
        self.shim_installed = False
        self.deleted: set[str] = set()
        self.requests: list[tuple[str, str]] = []

    def _payload(self) -> dict[str, object]:
        applications = [
            _application("mainnetc-super1", "besu123", "running:healthy", "hyperledger/besu:latest", exclude_from_status=True),
            _application("mother-super-node-fdb", "fdb123", "running:healthy", "foundationdb/foundationdb:7.4.6"),
            _application("mother-super-node-hub", "hub123", "running:healthy", "mainnetc-super1-hub:c56a20a0fd05"),
        ]
        if "admissionvoter123" not in self.deleted:
            applications.append(
                _application(
                    "mother-add-node-validator-admission-voter-mainnetc-super1",
                    "admissionvoter123",
                    "running:healthy:excluded",
                    exclude_from_status=True,
                )
            )
        if self.shim_installed:
            applications.append(
                {
                    **_application("mother-genesis-proof-guardian", "genesisguardian123", "running:healthy", "alpine:3.20"),
                    "labels": {
                        "main_computer.mother.post_work_shim": "true",
                        "main_computer.mother.not_a_proof_guardian": "true",
                    },
                }
            )
        else:
            applications.append(_application("mother-genesis-proof-guardian", "genesisguardian123", "exited"))
        return {
            "name": "mainnetc-super1",
            "uuid": SERVICE_UUID,
            "status": "running:healthy" if self.shim_installed else "degraded:unhealthy",
            "applications": applications,
            "docker_compose_raw": base64.b64encode(
                b"""services:
  mainnetc-super1:
    image: hyperledger/besu:latest
  mother-super-node-fdb:
    image: foundationdb/foundationdb:7.4.6
  mother-super-node-hub:
    image: mainnetc-super1-hub:c56a20a0fd05
"""
            ).decode("ascii"),
        }

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        self.requests.append((method, path))
        if method == "GET" and path == f"/api/v1/services/{SERVICE_UUID}":
            return _Response(self._payload())
        if method == "DELETE" and path == "/api/v1/applications/admissionvoter123":
            self.deleted.add("admissionvoter123")
            return _Response({"uuid": "admissionvoter123", "deleted": True})
        if method == "PATCH" and path == f"/api/v1/services/{SERVICE_UUID}":
            raw = json.loads(request.data.decode("utf-8"))
            decoded = base64.b64decode(raw["docker_compose_raw"]).decode("utf-8")
            compose = yaml.safe_load(decoded)
            service_def = compose["services"]["mother-genesis-proof-guardian"]
            assert service_def["image"] == "alpine:3.20"
            assert service_def["container_name"] == f"mother-genesis-proof-guardian-{SERVICE_UUID}"
            assert service_def["healthcheck"]["test"] == ["CMD", "sh", "-lc", "test -f /tmp/mother-retired-helper-shim"]
            assert service_def["labels"]["main_computer.mother.post_work_shim"] == "true"
            assert service_def["labels"]["main_computer.mother.not_a_proof_guardian"] == "true"
            assert "ports" not in service_def
            assert "volumes" not in service_def
            assert "/proof" not in json.dumps(service_def, sort_keys=True)
            self.shim_installed = True
            return _Response({"uuid": SERVICE_UUID, "updated": True})
        raise AssertionError(f"unexpected request: {method} {path}")


def test_post_work_cleanup_execute_can_install_minimal_retired_genesis_guardian_shim(tmp_path: Path) -> None:
    _, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_evidence(tmp_path, _clean_add_evidence())
    opener = _PostWorkShimOpener()

    result = execute_post_work_completed_helper_cleanup(
        private_state,
        network="mainnet",
        controller_id="coolify-c",
        service_uuid=SERVICE_UUID,
        node="mainnetc-super1",
        acknowledged_service_uuid=SERVICE_UUID,
        completion_evidence_path=evidence_path,
        completion_evidence_sha256=evidence_sha,
        workflow="add-node",
        allow_compose_rewrite=True,
        instant_deploy_compose_rewrite=True,
        allow_retired_genesis_proof_guardian_shim=True,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
        operation=_operation("post-work-cleanup-shim-execute"),
    )

    assert result["status"] == "manual-review-required"
    assert result["summary"]["clean"] is True
    assert result["summary"]["application_delete_count"] == 1
    assert result["summary"]["operator_host_cleanup_required"] is True
    assert result["summary"]["retired_genesis_proof_guardian_shim_installed"] is True
    assert result["summary"]["retired_genesis_proof_guardian_shim_candidate_count"] == 1
    assert result["summary"]["retired_post_work_shim_count"] == 1
    assert result["compose_rewrite"]["installed_retired_genesis_proof_guardian_shim"] is True
    assert result["compose_rewrite"]["retired_genesis_proof_guardian_shim_serves_proof"] is False
    assert result["compose_rewrite"]["retired_genesis_proof_guardian_shim_mounts_proof_volume"] is False
    assert ("PATCH", f"/api/v1/services/{SERVICE_UUID}") in opener.requests

def test_post_work_cleanup_classifies_excluded_helper_still_declared_in_rendered_compose() -> None:
    raw_clean = base64.b64encode(
        b"""services:
  mainnetc-super1:
    image: hyperledger/besu:latest
  mother-super-node-fdb:
    image: foundationdb/foundationdb:7.4.6
  mother-super-node-hub:
    image: mainnetc-super1-hub:c56a20a0fd05
"""
    ).decode("ascii")
    rendered_stale = base64.b64encode(
        b"""services:
  mainnetc-super1:
    image: hyperledger/besu:latest
  mother-super-node-fdb:
    image: foundationdb/foundationdb:7.4.6
  mother-super-node-hub:
    image: mainnetc-super1-hub:c56a20a0fd05
  mother-add-node-validator-admission-voter-mainnetc-super1:
    image: python:3.12-alpine
"""
    ).decode("ascii")

    summary = classify_post_work_helpers(
        payload={
            "name": "mainnetc-super1",
            "uuid": SERVICE_UUID,
            "status": "running:healthy",
            "applications": [
                _application("mainnetc-super1", "core", "running:healthy", "hyperledger/besu:latest"),
                _application("mother-super-node-fdb", "fdb", "running:healthy", "foundationdb/foundationdb:7.4.6"),
                _application("mother-super-node-hub", "hub", "running:healthy", "mainnetc-super1-hub:c56a20a0fd05"),
                _application(
                    "mother-add-node-validator-admission-voter-mainnetc-super1",
                    "voter",
                    "running:healthy:excluded",
                    exclude_from_status=True,
                ),
            ],
            "docker_compose_raw": raw_clean,
            "docker_compose": rendered_stale,
        },
        node="mainnetc-super1",
    )

    assert summary["summary"]["clean"] is False
    assert summary["summary"]["post_work_helper_candidate_count"] == 1
    assert summary["summary"]["compose_declared_post_work_helper_count"] == 1
    assert summary["post_work_helper_candidates"][0]["name"] == "mother-add-node-validator-admission-voter-mainnetc-super1"
    assert summary["post_work_helper_candidates"][0]["compose_declared"] is True
    assert summary["compose_declared_post_work_helpers"][0]["compose_source_fields"][0]["field"] == "docker_compose"


class _PostWorkRenderedOnlyStaleVoterOpener:
    def __init__(self) -> None:
        self.deleted: set[str] = set()
        self.reconciled = False
        self.requests: list[tuple[str, str]] = []

    def _raw_clean(self) -> str:
        return base64.b64encode(
            b"""services:
  mainnetc-super1:
    image: hyperledger/besu:latest
  mother-super-node-fdb:
    image: foundationdb/foundationdb:7.4.6
  mother-super-node-hub:
    image: mainnetc-super1-hub:c56a20a0fd05
"""
        ).decode("ascii")

    def _rendered(self) -> str:
        if self.reconciled:
            return self._raw_clean()
        return base64.b64encode(
            b"""services:
  mainnetc-super1:
    image: hyperledger/besu:latest
  mother-super-node-fdb:
    image: foundationdb/foundationdb:7.4.6
  mother-super-node-hub:
    image: mainnetc-super1-hub:c56a20a0fd05
  mother-add-node-validator-admission-voter-mainnetc-super1:
    image: python:3.12-alpine
"""
        ).decode("ascii")

    def _payload(self) -> dict[str, object]:
        applications = [
            _application("mainnetc-super1", "besu123", "running:healthy", "hyperledger/besu:latest", exclude_from_status=True),
            _application("mother-super-node-fdb", "fdb123", "running:healthy", "foundationdb/foundationdb:7.4.6"),
            _application("mother-super-node-hub", "hub123", "running:healthy", "mainnetc-super1-hub:c56a20a0fd05"),
        ]
        if "admissionvoter123" not in self.deleted:
            applications.append(
                _application(
                    "mother-add-node-validator-admission-voter-mainnetc-super1",
                    "admissionvoter123",
                    "running:healthy:excluded",
                    exclude_from_status=True,
                )
            )
        return {
            "name": "mainnetc-super1",
            "uuid": SERVICE_UUID,
            "status": "running:healthy",
            "applications": applications,
            "docker_compose_raw": self._raw_clean(),
            "docker_compose": self._rendered(),
        }

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        self.requests.append((method, path))
        if method == "GET" and path == f"/api/v1/services/{SERVICE_UUID}":
            return _Response(self._payload())
        if method == "DELETE" and path == "/api/v1/applications/admissionvoter123":
            self.deleted.add("admissionvoter123")
            return _Response({"uuid": "admissionvoter123", "deleted": True})
        if method == "PATCH" and path == f"/api/v1/services/{SERVICE_UUID}":
            raw = json.loads(request.data.decode("utf-8"))
            decoded = base64.b64decode(raw["docker_compose_raw"]).decode("utf-8")
            assert "mother-add-node-validator-admission-voter-mainnetc-super1" not in decoded
            self.reconciled = True
            return _Response({"uuid": SERVICE_UUID, "updated": True})
        raise AssertionError(f"unexpected request: {method} {path}")


def test_post_work_cleanup_execute_reconciles_rendered_only_stale_helper(tmp_path: Path) -> None:
    _, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_evidence(tmp_path, _clean_add_evidence())
    opener = _PostWorkRenderedOnlyStaleVoterOpener()

    result = execute_post_work_completed_helper_cleanup(
        private_state,
        network="mainnet",
        controller_id="coolify-c",
        service_uuid=SERVICE_UUID,
        node="mainnetc-super1",
        acknowledged_service_uuid=SERVICE_UUID,
        completion_evidence_path=evidence_path,
        completion_evidence_sha256=evidence_sha,
        workflow="add-node",
        allow_compose_rewrite=True,
        instant_deploy_compose_rewrite=True,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
        operation=_operation("post-work-cleanup-rendered-reconcile"),
    )

    assert result["status"] == "manual-review-required"
    assert result["summary"]["clean"] is True
    assert result["summary"]["initial_post_work_helper_candidate_count"] == 1
    assert result["summary"]["operator_host_cleanup_required"] is True
    assert "mother-add-node-validator-admission-voter-mainnetc-super1-" in result["operator_host_cleanup_commands"][0]["command"]
    assert result["summary"]["compose_rewrite_performed"] is True
    assert result["summary"]["compose_rewrite_succeeded"] is True
    assert result["summary"]["compose_rewrite_refresh_scope"] == "compose-reconcile"
    assert result["summary"]["compose_reconcile_performed"] is True
    assert result["summary"]["compose_reconcile_succeeded"] is True
    assert result["compose_rewrite"]["removed_helper_count"] == 0
    assert opener.reconciled is True
    assert ("DELETE", "/api/v1/applications/admissionvoter123") in opener.requests
    assert ("PATCH", f"/api/v1/services/{SERVICE_UUID}") in opener.requests



def test_cleanup_classifies_excluded_vote_helper_as_host_prune_candidate() -> None:
    raw_clean = base64.b64encode(
        b"""services:
  mainnetc-super1:
    image: hyperledger/besu:latest
  mother-super-node-fdb:
    image: foundationdb/foundationdb:7.4.6
  mother-super-node-hub:
    image: mainnetc-super1-hub:c56a20a0fd05
"""
    ).decode("ascii")

    summary = classify_post_work_helpers(
        payload={
            "name": "mainnetc-super1",
            "uuid": SERVICE_UUID,
            "status": "running:healthy",
            "applications": [
                _application("mainnetc-super1", "core", "running:healthy", "hyperledger/besu:latest"),
                _application("mother-super-node-fdb", "fdb", "running:healthy", "foundationdb/foundationdb:7.4.6"),
                _application("mother-super-node-hub", "hub", "running:healthy", "mainnetc-super1-hub:c56a20a0fd05"),
                _application(
                    "mother-add-node-validator-admission-voter-mainnetc-super1",
                    "voter",
                    "exited",
                    exclude_from_status=True,
                ),
                _application(
                    "mother-genesis-init",
                    "genesis-init",
                    "exited",
                    image="alpine:3.20",
                    exclude_from_status=True,
                ),
            ],
            "docker_compose_raw": raw_clean,
        },
        node="mainnetc-super1",
    )

    assert summary["summary"]["clean"] is False
    assert summary["summary"]["post_work_helper_candidate_count"] == 1
    assert summary["summary"]["operator_host_prune_helper_count"] == 1
    assert summary["post_work_helper_candidates"][0]["name"] == "mother-add-node-validator-admission-voter-mainnetc-super1"
    assert summary["post_work_helper_candidates"][0]["operator_host_prune_required"] is True
    assert [item["name"] for item in summary["already_excluded_post_work_helpers"]] == [
        "mother-add-node-validator-admission-voter-mainnetc-super1",
        "mother-genesis-init",
    ]


class _PostWorkExcludedVoterOrphanOpener:
    def __init__(self) -> None:
        self.deleted = False
        self.redeployed = False
        self.requests: list[tuple[str, str]] = []

    def _payload(self) -> dict[str, object]:
        applications = [
            _application("mainnetc-super1", "besu123", "running:healthy", "hyperledger/besu:latest", exclude_from_status=True),
            _application("mother-super-node-fdb", "fdb123", "running:healthy", "foundationdb/foundationdb:7.4.6"),
            _application("mother-super-node-hub", "hub123", "running:healthy", "mainnetc-super1-hub:c56a20a0fd05"),
        ]
        if not self.deleted:
            applications.append(
                _application(
                    "mother-add-node-validator-admission-voter-mainnetc-super1",
                    "admissionvoter123",
                    "exited",
                    exclude_from_status=True,
                )
            )
        return {
            "name": "mainnetc-super1",
            "uuid": SERVICE_UUID,
            "status": "running:healthy",
            "applications": applications,
            "docker_compose_raw": base64.b64encode(
                b"""services:
  mainnetc-super1:
    image: hyperledger/besu:latest
  mother-super-node-fdb:
    image: foundationdb/foundationdb:7.4.6
  mother-super-node-hub:
    image: mainnetc-super1-hub:c56a20a0fd05
"""
            ).decode("ascii"),
        }

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        self.requests.append((method, path))
        if method == "GET" and path == f"/api/v1/services/{SERVICE_UUID}":
            return _Response(self._payload())
        if method == "DELETE" and path == "/api/v1/applications/admissionvoter123":
            self.deleted = True
            return _Response({"uuid": "admissionvoter123", "deleted": True})
        if method == "GET" and path == "/api/v1/deploy":
            self.redeployed = True
            return _Response({"uuid": SERVICE_UUID, "deployment": "queued"})
        if method == "PATCH" and path == f"/api/v1/services/{SERVICE_UUID}":
            return _Response({"uuid": SERVICE_UUID, "updated": True})
        raise AssertionError(f"unexpected request: {method} {path}")


def test_execute_deletes_excluded_voter_but_requires_operator_host_prune(tmp_path: Path) -> None:
    _, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_evidence(tmp_path, _clean_add_evidence())
    opener = _PostWorkExcludedVoterOrphanOpener()

    result = execute_post_work_completed_helper_cleanup(
        private_state,
        network="mainnet",
        controller_id="coolify-c",
        service_uuid=SERVICE_UUID,
        node="mainnetc-super1",
        acknowledged_service_uuid=SERVICE_UUID,
        completion_evidence_path=evidence_path,
        completion_evidence_sha256=evidence_sha,
        workflow="add-node",
        allow_nested_application_delete=True,
        allow_compose_rewrite=True,
        instant_deploy_compose_rewrite=True,
        allow_service_redeploy_refresh=True,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
        operation=_operation("post-work-cleanup-excluded-voter-orphan"),
    )

    assert result["status"] == "manual-review-required"
    assert result["summary"]["clean"] is True
    assert result["summary"]["application_delete_count"] == 1
    assert result["summary"]["service_redeploy_refresh_performed"] is True
    assert result["summary"]["operator_host_cleanup_required"] is True
    assert result["summary"]["operator_host_cleanup_command_count"] == 1
    assert result["initial_operator_host_prune_helpers"][0]["name"] == "mother-add-node-validator-admission-voter-mainnetc-super1"
    command = result["operator_host_cleanup_commands"][0]["command"]
    assert "docker rm -f" in command
    assert f"mother-add-node-validator-admission-voter-mainnetc-super1-{SERVICE_UUID}" in command
    assert opener.deleted is True
    assert opener.redeployed is True



def test_cleanup_does_not_require_absent_optional_hub_fdb_before_helper_rewrite() -> None:
    raw_dirty = base64.b64encode(
        b"""services:
  mainneta-super1:
    image: hyperledger/besu:latest
  mother-add-node-validator-admission-voter-mainneta-super1:
    image: python:3.12-alpine
"""
    ).decode("ascii")

    summary = classify_post_work_helpers(
        payload={
            "name": "mainneta-super1",
            "uuid": "mzg0ttmqt4yqsurwjkv5un4a",
            "status": "running:unhealthy",
            "applications": [
                _application("mainneta-super1", "core", "running:healthy", "hyperledger/besu:latest", exclude_from_status=True),
                _application(
                    "mother-add-node-validator-admission-voter-mainneta-super1",
                    "voter",
                    "exited",
                    exclude_from_status=True,
                ),
            ],
            "docker_compose_raw": raw_dirty,
        },
        node="mainneta-super1",
    )

    assert summary["summary"]["core_required_components_healthy"] is True
    assert summary["summary"]["manual_review_required"] is False
    assert summary["summary"]["optional_absent_required_components"] == [
        "mother-super-node-fdb",
        "mother-super-node-hub",
    ]
    assert summary["summary"]["post_work_helper_candidate_count"] == 1
    assert summary["post_work_helper_candidates"][0]["name"] == "mother-add-node-validator-admission-voter-mainneta-super1"
    assert summary["post_work_helper_candidates"][0]["compose_declared"] is True


class _PostWorkNoHubFdbDirtyComposeOpener:
    def __init__(self) -> None:
        self.deleted: set[str] = set()
        self.patched = False
        self.requests: list[tuple[str, str]] = []

    def _compose(self) -> str:
        if self.patched:
            body = b"""services:
  mainneta-super1:
    image: hyperledger/besu:latest
"""
        else:
            body = b"""services:
  mainneta-super1:
    image: hyperledger/besu:latest
    depends_on:
      mother-validator-activation-init:
        condition: service_completed_successfully
  mother-validator-activation-init:
    image: alpine:3.20
  mother-replica-sync-guardian:
    image: python:3.12-alpine
  mother-add-node-validator-activation-guardian:
    image: python:3.12-alpine
  mother-add-node-validator-admission-voter-mainneta-super1:
    image: python:3.12-alpine
"""
        return base64.b64encode(body).decode("ascii")

    def _payload(self) -> dict[str, object]:
        applications = [
            _application("mainneta-super1", "besu123", "running:healthy", "hyperledger/besu:latest", exclude_from_status=True),
        ]
        for name, uuid, status, excluded in (
            ("mother-validator-activation-init", "activationinit123", "exited", True),
            ("mother-replica-sync-guardian", "replicaguardian123", "running:healthy", False),
            ("mother-add-node-validator-activation-guardian", "activationguardian123", "running:unhealthy", False),
            ("mother-add-node-validator-admission-voter-mainneta-super1", "admissionvoter123", "exited", True),
        ):
            if uuid not in self.deleted:
                applications.append(_application(name, uuid, status, exclude_from_status=excluded))
        return {
            "name": "mainneta-super1",
            "uuid": "mzg0ttmqt4yqsurwjkv5un4a",
            "status": "running:unhealthy" if not self.patched else "running:healthy",
            "applications": applications,
            "docker_compose_raw": self._compose(),
        }

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        self.requests.append((method, path))
        if method == "GET" and path == "/api/v1/services/mzg0ttmqt4yqsurwjkv5un4a":
            return _Response(self._payload())
        if method == "DELETE" and path.startswith("/api/v1/applications/"):
            self.deleted.add(path.rsplit("/", 1)[-1])
            return _Response({"uuid": path.rsplit("/", 1)[-1], "deleted": True})
        if method == "PATCH" and path == "/api/v1/services/mzg0ttmqt4yqsurwjkv5un4a":
            raw = json.loads(request.data.decode("utf-8"))
            decoded = base64.b64decode(raw["docker_compose_raw"]).decode("utf-8")
            assert "mother-add-node-validator-admission-voter-mainneta-super1" not in decoded
            assert "mother-add-node-validator-activation-guardian" not in decoded
            assert "mother-replica-sync-guardian" not in decoded
            assert "mother-validator-activation-init" not in decoded
            parsed = yaml.safe_load(decoded)
            assert "depends_on" not in parsed["services"]["mainneta-super1"]
            assert cleanup_cli._compose_missing_depends_on_targets(decoded) == ()
            self.patched = True
            return _Response({"uuid": "mzg0ttmqt4yqsurwjkv5un4a", "updated": True})
        raise AssertionError(f"unexpected request: {method} {path}")


def test_execute_rewrites_dirty_add_node_standby_without_optional_hub_fdb(tmp_path: Path) -> None:
    _, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_evidence(tmp_path, _clean_add_evidence(service_uuid="mzg0ttmqt4yqsurwjkv5un4a"))
    opener = _PostWorkNoHubFdbDirtyComposeOpener()

    result = execute_post_work_completed_helper_cleanup(
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        service_uuid="mzg0ttmqt4yqsurwjkv5un4a",
        node="mainneta-super1",
        acknowledged_service_uuid="mzg0ttmqt4yqsurwjkv5un4a",
        completion_evidence_path=evidence_path,
        completion_evidence_sha256=evidence_sha,
        workflow="add-node",
        allow_compose_rewrite=True,
        instant_deploy_compose_rewrite=True,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
        operation=_operation("post-work-cleanup-no-hub-fdb"),
    )

    assert result["status"] == "manual-review-required"
    assert result["summary"]["clean"] is True
    assert result["summary"]["operator_host_cleanup_required"] is True
    command = result["operator_host_cleanup_commands"][0]["command"]
    assert "mother-validator-activation-init-mzg0ttmqt4yqsurwjkv5un4a" in command
    assert "mother-add-node-validator-activation-guardian-mzg0ttmqt4yqsurwjkv5un4a" in command
    assert "mother-replica-sync-guardian-mzg0ttmqt4yqsurwjkv5un4a" in command
    assert "mother-add-node-validator-admission-voter-mainneta-super1-mzg0ttmqt4yqsurwjkv5un4a" in command
    assert result["summary"]["optional_absent_required_components"] == [
        "mother-super-node-fdb",
        "mother-super-node-hub",
    ]
    assert result["summary"]["initial_post_work_helper_candidate_count"] == 4
    assert result["summary"]["compose_rewrite_performed"] is True
    assert result["summary"]["compose_rewrite_succeeded"] is True
    assert result["summary"]["compose_rewrite_refresh_scope"] != "compose-reconcile"
    assert opener.patched is True


class _PostWorkDanglingActivationDependsOpener:
    def __init__(self) -> None:
        self.deleted: set[str] = set()
        self.patched = False
        self.requests: list[tuple[str, str]] = []

    def _compose(self) -> str:
        if self.patched:
            body = b"""services:
  mainneta-super1:
    image: hyperledger/besu:latest
    command:
      - '--host-allowlist=localhost,127.0.0.1,mainneta-super1,mother-add-node-validator-activation-guardian'
"""
        else:
            body = b"""services:
  mainneta-super1:
    image: hyperledger/besu:latest
    depends_on:
      mother-validator-activation-init:
        condition: service_completed_successfully
    command:
      - '--host-allowlist=localhost,127.0.0.1,mainneta-super1,mother-add-node-validator-activation-guardian'
"""
        return base64.b64encode(body).decode("ascii")

    def _payload(self) -> dict[str, object]:
        applications = [
            _application("mainneta-super1", "besu123", "running:healthy", "hyperledger/besu:latest", exclude_from_status=True),
        ]
        for name, uuid, status, excluded in (
            ("mother-replica-sync-guardian", "replicaguardian123", "running:healthy", False),
            ("mother-add-node-validator-activation-guardian", "activationguardian123", "running:unhealthy", False),
            ("mother-add-node-validator-admission-voter-mainneta-super1", "admissionvoter123", "exited", True),
        ):
            if uuid not in self.deleted:
                applications.append(_application(name, uuid, status, exclude_from_status=excluded))
        return {
            "name": "mainneta-super1",
            "uuid": "mzg0ttmqt4yqsurwjkv5un4a",
            "status": "running:healthy" if self.patched and len(self.deleted) == 3 else "running:unhealthy",
            "applications": applications,
            "docker_compose_raw": self._compose(),
        }

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        self.requests.append((method, path))
        if method == "GET" and path == "/api/v1/services/mzg0ttmqt4yqsurwjkv5un4a":
            return _Response(self._payload())
        if method == "DELETE" and path.startswith("/api/v1/applications/"):
            self.deleted.add(path.rsplit("/", 1)[-1])
            return _Response({"uuid": path.rsplit("/", 1)[-1], "deleted": True})
        if method == "PATCH" and path == "/api/v1/services/mzg0ttmqt4yqsurwjkv5un4a":
            raw = json.loads(request.data.decode("utf-8"))
            decoded = base64.b64decode(raw["docker_compose_raw"]).decode("utf-8")
            parsed_compose = yaml.safe_load(decoded)
            assert "depends_on" not in parsed_compose["services"]["mainneta-super1"]
            assert "mother-validator-activation-init:" not in decoded
            assert "mother-add-node-validator-activation-guardian" in decoded
            assert cleanup_cli._compose_missing_depends_on_targets(decoded) == ()
            self.patched = True
            return _Response({"uuid": "mzg0ttmqt4yqsurwjkv5un4a", "updated": True})
        raise AssertionError(f"unexpected request: {method} {path}")


def test_execute_repairs_dangling_activation_init_depends_on_without_helper_service(tmp_path: Path) -> None:
    _, private_state = _install(tmp_path)
    evidence_path, evidence_sha = _write_evidence(tmp_path, _clean_add_evidence(service_uuid="mzg0ttmqt4yqsurwjkv5un4a"))
    opener = _PostWorkDanglingActivationDependsOpener()

    result = execute_post_work_completed_helper_cleanup(
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        service_uuid="mzg0ttmqt4yqsurwjkv5un4a",
        node="mainneta-super1",
        acknowledged_service_uuid="mzg0ttmqt4yqsurwjkv5un4a",
        completion_evidence_path=evidence_path,
        completion_evidence_sha256=evidence_sha,
        workflow="add-node",
        allow_compose_rewrite=True,
        instant_deploy_compose_rewrite=True,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
        operation=_operation("post-work-cleanup-dangling-activation-depends"),
    )

    assert result["status"] == "manual-review-required"
    assert result["summary"]["clean"] is True
    assert result["summary"]["compose_rewrite_performed"] is True
    assert result["summary"]["compose_rewrite_succeeded"] is True
    assert result["compose_rewrite"]["removed_service_count"] == 0
    assert result["compose_rewrite"]["removed_dependency_names"] == ["mother-validator-activation-init"]
    command = result["operator_host_cleanup_commands"][0]["command"]
    assert "mother-validator-activation-init-mzg0ttmqt4yqsurwjkv5un4a" in command
    assert "mother-add-node-validator-activation-guardian-mzg0ttmqt4yqsurwjkv5un4a" in command
    assert "mother-replica-sync-guardian-mzg0ttmqt4yqsurwjkv5un4a" in command
    assert opener.patched is True


def test_cli_always_prints_operator_host_commands_footer(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    command = (
        "for c in 'mother-add-node-validator-admission-voter-mainneta-super1-mzg0ttmqt4yqsurwjkv5un4a'; do\n"
        "  docker rm -f \"$c\" 2>/dev/null || true\n"
        "done"
    )

    def fake_execute(*args, **kwargs):  # noqa: ANN002, ANN003
        return {
            "kind": "main_computer.mother.post_work_completed_helper_cleanup.v1",
            "schema_version": 1,
            "status": "manual-review-required",
            "operator_host_cleanup_commands": [
                {
                    "shell": "bash",
                    "description": "Run on the Docker host for this Coolify service.",
                    "command": command,
                    "container_names": [
                        "mother-add-node-validator-admission-voter-mainneta-super1-mzg0ttmqt4yqsurwjkv5un4a"
                    ],
                }
            ],
            "summary": {
                "operator_host_cleanup_required": True,
                "operator_host_cleanup_command_count": 1,
            },
        }

    monkeypatch.setattr(cleanup_cli, "_load_private_state", lambda *args, **kwargs: object())
    monkeypatch.setattr(cleanup_cli, "execute_post_work_completed_helper_cleanup", fake_execute)

    rc = cleanup_cli.main(
        [
            "--runtime-state-root",
            "runtime/state",
            "--network",
            "mainnet",
            "--controller-id",
            "coolify-a",
            "--service-uuid",
            "mzg0ttmqt4yqsurwjkv5un4a",
            "--node-name",
            "mainneta-super1",
            "--workflow",
            "add-node",
            "--completion-evidence",
            "completion.json",
            "--execute",
            "--acknowledge-service-uuid",
            "mzg0ttmqt4yqsurwjkv5un4a",
            "--quiet-progress",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 2
    parsed = json.loads(captured.out)
    assert parsed["summary"]["operator_host_cleanup_required"] is True
    assert "=== OPERATOR HOST CLEANUP REQUIRED ===" in captured.err
    assert "Run the following command(s) on the Docker host" in captured.err
    assert "docker rm -f" in captured.err
    assert "mother-add-node-validator-admission-voter-mainneta-super1-mzg0ttmqt4yqsurwjkv5un4a" in captured.err
    assert "--quiet-progress" not in captured.err


def test_cli_does_not_print_operator_host_footer_when_no_commands(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_inspect(*args, **kwargs):  # noqa: ANN002, ANN003
        return {
            "kind": "main_computer.mother.post_work_completed_helper_cleanup.v1",
            "schema_version": 1,
            "status": "pass",
            "operator_host_cleanup_commands": [],
            "summary": {
                "operator_host_cleanup_required": False,
                "operator_host_cleanup_command_count": 0,
            },
        }

    monkeypatch.setattr(cleanup_cli, "_load_private_state", lambda *args, **kwargs: object())
    monkeypatch.setattr(cleanup_cli, "inspect_post_work_completed_helper_cleanup", fake_inspect)

    rc = cleanup_cli.main(
        [
            "--runtime-state-root",
            "runtime/state",
            "--network",
            "mainnet",
            "--controller-id",
            "coolify-a",
            "--service-uuid",
            "mzg0ttmqt4yqsurwjkv5un4a",
            "--node-name",
            "mainneta-super1",
            "--workflow",
            "add-node",
            "--completion-evidence",
            "completion.json",
            "--quiet-progress",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 0
    json.loads(captured.out)
    assert "=== OPERATOR HOST CLEANUP REQUIRED ===" not in captured.err
    assert "docker rm -f" not in captured.err

