from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import urlsplit

from tools.mother.common.deployment_completed_helper_cleanup import (
    execute_completed_mother_helper_cleanup,
    inspect_completed_mother_helper_cleanup,
    verify_completed_mother_helper_cleanup_evidence,
)
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import (
    install_verified_private_state,
    prepare_private_state_bootstrap,
    read_private_state,
)
from tests.test_mother_deployment_executor import TOKEN_A, _operation, _starter_document


SERVICE_UUID = "lmjwoglwv7ryvrfsbfuu4o7k"


def _install(tmp_path: Path):
    runtime = tmp_path / "runtime" / "state"
    paths = MotherPaths(runtime_state_root=runtime).resolve_private_state_paths()
    operation = _operation("completed-helper-cleanup-install")
    closure = prepare_private_state_bootstrap(
        paths,
        _starter_document(),
        updated_at="2026-07-31T01:01:00Z",
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    install_verified_private_state(paths, closure, None, operation=operation)
    private_state = read_private_state(paths, operation=_operation("completed-helper-cleanup-read"))
    return paths, private_state


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


def _application(name: str, uuid: str, status: str, image: str = "python:3.12-alpine") -> dict[str, str]:
    return {
        "name": name,
        "uuid": uuid,
        "status": status,
        "image": image,
    }


def _compose_with_helpers() -> str:
    return """services:
  mainneta-super1:
    image: hyperledger/besu:latest
  mother-super-node-fdb:
    image: foundationdb/foundationdb:7.4.6
  mother-super-node-hub:
    image: mainneta-super1-hub:c56a20a0fd05
  mother-validator-quorum-recovery-initial-guardian:
    image: python:3.12-alpine
  mother-validator-admission-guardian:
    image: python:3.12-alpine
  mother-genesis-proof-guardian:
    image: python:3.12-alpine
  mother-genesis-init:
    image: alpine:3.20
  mother-superseded-service-cleanup:
    image: docker:27-cli
"""


class _CleanupOpener:
    def __init__(self, *, core_status: str = "running:healthy") -> None:
        self.deleted: set[str] = set()
        self.requests: list[tuple[str, str]] = []
        self.core_status = core_status
        self.helper_uuids = {
            "mother-validator-admission-guardian": "r12anream90irkyn0td5uhso",
            "mother-genesis-proof-guardian": "dd5122y9wf5tggblnszbbf7v",
            "mother-genesis-init": "uf9ulq9tadgkl0ygaq33y72q",
            "mother-superseded-service-cleanup": "um6yb9se98x55qt06f4wprk1",
        }

    def _payload(self) -> dict:
        applications = [
            _application(
                "mother-validator-quorum-recovery-initial-guardian",
                "q11z2wtucqpvdnqrxwx02yc3",
                "running:healthy",
            ),
            _application("mother-super-node-hub", "b2plnj2sakvdvuoi3ybr3e1g", "running:healthy", "mainneta-super1-hub:c56a20a0fd05"),
            _application("mainneta-super1", "rkf1x9lnuq0c9s9iumx2fgu7", self.core_status, "hyperledger/besu:latest"),
            _application("mother-super-node-fdb", "chqaglz7112nqbv6966l70ln", "running:healthy", "foundationdb/foundationdb:7.4.6"),
        ]
        for name, uuid in self.helper_uuids.items():
            if uuid not in self.deleted:
                image = "alpine:3.20" if name == "mother-genesis-init" else "python:3.12-alpine"
                if name == "mother-superseded-service-cleanup":
                    image = "docker:27-cli"
                applications.append(_application(name, uuid, "exited", image))
        parent_clean = self.deleted == set(self.helper_uuids.values()) and self.core_status == "running:healthy"
        return {
            "name": "mainneta-super1",
            "uuid": SERVICE_UUID,
            "status": "running:healthy" if parent_clean else "degraded:unhealthy",
            "applications": applications,
            "docker_compose_raw": base64.b64encode(_compose_with_helpers().encode("utf-8")).decode("ascii"),
        }

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        assert parsed.hostname == "coolify-a.invalid"
        assert request.headers.get("Authorization") == f"Bearer {TOKEN_A}"
        assert timeout > 0
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
        raise AssertionError(f"unexpected request: {method} {path}")


def test_completed_helper_cleanup_deletes_only_known_exited_helpers_and_preserves_core(
    tmp_path: Path,
) -> None:
    paths, private_state = _install(tmp_path)
    opener = _CleanupOpener()

    inspected = inspect_completed_mother_helper_cleanup(
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        service_uuid=SERVICE_UUID,
        node="mainneta-super1",
        opener=opener,
        operation=_operation("completed-helper-cleanup-inspect"),
    )
    assert inspected["status"] == "manual-review-required"
    assert inspected["summary"]["completed_helper_candidate_count"] == 4
    assert inspected["summary"]["core_required_components_healthy"] is True

    result = execute_completed_mother_helper_cleanup(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        service_uuid=SERVICE_UUID,
        node="mainneta-super1",
        acknowledged_service_uuid=SERVICE_UUID,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
        operation=_operation("completed-helper-cleanup-execute"),
    )

    assert result["status"] == "pass"
    assert result["summary"]["clean"] is True
    assert result["summary"]["application_delete_count"] == 4
    assert result["summary"]["validator_mutation_count"] == 0
    assert result["summary"]["validator_restart_count"] == 0
    assert result["summary"]["validator_vote_performed"] is False
    assert result["final_parent"]["status"] == "running:healthy"
    assert {item["application_uuid"] for item in result["deleted_applications"]} == set(opener.helper_uuids.values())
    assert ("DELETE", "/api/v1/applications/q11z2wtucqpvdnqrxwx02yc3") not in opener.requests

    verified = verify_completed_mother_helper_cleanup_evidence(
        paths,
        Path(result["evidence"]["path"]),
    )
    assert verified["clean"] is True
    assert verified["service_uuid"] == SERVICE_UUID


def test_completed_helper_cleanup_does_not_mask_real_unhealthy_core(
    tmp_path: Path,
) -> None:
    paths, private_state = _install(tmp_path)
    opener = _CleanupOpener(core_status="running:unhealthy")

    result = execute_completed_mother_helper_cleanup(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        service_uuid=SERVICE_UUID,
        node="mainneta-super1",
        acknowledged_service_uuid=SERVICE_UUID,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
        operation=_operation("completed-helper-cleanup-unhealthy-core"),
    )

    assert result["status"] == "manual-review-required"
    assert result["summary"]["application_delete_count"] == 4
    assert result["summary"]["core_required_components_healthy"] is False
    assert result["summary"]["clean"] is False
    assert result["final_parent"]["status"] == "degraded:unhealthy"
    assert result["summary"]["validator_mutation_count"] == 0
    assert result["summary"]["validator_restart_count"] == 0
    assert result["summary"]["validator_vote_performed"] is False


class _ComposeRewriteCleanupOpener(_CleanupOpener):
    def __init__(self) -> None:
        super().__init__()
        self.rewritten = False
        self.patch_bodies: list[dict] = []

    def _payload(self) -> dict:
        payload = super()._payload()
        if self.rewritten:
            applications = [
                item
                for item in payload["applications"]
                if item["name"] not in self.helper_uuids
            ]
            payload["applications"] = applications
            payload["status"] = "running:healthy"
        return payload

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        self.requests.append((method, path))
        if method == "GET" and path == f"/api/v1/services/{SERVICE_UUID}":
            return _Response(self._payload())
        if method == "DELETE" and path.startswith("/api/v1/applications/"):
            return _Response({"message": "Resource not found."}, status=404)
        if method == "PATCH" and path == f"/api/v1/services/{SERVICE_UUID}":
            raw = request.data or b"{}"
            body = json.loads(raw.decode("utf-8"))
            decoded = base64.b64decode(body["docker_compose_raw"]).decode("utf-8")
            assert "mother-validator-admission-guardian" not in decoded
            assert "mother-genesis-proof-guardian" not in decoded
            assert "mother-genesis-init" not in decoded
            assert "mother-superseded-service-cleanup" not in decoded
            assert "mainneta-super1" in decoded
            assert "mother-super-node-fdb" in decoded
            assert "mother-super-node-hub" in decoded
            assert body["instant_deploy"] is True
            self.patch_bodies.append(body)
            self.rewritten = True
            return _Response({"uuid": SERVICE_UUID})
        raise AssertionError(f"unexpected request: {method} {path}")


def test_completed_helper_cleanup_rewrites_service_compose_when_nested_application_delete_404s(
    tmp_path: Path,
) -> None:
    paths, private_state = _install(tmp_path)
    opener = _ComposeRewriteCleanupOpener()

    result = execute_completed_mother_helper_cleanup(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        service_uuid=SERVICE_UUID,
        node="mainneta-super1",
        acknowledged_service_uuid=SERVICE_UUID,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        allow_compose_rewrite=True,
        instant_deploy_compose_rewrite=True,
        opener=opener,
        operation=_operation("completed-helper-cleanup-compose-rewrite"),
    )

    assert result["status"] == "pass"
    assert result["summary"]["clean"] is True
    assert result["summary"]["application_delete_count"] == 4
    assert result["summary"]["application_delete_success_count"] == 0
    assert result["summary"]["service_compose_rewrite_count"] == 1
    assert result["summary"]["service_compose_rewrite_succeeded"] is True
    assert result["summary"]["service_compose_rewrite_instant_deploy"] is True
    assert result["summary"]["service_redeploy_requested"] is True
    assert result["summary"]["cleanup_mutation_succeeded"] is True
    assert result["service_compose_rewrite"]["removed_service_count"] == 4
    assert opener.patch_bodies
    assert ("PATCH", f"/api/v1/services/{SERVICE_UUID}") in opener.requests


class _SplitComposeCleanupOpener(_ComposeRewriteCleanupOpener):
    def _payload(self) -> dict:
        payload = super()._payload()
        payload["docker_compose_raw"] = base64.b64encode(
            """services:
  mainneta-super1:
    image: hyperledger/besu:latest
  mother-super-node-fdb:
    image: foundationdb/foundationdb:7.4.6
  mother-super-node-hub:
    image: mainneta-super1-hub:c56a20a0fd05
  mother-validator-quorum-recovery-initial-guardian:
    image: python:3.12-alpine
  mother-superseded-service-cleanup:
    image: docker:27-cli
""".encode("utf-8")
        ).decode("ascii")
        payload["docker_compose"] = _compose_with_helpers()
        return payload


def test_completed_helper_cleanup_uses_rendered_compose_when_raw_contains_only_one_helper(
    tmp_path: Path,
) -> None:
    paths, private_state = _install(tmp_path)
    opener = _SplitComposeCleanupOpener()

    result = execute_completed_mother_helper_cleanup(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        service_uuid=SERVICE_UUID,
        node="mainneta-super1",
        acknowledged_service_uuid=SERVICE_UUID,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        allow_compose_rewrite=True,
        instant_deploy_compose_rewrite=True,
        opener=opener,
        operation=_operation("completed-helper-cleanup-split-compose"),
    )

    assert result["status"] == "pass"
    assert result["summary"]["clean"] is True
    assert result["service_compose_rewrite"]["source_field"] == "docker_compose"
    assert result["service_compose_rewrite"]["removed_service_count"] == 4
    assert set(result["service_compose_rewrite"]["removed_helper_names"]) == set(opener.helper_uuids)
    attempts = result["service_compose_rewrite"]["compose_source_attempts"]
    assert [attempt["source_field"] for attempt in attempts] == ["docker_compose", "docker_compose_raw"]
    assert attempts[0]["removed_service_count"] == 4
    assert attempts[1]["removed_service_count"] == 1


class _StaleNestedApplicationCleanupOpener(_CleanupOpener):
    def __init__(self) -> None:
        super().__init__()
        self.nested_deleted: set[str] = set()

    def _payload(self) -> dict:
        applications = [
            _application(
                "mother-validator-quorum-recovery-initial-guardian",
                "q11z2wtucqpvdnqrxwx02yc3",
                "running:healthy",
            ),
            _application("mother-super-node-hub", "b2plnj2sakvdvuoi3ybr3e1g", "running:healthy", "mainneta-super1-hub:c56a20a0fd05"),
            _application("mainneta-super1", "rkf1x9lnuq0c9s9iumx2fgu7", self.core_status, "hyperledger/besu:latest"),
            _application("mother-super-node-fdb", "chqaglz7112nqbv6966l70ln", "running:healthy", "foundationdb/foundationdb:7.4.6"),
        ]
        for name, uuid in self.helper_uuids.items():
            if uuid not in self.nested_deleted:
                image = "alpine:3.20" if name == "mother-genesis-init" else "python:3.12-alpine"
                if name == "mother-superseded-service-cleanup":
                    image = "docker:27-cli"
                applications.append(_application(name, uuid, "exited", image))
        parent_clean = self.nested_deleted == set(self.helper_uuids.values()) and self.core_status == "running:healthy"
        return {
            "name": "mainneta-super1",
            "uuid": SERVICE_UUID,
            "status": "running:healthy" if parent_clean else "degraded:unhealthy",
            "applications": applications,
            "docker_compose_raw": base64.b64encode(
                """services:
  mainneta-super1:
    image: hyperledger/besu:latest
  mother-super-node-fdb:
    image: foundationdb/foundationdb:7.4.6
  mother-super-node-hub:
    image: mainneta-super1-hub:c56a20a0fd05
  mother-validator-quorum-recovery-initial-guardian:
    image: python:3.12-alpine
""".encode("utf-8")
            ).decode("ascii"),
            "docker_compose": """services:
  mainneta-super1:
    image: hyperledger/besu:latest
  mother-super-node-fdb:
    image: foundationdb/foundationdb:7.4.6
  mother-super-node-hub:
    image: mainneta-super1-hub:c56a20a0fd05
  mother-validator-quorum-recovery-initial-guardian:
    image: python:3.12-alpine
""",
        }

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        self.requests.append((method, path))
        if method == "GET" and path == f"/api/v1/services/{SERVICE_UUID}":
            return _Response(self._payload())
        if method == "DELETE" and path.startswith("/api/v1/applications/"):
            return _Response({"message": "Resource not found."}, status=404)
        prefix = f"/api/v1/services/{SERVICE_UUID}/applications/"
        if method == "DELETE" and path.startswith(prefix):
            app_uuid = path.rsplit("/", 1)[-1]
            assert app_uuid in self.helper_uuids.values()
            self.nested_deleted.add(app_uuid)
            return _Response({"uuid": app_uuid, "deleted": True})
        if method == "PATCH" and path == f"/api/v1/services/{SERVICE_UUID}":
            raise AssertionError("nested stale application cleanup should not rewrite compose after nested delete succeeds")
        raise AssertionError(f"unexpected request: {method} {path}")


def test_completed_helper_cleanup_deletes_stale_nested_application_records_when_compose_is_already_clean(
    tmp_path: Path,
) -> None:
    paths, private_state = _install(tmp_path)
    opener = _StaleNestedApplicationCleanupOpener()

    result = execute_completed_mother_helper_cleanup(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        service_uuid=SERVICE_UUID,
        node="mainneta-super1",
        acknowledged_service_uuid=SERVICE_UUID,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        allow_nested_application_delete=True,
        allow_compose_rewrite=True,
        instant_deploy_compose_rewrite=True,
        opener=opener,
        operation=_operation("completed-helper-cleanup-stale-nested-records"),
    )

    assert result["status"] == "pass"
    assert result["summary"]["clean"] is True
    assert result["final_parent"]["status"] == "running:healthy"
    assert result["summary"]["application_delete_count"] == 4
    assert result["summary"]["application_delete_success_count"] == 0
    assert result["summary"]["nested_application_delete_count"] == 4
    assert result["summary"]["nested_application_delete_success_count"] == 4
    assert result["summary"]["all_nested_application_delete_requests_succeeded"] is True
    assert result["summary"]["service_compose_rewrite_count"] == 0
    assert result["service_compose_rewrite"] is None
    assert {item["application_uuid"] for item in result["nested_deleted_applications"]} == set(opener.helper_uuids.values())
    assert ("DELETE", f"/api/v1/services/{SERVICE_UUID}/applications/r12anream90irkyn0td5uhso") in opener.requests


class _RedeployRefreshCleanupOpener(_StaleNestedApplicationCleanupOpener):
    def __init__(self) -> None:
        super().__init__()
        self.deploy_requested = False

    def _payload(self) -> dict:
        payload = super()._payload()
        if self.deploy_requested:
            payload["applications"] = [
                item
                for item in payload["applications"]
                if item["name"] not in self.helper_uuids
            ]
            payload["status"] = "running:healthy"
        return payload

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        self.requests.append((method, path))
        if method == "GET" and path == f"/api/v1/services/{SERVICE_UUID}":
            return _Response(self._payload())
        if method == "DELETE" and path.startswith("/api/v1/applications/"):
            return _Response({"message": "Resource not found."}, status=404)
        if method == "DELETE" and path.startswith(f"/api/v1/services/{SERVICE_UUID}/applications/"):
            return _Response({"message": "Resource not found."}, status=404)
        if method == "DELETE" and path.startswith(f"/api/v1/services/{SERVICE_UUID}/application/"):
            return _Response({"message": "Resource not found."}, status=404)
        if method == "GET" and path == "/api/v1/deploy":
            assert parsed.query == f"uuid={SERVICE_UUID}&force=true"
            self.deploy_requested = True
            return _Response({"deployments": [{"resource_uuid": SERVICE_UUID, "deployment_uuid": "dep-refresh-1"}]})
        if method == "PATCH" and path == f"/api/v1/services/{SERVICE_UUID}":
            raise AssertionError("redeploy refresh should be used when compose is already clean")
        raise AssertionError(f"unexpected request: {method} {path}")


def test_completed_helper_cleanup_requests_redeploy_refresh_when_compose_is_clean_and_delete_endpoints_404(
    tmp_path: Path,
) -> None:
    paths, private_state = _install(tmp_path)
    opener = _RedeployRefreshCleanupOpener()

    result = execute_completed_mother_helper_cleanup(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        service_uuid=SERVICE_UUID,
        node="mainneta-super1",
        acknowledged_service_uuid=SERVICE_UUID,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        allow_nested_application_delete=True,
        allow_compose_rewrite=True,
        instant_deploy_compose_rewrite=True,
        allow_service_redeploy_refresh=True,
        opener=opener,
        operation=_operation("completed-helper-cleanup-redeploy-refresh"),
    )

    assert result["status"] == "pass"
    assert result["summary"]["clean"] is True
    assert result["summary"]["application_delete_success_count"] == 0
    assert result["summary"]["nested_application_delete_success_count"] == 0
    assert result["summary"]["service_compose_rewrite_succeeded"] is False
    assert result["summary"]["service_redeploy_refresh_count"] == 1
    assert result["summary"]["service_redeploy_refresh_succeeded"] is True
    assert result["summary"]["service_redeploy_refresh_force"] is True
    assert result["summary"]["service_redeploy_requested"] is True
    assert result["summary"]["cleanup_mutation_succeeded"] is True
    assert result["final_parent"]["status"] == "running:healthy"
    assert result["service_redeploy_refresh"]["endpoint"] == f"/api/v1/deploy?uuid={SERVICE_UUID}&force=true"
    assert ("GET", "/api/v1/deploy") in opener.requests


class _ComposeReconcileRefreshCleanupOpener(_RedeployRefreshCleanupOpener):
    def __init__(self) -> None:
        super().__init__()
        self.compose_reconciled = False
        self.reconcile_body: dict | None = None

    def _payload(self) -> dict:
        payload = _StaleNestedApplicationCleanupOpener._payload(self)
        if self.compose_reconciled:
            payload["applications"] = [
                item
                for item in payload["applications"]
                if item["name"] not in self.helper_uuids
            ]
            payload["status"] = "running:healthy"
        return payload

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        self.requests.append((method, path))
        if method == "GET" and path == f"/api/v1/services/{SERVICE_UUID}":
            return _Response(self._payload())
        if method == "DELETE" and path.startswith("/api/v1/applications/"):
            return _Response({"message": "Resource not found."}, status=404)
        if method == "DELETE" and path.startswith(f"/api/v1/services/{SERVICE_UUID}/applications/"):
            return _Response({"message": "Resource not found."}, status=404)
        if method == "DELETE" and path.startswith(f"/api/v1/services/{SERVICE_UUID}/application/"):
            return _Response({"message": "Resource not found."}, status=404)
        if method == "PATCH" and path == f"/api/v1/services/{SERVICE_UUID}":
            self.reconcile_body = json.loads(request.data.decode("utf-8"))
            decoded = base64.b64decode(self.reconcile_body["docker_compose_raw"]).decode("utf-8")
            assert "mother-genesis-init" not in decoded
            assert "mother-validator-admission-guardian" not in decoded
            assert self.reconcile_body["instant_deploy"] is True
            self.compose_reconciled = True
            return _Response({"message": "Service updated."})
        if method == "GET" and path == "/api/v1/deploy":
            raise AssertionError("compose reconcile should run before service redeploy refresh")
        raise AssertionError(f"unexpected request: {method} {path}")


def test_completed_helper_cleanup_reconciles_clean_compose_when_stale_records_survive_delete_and_redeploy_is_not_enough(
    tmp_path: Path,
) -> None:
    paths, private_state = _install(tmp_path)
    opener = _ComposeReconcileRefreshCleanupOpener()

    result = execute_completed_mother_helper_cleanup(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        service_uuid=SERVICE_UUID,
        node="mainneta-super1",
        acknowledged_service_uuid=SERVICE_UUID,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        allow_nested_application_delete=True,
        allow_compose_rewrite=True,
        instant_deploy_compose_rewrite=True,
        allow_compose_reconcile_refresh=True,
        instant_deploy_compose_reconcile_refresh=True,
        allow_service_redeploy_refresh=True,
        opener=opener,
        operation=_operation("completed-helper-cleanup-compose-reconcile-refresh"),
    )

    assert result["status"] == "pass"
    assert result["summary"]["clean"] is True
    assert result["summary"]["application_delete_success_count"] == 0
    assert result["summary"]["nested_application_delete_success_count"] == 0
    assert result["summary"]["service_compose_rewrite_succeeded"] is False
    assert result["summary"]["service_compose_reconcile_count"] == 1
    assert result["summary"]["service_compose_reconcile_succeeded"] is True
    assert result["summary"]["service_compose_reconcile_instant_deploy"] is True
    assert result["summary"]["service_redeploy_refresh_count"] == 0
    assert result["summary"]["service_redeploy_requested"] is True
    assert result["final_parent"]["status"] == "running:healthy"
    assert result["service_compose_reconcile"]["refresh_scope"] == "compose-reconcile"
    assert result["service_compose_reconcile"]["removed_helper_count"] == 0
    assert ("PATCH", f"/api/v1/services/{SERVICE_UUID}") in opener.requests



class _DockerOrphanCleanupOpener(_ComposeReconcileRefreshCleanupOpener):
    def __init__(self) -> None:
        super().__init__()
        self.orphan_cleanup_created = False
        self.orphan_cleanup_started = False
        self.orphan_cleanup_deleted = False
        self.orphan_cleanup_body: dict | None = None

    def _payload(self) -> dict:
        payload = _StaleNestedApplicationCleanupOpener._payload(self)
        if self.orphan_cleanup_started:
            payload["applications"] = [
                item
                for item in payload["applications"]
                if item["name"] not in self.helper_uuids
            ]
            payload["status"] = "running:healthy"
        return payload

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        self.requests.append((method, path))
        if method == "GET" and path == f"/api/v1/services/{SERVICE_UUID}":
            return _Response(self._payload())
        if method == "DELETE" and path.startswith("/api/v1/applications/"):
            return _Response({"message": "Resource not found."}, status=404)
        if method == "DELETE" and path.startswith(f"/api/v1/services/{SERVICE_UUID}/applications/"):
            return _Response({"message": "Resource not found."}, status=404)
        if method == "DELETE" and path.startswith(f"/api/v1/services/{SERVICE_UUID}/application/"):
            return _Response({"message": "Resource not found."}, status=404)
        if method == "PATCH" and path == f"/api/v1/services/{SERVICE_UUID}":
            self.reconcile_body = json.loads(request.data.decode("utf-8"))
            self.compose_reconciled = True
            return _Response({"message": "Service updated."})
        if method == "GET" and path == "/api/v1/deploy":
            self.deploy_requested = True
            return _Response({"deployments": [{"resource_uuid": SERVICE_UUID, "deployment_uuid": "dep-refresh-1"}]})
        if method == "GET" and path.startswith("/api/v1/projects/") and path.endswith("/environments"):
            return _Response({"environments": [{"name": "mainnet", "uuid": "env-mainnet-1"}]})
        if method == "POST" and path == "/api/v1/services":
            self.orphan_cleanup_body = json.loads(request.data.decode("utf-8"))
            decoded = base64.b64decode(self.orphan_cleanup_body["docker_compose_raw"]).decode("utf-8")
            assert "/var/run/docker.sock:/var/run/docker.sock" in decoded
            assert f"project='{SERVICE_UUID}'" in decoded
            assert "docker rm -f" in decoded
            assert "mother-validator-admission-guardian" in decoded
            assert "mother-genesis-proof-guardian" in decoded
            assert "mother-genesis-init" in decoded
            assert "mother-superseded-service-cleanup" in decoded
            assert self.orphan_cleanup_body["environment_uuid"] == "env-mainnet-1"
            self.orphan_cleanup_created = True
            return _Response({"uuid": "tmpcleanup123"})
        if method == "POST" and path == "/api/v1/services/tmpcleanup123/start":
            assert self.orphan_cleanup_created
            self.orphan_cleanup_started = True
            return _Response({"message": "started"})
        if method == "GET" and path == "/api/v1/services/tmpcleanup123":
            status = "running:healthy" if self.orphan_cleanup_started else "exited"
            return _Response({"uuid": "tmpcleanup123", "name": f"mother-helper-orphan-cleanup-{SERVICE_UUID[:8]}", "status": status})
        if method == "DELETE" and path == "/api/v1/services/tmpcleanup123":
            self.orphan_cleanup_deleted = True
            return _Response({"message": "deleted"})
        raise AssertionError(f"unexpected request: {method} {path}")


def test_completed_helper_cleanup_removes_docker_orphan_containers_after_api_and_compose_refresh_fail(
    tmp_path: Path,
) -> None:
    paths, private_state = _install(tmp_path)
    opener = _DockerOrphanCleanupOpener()

    result = execute_completed_mother_helper_cleanup(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        service_uuid=SERVICE_UUID,
        node="mainneta-super1",
        acknowledged_service_uuid=SERVICE_UUID,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        allow_nested_application_delete=True,
        allow_compose_rewrite=True,
        instant_deploy_compose_rewrite=True,
        allow_compose_reconcile_refresh=True,
        instant_deploy_compose_reconcile_refresh=True,
        allow_service_redeploy_refresh=True,
        allow_docker_orphan_container_cleanup=True,
        opener=opener,
        operation=_operation("completed-helper-cleanup-docker-orphans"),
    )

    assert result["status"] == "pass"
    assert result["summary"]["clean"] is True
    assert result["final_parent"]["status"] == "running:healthy"
    assert result["summary"]["docker_orphan_container_cleanup_count"] == 1
    assert result["summary"]["docker_orphan_container_cleanup_succeeded"] is True
    assert result["summary"]["docker_orphan_container_cleanup_enabled"] is True
    assert result["docker_orphan_container_cleanup"]["ok"] is True
    assert result["docker_orphan_container_cleanup"]["health"]["healthy"] is True
    assert result["docker_orphan_container_cleanup"]["health"]["temporary_service_delete"]["ok"] is True
    assert opener.orphan_cleanup_created is True
    assert opener.orphan_cleanup_started is True
    assert opener.orphan_cleanup_deleted is True
    decoded_compose = base64.b64decode(opener.orphan_cleanup_body["docker_compose_raw"]).decode("utf-8")
    assert "restart: \"no\"" in decoded_compose
    assert "DOCKER_CONFIG: /proof/.docker" in decoded_compose
    assert "TMPDIR: /tmp" in decoded_compose
    assert 'healthy="$$proof_dir/healthy"' in decoded_compose
    assert 'proof="$$proof_dir/completed-helper-orphan-cleanup.json"' in decoded_compose
    assert 'failure="$$proof_dir/completed-helper-orphan-cleanup-failed.json"' in decoded_compose
    assert 'log="$$proof_dir/completed-helper-orphan-cleanup.log"' in decoded_compose
    assert 'mkdir -p "$$proof_dir" "$$proof_dir/.docker"' in decoded_compose
    assert 'case "$${1:-}" in' in decoded_compose
    assert 'candidate_count=$$((candidate_count + 1))' in decoded_compose
    assert "$" not in decoded_compose.replace("$$", "")
    assert "healthy='$proof_dir/healthy'" not in decoded_compose
    assert "mkdir -p '$proof_dir' '$proof_dir/.docker'" not in decoded_compose
    assert "normalize_label()" in decoded_compose
    assert "''|'<no value>'|'<nil>'|'null')" in decoded_compose
    assert "*mother-genesis-init*)" in decoded_compose
    assert "exec tail -f /dev/null" in decoded_compose
    assert "test -f /proof/healthy && test -f /proof/completed-helper-orphan-cleanup.json" in decoded_compose
    assert "ps -o comm= -p 1" not in decoded_compose
    assert "$${1:-}" in decoded_compose
    assert "$$(docker ps -aq" in decoded_compose
    assert ("POST", "/api/v1/services") in opener.requests
    assert ("POST", "/api/v1/services/tmpcleanup123/start") in opener.requests
    assert ("DELETE", "/api/v1/services/tmpcleanup123") in opener.requests

class _StaleTemporaryStatusDockerOrphanCleanupOpener(_DockerOrphanCleanupOpener):
    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        method = request.get_method()
        path = parsed.path
        if method == "GET" and path == "/api/v1/services/tmpcleanup123":
            self.requests.append((method, path))
            return _Response(
                {
                    "uuid": "tmpcleanup123",
                    "name": f"mother-helper-orphan-cleanup-{SERVICE_UUID[:8]}",
                    "status": "exited",
                }
            )
        return super().open(request, timeout)


def test_completed_helper_cleanup_credits_docker_cleanup_from_clean_parent_when_temporary_status_is_stale(
    tmp_path: Path,
) -> None:
    paths, private_state = _install(tmp_path)
    opener = _StaleTemporaryStatusDockerOrphanCleanupOpener()

    result = execute_completed_mother_helper_cleanup(
        paths,
        private_state,
        network="mainnet",
        controller_id="coolify-a",
        service_uuid=SERVICE_UUID,
        node="mainneta-super1",
        acknowledged_service_uuid=SERVICE_UUID,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        allow_nested_application_delete=True,
        allow_compose_rewrite=True,
        instant_deploy_compose_rewrite=True,
        allow_compose_reconcile_refresh=True,
        instant_deploy_compose_reconcile_refresh=True,
        allow_service_redeploy_refresh=True,
        allow_docker_orphan_container_cleanup=True,
        opener=opener,
        operation=_operation("completed-helper-cleanup-docker-orphans-stale-temp-status"),
    )

    assert result["status"] == "pass"
    assert result["summary"]["clean"] is True
    assert result["final_parent"]["status"] == "running:healthy"
    assert result["summary"]["docker_orphan_container_cleanup_succeeded"] is True
    cleanup_result = result["docker_orphan_container_cleanup"]
    assert cleanup_result["ok"] is True
    assert cleanup_result["health"]["healthy"] is False
    assert cleanup_result["health"]["final_status"] == "exited"
    assert cleanup_result["verification"] == {
        "source": "final-parent-service-recheck",
        "parent_status": "running:healthy",
        "clean": True,
        "temporary_service_status_advisory": True,
    }
    assert cleanup_result["health"]["temporary_service_delete"]["ok"] is True

