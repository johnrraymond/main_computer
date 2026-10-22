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



@pytest.mark.parametrize("network", ["mainnet", "testnet"])
def test_create_hub_storage_uses_coolify_persistent_volume_schema(network: str) -> None:
    client = SequenceClient([_response(200, []), _response(201, {"uuid": "volume-id"})])
    target = {
        "network": network,
        "application_name": f"main-computer-{network}-hub1",
        "runtime_dir": f"/data/main-computer/hub/{network}-hub1",
    }
    tried: list[dict[str, Any]] = []

    deployment._ensure_storage(client, target, "existing-hub-uuid", tried)

    assert client.calls == [
        ("GET", "/api/v1/applications/existing-hub-uuid/storages", None),
        (
            "POST",
            "/api/v1/applications/existing-hub-uuid/storages",
            {
                "type": "persistent",
                "name": f"main-computer-{network}-hub1-data",
                "mount_path": target["runtime_dir"],
            },
        ),
    ]
    assert tried == [{"operation": "create-storage", "status": 201}]


def test_hub_storage_retry_reuses_existing_volume() -> None:
    target = {"application_name": "main-computer-testnetb-hub1", "runtime_dir": "/data/main-computer/hub/testnetb-hub1"}
    client = SequenceClient([_response(200, [{"uuid": "saved-volume", "mount_path": target["runtime_dir"]}])])
    tried: list[dict[str, Any]] = []

    deployment._ensure_storage(client, target, "wxpystvphksftagzfmxgoctm", tried)

    assert client.calls == [("GET", "/api/v1/applications/wxpystvphksftagzfmxgoctm/storages", None)]
    assert tried == []


