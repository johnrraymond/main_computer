from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit

import pytest

from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_execution import (
    MotherDeploymentExecutionError,
    build_deployment_execution_request,
    verify_deployment_execution_request,
    write_deployment_execution_request,
)
from tools.mother.common.deployment_preflight import (
    MotherDeploymentPreflightError,
    run_starter_deployment_preflight,
    write_deployment_preflight_evidence,
)
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import (
    install_verified_private_state,
    prepare_private_state_bootstrap,
    read_private_state,
)
from tools.mother.common.starter_identity import reserve_starter_identity


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "tools" / "mother_deploy.py"
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
                            "observed_environments": {},
                            "project_uuid": "project-a",
                            "server_uuid": "server-a",
                            "url": "http://coolify-a.invalid",
                        },
                        "coolify-c": {
                            "api_token": TOKEN_C,
                            "enabled": True,
                            "observed_environments": {},
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
    operation = _operation("deployment-execution-install")
    closure = prepare_private_state_bootstrap(
        paths,
        _starter_document(),
        updated_at="2026-07-31T01:01:00Z",
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    install_verified_private_state(paths, closure, None, operation=operation)
    private_state = read_private_state(paths, operation=_operation("deployment-execution-read"))
    return runtime, paths, private_state


class _Response:
    def __init__(self, payload: Any) -> None:
        self.status = 200
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
        self.requests: list[tuple[str, str]] = []

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        path = parsed.path
        self.requests.append((request.get_method(), path))
        assert timeout > 0
        assert request.get_method() == "GET"
        if path != "/api/health":
            token = TOKEN_A if host == "coolify-a.invalid" else TOKEN_C
            assert request.headers.get("Authorization") == f"Bearer {token}"
        project = "project-a" if host == "coolify-a.invalid" else "project-c"
        server = "server-a" if host == "coolify-a.invalid" else "server-c"
        if path == "/api/health":
            return _Response({"status": "ok"})
        if path == "/api/v1/version":
            return _Response("4.1.2")
        if path == "/api/v1/projects":
            return _Response([{"uuid": project, "name": "My first project"}])
        if path == "/api/v1/servers":
            return _Response([{"uuid": server, "name": "localhost"}])
        if path.endswith("/environments") or path in {
            "/api/v1/applications",
            "/api/v1/services",
            "/api/v1/resources",
        }:
            return _Response([])
        raise AssertionError(f"unexpected path: {path}")


def _evidence(paths, private_state, *, observed_at: str):
    opener = _Opener()
    report = run_starter_deployment_preflight(
        private_state,
        opener=opener,
        observed_at=observed_at,
    )
    path, _ = write_deployment_preflight_evidence(
        paths,
        report,
        operation=_operation("deployment-execution-evidence"),
    )
    assert all(method == "GET" for method, _ in opener.requests)
    return path


def test_execution_request_is_secret_safe_immutable_and_verifiable(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    evidence = _evidence(paths, private_state, observed_at="2026-07-31T17:00:00Z")
    request = build_deployment_execution_request(
        paths,
        private_state,
        evidence,
        created_at="2026-07-31T17:01:00Z",
        now=datetime(2026, 7, 31, 17, 2, tzinfo=timezone.utc),
    )

    assert request["kind"] == "main_computer.mother.deployment_execution_request.v1"
    assert request["authority"] == {
        "current": "observe-only",
        "live_execution_authorized": False,
    }
    assert request["policy"] == {
        "authoritative_prep_completed": False,
        "legacy_allfather_executor_invoked": False,
        "legacy_qbft_executor_invoked": False,
        "live_mutation_performed": False,
        "network_access_performed": False,
        "secrets_in_output": False,
    }
    assert [item["node"] for item in request["sequence"]] == [
        "mainneta-super1",
        "mainnetc-super1",
    ]
    assert request["summary"]["blocker_codes"] == [
        "MOTHER_DEPLOY_EXECUTOR_NOT_IMPLEMENTED",
        "MOTHER_DEPLOY_MUTATION_AUTHORITY_DISABLED",
    ]
    rendered = json.dumps(request)
    assert TOKEN_A not in rendered
    assert TOKEN_C not in rendered
    assert "private_key" not in rendered

    request_path, digest = write_deployment_execution_request(
        paths,
        request,
        operation=_operation("deployment-execution-write"),
    )
    assert request_path.parent == paths.root / "actions" / "deployment-requests"
    assert request_path.read_bytes() == canonical_json(request)
    assert digest == request["request_sha256"]

    verified = verify_deployment_execution_request(
        paths,
        private_state,
        request_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        now=datetime(2026, 7, 31, 17, 3, tzinfo=timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["live_execution_authorized"] is False
    assert verified["request_sha256"] == digest


def test_execution_request_rejects_stale_preflight_before_write(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    evidence = _evidence(paths, private_state, observed_at="2026-07-31T17:00:00Z")

    with pytest.raises(MotherDeploymentPreflightError) as caught:
        build_deployment_execution_request(
            paths,
            private_state,
            evidence,
            now=datetime(2026, 7, 31, 17, 6, tzinfo=timezone.utc),
        )
    assert caught.value.code == "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_STALE_TIME"
    assert not (paths.root / "actions").exists()


def test_execution_request_rejects_modified_bytes_and_binding(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    evidence = _evidence(paths, private_state, observed_at="2026-07-31T17:00:00Z")
    request = build_deployment_execution_request(
        paths,
        private_state,
        evidence,
        created_at="2026-07-31T17:01:00Z",
        now=datetime(2026, 7, 31, 17, 1, tzinfo=timezone.utc),
    )
    request_path, _ = write_deployment_execution_request(
        paths,
        request,
        operation=_operation("deployment-execution-tamper"),
    )

    modified = dict(request)
    modified["summary"] = {**request["summary"], "target_count": 99}
    request_path.write_bytes(canonical_json(modified))
    with pytest.raises(MotherDeploymentExecutionError) as tampered:
        verify_deployment_execution_request(
            paths,
            private_state,
            request_path,
            now=datetime(2026, 7, 31, 17, 2, tzinfo=timezone.utc),
        )
    assert tampered.value.code == "MOTHER_DEPLOY_EXECUTION_REQUEST_INVALID"

    stale = dict(request)
    stale["mother_binding"] = {**request["mother_binding"], "generation": 99}
    stale["request_sha256"] = hashlib.sha256(
        canonical_json({key: value for key, value in stale.items() if key != "request_sha256"})
    ).hexdigest()
    request_path.write_bytes(canonical_json(stale))
    with pytest.raises(MotherDeploymentExecutionError) as binding:
        verify_deployment_execution_request(
            paths,
            private_state,
            request_path,
            now=datetime(2026, 7, 31, 17, 2, tzinfo=timezone.utc),
        )
    assert binding.value.code == "MOTHER_DEPLOY_EXECUTION_REQUEST_STALE_BINDING"


def test_execution_request_requires_exact_preflight_selection(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    evidence = _evidence(paths, private_state, observed_at="2026-07-31T17:00:00Z")

    with pytest.raises(MotherDeploymentPreflightError) as caught:
        build_deployment_execution_request(
            paths,
            private_state,
            evidence,
            selected_nodes=("mainneta-super1",),
            now=datetime(2026, 7, 31, 17, 1, tzinfo=timezone.utc),
        )
    assert caught.value.code == "MOTHER_DEPLOY_PREFLIGHT_EVIDENCE_SELECTION_MISMATCH"


def test_cli_prepares_and_verifies_without_network_or_live_mutation(tmp_path: Path) -> None:
    runtime, paths, private_state = _install(tmp_path)
    observed = datetime.now(timezone.utc).replace(microsecond=0)
    evidence = _evidence(paths, private_state, observed_at=observed.isoformat().replace("+00:00", "Z"))
    before = sorted(path.relative_to(paths.root) for path in paths.root.rglob("*") if path.is_file())

    dry = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "prepare-execution",
            "--evidence",
            str(evidence),
            "--runtime-state-root",
            str(runtime),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert dry.returncode == 0, dry.stderr
    payload = json.loads(dry.stdout)
    assert payload["policy"]["network_access_performed"] is False
    assert payload["policy"]["live_mutation_performed"] is False
    assert before == sorted(path.relative_to(paths.root) for path in paths.root.rglob("*") if path.is_file())

    written = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "prepare-execution",
            "--evidence",
            str(evidence),
            "--runtime-state-root",
            str(runtime),
            "--write-request",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert written.returncode == 0, written.stderr
    artifact = json.loads(written.stdout)["request_artifact"]["path"]

    verified = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "verify-execution",
            "--request",
            artifact,
            "--runtime-state-root",
            str(runtime),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert verified.returncode == 0, verified.stderr
    result = json.loads(verified.stdout)
    assert result["clean"] is True
    assert result["live_execution_authorized"] is False
    assert TOKEN_A not in written.stdout
    assert TOKEN_C not in written.stdout
