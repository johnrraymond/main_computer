from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit

import pytest

from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_execution import (
    build_deployment_execution_request,
    write_deployment_execution_request,
)
from tools.mother.common.deployment_preflight import (
    run_starter_deployment_preflight,
    write_deployment_preflight_evidence,
)
from tools.mother.common.deployment_transaction import (
    MotherDeploymentTransactionError,
    build_deployment_mutation_transaction,
    verify_deployment_mutation_transaction,
    write_deployment_mutation_transaction,
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
    operation = _operation("deployment-transaction-install")
    closure = prepare_private_state_bootstrap(
        paths,
        _starter_document(),
        updated_at="2026-07-31T01:01:00Z",
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    install_verified_private_state(paths, closure, None, operation=operation)
    private_state = read_private_state(paths, operation=_operation("deployment-transaction-read"))
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
    def __init__(self, *, existing_environment: bool = False) -> None:
        self.existing_environment = existing_environment
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
        if path.endswith("/environments"):
            if self.existing_environment:
                return _Response([{"uuid": "environment-a" if host.startswith("coolify-a") else "environment-c", "name": "mainnet"}])
            return _Response([])
        if path in {"/api/v1/applications", "/api/v1/services", "/api/v1/resources"}:
            return _Response([])
        raise AssertionError(f"unexpected path: {path}")


def _request_artifact(
    paths,
    private_state,
    *,
    observed_at: str,
    existing_environment: bool = False,
    selected_nodes: tuple[str, ...] = (),
):
    opener = _Opener(existing_environment=existing_environment)
    report = run_starter_deployment_preflight(
        private_state,
        selected_nodes=selected_nodes,
        opener=opener,
        observed_at=observed_at,
    )
    evidence_path, _ = write_deployment_preflight_evidence(
        paths,
        report,
        operation=_operation("deployment-transaction-evidence"),
    )
    request = build_deployment_execution_request(
        paths,
        private_state,
        evidence_path,
        selected_nodes=selected_nodes,
        created_at=observed_at,
        now=datetime.fromisoformat(observed_at.replace("Z", "+00:00")),
    )
    request_path, _ = write_deployment_execution_request(
        paths,
        request,
        operation=_operation("deployment-transaction-request"),
    )
    assert all(method == "GET" for method, _ in opener.requests)
    return request_path


def test_transaction_materializes_exact_secret_free_standby_write_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, paths, private_state = _install(tmp_path)
    request_path = _request_artifact(
        paths,
        private_state,
        observed_at="2026-07-31T17:00:00Z",
    )

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("transaction staging must not perform network access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    transaction = build_deployment_mutation_transaction(
        paths,
        private_state,
        request_path,
        created_at="2026-07-31T17:02:00Z",
        now=datetime(2026, 7, 31, 17, 3, tzinfo=timezone.utc),
    )

    assert transaction["kind"] == "main_computer.mother.deployment_mutation_transaction.v1"
    assert transaction["staged_scope"] == "prepare-standby-service"
    assert transaction["authority"] == {
        "current": "observe-only",
        "live_execution_authorized": False,
        "transaction_apply_authorized": False,
    }
    assert transaction["policy"]["network_access_performed"] is False
    assert transaction["policy"]["live_mutation_performed"] is False
    assert transaction["summary"]["mutation_count"] == 4
    assert transaction["summary"]["templated_body_count"] == 2
    assert transaction["summary"]["blocker_codes"] == [
        "MOTHER_DEPLOY_EXECUTOR_NOT_IMPLEMENTED",
        "MOTHER_DEPLOY_MUTATION_AUTHORITY_DISABLED",
    ]

    mutations = transaction["mutations"]
    assert [(item["node"], item["method"], item["endpoint"]) for item in mutations] == [
        ("mainneta-super1", "POST", "/api/v1/projects/project-a/environments"),
        ("mainneta-super1", "POST", "/api/v1/services"),
        ("mainnetc-super1", "POST", "/api/v1/projects/project-c/environments"),
        ("mainnetc-super1", "POST", "/api/v1/services"),
    ]
    for mutation in mutations:
        assert mutation["body_sha256"] == hashlib.sha256(
            canonical_json(mutation["canonical_request_body"])
        ).hexdigest()

    first_service = mutations[1]
    assert first_service["depends_on"] == ["mainneta-super1.create-environment"]
    assert first_service["canonical_request_body"]["environment_uuid"] == {
        "$result": "mainneta-super1.create-environment.environment_uuid"
    }
    assert first_service["canonical_request_body"]["instant_deploy"] is False
    compose = base64.b64decode(first_service["canonical_request_body"]["docker_compose_raw"]).decode("utf-8")
    assert "main_computer.mother.stage: standby" in compose
    assert "private_key" not in compose
    assert "genesis" not in compose

    rendered = json.dumps(transaction)
    assert TOKEN_A not in rendered
    assert TOKEN_C not in rendered
    assert "private_key" not in rendered
    assert all(stage["deferred_phases"] for stage in transaction["nodes"])


def test_transaction_uses_existing_unique_environment_without_create(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    request_path = _request_artifact(
        paths,
        private_state,
        observed_at="2026-07-31T17:00:00Z",
        existing_environment=True,
        selected_nodes=("mainneta-super1",),
    )
    transaction = build_deployment_mutation_transaction(
        paths,
        private_state,
        request_path,
        selected_nodes=("mainneta-super1",),
        created_at="2026-07-31T17:02:00Z",
        now=datetime(2026, 7, 31, 17, 3, tzinfo=timezone.utc),
    )
    assert transaction["summary"]["mutation_count"] == 1
    mutation = transaction["mutations"][0]
    assert mutation["mutation_id"] == "mainneta-super1.create-standby-service"
    assert mutation["depends_on"] == []
    assert mutation["body_materialization"] == "concrete"
    assert mutation["canonical_request_body"]["environment_uuid"] == "environment-a"


def test_transaction_is_canonical_immutable_and_verifiable(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    request_path = _request_artifact(paths, private_state, observed_at="2026-07-31T17:00:00Z")
    transaction = build_deployment_mutation_transaction(
        paths,
        private_state,
        request_path,
        created_at="2026-07-31T17:02:00Z",
        now=datetime(2026, 7, 31, 17, 3, tzinfo=timezone.utc),
    )
    path, digest = write_deployment_mutation_transaction(
        paths,
        transaction,
        operation=_operation("deployment-transaction-write"),
    )
    assert path.parent == paths.root / "actions" / "deployment-transactions"
    assert path.read_bytes() == canonical_json(transaction)
    assert digest == transaction["transaction_sha256"]

    result = verify_deployment_mutation_transaction(
        paths,
        private_state,
        path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        now=datetime(2026, 7, 31, 17, 4, tzinfo=timezone.utc),
    )
    assert result["clean"] is True
    assert result["mutation_count"] == 4
    assert result["transaction_apply_authorized"] is False
    assert result["network_access_performed"] is False
    assert result["live_mutation_performed"] is False


def test_transaction_rejects_modified_body_even_with_recomputed_outer_digest(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    request_path = _request_artifact(paths, private_state, observed_at="2026-07-31T17:00:00Z")
    transaction = build_deployment_mutation_transaction(
        paths,
        private_state,
        request_path,
        created_at="2026-07-31T17:02:00Z",
        now=datetime(2026, 7, 31, 17, 3, tzinfo=timezone.utc),
    )
    path, _ = write_deployment_mutation_transaction(
        paths,
        transaction,
        operation=_operation("deployment-transaction-tamper"),
    )
    modified = json.loads(path.read_text("utf-8"))
    modified["mutations"][0]["canonical_request_body"]["name"] = "wrong"
    payload = {key: value for key, value in modified.items() if key != "transaction_sha256"}
    modified["transaction_sha256"] = hashlib.sha256(canonical_json(payload)).hexdigest()
    path.write_bytes(canonical_json(modified))

    with pytest.raises(MotherDeploymentTransactionError) as caught:
        verify_deployment_mutation_transaction(
            paths,
            private_state,
            path,
            now=datetime(2026, 7, 31, 17, 4, tzinfo=timezone.utc),
        )
    assert caught.value.code == "MOTHER_DEPLOY_TRANSACTION_MISMATCH"


def test_transaction_rejects_stale_request_evidence(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    request_path = _request_artifact(paths, private_state, observed_at="2026-07-31T17:00:00Z")
    with pytest.raises(Exception) as caught:
        build_deployment_mutation_transaction(
            paths,
            private_state,
            request_path,
            now=datetime(2026, 7, 31, 17, 6, tzinfo=timezone.utc),
        )
    assert "STALE" in getattr(caught.value, "code", "")
    assert not (paths.root / "actions" / "deployment-transactions").exists()


def test_cli_stages_writes_and_verifies_without_network_or_mutation(tmp_path: Path) -> None:
    runtime, paths, private_state = _install(tmp_path)
    observed = datetime.now(timezone.utc).replace(microsecond=0)
    request_path = _request_artifact(
        paths,
        private_state,
        observed_at=observed.isoformat().replace("+00:00", "Z"),
    )
    before = sorted(path.relative_to(paths.root) for path in paths.root.rglob("*") if path.is_file())

    dry = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "stage-mutation",
            "--request",
            str(request_path),
            "--runtime-state-root",
            str(runtime),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert dry.returncode == 0, dry.stderr
    dry_payload = json.loads(dry.stdout)
    assert dry_payload["summary"]["mutation_count"] == 4
    assert dry_payload["policy"]["network_access_performed"] is False
    assert dry_payload["policy"]["live_mutation_performed"] is False
    assert before == sorted(path.relative_to(paths.root) for path in paths.root.rglob("*") if path.is_file())

    written = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "stage-mutation",
            "--request",
            str(request_path),
            "--runtime-state-root",
            str(runtime),
            "--write-transaction",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert written.returncode == 0, written.stderr
    transaction_path = json.loads(written.stdout)["transaction_artifact"]["path"]

    verified = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "verify-mutation",
            "--transaction",
            transaction_path,
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
    assert result["transaction_apply_authorized"] is False
    assert result["live_execution_authorized"] is False
    assert result["network_access_performed"] is False
    assert result["live_mutation_performed"] is False
    assert TOKEN_A not in written.stdout
    assert TOKEN_C not in written.stdout
