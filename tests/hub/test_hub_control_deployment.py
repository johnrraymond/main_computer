from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from tools import coolify_hub_service as legacy
from tools.hub_control.common import deployment
from tools.hub_control.common.errors import HubControlError


class SequenceClient:
    def __init__(self, responses: list[legacy.CoolifyResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, Any]] = []

    def request(self, method: str, path: str, payload: Any | None = None) -> legacy.CoolifyResponse:
        self.calls.append((method, path, payload))
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {path}")
        return self.responses.pop(0)


def _response(status: int, body: Any, *, path: str = "/api/v1/deployments/dep-1") -> legacy.CoolifyResponse:
    return legacy.CoolifyResponse(
        ok=200 <= status < 300,
        status=status,
        method="GET",
        path=path,
        body=body,
    )



def test_application_payload_bootstraps_frozen_projection_before_launcher() -> None:
    target = {
        "application_name": "mainneta-hub1",
        "network": "mainnet",
        "hub_bind_port": 8790,
        "public_url": "https://mainnet-hub.example.invalid",
        "runtime_dir": "/data/main-computer/hub/mainneta-hub1",
        "cluster_file_path": "/data/main-computer/hub/mainneta-hub1/fdb.cluster",
        "topology_path": "/data/main-computer/hub/mainneta-hub1/hub-topology.json",
        "git_repository": "https://github.com/johnrraymond/main_computer",
        "git_branch": "main",
        "dockerfile_location": "/Dockerfile.hub.exp-fdb",
        "coolify": {"project_uuid": "project", "server_uuid": "server"},
    }

    payload = deployment._application_payload(target)
    command = payload["start_command"]

    assert len(command) <= 255
    assert '"$MCF"' in command
    assert '"$MCT"' in command
    assert ">/data/main-computer/hub/mainneta-hub1/fdb.cluster" in command
    assert "base64 -d>/data/main-computer/hub/mainneta-hub1/hub-topology.json" in command
    assert command.endswith("exec python /app/run-exp-fdb-hub.py'")
    assert "main_computer_mainnet" not in command
    assert payload["health_check_enabled"] is False
    assert payload["health_check_path"] == "/api/hub/v1/health"
    assert payload["environment_name"] == "mainnet-hubs"


def test_application_payload_routes_direct_identity_and_shared_ingress_to_first_hub_on_controller() -> None:
    target = {
        "application_name": "mainneta-hub1",
        "network": "mainnet",
        "hub_bind_port": 8790,
        "public_url": "https://mainneta-hub1.greatlibrary.io",
        "network_ingress_url": "https://mainnet-hub.greatlibrary.io",
        "serve_network_ingress": True,
        "runtime_dir": "/data/main-computer/hub/mainneta-hub1",
        "cluster_file_path": "/data/main-computer/hub/mainneta-hub1/fdb.cluster",
        "topology_path": "/data/main-computer/hub/mainneta-hub1/hub-topology.json",
        "git_repository": "https://github.com/johnrraymond/main_computer",
        "git_branch": "main",
        "dockerfile_location": "/Dockerfile.hub.exp-fdb",
        "coolify": {"project_uuid": "project", "server_uuid": "server"},
    }

    payload = deployment._application_payload(target)

    assert payload["domains"] == (
        "https://mainneta-hub1.greatlibrary.io:8790,"
        "https://mainnet-hub.greatlibrary.io:8790"
    )


def test_application_payload_keeps_later_same_controller_hub_direct_only() -> None:
    target = {
        "application_name": "mainneta-hub2",
        "network": "mainnet",
        "hub_bind_port": 8790,
        "public_url": "https://mainneta-hub2.greatlibrary.io",
        "network_ingress_url": "https://mainnet-hub.greatlibrary.io",
        "serve_network_ingress": False,
        "runtime_dir": "/data/main-computer/hub/mainneta-hub2",
        "cluster_file_path": "/data/main-computer/hub/mainneta-hub2/fdb.cluster",
        "topology_path": "/data/main-computer/hub/mainneta-hub2/hub-topology.json",
        "git_repository": "https://github.com/johnrraymond/main_computer",
        "git_branch": "main",
        "dockerfile_location": "/Dockerfile.hub.exp-fdb",
        "coolify": {"project_uuid": "project", "server_uuid": "server"},
    }

    payload = deployment._application_payload(target)

    assert payload["domains"] == "https://mainneta-hub2.greatlibrary.io:8790"