def test_hub_storage_creation_failure_still_fails_closed() -> None:
    target = {"application_name": "main-computer-testnetb-hub1", "runtime_dir": "/data/main-computer/hub/testnetb-hub1"}
    client = SequenceClient([_response(200, []), _response(422, {"message": "Validation failed."})])
    tried: list[dict[str, Any]] = []

    with pytest.raises(HubControlError) as exc_info:
        deployment._ensure_storage(client, target, "existing-hub-uuid", tried)

    assert exc_info.value.code == "HUB_COOLIFY_STORAGE_FAILED"
    assert tried == [{"operation": "create-storage", "status": 422}]


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
    assert command.endswith("exec python /app/run-stable-hub.py'")
    assert "main_computer_mainnet" not in command
    assert payload["health_check_enabled"] is False
    assert payload["health_check_path"] == "/health"
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
    assert payload["health_check_path"] == "/health"


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
        "bridge_signer_required": False,  # isolate the Coolify-placement guard
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
    (package / "stable_hub.py").write_text("from main_computer import hub\n", encoding="utf-8")
    (package / "hub.py").write_text("HUB = True\n", encoding="utf-8")
    (package / "hub_networks.py").write_text("NETWORKS = True\n", encoding="utf-8")
    (package / "runtime_env_file.py").write_text("RUNTIME_ENV = True\n", encoding="utf-8")
    (package / "game_web_loader.py").write_text("GAME = True\n", encoding="utf-8")
    config = package / "config"
    config.mkdir()
    (config / "hub_networks.json").write_text("{}\n", encoding="utf-8")
    (config / "mainnet_contracts.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "run-stable-hub.py").write_text("from main_computer.stable_hub import HUB\n", encoding="utf-8")
    
    (tmp_path / "Dockerfile.hub.stable").write_text("FROM python:3.12-slim\nCOPY . /app\n", encoding="utf-8")
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
    path = tmp_path / "Dockerfile.hub.stable"
    path.write_text("FROM python:3.12-slim\n# local Hub change\n", encoding="utf-8")

    with pytest.raises(HubControlError) as exc_info:
        deployment._check_deployment_git_source(
            {"network": "mainnet", "_local_repo_root": str(tmp_path), "_force_git": True}
        )

    assert exc_info.value.code == "HUB_DEPLOY_GIT_DIRTY"
    assert "Dockerfile.hub.stable" in exc_info.value.message
    assert "--no-force-git" in exc_info.value.message
    stderr = capsys.readouterr().err
    assert "HUB_GIT_COMMIT_PUSH_COMMAND:" in stderr
    assert (
        "git add -- 'Dockerfile.hub.stable'; "
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


def test_contract_deployer_command_uses_verified_mother_without_private_yaml_or_secret_literal() -> None:
    command = deployment._render_contract_deployer_command(_bridge_preflight_target())

    assert command.startswith("python -m tools.mother_contract_deploy ")
    assert "--network 'mainnet'" in command
    assert "--chain-id 42424240" in command
    assert "--rpc-url 'https://mainnet-rpc.greatlibrary.io'" in command
    assert "--yes" in command
    assert "runtime/state/main_computer.private.yaml" not in command
    assert "python -c" not in command
    assert "PRIVATE_KEY" not in command
    assert "0x" + "11" * 32 not in command


def test_contract_deployer_command_rejects_unsupported_network_and_quotes_rpc() -> None:
    target = _bridge_preflight_target()
    target["network"] = "development'; Write-Host unsafe"
    assert deployment._render_contract_deployer_command(target) == ""
    target["network"] = "testnet"
    target["chain_contract"]["rpc_url"] = "https://host.invalid/a'b"
    command = deployment._render_contract_deployer_command(target)
    assert "--network 'testnet'" in command
    assert "--rpc-url 'https://host.invalid/a''b'" in command


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
    assert "python -m tools.mother_contract_deploy" in stderr
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
    assert "HUB_CONTRACT_DEPLOYER_COMMAND:" not in capsys.readouterr().err


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


def test_apply_deployment_syncs_assigned_bridge_signer_to_existing_coolify_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: previous 502 crash loop used a persisted legacy controller.

    The escrow preflight passed locally, but the production signer env was not
    sent to Coolify. Reusing an existing application must sync the new signer
    BEFORE forced deploy, without leaking its private material in the receipt.
    """
    import base64
    import json

    bridge_private_key = "0x" + "3" * 64
    controller = "0x" + "ab" * 20
    escrow = "0x000000000000000000000000000000000000e5c0"
    signer_payload = {
        "schema": "main-computer.bridge-signer.v1",
        "network": "mainnet",
        "chain_id": 42424240,
        "chain_rpc_url": "https://rpc.example.invalid",
        "contracts": {"hub_credit_bridge_escrow": {
            "address": escrow,
            "bridge_controller_address": controller,
        }},
        "bridge_controller": {"address": controller, "private_key": bridge_private_key},
    }
    encoded = base64.b64encode(json.dumps(signer_payload).encode()).decode("ascii")
    signer = {
        "env_key": deployment.BRIDGE_SIGNER_ENV,
        "bundle_b64": encoded,
        "bundle_sha256": "test-sha256",
        "bundle_bytes": len(encoded),
        "bridge_controller_address": controller,
        "escrow_address": escrow,
        "chain_id": 42424240,
        "remote_path": "/data/main-computer/hub/mainneta-hub1/private/bridge-signer/bridge-signer-bundle.json",
    }
    target = {
        "network": "mainnet",
        "hub_id": "mainneta-hub1",
        "application_name": "main-computer-mainneta-hub1",
        "environment_name": "mainnet-hubs",
        "public_url": "https://mainneta-hub1.example.invalid",
        "runtime_dir": "/data/main-computer/hub/mainneta-hub1",
        "cluster_file_path": "/data/main-computer/hub/mainneta-hub1/fdb.cluster",
        "topology_path": "/data/main-computer/hub/mainneta-hub1/hub-topology.json",
        "hub_bind_port": 8790,
        "network_display_name": "Mainnet",
        "network_kind": "mainnet",
        "topology": {"hubs": []},
        "fdb_contract": {"connection_string": "test:test@127.0.0.1:4500", "namespace": "mainnet", "sha256": "fdb-sha"},
        "chain_contract": {"chain_id": 42424240, "rpc_url": "https://rpc.example.invalid", "sha256": "chain-sha"},
        "private_https_routes": {"rpc.example.invalid": "10.116.0.3", "mainneta-hub1.example.invalid": "10.116.0.3"},
        "git_repository": "https://github.com/example/main_computer.git",
        "git_branch": "main",
        "dockerfile_location": "/Dockerfile.hub.exp-fdb",
        "coolify": {"url": "https://coolify.example.invalid", "api_token": "token", "project_uuid": "project", "server_uuid": "server"},
    }
    events: list[tuple[str, str]] = []
    synced: dict[str, str] = {}

    monkeypatch.setattr(deployment, "_check_deployment_git_source", lambda _target: {"checked": True, "dirty": False})
    monkeypatch.setattr(deployment, "_build_bridge_signer_for_deployment", lambda _target: signer)
    monkeypatch.setattr(deployment, "_verify_live_bridge_signer_contract", lambda *_args: {"verified": True})
    monkeypatch.setattr(deployment.legacy, "find_application", lambda *_args, **_kwargs: ("existing-hub", {"uuid": "existing-hub"}))
    monkeypatch.setattr(deployment, "_ensure_storage", lambda *_args, **_kwargs: None)

    def sync(_client: object, *, application_uuid: str, values: dict, tried: list) -> None:
        assert application_uuid == "existing-hub"
        synced.update(values)
        events.extend(("sync", key) for key in values)

    def trigger(_client: object, *, application_uuid: str, force: bool, tried: list) -> dict:
        assert application_uuid == "existing-hub" and force
        assert synced[deployment.BRIDGE_SIGNER_ENV] == encoded
        events.append(("deploy", application_uuid))
        return {"deployment_uuid": "deployment-2"}

    monkeypatch.setattr(deployment, "_sync_application_envs_bulk", sync)
    monkeypatch.setattr(deployment.legacy, "trigger_deploy", trigger)
    monkeypatch.setattr(deployment, "_wait_for_coolify_deployment", lambda *_args, **_kwargs: {
        "status": "finished", "waited": True,
    })

    class FakeClient:
        def request(self, method: str, path: str, payload: Any = None):
            assert method == "PATCH" and path == "/api/v1/applications/existing-hub"
            return _response(200, {"uuid": "existing-hub"}, path=path)

    result = deployment.apply_deployment(target, client_factory=lambda *_args: FakeClient())
    assert result["application_uuid"] == "existing-hub"
    assert result["action"] == "updated"
    assert result["bridge_signer"]["bridge_controller_address"] == controller
    assert synced[deployment.BRIDGE_SIGNER_ENV] == encoded
    assert events.index(("sync", deployment.BRIDGE_SIGNER_ENV)) < events.index(("deploy", "existing-hub"))
    assert json.loads(synced["MAIN_COMPUTER_HUB_PRIVATE_HTTPS_ROUTES"]) == target["private_https_routes"]
    assert synced["MAIN_COMPUTER_HUB_REQUIRE_PRIVATE_HTTPS_ROUTES"] == "true"
    assert events.index(("sync", "MAIN_COMPUTER_HUB_PRIVATE_HTTPS_ROUTES")) < events.index(("deploy", "existing-hub"))
    assert deployment.BRIDGE_SIGNER_ENV in result["environment_variables"]
    assert encoded not in json.dumps(result)
    assert bridge_private_key not in json.dumps(result)


def test_synced_production_bridge_signer_overwrites_persisted_legacy_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing Hub bootstrap materializes the authoritative Coolify env over stale disk state."""
    import base64
    import importlib
    import json

    module = importlib.import_module("main_computer.exp_fdb_hub")
    destination = tmp_path / "private/bridge-signer/bridge-signer-bundle.json"
    destination.parent.mkdir(parents=True)
    destination.write_text(json.dumps({"schema": "main-computer.bridge-signer.v1", "bridge_controller": {
        "address": "0x2502BcEe4e4D43654F98f1202e796B997C55Ed1f"}}))
    replacement = {"schema": "main-computer.bridge-signer.v1", "bridge_controller": {
        "address": "0x" + "ab" * 20, "private_key": "0x" + "3" * 64}}
    monkeypatch.setenv(deployment.BRIDGE_SIGNER_ENV, base64.b64encode(json.dumps(replacement).encode()).decode())
    assert module._materialize_bridge_signer_bundle_from_env(destination)
    assert json.loads(destination.read_text())["bridge_controller"]["address"] == "0x" + "ab" * 20
    assert deployment.BRIDGE_SIGNER_ENV not in __import__("os").environ


def test_stable_runtime_observer_checks_stable_identity_not_experimental_status(monkeypatch: pytest.MonkeyPatch) -> None:
    target = {**_observer_target(), "hub_runtime": "stable"}
    called = []

    def get(url: str, *, timeout_s: float) -> dict[str, Any]:
        del timeout_s
        called.append(url)
        if url.endswith("/health"):
            return {"ok": True, "service": "main_computer.stable_hub"}
        if url.endswith("/hub-identity"):
            return {
                "service": "main_computer.stable_hub",
                "hub_id": target["hub_id"],
                "network": {"chain_id": "42424240", "chain_rpc_url": "https://mainnet-rpc.example.invalid"},
                "storage": {"backend": "foundationdb", "cluster_file": target["cluster_file_path"], "namespace": "mainnet"},
            }
        if url.endswith("/topology"):
            return {"service": "main_computer.stable_hub", "hub_id": target["hub_id"]}
        raise AssertionError(url)

    monkeypatch.setattr(deployment, "_get_json", get)
    status = deployment.observe_hub(target, wait_timeout_s=0)
    assert status["verified"] is True
    assert status["bridge_signer_verified"] is False
    assert status["wallet_funding_supported"] is False
    assert not any(url.endswith("/status") for url in called)


def test_stable_runtime_observer_rejects_exp_service(monkeypatch: pytest.MonkeyPatch) -> None:
    target = {**_observer_target(), "hub_runtime": "stable"}

    def get(url: str, *, timeout_s: float) -> dict[str, Any]:
        del timeout_s
        if url.endswith("/health"):
            return {"ok": True, "service": "main_computer.exp_fdb_hub"}
        if url.endswith("/hub-identity"):
            return {"service": "main_computer.exp_fdb_hub", "hub_id": target["hub_id"]}
        return {"service": "main_computer.exp_fdb_hub", "hub_id": target["hub_id"]}

    monkeypatch.setattr(deployment, "_get_json", get)
    status = deployment.observe_hub(target, wait_timeout_s=0)
    assert status["verified"] is False
    assert status["checks"]["stable_identity"] is False


@pytest.mark.parametrize("network", ["mainnet", "testnet"])
def test_bulk_application_env_sync_is_one_write_then_exact_readback(network: str) -> None:
    values = {"MCT": "encoded-topology", "MCF": "cluster-file", "PRIVATE_BUNDLE": "secret-value"}
    path = "/api/v1/applications/hub-1/envs"
    client = SequenceClient([
        _response(201, [{"key": key} for key in values], path=path + "/bulk"),
        _response(200, [{"key": key, "value": value, "is_preview": False} for key, value in values.items()], path=path),
    ])
    tried: list[dict[str, Any]] = []
    deployment._sync_application_envs_bulk(client, application_uuid="hub-1", values=values, tried=tried)
    assert [call[:2] for call in client.calls] == [
        ("PATCH", path + "/bulk"), ("GET", path),
    ]
    posted = client.calls[0][2]
    assert set(posted) == {"data"}
    assert {e["key"]: e["value"] for e in posted["data"]} == values
    assert all(e["is_literal"] and e["is_runtime"] and not e["is_buildtime"] for e in posted["data"])
    assert "secret-value" not in repr(tried)
    assert tried[-1]["verified_count"] == 3


def test_bulk_sync_retries_coolify_rate_limit_using_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    path = "/api/v1/applications/hub-1/envs"
    client = SequenceClient([
        legacy.CoolifyResponse(False, 429, "PATCH", path + "/bulk", {"message": "Too Many Attempts."}, retry_after_s=7.0),
        _response(201, {}, path=path + "/bulk"),
        _response(200, [{"key": "MCT", "real_value": "token"}], path=path),
    ])
    slept: list[float] = []
    monkeypatch.setattr(deployment.time, "sleep", slept.append)
    deployment._sync_application_envs_bulk(client, application_uuid="hub-1", values={"MCT": "token"}, tried=[])
    assert slept == [7.0]
    assert [call[0] for call in client.calls] == ["PATCH", "PATCH", "GET"]


def test_bulk_sync_429_exhaustion_fails_without_any_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    path = "/api/v1/applications/hub-1/envs/bulk"
    client = SequenceClient([_response(429, {"message": "Too Many Attempts."}) for _ in range(3)])
    monkeypatch.setattr(deployment.time, "sleep", lambda _n: None)
    with pytest.raises(HubControlError, match="rate limited") as exc:
        deployment._sync_application_envs_bulk(client, application_uuid="hub-1", values={"KEY": "secret"}, tried=[])
    assert exc.value.code == "HUB_COOLIFY_ENVS_RATE_LIMITED"
    assert len(client.calls) == 3 and all(call[1] == path for call in client.calls)


@pytest.mark.parametrize("bad_items", [
    [{"key": "MCT", "value": "old"}],
    [{"key": "OTHER", "value": "token"}],
    [{"key": "MCT", "value": "token"}, {"key": "MCT", "value": "token"}],
    [{"key": "MCT", "value": "token", "is_preview": True}],
])
def test_bulk_sync_never_accepts_incomplete_or_ambiguous_readback(
    bad_items: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SequenceClient([_response(201, {})] + [_response(200, bad_items) for _ in range(3)])
    monkeypatch.setattr(deployment.time, "sleep", lambda _n: None)
    with pytest.raises(HubControlError) as exc:
        deployment._sync_application_envs_bulk(client, application_uuid="hub-1", values={"MCT": "token"}, tried=[])
    assert exc.value.code == "HUB_COOLIFY_ENVS_VERIFY_FAILED"
    assert [x[0] for x in client.calls] == ["PATCH", "GET", "GET", "GET"]


def test_bulk_sync_rejects_unsupported_endpoint_without_per_key_fallback() -> None:
    client = SequenceClient([_response(404, {"message": "Not found"})])
    with pytest.raises(HubControlError) as exc:
        deployment._sync_application_envs_bulk(client, application_uuid="hub-1", values={"MCT": "token"}, tried=[])
    assert exc.value.code == "HUB_COOLIFY_ENVS_BULK_FAILED"
    assert len(client.calls) == 1


def test_bulk_sync_readback_429_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SequenceClient([_response(201, {})] + [_response(429, {}) for _ in range(3)])
    slept: list[float] = []
    monkeypatch.setattr(deployment.time, "sleep", slept.append)
    with pytest.raises(HubControlError) as exc:
        deployment._sync_application_envs_bulk(client, application_uuid="hub-1", values={"MCT": "token"}, tried=[])
    assert exc.value.code == "HUB_COOLIFY_ENVS_RATE_LIMITED"
    assert slept == [20.0, 40.0]
    assert len(client.calls) == 4


def test_coolify_client_captures_retry_after_header(monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    from urllib.error import HTTPError

    def reject(_request: object, *, timeout: float) -> None:
        raise HTTPError(
            "https://coolify.example.invalid/api/v1/applications/app/envs/bulk", 429,
            "Too Many Attempts", {"Retry-After": "17"}, io.BytesIO(b'{"message":"Too Many Attempts."}'),
        )

    monkeypatch.setattr(legacy.urllib.request, "urlopen", reject)
    client = legacy.CoolifyClient("https://coolify.example.invalid", "private-token")
    response = client.request("PATCH", "/api/v1/applications/app/envs/bulk", {"data": []})
    assert response.status == 429
    assert response.retry_after_s == 17.0


@pytest.mark.parametrize("network", ["mainnet", "testnet"])
def test_bulk_sync_absent_optional_path_is_omitted_and_verified(network: str) -> None:
    del network  # The same Hub deployment path serves both networks.
    values = {"MCT": "encoded-topology", "MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH": ""}
    client = SequenceClient([
        _response(201, {}),
        _response(200, [{"key": "MCT", "value": "encoded-topology"}]),
    ])
    tried: list[dict[str, Any]] = []
    deployment._sync_application_envs_bulk(client, application_uuid="hub-1", values=values, tried=tried)
    assert [call[0] for call in client.calls] == ["PATCH", "GET"]
    assert [env["key"] for env in client.calls[0][2]["data"]] == ["MCT"]
    assert tried[-1]["verified_count"] == 2


@pytest.mark.parametrize("network", ["mainnet", "testnet"])
def test_bulk_sync_deletes_stale_optional_path_then_verifies_absence(network: str, monkeypatch: pytest.MonkeyPatch) -> None:
    del network
    monkeypatch.setattr(deployment.time, "sleep", lambda _s: None)
    values = {"MCT": "encoded-topology", "MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH": ""}
    existing = [{"key": "MCT", "value": "encoded-topology"},
                {"key": "MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH", "uuid": "old-env-uuid",
                 "value": "/stale/bridge/path"}]
    client = SequenceClient([
        _response(201, {}), _response(200, existing), _response(200, {"message": "Environment variable deleted."}),
        _response(200, [{"key": "MCT", "value": "encoded-topology"}]),
    ])
    deployment._sync_application_envs_bulk(client, application_uuid="hub-1", values=values, tried=[])
    assert [call[:2] for call in client.calls] == [
        ("PATCH", "/api/v1/applications/hub-1/envs/bulk"),
        ("GET", "/api/v1/applications/hub-1/envs"),
        ("DELETE", "/api/v1/applications/hub-1/envs/old-env-uuid"),
        ("GET", "/api/v1/applications/hub-1/envs"),
    ]


def test_bulk_sync_never_accepts_stale_optional_path_without_verified_deletion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deployment.time, "sleep", lambda _s: None)
    row = [{"key": "MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH", "uuid": "env-id", "value": "old"}]
    client = SequenceClient([
        _response(200, row), _response(200, {"message": "deleted"}),
        _response(200, row), _response(200, row),
    ])
    with pytest.raises(HubControlError, match="pending-deletion") as exc:
        deployment._sync_application_envs_bulk(
            client, application_uuid="hub-1", values={"MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH": ""}, tried=[],
        )
    assert exc.value.code == "HUB_COOLIFY_ENVS_VERIFY_FAILED"
    assert [c[0] for c in client.calls] == ["GET", "DELETE", "GET", "GET"]


def test_bulk_sync_rejects_unknown_optional_env_identity_without_deleting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deployment.time, "sleep", lambda _s: None)
    item = {"key": "MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH", "value": None, "is_shown_once": True}
    client = SequenceClient([_response(200, [item])] * 3)
    with pytest.raises(HubControlError, match="missing-uuid"):
        deployment._sync_application_envs_bulk(
            client, application_uuid="hub-1", values={"MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH": ""}, tried=[],
        )
    assert [c[0] for c in client.calls] == ["GET", "GET", "GET"]


def test_bulk_sync_fails_closed_when_optional_env_delete_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deployment.time, "sleep", lambda _s: None)
    client = SequenceClient([
        _response(200, [{"key": "MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH", "uuid": "old-env-uuid", "value": "old"}]),
        _response(403, {"message": "Forbidden"}),
    ])
    with pytest.raises(HubControlError, match="deletion failed") as exc:
        deployment._sync_application_envs_bulk(
            client, application_uuid="hub-1", values={"MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH": ""}, tried=[],
        )
    assert exc.value.code == "HUB_COOLIFY_ENVS_VERIFY_FAILED"


def test_bulk_sync_rejects_ambiguous_optional_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deployment.time, "sleep", lambda _s: None)
    same_key = "MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH"
    client = SequenceClient([_response(200, [{"key": same_key, "uuid": "x"}, {"key": same_key, "uuid": "y"}])] * 3)
    with pytest.raises(HubControlError, match="duplicate"):
        deployment._sync_application_envs_bulk(client, application_uuid="hub-1", values={same_key: ""}, tried=[])
    assert all(call[0] == "GET" for call in client.calls)


