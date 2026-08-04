from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tools import mother_deploy
from tools.mother.common.canonical import canonical_json
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
from tools.mother.common.deployment_rollback import (
    MotherDeploymentRollbackError,
    execute_deployment_journal_rollback,
    execute_deployment_mutation_rollback,
    inspect_deployment_mutation_rollback,
    verify_deployment_mutation_rollback,
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

        if method == "DELETE":
            if path.startswith("/api/v1/services/"):
                resource_uuid = path.rsplit("/", 1)[-1]
                before = len(self.services[host])
                self.services[host] = [
                    item for item in self.services[host] if item.get("uuid") != resource_uuid
                ]
                status = 200 if len(self.services[host]) != before else 404
                return _Response({"message": "deleted" if status == 200 else "not found"}, status=status)
            marker = f"/api/v1/projects/{project}/environments/"
            if path.startswith(marker):
                resource_uuid = path[len(marker):]
                before = len(self.environments[host])
                self.environments[host] = [
                    item for item in self.environments[host] if item.get("uuid") != resource_uuid
                ]
                status = 200 if len(self.environments[host]) != before else 404
                return _Response({"message": "deleted" if status == 200 else "not found"}, status=status)
            raise AssertionError(f"unexpected DELETE path: {path}")

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


class _EventuallyConsistentDeleteOpener(_CoolifyOpener):
    """Keep a deleted service visible for a bounded number of list observations."""

    def __init__(self, *, stale_service_observations: int = 1) -> None:
        super().__init__()
        self.stale_service_observations = stale_service_observations
        self._pending_service_deletes: dict[tuple[str, str], dict] = {}

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        path = parsed.path
        method = request.get_method()

        if method == "DELETE" and path.startswith("/api/v1/services/"):
            resource_uuid = path.rsplit("/", 1)[-1]
            existing = next(
                (dict(item) for item in self.services[host] if item.get("uuid") == resource_uuid),
                None,
            )
            response = super().open(request, timeout)
            if existing is not None and response.status in {200, 202, 204}:
                self.services[host].append(existing)
                self._pending_service_deletes[(host, resource_uuid)] = {
                    "item": existing,
                    "remaining": self.stale_service_observations,
                }
            return response

        response = super().open(request, timeout)
        if method == "GET" and path == "/api/v1/services":
            for key, pending in list(self._pending_service_deletes.items()):
                pending_host, resource_uuid = key
                if pending_host != host:
                    continue
                pending["remaining"] -= 1
                if pending["remaining"] <= 0:
                    self.services[host] = [
                        item for item in self.services[host] if item.get("uuid") != resource_uuid
                    ]
                    del self._pending_service_deletes[key]
        return response


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
    assert result["summary"]["automatic_rollback_complete"] is True
    assert result["summary"]["net_live_mutation_remaining"] is None
    assert result["summary"]["rollback_reconciliation_required"] is True
    assert result["policy"]["automatic_rollback_performed"] is True
    assert result["rollback"]["automatic_attempt"]["summary"]["complete"] is True
    assert live.environments["coolify-a.invalid"] == []
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


def test_successful_first_step_can_be_explicitly_rolled_back_and_verified(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    release_path, release_digest = _release_artifact(paths, private_state, _CoolifyOpener())
    live = _CoolifyOpener()
    execution = execute_released_mutation(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=live,
        operation=_operation("executor-rollback-source"),
    )
    execution_path = Path(execution["result_artifact"]["path"])
    execution_digest = execution["result_artifact"]["sha256"]

    before = len(live.requests)
    inspected = inspect_deployment_mutation_rollback(
        paths,
        private_state,
        execution_path,
        acknowledged_execution_sha256=execution_digest,
    )
    assert len(live.requests) == before
    assert inspected["rollback_boundary_open"] is True
    assert inspected["rollback_operation_count"] == 4
    assert inspected["frame"]["operations"][0]["resource_kind"] == "service"
    assert inspected["frame"]["operations"][-1]["resource_kind"] == "environment"

    rollback = execute_deployment_mutation_rollback(
        paths,
        private_state,
        execution_path,
        acknowledged_execution_sha256=execution_digest,
        opener=live,
        operation=_operation("executor-explicit-rollback"),
    )

    deletes = [item for item in live.requests if item["method"] == "DELETE"]
    assert [(item["host"], item["path"]) for item in deletes] == [
        ("coolify-c.invalid", "/api/v1/services/svc-mainnetc-super1"),
        ("coolify-c.invalid", "/api/v1/projects/project-c/environments/env-c"),
        ("coolify-a.invalid", "/api/v1/services/svc-mainneta-super1"),
        ("coolify-a.invalid", "/api/v1/projects/project-a/environments/env-a"),
    ]
    assert rollback["status"] == "pass"
    assert rollback["summary"]["complete"] is True
    assert rollback["summary"]["postconditions_verified"] is True
    assert live.services["coolify-a.invalid"] == []
    assert live.services["coolify-c.invalid"] == []
    assert live.environments["coolify-a.invalid"] == []
    assert live.environments["coolify-c.invalid"] == []

    verified = verify_deployment_mutation_rollback(
        paths,
        private_state,
        Path(rollback["result_artifact"]["path"]),
        opener=live,
    )
    assert verified["clean"] is True
    assert verified["summary"]["absent_count"] == 4


def test_explicit_rollback_waits_for_eventual_service_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, paths, private_state = _install(tmp_path)
    live = _EventuallyConsistentDeleteOpener(stale_service_observations=1)
    release_path, release_digest = _release_artifact(paths, private_state, live)
    execution = execute_released_mutation(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=live,
        operation=_operation("executor-eventual-delete-source"),
    )
    monkeypatch.setattr(
        "tools.mother.common.deployment_rollback.time.sleep",
        lambda _seconds: None,
    )

    rollback = execute_deployment_mutation_rollback(
        paths,
        private_state,
        Path(execution["result_artifact"]["path"]),
        acknowledged_execution_sha256=execution["result_artifact"]["sha256"],
        opener=live,
        operation=_operation("executor-eventual-delete-rollback"),
    )

    assert rollback["status"] == "pass"
    service_receipts = [
        item for item in rollback["rollback_receipts"] if item["resource_kind"] == "service"
    ]
    assert {item["postcondition_observation_attempts"] for item in service_receipts} == {2}
    assert live.services["coolify-a.invalid"] == []
    assert live.services["coolify-c.invalid"] == []


def test_explicit_first_step_rollback_is_idempotent(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    release_path, release_digest = _release_artifact(paths, private_state, _CoolifyOpener())
    live = _CoolifyOpener()
    execution = execute_released_mutation(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=live,
        operation=_operation("executor-idempotent-source"),
    )
    kwargs = {
        "acknowledged_execution_sha256": execution["result_artifact"]["sha256"],
        "opener": live,
    }
    first = execute_deployment_mutation_rollback(
        paths,
        private_state,
        Path(execution["result_artifact"]["path"]),
        **kwargs,
        operation=_operation("executor-idempotent-first"),
    )
    second = execute_deployment_mutation_rollback(
        paths,
        private_state,
        Path(execution["result_artifact"]["path"]),
        **kwargs,
        operation=_operation("executor-idempotent-second"),
    )

    assert first["summary"]["delete_performed_count"] == 4
    assert second["status"] == "pass"
    assert second["summary"]["delete_performed_count"] == 0
    assert {item["status"] for item in second["rollback_receipts"]} == {"already-absent"}


def _write_downstream_success(
    paths,
    execution: dict,
    *,
    completed_at: str,
    mother_binding: dict | None = None,
    name: str,
) -> Path:
    directory = paths.root / "actions" / "deployment-identity-executions"
    directory.mkdir(parents=True, exist_ok=True)
    document = {
        "kind": "main_computer.mother.deployment_identity_execution_result.v1",
        "schema_version": 1,
        "started_at": completed_at,
        "completed_at": completed_at,
        "status": "pass",
        "mother_binding": mother_binding or dict(execution["mother_binding"]),
        "network": execution["network"],
        "nodes": list(execution["nodes"]),
        "staged_scope": "install-reserved-identity",
    }
    destination = directory / f"{name}.json"
    destination.write_bytes(canonical_json(document))
    return destination


def test_rollback_ignores_historical_downstream_success_from_before_execution(
    tmp_path: Path,
) -> None:
    _, paths, private_state = _install(tmp_path)
    release_path, release_digest = _release_artifact(paths, private_state, _CoolifyOpener())
    live = _CoolifyOpener()
    execution = execute_released_mutation(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=live,
        operation=_operation("executor-history-source"),
    )
    _write_downstream_success(
        paths,
        execution,
        completed_at="2026-07-01T00:00:00Z",
        name="historical-success",
    )

    inspected = inspect_deployment_mutation_rollback(
        paths,
        private_state,
        Path(execution["result_artifact"]["path"]),
        acknowledged_execution_sha256=execution["result_artifact"]["sha256"],
    )

    assert inspected["rollback_boundary_open"] is True
    assert inspected["downstream_success_blockers"] == []


def test_rollback_rejects_current_generation_downstream_success_after_execution(
    tmp_path: Path,
) -> None:
    _, paths, private_state = _install(tmp_path)
    release_path, release_digest = _release_artifact(paths, private_state, _CoolifyOpener())
    live = _CoolifyOpener()
    execution = execute_released_mutation(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=live,
        operation=_operation("executor-later-source"),
    )
    blocker = _write_downstream_success(
        paths,
        execution,
        completed_at="2099-01-01T00:00:00Z",
        name="later-success",
    )

    with pytest.raises(MotherDeploymentRollbackError) as caught:
        inspect_deployment_mutation_rollback(
            paths,
            private_state,
            Path(execution["result_artifact"]["path"]),
            acknowledged_execution_sha256=execution["result_artifact"]["sha256"],
        )

    assert getattr(caught.value, "code", None) == "MOTHER_DEPLOY_ROLLBACK_BOUNDARY_CROSSED"
    assert blocker.name in str(caught.value)


def test_rollback_ignores_downstream_success_from_another_mother_generation(
    tmp_path: Path,
) -> None:
    _, paths, private_state = _install(tmp_path)
    release_path, release_digest = _release_artifact(paths, private_state, _CoolifyOpener())
    live = _CoolifyOpener()
    execution = execute_released_mutation(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=live,
        operation=_operation("executor-other-generation-source"),
    )
    other_binding = dict(execution["mother_binding"])
    other_binding["generation"] = int(other_binding["generation"]) + 1
    _write_downstream_success(
        paths,
        execution,
        completed_at="2099-01-01T00:00:00Z",
        mother_binding=other_binding,
        name="other-generation-success",
    )

    inspected = inspect_deployment_mutation_rollback(
        paths,
        private_state,
        Path(execution["result_artifact"]["path"]),
        acknowledged_execution_sha256=execution["result_artifact"]["sha256"],
    )

    assert inspected["rollback_boundary_open"] is True


def test_explicit_rollback_refuses_uuid_with_changed_name(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    release_path, release_digest = _release_artifact(paths, private_state, _CoolifyOpener())
    live = _CoolifyOpener()
    execution = execute_released_mutation(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=live,
        operation=_operation("executor-ownership-source"),
    )
    live.services["coolify-c.invalid"][0]["name"] = "not-mother-owned-anymore"

    rollback = execute_deployment_mutation_rollback(
        paths,
        private_state,
        Path(execution["result_artifact"]["path"]),
        acknowledged_execution_sha256=execution["result_artifact"]["sha256"],
        opener=live,
        operation=_operation("executor-ownership-rollback"),
    )

    assert rollback["status"] == "failed"
    assert rollback["failure"]["code"] == "MOTHER_DEPLOY_ROLLBACK_OWNERSHIP_MISMATCH"
    assert live.services["coolify-c.invalid"][0]["uuid"] == "svc-mainnetc-super1"


def test_cli_rollback_defaults_to_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runtime, paths, private_state = _install(tmp_path)
    release_path, release_digest = _release_artifact(paths, private_state, _CoolifyOpener())
    live = _CoolifyOpener()
    execution = execute_released_mutation(
        paths,
        private_state,
        release_path,
        acknowledged_release_sha256=release_digest,
        opener=live,
        operation=_operation("executor-cli-rollback-source"),
    )

    code = mother_deploy.main(
        [
            "rollback-mutation",
            "--runtime-state-root",
            str(runtime),
            "--execution",
            execution["result_artifact"]["path"],
            "--acknowledge-execution-sha256",
            execution["result_artifact"]["sha256"],
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["execute_requested"] is False
    assert output["network_access_performed"] is False
    assert output["live_mutation_performed"] is False


class _CrashResponse(_Response):
    def __init__(self, payload, *, status: int = 200) -> None:
        super().__init__(payload, status=status)
        self._crashed = False

    def read(self, limit: int = -1) -> bytes:
        if not self._crashed:
            self._crashed = True
            raise KeyboardInterrupt("simulated executor process crash after remote create")
        return super().read(limit)


class _CrashAfterEnvironmentCreateOpener(_CoolifyOpener):
    def __init__(self) -> None:
        super().__init__()
        self.crash_enabled = True

    def open(self, request, timeout: float):  # noqa: ANN001
        response = super().open(request, timeout)
        parsed = urlsplit(request.full_url)
        if (
            self.crash_enabled
            and request.get_method() == "POST"
            and parsed.path.endswith("/environments")
        ):
            self.crash_enabled = False
            return _CrashResponse(
                {"uuid": f"env-{(parsed.hostname or '')[8]}", "name": "mainnet"},
                status=201,
            )
        return response


def test_crash_after_remote_create_is_recoverable_from_durable_journal(tmp_path: Path) -> None:
    _, paths, private_state = _install(tmp_path)
    release_path, release_digest = _release_artifact(paths, private_state, _CoolifyOpener())
    live = _CrashAfterEnvironmentCreateOpener()

    with pytest.raises(KeyboardInterrupt):
        execute_released_mutation(
            paths,
            private_state,
            release_path,
            acknowledged_release_sha256=release_digest,
            opener=live,
            operation=_operation("executor-crash-source"),
        )

    journal_path = (
        paths.root
        / "actions"
        / "deployment-rollback-journals"
        / f"{release_digest}.json"
    )
    assert journal_path.is_file()
    journal_digest = __import__("hashlib").sha256(journal_path.read_bytes()).hexdigest()
    journal = json.loads(journal_path.read_text())
    assert journal["status"] == "mutation-in-progress"
    assert journal["candidates"][0]["state"] == "in-flight"
    assert live.environments["coolify-a.invalid"] == [
        {"uuid": "env-a", "name": "mainnet"}
    ]

    rollback = execute_deployment_journal_rollback(
        paths,
        private_state,
        journal_path,
        acknowledged_journal_sha256=journal_digest,
        opener=live,
        operation=_operation("executor-crash-recovery"),
    )

    assert rollback["status"] == "pass"
    assert rollback["summary"]["complete"] is True
    assert rollback["authority"]["crash_recovery_path"] is True
    assert live.environments["coolify-a.invalid"] == []
    verified = verify_deployment_mutation_rollback(
        paths,
        private_state,
        Path(rollback["result_artifact"]["path"]),
        opener=live,
    )
    assert verified["clean"] is True