def test_hub_control_disables_coolify_rolling_health_gate() -> None:
    target = {
        "application_name": "mainneta-hub1",
        "network": "mainnet",
        "hub_bind_port": 8790,
        "public_url": "https://mainnet-hub.example.invalid",
        "runtime_dir": "/data/main-computer/hub/mainneta-hub1",
        "cluster_file_path": "/data/main-computer/hub/mainneta-hub1/fdb.cluster",
        "topology_path": "/data/main-computer/hub/mainneta-hub1/hub-topology.json",
        "git_repository": "https://github.com/johnrraymond/main_computer",
        "git_branch": "main",
        "dockerfile_location": "/Dockerfile.hub.exp-fdb",
        "coolify": {"project_uuid": "project", "server_uuid": "server"},
    }

    payload = deployment._application_payload(target)

    # Coolify is only the deployment/materialization gate here. Hub Control's
    # observer is the authoritative readiness and dependency-consumption proof.
    assert payload["health_check_enabled"] is False
    assert payload["health_check_path"] == "/api/hub/v1/health"


def test_deployment_uuid_parser_accepts_known_coolify_shapes() -> None:
    assert deployment._deployment_uuid_from_trigger({"body": {"deployment_uuid": "dep-direct"}}) == "dep-direct"
    assert deployment._deployment_uuid_from_trigger({"body": {"deployments": [{"deployment_uuid": "dep-list"}]}}) == "dep-list"
    assert deployment._deployment_uuid_from_trigger({"body": [{"deployment_uuid": "dep-body-list"}]}) == "dep-body-list"
    assert deployment._deployment_uuid_from_trigger([{"deployment_uuid": "dep-top-list"}]) == "dep-top-list"


