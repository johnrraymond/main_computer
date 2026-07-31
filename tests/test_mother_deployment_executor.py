from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tools import mother_deploy
from tools.mother.common.deployment_execution import (
    build_deployment_execution_request,
    write_deployment_execution_request,
)
from tools.mother.common.deployment_executor import (
    MotherDeploymentExecutorError,
    execute_released_mutation,
    inspect_released_mutation,
)
from tools.mother.common.deployment_preflight import (
    run_starter_deployment_preflight,
    write_deployment_preflight_evidence,
)
from tools.mother.common.deployment_release import (
    build_deployment_mutation_release,
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


TOKEN_A = "1|THISISASECRETTOKENVALUEAAAAAAAA"
TOKEN_C = "1|THISISASECRETTOKENVALUECCCCCCCC"


def _operation(name: str) -> OperationIdentity:
    return OperationIdentity(
        operation_id=name,
        request_id=f"{name}-request",
        network="mainnet",
        operation_kind="MOTHER-OP-ADD-NODE",
    )


def _starter_document() -> dict:
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
    operation = _operation("executor-install")
    closure = prepare_private_state_bootstrap(
        paths,
        _starter_document(),
        updated_at="2026-07-31T01:01:00Z",
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    install_verified_private_state(paths, closure, None, operation=operation)
    private_state = read_private_state(paths, operation=_operation("executor-read"))
    return runtime, paths, private_state


class _Response:
    def __init__(self, payload, status: int = 200) -> None:
        self.status = status
        self.headers = {"Content-Type": "application/json"}
        self._body = json.dumps(payload).encode("utf-8")

    def getcode(self) -> int:
        return self.status

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]

    def close(self) -> None:
        return None


class _CoolifyOpener:
    def __init__(self, *, fail_mutation: str | None = None) -> None:
        self.fail_mutation = fail_mutation
        self.requests: list[dict] = []
        self.environments = {"coolify-a.invalid": [], "coolify-c.invalid": []}
        self.services = {"coolify-a.invalid": [], "coolify-c.invalid": []}

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        path = parsed.path
        method = request.get_method()
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append({"method": method, "host": host, "path": path, "body": body})
        assert timeout > 0
        if path != "/api/health":
            token = TOKEN_A if host == "coolify-a.invalid" else TOKEN_C
            assert request.headers.get("Authorization") == f"Bearer {token}"
        project = "project-a" if host == "coolify-a.invalid" else "project-c"
        server = "server-a" if host == "coolify-a.invalid" else "server-c"

        if method == "GET":
            if path == "/api/health":
                return _Response({"status": "ok"})
            if path == "/api/v1/version":
                return _Response("4.1.2")
            if path == "/api/v1/projects":
                return _Response([{"uuid": project, "name": "My first project"}])
            if path == "/api/v1/servers":
                return _Response([{"uuid": server, "name": "localhost"}])
            if path.endswith("/environments"):
                return _Response(self.environments[host])
            if path == "/api/v1/applications":
                return _Response([])
            if path in {"/api/v1/services", "/api/v1/resources"}:
                return _Response(self.services[host])
            raise AssertionError(f"unexpected GET path: {path}")

        assert method == "POST"
        if path.endswith("/environments"):
            mutation = f"{host}.environment"
            if self.fail_mutation == mutation:
                return _Response({"message": "failed"}, status=500)
            value = {"uuid": f"env-{host[8]}", "name": body["name"]}
            self.environments[host].append(value)
            return _Response(value, status=201)
        if path == "/api/v1/services":
            node = body["name"]
            mutation = f"{host}.service"
            if self.fail_mutation == mutation:
                return _Response({"message": "failed"}, status=500)
            assert body["instant_deploy"] is False
            assert type(body["environment_uuid"]) is str
            value = {"uuid": f"svc-{node}", "name": node, "status": "stopped"}
            self.services[host].append(value)
            return _Response(value, status=201)
        raise AssertionError(f"unexpected POST path: {path}")


def _release_artifact(paths, private_state, opener: _CoolifyOpener):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = now.isoformat().replace("+00:00", "Z")
    report = run_starter_deployment_preflight(
        private_state,
        opener=opener,
        observed_at=stamp,
    )
    evidence_path, _ = write_deployment_preflight_evidence(
        paths,
        report,
        operation=_operation("executor-evidence"),
    )
    request = build_deployment_execution_request(
        paths,
        private_state,
        evidence_path,
        created_at=stamp,
        now=now,
    )
    request_path, _ = write_deployment_execution_request(
        paths,
        request,
        operation=_operation("executor-request"),
    )
    transaction = build_deployment_mutation_transaction(
        paths,
        private_state,
        request_path,
        created_at=stamp,
        now=now,
    )
    transaction_path, transaction_digest = write_deployment_mutation_transaction(
        paths,
        transaction,
        operation=_operation("executor-transaction"),
    )
    release = build_deployment_mutation_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_transaction_sha256=transaction_digest,
        created_at=stamp,
        now=now,
    )
    release_path, release_digest = write_deployment_mutation_release(
        paths,
        release,
        operation=_operation("executor-release"),
    )
    return release_path, release_digest


