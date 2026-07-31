from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tools.mother.common.deployment_preflight import run_starter_deployment_preflight
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import (
    install_verified_private_state,
    prepare_private_state_bootstrap,
    read_private_state,
)
from tools.mother.common.starter_identity import reserve_starter_identity


TOKEN_A = "1|THISISASECRETTOKENVALUEAAAAAAAA"
TOKEN_C = "1|THISISASECRETTOKENVALUECCCCCCCC"


def _operation(name: str) -> OperationIdentity:
    return OperationIdentity(
        operation_id=name,
        request_id=f"{name}-request",
        network="mainnet",
        operation_kind="MOTHER-OP-PLAN",
    )


def _starter_document() -> dict[str, Any]:
    source = {
        "schema_version": 1,
        "kind": "main_computer.mother.private_state.v1",
        "networks": {
            "mainnet": {
                "chain_id": 42424240,
                "coolify": {
                    "controllers": {
                        "coolify-a": {
                            "api_token": TOKEN_A,
                            "enabled": True,
                            "observed_environments": {
                                "hub-site": {
                                    "available_for_mainnet_nodes": False,
                                    "environment_uuid": "hub-environment",
                                    "reserved_for": "existing-hub-service",
                                }
                            },
                            "project_name": "My first project",
                            "project_uuid": "project-a",
                            "server_uuid": "server-a",
                            "url": "http://coolify-a.invalid",
                        },
                        "coolify-c": {
                            "api_token": TOKEN_C,
                            "enabled": True,
                            "observed_environments": {},
                            "project_name": "My first project",
                            "project_uuid": "project-c",
                            "server_uuid": "server-c",
                            "url": "http://coolify-c.invalid",
                        },
                    },
                    "mutation_authority": "observe-only",
                },
                "deployment": {
                    "mode": "clean-start",
                    "status": "awaiting-offline-plan",
                    "targets": {
                        "mainneta-super1": {
                            "controller_ref": "networks.mainnet.coolify.controllers.coolify-a",
                            "desired_environment_name": "mainnet",
                            "desired_service_name": "mainneta-super1",
                            "hub_admin_address": None,
                            "hub_admin_private_key_path": "networks.mainnet.node_seed_material.mainneta-super1.wallets.hub_admin.private_key",
                            "key_material_status": "missing",
                            "live_resource_uuid": None,
                            "status": "absent-awaiting-redeployment",
                        },
                        "mainnetc-super1": {
                            "controller_ref": "networks.mainnet.coolify.controllers.coolify-c",
                            "desired_environment_name": "mainnet",
                            "desired_service_name": "mainnetc-super1",
                            "hub_admin_address": None,
                            "hub_admin_private_key_path": "networks.mainnet.node_seed_material.mainnetc-super1.wallets.hub_admin.private_key",
                            "key_material_status": "missing",
                            "live_resource_uuid": None,
                            "status": "absent-awaiting-redeployment",
                        },
                    },
                },
                "foundationdb": {},
                "node_seed_material": {},
                "nodes": {},
                "validators": {},
                "wallets": {},
            }
        },
    }
    keys = iter("0x" + f"{value:064x}" for value in range(1, 32))
    return reserve_starter_identity(
        source,
        generated_at="2026-07-31T01:00:00Z",
        key_factory=lambda: next(keys),
    ).document


def _install(tmp_path: Path):
    runtime = tmp_path / "runtime" / "state"
    paths = MotherPaths(runtime_state_root=runtime).resolve_private_state_paths()
    operation = _operation("deployment-preflight-install")
    closure = prepare_private_state_bootstrap(
        paths,
        _starter_document(),
        updated_at="2026-07-31T01:01:00Z",
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    install_verified_private_state(paths, closure, None, operation=operation)
    return read_private_state(paths, operation=_operation("deployment-preflight-read"))


class _Response:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self.status = status
        self.headers = {"Content-Type": "application/json"}
        self._body = json.dumps(payload).encode("utf-8") if type(payload) is not str else payload.encode("utf-8")

    def getcode(self) -> int:
        return self.status

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]

    def close(self) -> None:
        return None


