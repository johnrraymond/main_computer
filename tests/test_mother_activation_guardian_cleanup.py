from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import (
    install_verified_private_state,
    prepare_private_state_bootstrap,
    read_private_state,
)
from tools.mother_activation_guardian_cleanup import (
    MotherActivationGuardianCleanupError,
    SHIM_NAME,
    execute_activation_guardian_cleanup,
    inspect_activation_guardian_cleanup,
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
    operation = _operation("activation-guardian-cleanup-install")
    closure = prepare_private_state_bootstrap(
        paths,
        _starter_document(),
        updated_at="2026-08-18T01:00:00Z",
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    install_verified_private_state(paths, closure, None, operation=operation)
    private_state = read_private_state(paths, operation=_operation("activation-guardian-cleanup-read"))
    return runtime, private_state


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


class _ActivationShimOpener:
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
  mother-validator-activation-init:
    image: alpine:3.20
  mother-replica-sync-guardian:
    image: python:3.12-alpine
  mother-genesis-proof-guardian:
    image: alpine:3.20
  mother-add-node-validator-admission-voter-mainnetc-super1:
    image: python:3.12-alpine
"""
        if self.shim_installed:
            compose_obj = yaml.safe_load(compose)
            compose_obj["services"]["mother-add-node-validator-activation-guardian"] = {
                "image": "alpine:3.20",
                "container_name": f"mother-add-node-validator-activation-guardian-{SERVICE_UUID}",
                "labels": {
                    "main_computer.mother.post_work_shim": "true",
                    "main_computer.mother.not_a_proof_guardian": "true",
                },
                "healthcheck": {
                    "test": ["CMD", "sh", "-lc", "test -f /tmp/mother-retired-helper-shim"],
                },
            }
            compose = yaml.safe_dump(compose_obj, sort_keys=False)
        else:
            compose_obj = yaml.safe_load(compose)
            compose_obj["services"]["mother-add-node-validator-activation-guardian"] = {
                "image": "python:3.12-alpine",
                "volumes": ["proof-volume:/proof:ro"],
            }
            compose = yaml.safe_dump(compose_obj, sort_keys=False)
        return base64.b64encode(compose.encode("utf-8")).decode("ascii")

    def _payload(self) -> dict[str, object]:
        applications = [
            _application("mainnetc-super1", "besu123", "running:healthy", "hyperledger/besu:latest"),
            _application("mother-super-node-fdb", "fdb123", "running:healthy", "foundationdb/foundationdb:7.4.6"),
            _application("mother-super-node-hub", "hub123", "running:healthy", "mainnetc-super1-hub:c56a20a0fd05"),
            _application("mother-validator-activation-init", "activationinit123", "exited", "alpine:3.20"),
            _application("mother-replica-sync-guardian", "replicasync123", "running:healthy", "python:3.12-alpine"),
            _application("mother-genesis-proof-guardian", "genesisguardian123", "running:healthy", "alpine:3.20"),
            _application(
                "mother-add-node-validator-admission-voter-mainnetc-super1",
                "admissionvoter123",
                "running:unhealthy:excluded",
                exclude_from_status=True,
            ),
        ]
        if self.shim_installed:
            applications.append(
                _application(
                    "mother-add-node-validator-activation-guardian",
                    "activationguardian123",
                    "running:healthy",
                    "alpine:3.20",
                    labels={
                        "main_computer.mother.post_work_shim": "true",
                        "main_computer.mother.not_a_proof_guardian": "true",
                        "main_computer.mother.not_an_activation_guardian": "true",
                    },
                )
            )
        else:
            applications.append(_application("mother-add-node-validator-activation-guardian", "activationguardian123", "running:unhealthy"))
        return {
            "name": "mainnetc-super1",
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
            service_def = compose["services"]["mother-add-node-validator-activation-guardian"]
            assert service_def["image"] == "alpine:3.20"
            assert service_def["container_name"] == f"mother-add-node-validator-activation-guardian-{SERVICE_UUID}"
            assert service_def["healthcheck"]["test"] == ["CMD", "sh", "-lc", "test -f /tmp/mother-retired-helper-shim"]
            assert service_def["labels"]["main_computer.mother.post_work_shim"] == "true"
            assert service_def["labels"]["main_computer.mother.not_a_proof_guardian"] == "true"
            assert service_def["labels"]["main_computer.mother.not_an_activation_guardian"] == "true"
            assert "ports" not in service_def
            assert "volumes" not in service_def
            assert "/proof" not in json.dumps(service_def, sort_keys=True)

            # Scope guard: this standalone script preserves every non-target service,
            # including helpers that belong to later cleanup parts.
            services = compose["services"]
            assert "mainnetc-super1" in services
            assert "mother-super-node-fdb" in services
            assert "mother-super-node-hub" in services
            assert "mother-validator-activation-init" in services
            assert "mother-replica-sync-guardian" in services
            assert "mother-genesis-proof-guardian" in services
            assert "mother-add-node-validator-admission-voter-mainnetc-super1" in services

            self.shim_installed = True
            return _Response({"uuid": SERVICE_UUID, "updated": True})
        raise AssertionError(f"unexpected request: {method} {path}")


def test_inspect_reports_activation_guardian_cleanup_required_without_mutation(tmp_path: Path) -> None:
    _, private_state = _install(tmp_path)
    opener = _ActivationShimOpener()

    result = inspect_activation_guardian_cleanup(
        private_state,
        network="mainnet",
        controller_id="coolify-c",
        service_uuid=SERVICE_UUID,
        node="mainnetc-super1",
        opener=opener,
    )

    assert result["status"] == "cleanup-required"
    assert result["mutation_performed"] is False
    assert result["docker_touched"] is False
    assert result["chain_touched"] is False
    assert result["summary"]["compose_target_is_retired_shim"] is False
    assert result["summary"]["target_record_is_retired_shim"] is False
    assert opener.requests == [("GET", f"/api/v1/services/{SERVICE_UUID}")]


def test_execute_installs_only_minimal_retired_activation_guardian_shim(tmp_path: Path) -> None:
    _, private_state = _install(tmp_path)
    opener = _ActivationShimOpener()

    result = execute_activation_guardian_cleanup(
        private_state,
        network="mainnet",
        controller_id="coolify-c",
        service_uuid=SERVICE_UUID,
        node="mainnetc-super1",
        acknowledged_service_uuid=SERVICE_UUID,
        allow_retired_activation_guardian_shim=True,
        instant_deploy=True,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
    )

    assert result["status"] == "pass"
    assert result["summary"]["clean"] is True
    assert result["summary"]["retired_activation_guardian_shim_installed"] is True
    assert result["summary"]["retired_activation_guardian_shim_serves_proof"] is False
    assert result["summary"]["retired_activation_guardian_shim_mounts_proof_volume"] is False
    assert result["docker_touched"] is False
    assert result["chain_touched"] is False
    assert ("PATCH", f"/api/v1/services/{SERVICE_UUID}") in opener.requests
    assert opener.patched_compose is not None


def test_execute_refuses_without_explicit_shim_flag(tmp_path: Path) -> None:
    _, private_state = _install(tmp_path)
    opener = _ActivationShimOpener()

    with pytest.raises(MotherActivationGuardianCleanupError) as exc:
        execute_activation_guardian_cleanup(
            private_state,
            network="mainnet",
            controller_id="coolify-c",
            service_uuid=SERVICE_UUID,
            node="mainnetc-super1",
            acknowledged_service_uuid=SERVICE_UUID,
            allow_retired_activation_guardian_shim=False,
            opener=opener,
        )

    assert exc.value.code == "MOTHER_ACTIVATION_GUARDIAN_CLEANUP_SHIM_FLAG_REQUIRED"
    assert opener.requests == []


def test_execute_refuses_wrong_service_ack(tmp_path: Path) -> None:
    _, private_state = _install(tmp_path)
    opener = _ActivationShimOpener()

    with pytest.raises(MotherActivationGuardianCleanupError) as exc:
        execute_activation_guardian_cleanup(
            private_state,
            network="mainnet",
            controller_id="coolify-c",
            service_uuid=SERVICE_UUID,
            node="mainnetc-super1",
            acknowledged_service_uuid="wrong-service-uuid",
            allow_retired_activation_guardian_shim=True,
            opener=opener,
        )

    assert exc.value.code == "MOTHER_ACTIVATION_GUARDIAN_CLEANUP_ACK_REQUIRED"
    assert opener.requests == []
