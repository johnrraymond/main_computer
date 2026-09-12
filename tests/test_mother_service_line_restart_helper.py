from __future__ import annotations

import base64
import json
from pathlib import Path
import urllib.parse

import pytest
import yaml

from tools.mother.common.private_state import PrivateStateReadResult
from tools.mother_service_line_restart_helper import (
    RUNTIME_PREFIX,
    MotherServiceLineRestartHelperError,
    _helper_compose,
    _helper_shell_script,
    _parse_runtime_event_line,
    _validate_display_name_guard,
    execute_service_line_restart_helper,
)


PARENT_SERVICE_UUID = "9agfnimu0pwdizo9dogbevfc"
HELPER_SERVICE_UUID = "restarthelper123"
NODE = "mainnetc-super1"


class FakeResponse:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._raw = json.dumps(payload).encode("utf-8")

    def getcode(self) -> int:
        return self.status

    def read(self, _limit: int = -1) -> bytes:
        return self._raw

    def close(self) -> None:
        return None


class FakeCoolifyOpener:
    def __init__(self, *, delete_ok: bool = True, logs_payload: object | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.created_body: dict[str, object] | None = None
        self.delete_ok = delete_ok
        self.logs_payload = logs_payload

    def __call__(self, request, timeout: float):  # noqa: ANN001 - urllib-compatible fake
        parsed = urllib.parse.urlsplit(request.full_url)
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        method = request.get_method()
        body = None
        if request.data:
            body = json.loads(request.data.decode("utf-8"))
        self.calls.append({"method": method, "path": path, "body": body, "timeout": timeout})

        if method == "GET" and path == "/api/v1/services":
            return FakeResponse(200, {"services": [{"uuid": PARENT_SERVICE_UUID, "name": NODE, "status": "exited"}]})

        if method == "GET" and path == "/api/v1/projects/project-mainnet/environments":
            return FakeResponse(200, {"environments": [{"uuid": "environment-mainnet", "name": "mainnet"}]})

        if method == "POST" and path == "/api/v1/services":
            assert isinstance(body, dict)
            self.created_body = body
            return FakeResponse(201, {"uuid": HELPER_SERVICE_UUID})

        if method == "POST" and path == f"/api/v1/services/{HELPER_SERVICE_UUID}/start":
            return FakeResponse(200, {"status": "queued"})

        if method == "GET" and path == f"/api/v1/services/{HELPER_SERVICE_UUID}":
            return FakeResponse(200, {"uuid": HELPER_SERVICE_UUID, "name": "mother-service-line-restart-coolify-c-mainnetc-super1-test", "status": "exited:0"})

        if method == "GET" and path.startswith(f"/api/v1/services/{HELPER_SERVICE_UUID}/logs"):
            payload = self.logs_payload
            if payload is None:
                payload = {
                    "logs": "\n".join(
                        [
                            f"{RUNTIME_PREFIX} phase=script_start project={PARENT_SERVICE_UUID} service_line={NODE}",
                            f"{RUNTIME_PREFIX} phase=complete status=pass reason=service-line-running action=start container_id=a1ed01acf36a container_name=22329075c38f_{NODE}-{PARENT_SERVICE_UUID} after_status=running after_running=true after_health=healthy",
                        ]
                    )
                }
            return FakeResponse(200, payload)

        if method == "DELETE" and path == f"/api/v1/services/{HELPER_SERVICE_UUID}":
            return FakeResponse(200 if self.delete_ok else 500, {"deleted": self.delete_ok})

        raise AssertionError(f"unexpected request: {method} {path}")


def private_state() -> PrivateStateReadResult:
    document = {
        "networks": {
            "mainnet": {
                "coolify": {
                    "mutation_authority": "observe-only",
                    "controllers": {
                        "coolify-c": {
                            "url": "http://coolify.example",
                            "api_token": "123|abcdefghijklmnop",
                            "enabled": True,
                            "project_uuid": "project-mainnet",
                            "server_uuid": "server-mainnet",
                            "project_name": "My first project",
                        }
                    },
                }
            }
        }
    }
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return PrivateStateReadResult(
        paths=None,
        document_bytes=raw,
        canonical_object_bytes=raw,
        metadata=None,
        recovery_manifest=None,
        recovery_objects=(),
        binding=None,
    )


def test_display_name_guard_accepts_human_name_for_node() -> None:
    guard = _validate_display_name_guard("mainnetc-super1", "Mainnetc Super1")

    assert guard["matched"] is True
    assert guard["display_name_slug"] == "mainnetc-super1"


def test_display_name_guard_rejects_wrong_human_name() -> None:
    with pytest.raises(MotherServiceLineRestartHelperError) as caught:
        _validate_display_name_guard("mainnetc-super1", "Mainnetc Super2")

    assert caught.value.code == "MOTHER_SERVICE_LINE_RESTART_DISPLAY_NAME_MISMATCH"


def test_helper_shell_restarts_one_existing_compose_service_line_without_compose_up() -> None:
    shell = _helper_shell_script(
        parent_service_uuid=PARENT_SERVICE_UUID,
        service_line=NODE,
        max_wait_seconds=60,
        poll_interval_seconds=5,
    )

    assert "docker ps -a" in shell
    assert "label=com.docker.compose.project=$project" in shell
    assert "label=com.docker.compose.service=$line" in shell
    assert "docker restart \"$cid\"" in shell
    assert "docker start \"$cid\"" in shell
    assert "docker compose" not in shell.lower()
    assert "/api/v1/services" not in shell


def test_helper_compose_uses_docker_cli_socket_and_restart_scope_labels() -> None:
    compose = _helper_compose(
        helper_service_name="mother-service-line-restart-coolify-c-mainneta-super1-test",
        parent_service_uuid=PARENT_SERVICE_UUID,
        service_line=NODE,
        max_wait_seconds=60,
        poll_interval_seconds=5,
    )
    parsed = yaml.safe_load(compose)
    service = parsed["services"]["mother-service-line-restart-coolify-c-mainneta-super1-test"]

    assert service["image"] == "docker:27-cli"
    assert "/var/run/docker.sock:/var/run/docker.sock" in service["volumes"]
    assert service["restart"] == "no"
    assert service["labels"]["main_computer.mother.target-service-uuid"] == PARENT_SERVICE_UUID
    assert service["labels"]["main_computer.mother.target-service-line"] == NODE


def test_runtime_event_parser_reads_completion_line() -> None:
    event = _parse_runtime_event_line(
        f"{RUNTIME_PREFIX} phase=complete status=pass reason=service-line-running action=start container_id=a1"
    )

    assert event == {
        "phase": "complete",
        "status": "pass",
        "reason": "service-line-running",
        "action": "start",
        "container_id": "a1",
    }


def test_execute_resolves_node_creates_helper_and_preserves_by_default(tmp_path: Path) -> None:
    opener = FakeCoolifyOpener()

    result = execute_service_line_restart_helper(
        private_state(),
        runtime_state_root=tmp_path,
        network="mainnet",
        mode="execute",
        node=NODE,
        display_name="Mainnetc Super1",
        timeout=1.0,
        max_wait_seconds=1.0,
        poll_interval_seconds=0.0,
        opener=opener,
    )

    assert result["status"] == "pass"
    assert result["service_uuid"] == PARENT_SERVICE_UUID
    assert result["service_line"] == NODE
    assert result["helper_service_uuid"] == HELPER_SERVICE_UUID
    assert result["helper_left_for_inspection"] is True
    assert result["helper_delete_performed"] is False
    assert result["selected_container_id"] == "a1ed01acf36a"
    assert result["action"] == "start"
    assert not any(call["method"] == "DELETE" for call in opener.calls)

    assert opener.created_body is not None
    compose_raw = base64.b64decode(str(opener.created_body["docker_compose_raw"])).decode("utf-8")
    assert "docker:27-cli" in compose_raw
    assert PARENT_SERVICE_UUID in compose_raw
    assert NODE in compose_raw


def test_execute_deletes_helper_after_exit_when_requested(tmp_path: Path) -> None:
    opener = FakeCoolifyOpener()

    result = execute_service_line_restart_helper(
        private_state(),
        runtime_state_root=tmp_path,
        network="mainnet",
        mode="execute",
        node=NODE,
        display_name="Mainnetc Super1",
        delete_helper_after_exit=True,
        timeout=1.0,
        max_wait_seconds=1.0,
        poll_interval_seconds=0.0,
        opener=opener,
    )

    assert result["status"] == "pass"
    assert result["helper_delete_performed"] is True
    assert result["helper_left_for_inspection"] is False
    assert result["delete"]["ok"] is True
    assert any(call["method"] == "DELETE" and call["path"] == f"/api/v1/services/{HELPER_SERVICE_UUID}" for call in opener.calls)


def test_execute_fails_when_helper_result_logs_are_unavailable(tmp_path: Path) -> None:
    opener = FakeCoolifyOpener(logs_payload={"logs": "no restart helper result here"})

    result = execute_service_line_restart_helper(
        private_state(),
        runtime_state_root=tmp_path,
        network="mainnet",
        mode="execute",
        node=NODE,
        display_name="Mainnetc Super1",
        timeout=1.0,
        max_wait_seconds=1.0,
        poll_interval_seconds=0.0,
        opener=opener,
    )

    assert result["status"] == "failed"
    assert result["reason"] == "helper-result-not-observed"
    assert result["logs"]["observed"] is False


def test_inspect_mode_resolves_target_without_creating_helper(tmp_path: Path) -> None:
    opener = FakeCoolifyOpener()

    result = execute_service_line_restart_helper(
        private_state(),
        runtime_state_root=tmp_path,
        network="mainnet",
        mode="inspect",
        node=NODE,
        display_name="Mainnetc Super1",
        timeout=1.0,
        max_wait_seconds=1.0,
        poll_interval_seconds=0.0,
        opener=opener,
    )

    assert result["status"] == "pass"
    assert result["reason"] == "inspect-only"
    assert result["service_uuid"] == PARENT_SERVICE_UUID
    assert result["service_line"] == NODE
    assert not any(call["method"] == "POST" for call in opener.calls)