class _Opener:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, str]] = []
        self.service_conflict = False
        self.missing_project_on = ""
        self.failed_path = ""

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        path = parsed.path
        self.requests.append((request.get_method(), host, path))
        assert timeout > 0
        assert request.get_method() == "GET"
        if path != "/api/health":
            expected = TOKEN_A if host == "coolify-a.invalid" else TOKEN_C
            assert request.headers.get("Authorization") == f"Bearer {expected}"
        if path == self.failed_path:
            return _Response({"message": "failed"}, status=500)
        project_uuid = "project-a" if host == "coolify-a.invalid" else "project-c"
        server_uuid = "server-a" if host == "coolify-a.invalid" else "server-c"
        target = "mainneta-super1" if host == "coolify-a.invalid" else "mainnetc-super1"
        if path == "/api/health":
            return _Response({"status": "ok"})
        if path == "/api/v1/version":
            return _Response("4.1.2")
        if path == "/api/v1/projects":
            projects = [] if self.missing_project_on == host else [{"uuid": project_uuid, "name": "My first project"}]
            return _Response(projects)
        if path == "/api/v1/servers":
            return _Response([{"uuid": server_uuid, "name": "localhost"}])
        if path.endswith("/environments"):
            return _Response([])
        if path == "/api/v1/applications":
            return _Response([])
        if path == "/api/v1/services":
            return _Response([{"uuid": "existing", "name": target}] if self.service_conflict else [])
        if path == "/api/v1/resources":
            return _Response([])
        raise AssertionError(f"unexpected GET path: {path}")


def test_preflight_is_get_only_secret_safe_and_clean(tmp_path: Path) -> None:
    private_state = _install(tmp_path)
    opener = _Opener()
    report = run_starter_deployment_preflight(private_state, opener=opener)

    assert report["kind"] == "main_computer.mother.deployment_preflight.v1"
    assert report["summary"] == {
        "blocker_codes": [],
        "blocker_count": 0,
        "clean": True,
        "target_count": 2,
    }
    assert [item["node"] for item in report["results"]] == ["mainneta-super1", "mainnetc-super1"]
    assert all(item["environment"]["status"] == "absent-create-required" for item in report["results"])
    assert all(item["target_resource"]["status"] == "absent" for item in report["results"])
    assert {item["code"] for item in report["remaining_global_blockers"]} == {
        "MOTHER_DEPLOY_EXECUTOR_NOT_IMPLEMENTED",
        "MOTHER_DEPLOY_MUTATION_AUTHORITY_DISABLED",
    }
    assert all(method == "GET" for method, _, _ in opener.requests)
    rendered = json.dumps(report)
    assert TOKEN_A not in rendered
    assert TOKEN_C not in rendered
    for private_key in (entry["private_key"] for entry in _starter_document()["networks"]["mainnet"]["wallets"].values()):
        assert private_key not in rendered


def test_preflight_reports_existing_target_resource(tmp_path: Path) -> None:
    private_state = _install(tmp_path)
    opener = _Opener()
    opener.service_conflict = True
    report = run_starter_deployment_preflight(private_state, opener=opener)

    assert report["summary"]["clean"] is False
    assert report["summary"]["blocker_codes"] == ["MOTHER_DEPLOY_PREFLIGHT_TARGET_EXISTS"]
    assert all(item["target_resource"]["status"] == "conflict" for item in report["results"])


def test_preflight_reports_project_binding_mismatch(tmp_path: Path) -> None:
    private_state = _install(tmp_path)
    opener = _Opener()
    opener.missing_project_on = "coolify-c.invalid"
    report = run_starter_deployment_preflight(private_state, opener=opener)

    assert report["summary"]["clean"] is False
    assert "MOTHER_DEPLOY_PREFLIGHT_PROJECT_BINDING_MISMATCH" in report["summary"]["blocker_codes"]
    assert report["results"][0]["project"]["verified"] is True
    assert report["results"][1]["project"]["verified"] is False


def test_preflight_reports_endpoint_failure_without_mutation(tmp_path: Path) -> None:
    private_state = _install(tmp_path)
    opener = _Opener()
    opener.failed_path = "/api/v1/resources"
    report = run_starter_deployment_preflight(private_state, opener=opener)

    assert report["summary"]["clean"] is False
    assert report["summary"]["blocker_codes"] == ["MOTHER_DEPLOY_PREFLIGHT_ENDPOINT_FAILED"]
    assert report["policy"]["live_mutation_performed"] is False
    assert all(method == "GET" for method, _, _ in opener.requests)


def test_selected_node_contacts_only_its_controller(tmp_path: Path) -> None:
    private_state = _install(tmp_path)
    opener = _Opener()
    report = run_starter_deployment_preflight(
        private_state,
        selected_nodes=("mainnetc-super1",),
        opener=opener,
    )

    assert [item["node"] for item in report["results"]] == ["mainnetc-super1"]
    assert {host for _, host, _ in opener.requests} == {"coolify-c.invalid"}