def test_inspection_is_network_free_and_resolves_executor_blocker(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    builder = _CoolifyOpener()
    release_path, release_digest = _release_artifact(paths, private_state, builder)
    before = len(builder.requests)

    result = inspect_released_mutation(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
    )

    assert len(builder.requests) == before
    assert result["executor_implemented"] is True
    assert result["resolved_blocker_codes"] == ["MOTHER_DEPLOY_EXECUTOR_NOT_IMPLEMENTED"]
    assert result["remaining_blocker_codes"] == []
    assert result["live_execution_authorized"] is True


def test_live_executor_performs_exact_four_post_sequence_and_persists_receipt(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    builder = _CoolifyOpener()
    release_path, release_digest = _release_artifact(paths, private_state, builder)
    live = _CoolifyOpener()

    result = execute_released_mutation(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
        opener=live,
        operation=_operation("executor-apply"),
    )

    posts = [item for item in live.requests if item["method"] == "POST"]
    assert [(item["host"], item["path"]) for item in posts] == [
        ("coolify-a.invalid", "/api/v1/projects/project-a/environments"),
        ("coolify-a.invalid", "/api/v1/services"),
        ("coolify-c.invalid", "/api/v1/projects/project-c/environments"),
        ("coolify-c.invalid", "/api/v1/services"),
    ]
    assert posts[1]["body"]["environment_uuid"] == "env-a"
    assert posts[3]["body"]["environment_uuid"] == "env-c"
    assert result["status"] == "pass"
    assert result["summary"]["complete"] is True
    assert result["summary"]["succeeded_mutation_count"] == 4
    assert Path(result["result_artifact"]["path"]).is_file()
    claim = paths.root / "actions" / "deployment-execution-claims" / f"{release_digest}.json"
    assert claim.is_file()
    rendered = json.dumps(result)
    assert TOKEN_A not in rendered
    assert TOKEN_C not in rendered
    assert "private_key" not in rendered


def test_release_is_one_shot_and_replay_is_rejected(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    release_path, release_digest = _release_artifact(paths, private_state, _CoolifyOpener())
    live = _CoolifyOpener()
    execute_released_mutation(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=live,
        operation=_operation("executor-first"),
    )

    with pytest.raises(MotherDeploymentExecutorError) as caught:
        execute_released_mutation(
            paths,
            private_state,
            release_path,
            acknowledged_release_sha256=release_digest,
            opener=_CoolifyOpener(),
            operation=_operation("executor-replay"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_RELEASE_ALREADY_CONSUMED"


def test_executor_stops_on_first_failed_post_and_records_partial_effect(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    release_path, release_digest = _release_artifact(paths, private_state, _CoolifyOpener())
    live = _CoolifyOpener(fail_mutation="coolify-a.invalid.service")

    result = execute_released_mutation(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=live,
        operation=_operation("executor-failure"),
    )

    posts = [item for item in live.requests if item["method"] == "POST"]
    assert len(posts) == 2
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "MOTHER_DEPLOY_EXECUTOR_HTTP_STATUS_REJECTED"
    assert result["summary"]["succeeded_mutation_count"] == 1
    assert result["summary"]["failed_mutation_count"] == 1
    assert result["summary"]["complete"] is False
    assert Path(result["result_artifact"]["path"]).is_file()


def test_wrong_release_digest_is_rejected_before_claim_or_network(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    release_path, release_digest = _release_artifact(paths, private_state, _CoolifyOpener())
    live = _CoolifyOpener()

    with pytest.raises(MotherDeploymentExecutorError) as caught:
        execute_released_mutation(
            paths,
            private_state,
            release_path,
            acknowledged_release_sha256="0" * 64,
            opener=live,
            operation=_operation("executor-wrong-ack"),
        )
    assert caught.value.code == "MOTHER_DEPLOY_EXECUTOR_ACKNOWLEDGEMENT_MISMATCH"
    assert live.requests == []
    claim = paths.root / "actions" / "deployment-execution-claims" / f"{release_digest}.json"
    assert not claim.exists()


def test_cli_apply_defaults_to_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runtime, paths, private_state = _install(tmp_path)
    release_path, release_digest = _release_artifact(paths, private_state, _CoolifyOpener())

    code = mother_deploy.main(
        [
            "apply-mutation",
            "--runtime-state-root",
            str(runtime),
            "--release",
            str(release_path),
            "--acknowledge-release-sha256",
            release_digest,
            "--node",
            "mainneta-super1",
            "--node",
            "mainnetc-super1",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["execute_requested"] is False
    assert output["live_mutation_performed"] is False
    assert output["release_already_claimed"] is False