def test_bulk_sync_nonempty_deployment_path_still_requires_readback(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SequenceClient([_response(201, {})] + [_response(200, []) for _ in range(3)])
    monkeypatch.setattr(deployment.time, "sleep", lambda _n: None)
    with pytest.raises(HubControlError) as exc:
        deployment._sync_application_envs_bulk(
            client, application_uuid="hub-1",
            values={"MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH": "/app/private/secret.json"}, tried=[],
        )
    assert exc.value.code == "HUB_COOLIFY_ENVS_VERIFY_FAILED"
    assert "MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH=missing" in str(exc.value)
    assert "/app/private/secret.json" not in str(exc.value)


def test_bulk_sync_failure_identifies_mismatch_without_leaking_value(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SequenceClient([_response(201, {})] + [_response(200, [{"key": "MCT", "value": "stale"}]) for _ in range(3)])
    monkeypatch.setattr(deployment.time, "sleep", lambda _n: None)
    with pytest.raises(HubControlError) as exc:
        deployment._sync_application_envs_bulk(
            client, application_uuid="hub-1", values={"MCT": "private-secret-topology"}, tried=[],
        )
    assert exc.value.code == "HUB_COOLIFY_ENVS_VERIFY_FAILED"
    assert "MCT=different" in str(exc.value)
    assert "private-secret-topology" not in str(exc.value)
    assert "stale" not in str(exc.value)




def test_stable_runtime_testnet_requires_matching_bridge_completion_status(monkeypatch: pytest.MonkeyPatch) -> None:
    target = {**_observer_target(), "hub_runtime": "stable", "network": "testnet",
              "bridge_signer_required": True, "runtime_dir": "/data/main-computer/hub/testnetb-hub1",
              "hub_admin_address": "0x" + "a" * 40}
    target["chain_contract"] = {
        "chain_id": 42424241,
        "rpc_url": "https://testnet-rpc.example.invalid",
        "contracts": {"hub_credit_bridge_escrow": "0x" + "b" * 40},
    }
    target["fdb_contract"]["namespace"] = "testnet"
    target["hub_id"] = "testnetb-hub1"
    status = {
        "ok": True,
        "deployment": {
            "chain_id": 42424241,
            "contract_address": "0x" + "b" * 40,
            "bridge_controller_address": "0x" + "a" * 40,
            "deployment_manifest_path": target["runtime_dir"] + "/private/bridge-signer/bridge-signer-bundle.json",
        },
    }
    def get(url: str, *, timeout_s: float) -> dict[str, Any]:
        if url.endswith("/health"):
            return {"ok": True, "service": "main_computer.stable_hub"}
        if url.endswith("/hub-identity"):
            return {"service": "main_computer.stable_hub", "hub_id": target["hub_id"],
                    "network": {"chain_id": 42424241, "chain_rpc_url": target["chain_contract"]["rpc_url"]},
                    "storage": {"backend": "foundationdb", "cluster_file": target["cluster_file_path"],
                                "namespace": "testnet"}}
        if url.endswith("/topology"):
            return {"service": "main_computer.stable_hub", "hub_id": target["hub_id"]}
        if url.endswith("/credits/wallet-funding/completion"):
            return status
        raise AssertionError(url)

    monkeypatch.setattr(deployment, "_get_json", get)
    observed = deployment.observe_hub(target, wait_timeout_s=0)
    assert observed["verified"] is True
    assert observed["bridge_signer_verified"] is True
    assert observed["wallet_funding_supported"] is True
    status["ok"] = False
    status["error"] = "missing signer"
    observed = deployment.observe_hub(target, wait_timeout_s=0)
    assert observed["verified"] is False
    assert observed["checks"]["bridge_deployment_loaded"] is False
    status["ok"] = True
    status["deployment"]["chain_id"] = 42424240
    observed = deployment.observe_hub(target, wait_timeout_s=0)
    assert observed["verified"] is False
    assert observed["checks"]["bridge_chain_id"] is False


def test_signing_policy_covers_testnet_and_mainnet() -> None:
    from tools.hub_control.common.deployment import _bridge_signer_required
    assert _bridge_signer_required({"network": "testnet"})
    assert _bridge_signer_required({"network": "mainnet"})
    assert not _bridge_signer_required({"network": "dev"})
    assert not _bridge_signer_required({"network": "test"})
