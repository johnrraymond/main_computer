from __future__ import annotations

import socket
import tempfile
from pathlib import Path
from unittest import mock

from main_computer.heartbeat import HeartbeatConfig, status_payload
from main_computer.task_manager import TaskManagerService


def _listening_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock


def test_reachable_services_are_running_even_without_pid_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        control_root = root / ".proto-dev" / "control"
        viewport_sock = _listening_socket()
        heartbeat_sock = _listening_socket()
        try:
            server_port = int(viewport_sock.getsockname()[1])
            heartbeat_port = int(heartbeat_sock.getsockname()[1])
            config = HeartbeatConfig(
                workspace=root,
                bind_host="127.0.0.1",
                server_port=server_port,
                heartbeat_port=heartbeat_port,
                control_root=control_root,
            )

            payload = status_payload(config)
        finally:
            viewport_sock.close()
            heartbeat_sock.close()

        assert payload["server"]["running"] is True
        assert payload["server"]["ready"] is True
        assert payload["server"]["pid"] is None
        assert payload["server"]["control_tracking"] == "missing_pid_file"
        assert "listener_ready" in payload["server"]["evidence"]

        assert payload["heartbeat"]["running"] is True
        assert payload["heartbeat"]["ready"] is True
        assert payload["heartbeat"]["pid"] is None
        assert payload["heartbeat"]["control_tracking"] == "missing_pid_file"
        assert "health_endpoint_ready" in payload["heartbeat"]["evidence"]

        assert not config.viewport_pid_file.exists()
        assert not config.heartbeat_pid_file.exists()


def test_status_payload_does_not_delete_unverified_pid_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        control_root = root / ".proto-dev" / "control"
        config = HeartbeatConfig(
            workspace=root,
            bind_host="127.0.0.1",
            server_port=9,
            heartbeat_port=10,
            control_root=control_root,
        )
        config.viewport_pid_file.parent.mkdir(parents=True, exist_ok=True)
        config.viewport_pid_file.write_text("999999999", encoding="utf-8")
        config.heartbeat_pid_file.write_text("999999998", encoding="utf-8")

        payload = status_payload(config)

        assert payload["server"]["running"] is False
        assert payload["server"]["pid_file_pid"] == 999999999
        assert payload["server"]["control_tracking"] == "pid_file_unverified"
        assert payload["heartbeat"]["running"] is False
        assert payload["heartbeat"]["pid_file_pid"] == 999999998
        assert payload["heartbeat"]["control_tracking"] == "pid_file_unverified"
        assert config.viewport_pid_file.exists()
        assert config.heartbeat_pid_file.exists()


def test_task_manager_treats_ready_heartbeat_as_running_without_autostart() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = TaskManagerService(Path(tmp))
        ready_status = {
            "ok": True,
            "server": {
                "running": True,
                "pid": None,
                "port": 28865,
                "listener": "127.0.0.1:28865",
                "pid_file": str(service.pid_file),
            },
            "heartbeat": {
                "running": False,
                "pid": None,
                "port": 28866,
                "url": "http://127.0.0.1:28866/api/heartbeat/control",
                "pid_file": str(service.heartbeat_pid_file),
                "pid_file_pid": None,
                "ready": True,
                "control_tracking": "missing_pid_file",
                "evidence": ["health_endpoint_ready"],
            },
        }

        with mock.patch("main_computer.task_manager.status_payload", return_value=ready_status), mock.patch(
            "main_computer.task_manager.ensure_heartbeat_service"
        ) as ensure_mock:
            summary = service._server_summary(processes=[], connections=[])

        assert summary["heartbeat_running"] is True
        assert summary["heartbeat_ready"] is True
        assert summary["heartbeat_pid"] is None
        assert summary["heartbeat_control_tracking"] == "missing_pid_file"
        assert "health_endpoint_ready" in summary["heartbeat_evidence"]
        ensure_mock.assert_not_called()
