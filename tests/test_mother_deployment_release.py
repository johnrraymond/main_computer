from __future__ import annotations

from datetime import datetime, timezone
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
from tools.mother.common.deployment_release import (
    MotherDeploymentReleaseError,
    build_deployment_mutation_release,
    verify_deployment_mutation_release,
    write_deployment_mutation_release,
)
from tools.mother.common.deployment_transaction import (
    build_deployment_mutation_transaction,
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
    operation = _operation("deployment-release-install")
    closure = prepare_private_state_bootstrap(
        paths,
        _starter_document(),
        updated_at="2026-07-31T01:01:00Z",
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    install_verified_private_state(paths, closure, None, operation=operation)
    private_state = read_private_state(paths, operation=_operation("deployment-release-read"))
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
        if path.endswith("/environments"):
            return _Response([])
        if path in {"/api/v1/applications", "/api/v1/services", "/api/v1/resources"}:
            return _Response([])
        raise AssertionError(f"unexpected path: {path}")


def _transaction_artifact(paths, private_state, *, observed_at: str):
    opener = _Opener()
    now = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    report = run_starter_deployment_preflight(
        private_state,
        opener=opener,
        observed_at=observed_at,
    )
    evidence_path, _ = write_deployment_preflight_evidence(
        paths,
        report,
        operation=_operation("deployment-release-evidence"),
    )
    request = build_deployment_execution_request(
        paths,
        private_state,
        evidence_path,
        created_at=observed_at,
        now=now,
    )
    request_path, _ = write_deployment_execution_request(
        paths,
        request,
        operation=_operation("deployment-release-request"),
    )
    transaction = build_deployment_mutation_transaction(
        paths,
        private_state,
        request_path,
        created_at=observed_at,
        now=now,
    )
    transaction_path, digest = write_deployment_mutation_transaction(
        paths,
        transaction,
        operation=_operation("deployment-release-transaction"),
    )
    assert all(method == "GET" for method, _ in opener.requests)
    return transaction_path, digest


def test_release_binds_exact_digest_and_resolves_only_operator_authority_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, paths, private_state = _install(tmp_path)
    transaction_path, digest = _transaction_artifact(
        paths,
        private_state,
        observed_at="2026-07-31T18:00:00Z",
    )

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("release creation must not perform network access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    release = build_deployment_mutation_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_transaction_sha256=digest,
        created_at="2026-07-31T18:01:00Z",
        now=datetime(2026, 7, 31, 18, 1, tzinfo=timezone.utc),
    )

    assert release["kind"] == "main_computer.mother.deployment_mutation_release.v1"
    assert release["authority"] == {
        "identity_default": "observe-only",
        "authorization_source": "explicit-operator-release",
        "coolify_api_credential_required": True,
        "independent_authentication_system_created": False,
        "transaction_apply_authorized": True,
        "live_execution_authorized": False,
    }
    assert release["operator_release"]["requested_use_limit"] == 1
    assert release["policy"]["consumption_enforcement_implemented"] is False
    assert release["resolved_blocker_codes"] == ["MOTHER_DEPLOY_MUTATION_AUTHORITY_DISABLED"]
    assert release["summary"]["remaining_blocker_codes"] == ["MOTHER_DEPLOY_EXECUTOR_NOT_IMPLEMENTED"]
    assert release["policy"]["network_access_performed"] is False
    assert release["policy"]["live_mutation_performed"] is False
    rendered = json.dumps(release)
    assert TOKEN_A not in rendered
    assert TOKEN_C not in rendered
    assert "private_key" not in rendered


def test_release_rejects_wrong_operator_acknowledgement(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    transaction_path, _ = _transaction_artifact(
        paths,
        private_state,
        observed_at="2026-07-31T18:00:00Z",
    )
    with pytest.raises(MotherDeploymentReleaseError) as caught:
        build_deployment_mutation_release(
            paths,
            private_state,
            transaction_path,
            acknowledged_transaction_sha256="0" * 64,
            created_at="2026-07-31T18:01:00Z",
            now=datetime(2026, 7, 31, 18, 1, tzinfo=timezone.utc),
        )
    assert caught.value.code == "MOTHER_DEPLOY_RELEASE_ACKNOWLEDGEMENT_MISMATCH"


def test_release_is_canonical_immutable_and_verifiable(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    transaction_path, digest = _transaction_artifact(
        paths,
        private_state,
        observed_at="2026-07-31T18:00:00Z",
    )
    release = build_deployment_mutation_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_transaction_sha256=digest,
        expires_in_seconds=300,
        created_at="2026-07-31T18:01:00Z",
        now=datetime(2026, 7, 31, 18, 1, tzinfo=timezone.utc),
    )
    path, release_digest = write_deployment_mutation_release(
        paths,
        release,
        operation=_operation("deployment-release-write"),
    )
    assert path.parent == paths.root / "actions" / "deployment-releases"
    assert path.read_bytes() == canonical_json(release)
    assert release_digest == release["release_sha256"]

    verified = verify_deployment_mutation_release(
        paths,
        private_state,
        path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        now=datetime(2026, 7, 31, 18, 2, tzinfo=timezone.utc),
    )
    assert verified["clean"] is True
    assert verified["transaction_apply_authorized"] is True
    assert verified["live_execution_authorized"] is False
    assert verified["remaining_blocker_codes"] == ["MOTHER_DEPLOY_EXECUTOR_NOT_IMPLEMENTED"]
    assert verified["network_access_performed"] is False
    assert verified["live_mutation_performed"] is False


def test_release_rejects_expiry_and_out_of_range_lifetime(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    transaction_path, digest = _transaction_artifact(
        paths,
        private_state,
        observed_at="2026-07-31T18:00:00Z",
    )
    with pytest.raises(MotherDeploymentReleaseError) as caught:
        build_deployment_mutation_release(
            paths,
            private_state,
            transaction_path,
            acknowledged_transaction_sha256=digest,
            expires_in_seconds=901,
            created_at="2026-07-31T18:01:00Z",
            now=datetime(2026, 7, 31, 18, 1, tzinfo=timezone.utc),
        )
    assert caught.value.code == "MOTHER_DEPLOY_RELEASE_TTL_INVALID"

    release = build_deployment_mutation_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_transaction_sha256=digest,
        expires_in_seconds=30,
        created_at="2026-07-31T18:01:00Z",
        now=datetime(2026, 7, 31, 18, 1, tzinfo=timezone.utc),
    )
    path, _ = write_deployment_mutation_release(
        paths,
        release,
        operation=_operation("deployment-release-expired"),
    )
    with pytest.raises(MotherDeploymentReleaseError) as expired:
        verify_deployment_mutation_release(
            paths,
            private_state,
            path,
            now=datetime(2026, 7, 31, 18, 1, 31, tzinfo=timezone.utc),
        )
    assert expired.value.code == "MOTHER_DEPLOY_RELEASE_EXPIRED"


def test_release_rejects_modified_artifact_even_with_recomputed_digest(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    transaction_path, digest = _transaction_artifact(
        paths,
        private_state,
        observed_at="2026-07-31T18:00:00Z",
    )
    release = build_deployment_mutation_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_transaction_sha256=digest,
        created_at="2026-07-31T18:01:00Z",
        now=datetime(2026, 7, 31, 18, 1, tzinfo=timezone.utc),
    )
    path, _ = write_deployment_mutation_release(
        paths,
        release,
        operation=_operation("deployment-release-tamper"),
    )
    modified = json.loads(path.read_text("utf-8"))
    modified["authority"]["live_execution_authorized"] = True
    payload = {key: value for key, value in modified.items() if key != "release_sha256"}
    import hashlib

    modified["release_sha256"] = hashlib.sha256(canonical_json(payload)).hexdigest()
    path.write_bytes(canonical_json(modified))
    with pytest.raises(MotherDeploymentReleaseError) as caught:
        verify_deployment_mutation_release(
            paths,
            private_state,
            path,
            now=datetime(2026, 7, 31, 18, 2, tzinfo=timezone.utc),
        )
    assert caught.value.code == "MOTHER_DEPLOY_RELEASE_MISMATCH"


def test_cli_releases_and_verifies_without_network_or_mutation(tmp_path: Path) -> None:
    runtime, paths, private_state = _install(tmp_path)
    observed = datetime.now(timezone.utc).replace(microsecond=0)
    transaction_path, digest = _transaction_artifact(
        paths,
        private_state,
        observed_at=observed.isoformat().replace("+00:00", "Z"),
    )
    before = sorted(path.relative_to(paths.root) for path in paths.root.rglob("*") if path.is_file())

    dry = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "release-mutation",
            "--transaction",
            str(transaction_path),
            "--acknowledge-transaction-sha256",
            digest,
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
    assert dry_payload["summary"]["transaction_apply_authorized"] is True
    assert dry_payload["summary"]["live_execution_authorized"] is False
    assert before == sorted(path.relative_to(paths.root) for path in paths.root.rglob("*") if path.is_file())

    written = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "release-mutation",
            "--transaction",
            str(transaction_path),
            "--acknowledge-transaction-sha256",
            digest,
            "--runtime-state-root",
            str(runtime),
            "--write-release",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert written.returncode == 0, written.stderr
    release_path = json.loads(written.stdout)["release_artifact"]["path"]

    verified = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "verify-release",
            "--release",
            release_path,
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
    assert result["transaction_apply_authorized"] is True
    assert result["live_execution_authorized"] is False
    assert result["remaining_blocker_codes"] == ["MOTHER_DEPLOY_EXECUTOR_NOT_IMPLEMENTED"]
    assert TOKEN_A not in written.stdout
    assert TOKEN_C not in written.stdout