def test_offline_blockers_prevent_network_access(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime" / "state"
    paths = MotherPaths(runtime_state_root=runtime).resolve_private_state_paths()
    document = _starter_document()
    del document["networks"]["mainnet"]["validators"]["mainneta-super1"]
    operation = _operation("deployment-preflight-incomplete-install")
    closure = prepare_private_state_bootstrap(
        paths,
        document,
        updated_at="2026-07-31T01:02:00Z",
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    install_verified_private_state(paths, closure, None, operation=operation)
    private_state = read_private_state(paths, operation=_operation("deployment-preflight-incomplete-read"))
    opener = _Opener()

    from tools.mother.common.deployment_preflight import MotherDeploymentPreflightError

    try:
        run_starter_deployment_preflight(private_state, opener=opener)
    except MotherDeploymentPreflightError as exc:
        assert exc.code == "MOTHER_DEPLOY_PREFLIGHT_OFFLINE_BLOCKERS"
    else:
        raise AssertionError("incomplete identity must block live preflight")
    assert opener.requests == []


def test_preflight_evidence_is_canonical_bound_and_fresh(tmp_path: Path) -> None:
    from datetime import datetime, timezone
    from tools.mother.common.deployment_preflight import (
        verify_deployment_preflight_evidence,
        write_deployment_preflight_evidence,
    )

    private_state = _install(tmp_path)
    opener = _Opener()
    report = run_starter_deployment_preflight(
        private_state,
        opener=opener,
        observed_at="2026-07-31T17:00:00Z",
    )
    paths = MotherPaths(runtime_state_root=tmp_path / "runtime" / "state").resolve_private_state_paths()
    evidence_path, digest = write_deployment_preflight_evidence(
        paths,
        report,
        operation=_operation("deployment-preflight-evidence-write"),
    )

    assert evidence_path.is_file()
    assert evidence_path.name.endswith(f"-{digest[:16]}.json")
    raw = evidence_path.read_text(encoding="utf-8")
    assert TOKEN_A not in raw
    assert TOKEN_C not in raw
    assert '"live_mutation_performed":false' in raw
    verified = verify_deployment_preflight_evidence(
        paths,
        private_state,
        evidence_path,
        max_age_seconds=300,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        now=datetime(2026, 7, 31, 17, 4, tzinfo=timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["age_seconds"] == 240
    assert verified["nodes"] == ["mainneta-super1", "mainnetc-super1"]


def test_preflight_evidence_rejects_stale_time_and_wrong_selection(tmp_path: Path) -> None:
    from datetime import datetime, timezone
    import pytest
    from tools.mother.common.deployment_preflight import (
        MotherDeploymentPreflightError,
        verify_deployment_preflight_evidence,
        write_deployment_preflight_evidence,
    )

    private_state = _install(tmp_path)
    report = run_starter_deployment_preflight(
        private_state,
        opener=_Opener(),
        observed_at="2026-07-31T17:00:00Z",
    )
    paths = MotherPaths(runtime_state_root=tmp_path / "runtime" / "state").resolve_private_state_paths()
    evidence_path, _ = write_deployment_preflight_evidence(
        paths,
        report,
        operation=_operation("deployment-preflight-evidence-stale"),
    )

    with pytest.raises(MotherDeploymentPreflightError) as stale:
        verify_deployment_preflight_evidence(
            paths,
            private_state,
            evidence_path,
            max_age_seconds=300,
            now=datetime(2026, 7, 31, 17, 6, tzinfo=timezone.utc),
        )
    assert stale.value.code == "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_STALE_TIME"

    with pytest.raises(MotherDeploymentPreflightError) as selection:
        verify_deployment_preflight_evidence(
            paths,
            private_state,
            evidence_path,
            selected_nodes=("mainneta-super1",),
            now=datetime(2026, 7, 31, 17, 1, tzinfo=timezone.utc),
        )
    assert selection.value.code == "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_SELECTION_MISMATCH"


def test_preflight_evidence_rejects_binding_change(tmp_path: Path) -> None:
    from datetime import datetime, timezone
    import pytest
    from tools.mother.common.deployment_preflight import (
        MotherDeploymentPreflightError,
        verify_deployment_preflight_evidence,
        write_deployment_preflight_evidence,
    )

    private_state = _install(tmp_path)
    report = run_starter_deployment_preflight(
        private_state,
        opener=_Opener(),
        observed_at="2026-07-31T17:00:00Z",
    )
    paths = MotherPaths(runtime_state_root=tmp_path / "runtime" / "state").resolve_private_state_paths()
    evidence_path, _ = write_deployment_preflight_evidence(
        paths,
        report,
        operation=_operation("deployment-preflight-evidence-binding"),
    )
    tampered = dict(report)
    tampered["mother_binding"] = {**report["mother_binding"], "generation": 99}
    from tools.mother.common.canonical import canonical_json
    evidence_path.write_bytes(canonical_json(tampered))

    with pytest.raises(MotherDeploymentPreflightError) as caught:
        verify_deployment_preflight_evidence(
            paths,
            private_state,
            evidence_path,
            now=datetime(2026, 7, 31, 17, 1, tzinfo=timezone.utc),
        )
    assert caught.value.code == "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_STALE_BINDING"
