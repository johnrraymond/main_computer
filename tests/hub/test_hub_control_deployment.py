from __future__ import annotations

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