def test_wait_for_exact_coolify_deployment_reaches_finished(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SequenceClient(
        [
            _response(200, {"status": "queued"}),
            _response(200, {"status": "in_progress"}),
            _response(200, {"status": "finished", "commit": "abc123", "updated_at": "2026-09-26T00:10:00Z"}),
        ]
    )
    monkeypatch.setattr(deployment.time, "sleep", lambda _seconds: None)

    result = deployment._wait_for_coolify_deployment(client, "dep-1", timeout_s=30, poll_s=0)

    assert result == {
        "waited": True,
        "deployment_uuid": "dep-1",
        "status": "finished",
        "commit": "abc123",
        "updated_at": "2026-09-26T00:10:00Z",
    }
    assert [call[:2] for call in client.calls] == [
        ("GET", "/api/v1/deployments/dep-1"),
        ("GET", "/api/v1/deployments/dep-1"),
        ("GET", "/api/v1/deployments/dep-1"),
    ]


def test_wait_for_exact_coolify_deployment_surfaces_log_tail() -> None:
    client = SequenceClient([_response(200, {"status": "failed", "logs": "build exploded at step 17"})])

    with pytest.raises(HubControlError) as exc_info:
        deployment._wait_for_coolify_deployment(client, "dep-1", timeout_s=1, poll_s=0)

    assert exc_info.value.code == "HUB_COOLIFY_DEPLOYMENT_FAILED"
    assert "build exploded at step 17" in exc_info.value.message


def _observer_target() -> dict[str, Any]:
    return {
        "hub_id": "mainneta-hub1",
        "public_url": "https://mainnet-hub.example.invalid",
        "cluster_file_path": "/var/lib/main-computer/mainnet/hub/fdb.cluster",
        "fdb_contract": {"namespace": "mainnet"},
        "chain_contract": {"chain_id": 42424240, "rpc_url": "https://mainnet-rpc.example.invalid"},
    }


def test_observer_reports_partial_dependency_state_instead_of_generic_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    target = _observer_target()

    def fake_get_json(url: str, *, timeout_s: float) -> dict[str, Any]:
        del timeout_s
        if url.endswith("/health"):
            return {"ok": True}
        if url.endswith("/hub-identity"):
            return {
                "hub_id": "mainneta-hub1",
                "network": {"chain_id": 42424240, "chain_rpc_url": "https://mainnet-rpc.example.invalid"},
                "storage": {
                    "backend": "foundationdb",
                    "cluster_file": "/var/lib/main-computer/mainnet/hub/fdb.cluster",
                    "namespace": "WRONG",
                },
            }
        if url.endswith("/status"):
            return {"network": {"chain_id": 42424240, "chain_rpc_url": "https://mainnet-rpc.example.invalid"}}
        raise AssertionError(url)

    monkeypatch.setattr(deployment, "_get_json", fake_get_json)

    result = deployment.observe_hub(target, wait_timeout_s=0, request_timeout_s=0.1)

    assert result["verified"] is False
    assert result["hub_running"] is True
    assert result["fdb_adoption_verified"] is False
    assert result["chain_adoption_verified"] is True
    assert result["last_error"]["failed_checks"] == ["fdb_namespace"]
    assert result["endpoint_errors"] == {}


def test_application_payload_honors_frozen_hub_environment() -> None:
    target = {
        "application_name": "testneta-hub1",
        "network": "testnet",
        "environment_name": "testnet-hubs",
        "hub_bind_port": 8790,
        "public_url": "https://testnet-hub.example.invalid",
        "runtime_dir": "/data/main-computer/hub/testneta-hub1",
        "cluster_file_path": "/data/main-computer/hub/testneta-hub1/fdb.cluster",
        "topology_path": "/data/main-computer/hub/testneta-hub1/hub-topology.json",
        "git_repository": "https://github.com/johnrraymond/main_computer",
        "git_branch": "main",
        "dockerfile_location": "/Dockerfile.hub.exp-fdb",
        "coolify": {"project_uuid": "project", "server_uuid": "server"},
    }

    payload = deployment._application_payload(target)

    assert payload["environment_name"] == "testnet-hubs"


def _environment_target() -> dict[str, Any]:
    return {
        "network": "mainnet",
        "environment_name": "mainnet-hubs",
        "coolify": {
            "project_uuid": "project-1",
            "server_uuid": "server-1",
        },
    }


def test_ensure_coolify_environment_is_read_then_create_then_verify() -> None:
    path = "/api/v1/projects/project-1/environments"
    client = SequenceClient(
        [
            _response(200, {"environments": []}, path=path),
            _response(201, {"uuid": "env-1", "name": "mainnet-hubs"}, path=path),
            _response(200, {"environments": [{"uuid": "env-1", "name": "mainnet-hubs"}]}, path=path),
        ]
    )
    tried: list[dict[str, Any]] = []

    result = deployment._ensure_coolify_environment(client, _environment_target(), tried)

    assert result == {
        "environment_name": "mainnet-hubs",
        "environment_uuid": "env-1",
        "created": True,
    }
    assert client.calls == [
        ("GET", path, None),
        ("POST", path, {"name": "mainnet-hubs"}),
        ("GET", path, None),
    ]
    assert tried == [
        {"operation": "inspect-environment", "status": 200},
        {"operation": "create-environment", "status": 201},
        {"operation": "inspect-environment", "status": 200},
    ]


def test_ensure_coolify_environment_is_idempotent_when_environment_exists() -> None:
    path = "/api/v1/projects/project-1/environments"
    client = SequenceClient(
        [_response(200, {"environments": [{"uuid": "env-1", "name": "mainnet-hubs"}]}, path=path)]
    )
    tried: list[dict[str, Any]] = []

    result = deployment._ensure_coolify_environment(client, _environment_target(), tried)

    assert result == {
        "environment_name": "mainnet-hubs",
        "environment_uuid": "env-1",
        "created": False,
    }
    assert client.calls == [("GET", path, None)]


def test_ensure_coolify_environment_tolerates_concurrent_create_conflict() -> None:
    path = "/api/v1/projects/project-1/environments"
    client = SequenceClient(
        [
            _response(200, {"environments": []}, path=path),
            _response(422, {"message": "Environment already exists"}, path=path),
            _response(200, {"environments": [{"uuid": "env-1", "name": "mainnet-hubs"}]}, path=path),
        ]
    )

    result = deployment._ensure_coolify_environment(client, _environment_target(), [])

    assert result == {
        "environment_name": "mainnet-hubs",
        "environment_uuid": "env-1",
        "created": False,
    }


def test_apply_deployment_refuses_frozen_application_in_chain_environment() -> None:
    target = {
        "application_uuid": "app-1",
        "application_name": "mainneta-hub1",
        "network": "mainnet",
        "environment_name": "mainnet-hubs",
        "coolify": {
            "url": "https://coolify.example.invalid",
            "api_token": "token",
            "project_uuid": "project",
            "server_uuid": "server",
        },
    }
    client = SequenceClient([_response(200, {"uuid": "app-1", "environment_name": "mainnet"}, path="/api/v1/applications/app-1")])

    with pytest.raises(HubControlError) as exc_info:
        deployment.apply_deployment(target, client_factory=lambda *_args: client)

    assert exc_info.value.code == "HUB_COOLIFY_ENVIRONMENT_MISMATCH"
    assert "mainnet-hubs" in exc_info.value.message
    assert client.calls == [("GET", "/api/v1/applications/app-1", None)]


def test_progress_is_silent_by_default(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv(deployment.PROGRESS_ENV, raising=False)

    deployment._progress("hidden")

    assert capsys.readouterr().err == ""


def test_progress_goes_to_stderr_when_enabled(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv(deployment.PROGRESS_ENV, "1")

    deployment._progress("deployment: example")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "HUB_PROGRESS: deployment: example\n"


def test_wait_reports_status_transitions_when_progress_enabled(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = SequenceClient(
        [
            _response(200, {"status": "queued"}),
            _response(200, {"status": "in_progress"}),
            _response(200, {"status": "finished", "commit": "abc123"}),
        ]
    )
    monkeypatch.setenv(deployment.PROGRESS_ENV, "1")
    monkeypatch.setattr(deployment.time, "sleep", lambda _seconds: None)

    deployment._wait_for_coolify_deployment(client, "dep-1", timeout_s=30, poll_s=0)

    stderr = capsys.readouterr().err
    assert "HUB_PROGRESS: Coolify deployment: waiting uuid=dep-1" in stderr
    assert "status=queued" in stderr
    assert "status=in_progress" in stderr
    assert "finished status=finished" in stderr


def test_remove_deployment_deletes_only_frozen_application_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    target = {
        "hub_id": "mainneta-hub1",
        "application_uuid": "app-1",
        "application_name": "main-computer-mainneta-hub1",
        "legacy_application_name": "main-computer-mainnet-hub",
        "environment_name": "mainnet-hubs",
        "coolify": {
            "url": "https://coolify.example.invalid",
            "api_token": "token",
        },
    }
    app_path = "/api/v1/applications/app-1"
    client = SequenceClient(
        [
            _response(200, {"uuid": "app-1", "name": "main-computer-mainneta-hub1", "environment_name": "mainnet-hubs"}, path=app_path),
            legacy.CoolifyResponse(ok=True, status=200, method="DELETE", path=app_path, body={"message": "deleted"}),
        ]
    )
    monkeypatch.setattr(
        deployment,
        "inspect_removed_deployment",
        lambda _target, **_kwargs: {
            "verified": True,
            "verified_absent": True,
            "reason": "hub-deployment-absent",
        },
    )

    result = deployment.remove_deployment(
        target,
        client_factory=lambda *_args: client,
        absence_wait_timeout_s=0,
        absence_poll_s=0,
    )

    assert result["verified_absent"] is True
    assert result["deployment_deleted"] is True
    assert client.calls == [
        ("GET", app_path, None),
        ("DELETE", app_path, None),
    ]


def test_remove_deployment_refuses_application_identity_change() -> None:
    target = {
        "hub_id": "mainneta-hub1",
        "application_uuid": "app-1",
        "application_name": "main-computer-mainneta-hub1",
        "legacy_application_name": "main-computer-mainnet-hub",
        "environment_name": "mainnet-hubs",
        "coolify": {
            "url": "https://coolify.example.invalid",
            "api_token": "token",
        },
    }
    app_path = "/api/v1/applications/app-1"
    client = SequenceClient(
        [_response(200, {"uuid": "app-1", "name": "some-other-app", "environment_name": "mainnet-hubs"}, path=app_path)]
    )

    with pytest.raises(HubControlError) as exc_info:
        deployment.remove_deployment(target, client_factory=lambda *_args: client)

    assert exc_info.value.code == "HUB_COOLIFY_APPLICATION_IDENTITY_CHANGED"
    assert [call[:2] for call in client.calls] == [("GET", app_path)]


def _git(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(tmp_path), *args],
        text=True,
        capture_output=True,
        check=True,
    )


def _init_hub_git_fixture(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "hub-test@example.invalid")
    _git(tmp_path, "config", "user.name", "Hub Test")
    package = tmp_path / "main_computer"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "exp_fdb_hub.py").write_text("from main_computer import hub\n", encoding="utf-8")
    (package / "hub.py").write_text("HUB = True\n", encoding="utf-8")
    (package / "hub_networks.py").write_text("NETWORKS = True\n", encoding="utf-8")
    (package / "runtime_env_file.py").write_text("RUNTIME_ENV = True\n", encoding="utf-8")
    (package / "game_web_loader.py").write_text("GAME = True\n", encoding="utf-8")
    config = package / "config"
    config.mkdir()
    (config / "hub_networks.json").write_text("{}\n", encoding="utf-8")
    (config / "mainnet_contracts.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "exp-fdb-hub.py").write_text("from main_computer.exp_fdb_hub import HUB\n", encoding="utf-8")
    (tmp_path / "run-exp-fdb-hub.py").write_text("from main_computer.hub_networks import NETWORKS\n", encoding="utf-8")
    (tmp_path / "Dockerfile.hub.exp-fdb").write_text("FROM python:3.12-slim\nCOPY . /app\n", encoding="utf-8")
    (tmp_path / ".dockerignore").write_text(".git\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='hub-test'\nversion='0.0.0'\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "baseline")


def test_git_source_status_ignores_unrelated_main_computer_changes_and_detects_hub_runtime_changes(tmp_path: Path) -> None:
    _init_hub_git_fixture(tmp_path)

    game_file = tmp_path / "main_computer" / "game_web_loader.py"
    game_file.write_text("GAME = 'dirty but unrelated'\n", encoding="utf-8")

    clean = deployment._git_source_status(tmp_path, network="mainnet")

    assert clean["checked"] is True
    assert clean["dirty"] is False
    assert clean["paths"] == []
    assert "main_computer/game_web_loader.py" not in deployment._hub_git_source_paths(tmp_path, network="mainnet")

    hub_file = tmp_path / "main_computer" / "hub.py"
    hub_file.write_text("HUB = 'dirty and relevant'\n", encoding="utf-8")

    dirty = deployment._git_source_status(tmp_path, network="mainnet")

    assert dirty["checked"] is True
    assert dirty["dirty"] is True
    assert {item["path"] for item in dirty["paths"]} == {"main_computer/hub.py"}


def test_git_source_dependency_closure_follows_new_hub_imports(tmp_path: Path) -> None:
    _init_hub_git_fixture(tmp_path)
    hub_file = tmp_path / "main_computer" / "hub.py"
    hub_file.write_text("import main_computer.new_hub_runtime\nHUB = True\n", encoding="utf-8")
    new_runtime = tmp_path / "main_computer" / "new_hub_runtime.py"
    new_runtime.write_text("NEW_RUNTIME = True\n", encoding="utf-8")

    status = deployment._git_source_status(tmp_path, network="mainnet")

    assert status["dirty"] is True
    assert {item["path"] for item in status["paths"]} == {
        "main_computer/hub.py",
        "main_computer/new_hub_runtime.py",
    }


def test_git_source_guard_blocks_relevant_uncommitted_changes_by_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _init_hub_git_fixture(tmp_path)
    path = tmp_path / "Dockerfile.hub.exp-fdb"
    path.write_text("FROM python:3.12-slim\n# local Hub change\n", encoding="utf-8")

    with pytest.raises(HubControlError) as exc_info:
        deployment._check_deployment_git_source(
            {"network": "mainnet", "_local_repo_root": str(tmp_path), "_force_git": True}
        )

    assert exc_info.value.code == "HUB_DEPLOY_GIT_DIRTY"
    assert "Dockerfile.hub.exp-fdb" in exc_info.value.message
    assert "--no-force-git" in exc_info.value.message
    stderr = capsys.readouterr().err
    assert "HUB_GIT_COMMIT_PUSH_COMMAND:" in stderr
    assert (
        "git add -- 'Dockerfile.hub.exp-fdb'; "
        "if ($LASTEXITCODE -eq 0) { git commit -m 'Update Hub deployment source' }; "
        "if ($LASTEXITCODE -eq 0) { git push origin HEAD }"
    ) in stderr


def test_git_commit_push_command_contains_only_relevant_dirty_paths() -> None:
    command = deployment._render_git_commit_push_command(
        [
            {"status": "M", "path": "main_computer/hub.py"},
            {"status": "??", "path": "main_computer/hub_bridge_backend.py"},
        ]
    )

    assert command.startswith(
        "git add -- 'main_computer/hub.py' 'main_computer/hub_bridge_backend.py'; "
    )
    assert "git commit -m 'Update Hub deployment source'" in command
    assert command.endswith("git push origin HEAD }")
    assert "game_web_loader.py" not in command


def test_git_commit_push_command_powershell_quotes_paths() -> None:
    command = deployment._render_git_commit_push_command(
        [{"status": "M", "path": "main_computer/hub's helper.py"}]
    )

    assert "'main_computer/hub''s helper.py'" in command


def test_no_force_git_turns_dirty_hub_source_into_warning_not_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _init_hub_git_fixture(tmp_path)
    path = tmp_path / "main_computer" / "hub.py"
    path.write_text("HUB = 'local Hub change'\n", encoding="utf-8")
    monkeypatch.setenv(deployment.PROGRESS_ENV, "1")

    result = deployment._check_deployment_git_source(
        {"network": "mainnet", "_local_repo_root": str(tmp_path), "_force_git": False}
    )

    assert result["checked"] is True
    assert result["dirty"] is True
    assert result["blocked"] is False
    assert result["reason"] == "dirty-override"
    assert result["force_git"] is False
    stderr = capsys.readouterr().err
    assert "WARNING:" in stderr
    assert "--no-force-git allows deployment" in stderr
    assert "main_computer/hub.py" in stderr


def test_force_git_fails_closed_when_git_status_cannot_be_inspected(tmp_path: Path) -> None:
    with pytest.raises(HubControlError) as exc_info:
        deployment._check_deployment_git_source(
            {"_local_repo_root": str(tmp_path), "_force_git": True}
        )

    assert exc_info.value.code == "HUB_DEPLOY_GIT_INSPECTION_FAILED"


def test_no_force_git_can_override_git_inspection_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(deployment.PROGRESS_ENV, "1")

    result = deployment._check_deployment_git_source(
        {"_local_repo_root": str(tmp_path), "_force_git": False}
    )

    assert result["checked"] is False
    assert result["blocked"] is False
    assert result["reason"] == "inspection-failed-override"
    assert "proceeding because --no-force-git was specified" in capsys.readouterr().err


def test_observer_requires_bridge_signer_for_mainnet_target(monkeypatch: pytest.MonkeyPatch) -> None:
    target = _observer_target()
    target["bridge_signer_required"] = True

    def fake_get_json(url: str, *, timeout_s: float) -> dict[str, Any]:
        del timeout_s
        if url.endswith("/health"):
            return {"ok": True}
        if url.endswith("/hub-identity"):
            return {
                "hub_id": "mainneta-hub1",
                "network": {"chain_id": 42424240, "chain_rpc_url": "https://mainnet-rpc.example.invalid"},
                "storage": {
                    "backend": "foundationdb",
                    "cluster_file": "/var/lib/main-computer/mainnet/hub/fdb.cluster",
                    "namespace": "mainnet",
                },
            }
        if url.endswith("/status"):
            return {
                "network": {"chain_id": 42424240, "chain_rpc_url": "https://mainnet-rpc.example.invalid"},
                "bridge_backend": {
                    "mode": "contract-address-only",
                    "signer_configured": False,
                    "bridge_controller_authorized": False,
                    "write_operations_enabled": False,
                },
            }
        raise AssertionError(url)

    monkeypatch.setattr(deployment, "_get_json", fake_get_json)
    result = deployment.observe_hub(target, wait_timeout_s=0, request_timeout_s=0.1)

    assert result["verified"] is False
    assert result["hub_running"] is True
    assert result["fdb_adoption_verified"] is True
    assert result["chain_adoption_verified"] is True
    assert result["bridge_signer_verified"] is False
    assert result["last_error"]["failed_checks"] == [
        "bridge_signer_configured",
        "bridge_controller_authorized",
        "bridge_write_operations",
        "bridge_signer_mode",
    ]


def test_observer_verifies_bridge_signer_for_mainnet_target(monkeypatch: pytest.MonkeyPatch) -> None:
    target = _observer_target()
    target["bridge_signer_required"] = True

    def fake_get_json(url: str, *, timeout_s: float) -> dict[str, Any]:
        del timeout_s
        if url.endswith("/health"):
            return {"ok": True}
        if url.endswith("/hub-identity"):
            return {
                "hub_id": "mainneta-hub1",
                "network": {"chain_id": 42424240, "chain_rpc_url": "https://mainnet-rpc.example.invalid"},
                "storage": {
                    "backend": "foundationdb",
                    "cluster_file": "/var/lib/main-computer/mainnet/hub/fdb.cluster",
                    "namespace": "mainnet",
                },
            }
        if url.endswith("/status"):
            return {
                "network": {"chain_id": 42424240, "chain_rpc_url": "https://mainnet-rpc.example.invalid"},
                "bridge_backend": {
                    "mode": "bridge-signer",
                    "signer_configured": True,
                    "bridge_controller_authorized": True,
                    "write_operations_enabled": True,
                },
            }
        raise AssertionError(url)

    monkeypatch.setattr(deployment, "_get_json", fake_get_json)
    result = deployment.observe_hub(target, wait_timeout_s=0, request_timeout_s=0.1)

    assert result["verified"] is True
    assert result["bridge_signer_verified"] is True
    assert result["reason"] == "hub-fdb-chain-and-bridge-signer-consumption-verified"


def _bridge_preflight_target() -> dict[str, Any]:
    return {
        "network": "mainnet",
        "network_kind": "mainnet",
        "chain_contract": {
            "chain_id": 42424240,
            "rpc_url": "https://mainnet-rpc.greatlibrary.io",
        },
    }


def _bridge_preflight_signer() -> dict[str, Any]:
    return {
        "escrow_address": "0x1111111111111111111111111111111111111111",
        "bridge_controller_address": "0x2222222222222222222222222222222222222222",
    }


def test_contract_deployer_command_is_copy_paste_mainnet_operator_without_secret_literal() -> None:
    command = deployment._render_contract_deployer_command(_bridge_preflight_target())

    assert command.startswith("$env:MAINNET_DEPLOYER_PRIVATE_KEY = python -c ")
    assert "runtime/state/main_computer.private.yaml" in command
    assert "python .\\tools\\mainnet-operator.py deploy-contracts" in command
    assert "--target-environment mainnet" in command
    assert "--chain-id 42424240" in command
    assert "--rpc-url 'https://mainnet-rpc.greatlibrary.io'" in command
    assert "--private-key-env MAINNET_DEPLOYER_PRIVATE_KEY" in command
    assert "--offices $offices --yes" in command
    assert "0x" + "11" * 32 not in command


def test_bridge_signer_live_preflight_blocks_missing_escrow_and_emits_deployer_command(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    def fake_rpc(_url: str, method: str, _params: list[Any], *, timeout_s: float = 12.0) -> Any:
        del timeout_s
        calls.append(method)
        if method == "eth_chainId":
            return hex(42424240)
        if method == "eth_getCode":
            return "0x"
        raise AssertionError(method)

    monkeypatch.setattr(deployment, "_hub_chain_rpc", fake_rpc)

    with pytest.raises(HubControlError) as exc_info:
        deployment._verify_live_bridge_signer_contract(
            _bridge_preflight_target(),
            _bridge_preflight_signer(),
        )

    assert exc_info.value.code == "HUB_BRIDGE_ESCROW_NOT_LIVE"
    assert calls == ["eth_chainId", "eth_getCode"]
    stderr = capsys.readouterr().err
    assert "HUB_CONTRACT_DEPLOYER_COMMAND:" in stderr
    assert "mainnet-operator.py deploy-contracts" in stderr
    assert "rerun the same add-hub command" in stderr


def test_bridge_signer_live_preflight_requires_controller_authorization(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_rpc(_url: str, method: str, _params: list[Any], *, timeout_s: float = 12.0) -> Any:
        del timeout_s
        if method == "eth_chainId":
            return hex(42424240)
        if method == "eth_getCode":
            return "0x6001600055"
        if method == "eth_call":
            return "0x" + "0" * 64
        raise AssertionError(method)

    monkeypatch.setattr(deployment, "_hub_chain_rpc", fake_rpc)

    with pytest.raises(HubControlError) as exc_info:
        deployment._verify_live_bridge_signer_contract(
            _bridge_preflight_target(),
            _bridge_preflight_signer(),
        )

    assert exc_info.value.code == "HUB_BRIDGE_CONTROLLER_NOT_AUTHORIZED"
    assert "HUB_CONTRACT_DEPLOYER_COMMAND:" in capsys.readouterr().err


def test_bridge_signer_live_preflight_verifies_live_contract_and_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_call_data = ""

    def fake_rpc(_url: str, method: str, params: list[Any], *, timeout_s: float = 12.0) -> Any:
        nonlocal seen_call_data
        del timeout_s
        if method == "eth_chainId":
            return hex(42424240)
        if method == "eth_getCode":
            return "0x" + "60" * 123
        if method == "eth_call":
            seen_call_data = str(params[0]["data"])
            return "0x" + "0" * 63 + "1"
        raise AssertionError(method)

    monkeypatch.setattr(deployment, "_hub_chain_rpc", fake_rpc)
    result = deployment._verify_live_bridge_signer_contract(
        _bridge_preflight_target(),
        _bridge_preflight_signer(),
    )

    assert result["verified"] is True
    assert result["code_bytes"] == 123
    assert result["bridge_controller_authorized"] is True
    assert seen_call_data.startswith("0x" + deployment.IS_BRIDGE_CONTROLLER_SELECTOR)
    assert seen_call_data.endswith("2" * 40)


def test_apply_deployment_runs_bridge_contract_preflight_before_coolify_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _bridge_preflight_target()
    target.update({"_local_repo_root": "/repo", "hub_id": "mainneta-hub1"})
    signer = _bridge_preflight_signer()

    monkeypatch.setattr(deployment, "_check_deployment_git_source", lambda _target: {"checked": True, "dirty": False})
    monkeypatch.setattr(deployment, "_build_bridge_signer_for_deployment", lambda _target: signer)

    def fail_preflight(_target: Mapping[str, Any], _signer: Mapping[str, Any]) -> dict[str, Any]:
        raise HubControlError("HUB_BRIDGE_ESCROW_NOT_LIVE", "missing")

    monkeypatch.setattr(deployment, "_verify_live_bridge_signer_contract", fail_preflight)

    def client_factory(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Coolify client must not be created before bridge preflight passes")

    with pytest.raises(HubControlError) as exc_info:
        deployment.apply_deployment(target, client_factory=client_factory)

    assert exc_info.value.code == "HUB_BRIDGE_ESCROW_NOT_LIVE"
