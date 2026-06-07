from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.heartbeat import HeartbeatConfig, HeartbeatServer, status_payload
from main_computer.models import ChatResponse
from main_computer.task_manager import TaskManagerService
from main_computer.viewport import APPLICATIONS_INDEX_HTML, ViewportServer


class TaskManagerServiceTests(unittest.TestCase):
    def test_snapshot_and_schedule_crud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = TaskManagerService(root)
            snap = service.snapshot(limit=8, include_connections=False)
            self.assertTrue(snap["ok"])
            self.assertIn("overview", snap)
            self.assertIn("processes", snap)

            created = service.create_schedule(
                action="server_restart",
                run_at="2030-01-01T12:00",
                note="restart after patch test",
                payload={},
            )
            self.assertTrue(created["ok"])
            schedule_id = created["schedule"]["id"]

            listed = service.list_schedules()
            self.assertTrue(any(item["id"] == schedule_id for item in listed["schedules"]))

            removed = service.delete_schedule(schedule_id=schedule_id)
            self.assertTrue(removed["ok"])
            self.assertFalse(any(item["id"] == schedule_id for item in removed["schedules"]))

    def test_snapshot_includes_hardware_rollup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = TaskManagerService(Path(tmp))
            fake_cpu = {
                "available": True,
                "message": "CPU telemetry current.",
                "overall_percent": 37.5,
                "logical_cores": 8,
                "physical_cores": 4,
                "frequency_mhz": 3400.0,
                "max_frequency_mhz": 4200.0,
                "load_average": [0.5, 0.6, 0.7],
                "per_core": [{"index": 0, "label": "CPU 0", "percent": 42.0}],
            }
            fake_gpu = {
                "available": True,
                "message": "GPU telemetry current via nvidia-smi.",
                "overall_percent": 58.0,
                "devices": [
                    {
                        "index": 0,
                        "name": "NVIDIA RTX Test",
                        "utilization_percent": 58.0,
                        "memory_used_mb": 2048.0,
                        "memory_total_mb": 8192.0,
                        "temperature_c": 62.0,
                    }
                ],
            }
            with mock.patch.object(service, "_cpu_summary", return_value=fake_cpu), mock.patch.object(service, "_gpu_summary", return_value=fake_gpu):
                snap = service.snapshot(limit=8, include_connections=False)
            self.assertTrue(snap["ok"])
            self.assertEqual(snap["hardware"]["cpu"]["overall_percent"], 37.5)
            self.assertEqual(snap["hardware"]["gpu"]["overall_percent"], 58.0)
            self.assertEqual(snap["overview"]["cpu_percent"], 37.5)
            self.assertEqual(snap["overview"]["gpu_percent"], 58.0)
            self.assertIn("CPU 37.5%", snap["hardware"]["summary"])



    def test_heartbeat_config_keeps_pid_files_in_control_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control_root = root / ".proto-dev" / "control"
            config = HeartbeatConfig(
                workspace=root,
                bind_host="127.0.0.1",
                server_port=28865,
                heartbeat_port=28866,
                control_root=control_root,
            )

            self.assertEqual(config.viewport_pid_file, control_root / ".main_computer_viewport.pid")
            self.assertEqual(config.heartbeat_pid_file, control_root / ".main_computer_heartbeat.pid")
            self.assertEqual(config.viewport_out_log, control_root / "main_computer_viewport.out.log")
            self.assertEqual(config.heartbeat_err_log, control_root / "main_computer_heartbeat.err.log")


    def test_task_manager_uses_runtime_port_and_control_root_over_script_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control_root = root / ".proto-dev" / "control"
            (root / "control-main-computer.ps1").write_text("[int]$Port = 8765\n", encoding="utf-8")

            service = TaskManagerService(root, default_port=28865, control_root=control_root)

            self.assertEqual(service.port, 28865)
            self.assertEqual(service.heartbeat_port, 28866)
            self.assertEqual(service.pid_file, control_root / ".main_computer_viewport.pid")
            self.assertEqual(service.heartbeat_pid_file, control_root / ".main_computer_heartbeat.pid")

    def test_task_manager_env_can_override_proto_dev_control_ports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control_root = root / ".proto-dev" / "control"
            with mock.patch.dict(
                os.environ,
                {
                    "MAIN_COMPUTER_CONTROL_ROOT": str(control_root),
                    "MAIN_COMPUTER_CONTROL_PORT": "28865",
                    "MAIN_COMPUTER_HEARTBEAT_PORT": "28866",
                },
                clear=False,
            ):
                service = TaskManagerService(root)

            self.assertEqual(service.port, 28865)
            self.assertEqual(service.heartbeat_port, 28866)
            self.assertEqual(service.pid_file, control_root / ".main_computer_viewport.pid")
            self.assertEqual(service.heartbeat_pid_file, control_root / ".main_computer_heartbeat.pid")



    def test_task_manager_server_command_keeps_proto_dev_control_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control_root = root / ".proto-dev" / "control"
            (root / "control-main-computer.ps1").write_text("[int]$Port = 8765\n", encoding="utf-8")

            service = TaskManagerService(root, default_port=28865, control_root=control_root)
            command = service._server_command("restart")

            self.assertIn("-Port", command)
            self.assertIn("28865", command)
            self.assertIn("-HeartbeatPort", command)
            self.assertIn("28866", command)
            self.assertIn("-ControlRoot", command)
            self.assertIn(str(control_root.resolve()), command)
            self.assertIn("-PythonPath", command)
            self.assertIn(sys.executable, command)


    def test_nvidia_smi_gpu_summary_parses_devices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = TaskManagerService(Path(tmp))
            completed = subprocess.CompletedProcess(
                args=["nvidia-smi"],
                returncode=0,
                stdout="NVIDIA RTX 4090, 27, 1024, 24564, 61\nNVIDIA RTX 4000, 0, 512, 8192, 44\n",
                stderr="",
            )
            with mock.patch("main_computer.task_manager.shutil.which", return_value="/usr/bin/nvidia-smi"), mock.patch(
                "main_computer.task_manager.subprocess.run", return_value=completed
            ):
                gpu = service._nvidia_smi_gpu_summary()
            self.assertIsNotNone(gpu)
            self.assertTrue(gpu["available"])
            self.assertEqual(gpu["overall_percent"], 13.5)
            self.assertEqual(len(gpu["devices"]), 2)
            self.assertEqual(gpu["devices"][0]["name"], "NVIDIA RTX 4090")
            self.assertEqual(gpu["devices"][0]["memory_total_mb"], 24564.0)

    def test_snapshot_reports_heartbeat_control_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = TaskManagerService(Path(tmp))
            with mock.patch(
                "main_computer.task_manager.status_payload",
                return_value={
                    "ok": True,
                    "server": {"running": False, "pid": None, "port": 8765, "listener": "", "pid_file": str(Path(tmp) / ".main_computer_viewport.pid")},
                    "heartbeat": {
                        "running": True,
                        "pid": 43210,
                        "port": 8766,
                        "url": "http://127.0.0.1:8766/api/heartbeat/control",
                        "pid_file": str(Path(tmp) / ".main_computer_heartbeat.pid"),
                        "ready": True,
                    },
                },
            ):
                snap = service.snapshot(limit=8, include_connections=False)
            self.assertTrue(snap["server"]["heartbeat_running"])
            self.assertEqual(snap["server"]["heartbeat_pid"], 43210)
            self.assertEqual(snap["server"]["heartbeat_port"], 8766)
            self.assertIn("/api/heartbeat/control", snap["server"]["heartbeat_url"])

    def test_server_summary_autostarts_missing_heartbeat_for_live_viewport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = TaskManagerService(Path(tmp))
            service.pid_file.write_text(str(os.getpid()), encoding="utf-8")
            missing_status = {
                "ok": True,
                "server": {"running": True, "pid": os.getpid(), "port": 8765, "listener": "127.0.0.1:8765", "pid_file": str(service.pid_file)},
                "heartbeat": {
                    "running": False,
                    "pid": None,
                    "port": 8766,
                    "url": "http://127.0.0.1:8766/api/heartbeat/control",
                    "pid_file": str(service.heartbeat_pid_file),
                    "ready": False,
                },
            }
            started_status = {
                **missing_status,
                "heartbeat": {
                    **missing_status["heartbeat"],
                    "running": True,
                    "pid": 24680,
                },
            }
            with mock.patch("main_computer.task_manager.status_payload", return_value=missing_status), mock.patch(
                "main_computer.task_manager.ensure_heartbeat_service",
                return_value=started_status,
            ) as ensure_mock:
                summary = service._server_summary(processes=[], connections=[])
            self.assertTrue(summary["heartbeat_running"])
            self.assertEqual(summary["heartbeat_pid"], 24680)
            ensure_mock.assert_called_once()

    def test_powershell_json_timeout_returns_empty_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = TaskManagerService(Path(tmp))
            with mock.patch(
                "main_computer.task_manager.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="powershell", timeout=1.5),
            ) as run_mock:
                self.assertEqual(service._run_powershell_json("Get-Process | ConvertTo-Json"), [])
            self.assertIn("timeout", run_mock.call_args.kwargs)

    def test_process_action_plan_and_confirm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = TaskManagerService(Path(tmp))
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            try:
                plan = service.perform_action(action="terminate_pid", pid=proc.pid, confirm=False)
                self.assertTrue(plan["planned"])
                done = service.perform_action(action="terminate_pid", pid=proc.pid, confirm=True)
                self.assertTrue(done["ok"])
                proc.wait(timeout=5)
            finally:
                if proc.poll() is None:
                    proc.kill()

    def test_server_lifecycle_uses_auto_allow_for_legacy_control_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "control-main-computer.ps1").write_text("# test legacy helper\n", encoding="utf-8")
            service = TaskManagerService(root)

            with mock.patch("main_computer.task_manager.sys.platform", "win32"):
                command = service._server_command("restart")
                deferred = service._deferred_server_command("shutdown")

            self.assertEqual(command[6:8], ["restart", "--auto-allow"])
            self.assertIn("-Port", command)
            self.assertIn("-ControlRoot", command)
            self.assertIn('"shutdown" "--auto-allow"', deferred[-1])
            self.assertIn('"-ControlRoot"', deferred[-1])

    def test_windows_process_rows_use_powershell_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = TaskManagerService(Path(tmp))
            with mock.patch("main_computer.task_manager.sys.platform", "win32"), mock.patch.object(
                service, "_powershell_process_rows", return_value=[{"pid": 1}]
            ) as powershell_rows:
                rows = service._process_rows(query="", limit=8, include_all=False)
            self.assertEqual(rows, [{"pid": 1}])
            powershell_rows.assert_called_once()

    def test_applications_surface_and_routes(self) -> None:
        self.assertIn("/api/applications/task/overview", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/task/action", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/task/schedule/create", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/task/ai", APPLICATIONS_INDEX_HTML)
        self.assertIn("Server Processes", APPLICATIONS_INDEX_HTML)
        self.assertIn("All Processes", APPLICATIONS_INDEX_HTML)
        self.assertIn("Connections", APPLICATIONS_INDEX_HTML)
        self.assertIn("CPU + GPU", APPLICATIONS_INDEX_HTML)
        self.assertIn("task-hardware-table", APPLICATIONS_INDEX_HTML)
        self.assertIn("renderTaskHardware", APPLICATIONS_INDEX_HTML)
        self.assertIn("Ask AI", APPLICATIONS_INDEX_HTML)
        self.assertNotIn('id="task-include-all"', APPLICATIONS_INDEX_HTML)
        self.assertIn("taskHeartbeatRequest", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/heartbeat/control", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("Terminate Server", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("task-overview-card app-widget", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("task-notebook app-widget", APPLICATIONS_INDEX_HTML)

        task_manager_css = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "styles" / "task-manager.css").read_text(encoding="utf-8")
        self.assertIn(".task-manager-app .fullscreen-control", task_manager_css)
        self.assertIn("#task-process-table .task-table", task_manager_css)
        self.assertIn("table-layout: fixed", task_manager_css)
        self.assertIn("grid-template-columns: 300px minmax(0, 1fr);", task_manager_css)
        self.assertIn("width: 300px;", task_manager_css)
        self.assertIn("flex-direction: column;", task_manager_css)
        self.assertIn("grid-template-columns: 92px minmax(0, 1fr);", task_manager_css)
        self.assertNotIn("minmax(300px, 380px) minmax(0, 1fr)", task_manager_css)

        config = MainComputerConfig(model="fake-model", workspace=Path.cwd(), ollama_timeout_s=30)
        server = ViewportServer(("127.0.0.1", 0), config, verbose=False)

        class FakeCatalog:
            def project_summaries(self):
                return []

        class FakeProvider:
            name = "fake"
            model = "fake-model"

        class FakeComputer:
            catalog = FakeCatalog()
            provider = FakeProvider()

            def chat(self, prompt: str) -> ChatResponse:
                return ChatResponse(content=f"echo: {prompt}", provider="fake", model="fake-model")

        server.computer = FakeComputer()  # type: ignore[assignment]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"

            overview_request = Request(
                f"{base}/api/applications/task/overview",
                data=json.dumps({"limit": 10, "include_connections": False}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(overview_request, timeout=10) as response:
                overview = json.loads(response.read().decode("utf-8"))
            self.assertTrue(overview["ok"])
            self.assertIn("processes", overview)
            self.assertIn("hardware", overview)

            create_request = Request(
                f"{base}/api/applications/task/schedule/create",
                data=json.dumps({"action": "server_restart", "run_at": "2030-01-01T12:00", "note": "viewport test"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(create_request, timeout=10) as response:
                created = json.loads(response.read().decode("utf-8"))
            self.assertTrue(created["ok"])
            schedule_id = created["schedule"]["id"]

            list_request = Request(
                f"{base}/api/applications/task/schedules",
                data=json.dumps({}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(list_request, timeout=10) as response:
                listed = json.loads(response.read().decode("utf-8"))
            self.assertTrue(any(item["id"] == schedule_id for item in listed["schedules"]))

            ai_request = Request(
                f"{base}/api/applications/task/ai",
                data=json.dumps({"instruction": "Summarize the operator state.", "include_connections": False}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(ai_request, timeout=10) as response:
                ai = json.loads(response.read().decode("utf-8"))
            self.assertEqual(ai["provider"], "fake")
            self.assertEqual(ai["model"], "fake-model")
            self.assertIn("Summarize the operator state.", ai["content"])

            delete_request = Request(
                f"{base}/api/applications/task/schedule/delete",
                data=json.dumps({"schedule_id": schedule_id}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(delete_request, timeout=10) as response:
                deleted = json.loads(response.read().decode("utf-8"))
            self.assertTrue(deleted["ok"])
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


class HeartbeatPidDiscoveryTests(unittest.TestCase):
    def test_viewport_pid_falls_back_to_matching_runtime_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = HeartbeatConfig(workspace=workspace, bind_host="127.0.0.1", server_port=8765, heartbeat_port=8766, verbose=False)

            class FakeProc:
                def __init__(self, pid: int, cmdline: list[str]) -> None:
                    self.info = {"pid": pid, "cmdline": cmdline}

            with mock.patch("main_computer.heartbeat._read_pid_file", return_value=None), mock.patch(
                "main_computer.heartbeat.psutil"
            ) as fake_psutil, mock.patch("main_computer.heartbeat._pid_is_running", side_effect=lambda pid: pid not in {None, 0}):
                fake_psutil.process_iter.return_value = [
                    FakeProc(24680, [sys.executable, "-B", "-m", "main_computer.cli", "viewport", "--host", "127.0.0.1", "--port", "8765", "--workspace", str(workspace)]),
                ]
                pid = __import__("main_computer.heartbeat", fromlist=["_viewport_pid"])._viewport_pid(config)

            self.assertEqual(pid, 24680)
            self.assertEqual(config.viewport_pid_file.read_text(encoding="utf-8").strip(), "24680")

    def test_heartbeat_pid_falls_back_to_matching_runtime_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = HeartbeatConfig(workspace=workspace, bind_host="127.0.0.1", server_port=8765, heartbeat_port=8766, verbose=False)

            class FakeProc:
                def __init__(self, pid: int, cmdline: list[str]) -> None:
                    self.info = {"pid": pid, "cmdline": cmdline}

            with mock.patch("main_computer.heartbeat._read_pid_file", return_value=None), mock.patch(
                "main_computer.heartbeat.psutil"
            ) as fake_psutil, mock.patch("main_computer.heartbeat._pid_is_running", side_effect=lambda pid: pid not in {None, 0}):
                fake_psutil.process_iter.return_value = [
                    FakeProc(13579, [sys.executable, "-B", "-m", "main_computer.cli", "heartbeat", "--host", "127.0.0.1", "--port", "8766", "--server-port", "8765", "--workspace", str(workspace)]),
                ]
                pid = __import__("main_computer.heartbeat", fromlist=["_heartbeat_pid"])._heartbeat_pid(config)

            self.assertEqual(pid, 13579)
            self.assertEqual(config.heartbeat_pid_file.read_text(encoding="utf-8").strip(), "13579")


class HeartbeatStatusPayloadTests(unittest.TestCase):
    def test_wildcard_bind_host_reports_loopback_control_url(self) -> None:
        config = HeartbeatConfig(workspace=Path.cwd(), bind_host="0.0.0.0", server_port=8765, heartbeat_port=8766, verbose=False)
        with mock.patch("main_computer.heartbeat._viewport_pid", return_value=None), mock.patch(
            "main_computer.heartbeat._heartbeat_pid",
            return_value=None,
        ), mock.patch("main_computer.heartbeat._port_is_listening", return_value=False):
            payload = status_payload(config)
        self.assertEqual(payload["heartbeat"]["url"], "http://127.0.0.1:8766/api/heartbeat/control")
        self.assertFalse(payload["heartbeat"]["ready"])


def test_task_manager_mcel_adapter_is_default_on_with_query_disable_for_regular_app() -> None:
    script = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "task-manager.js").read_text(encoding="utf-8")

    assert "function taskManagerMcelFlagValue(search = window.location.search)" in script
    assert 'new URLSearchParams(String(search || "")).get("mcel")' in script
    assert 'const taskManagerMcelEnableValues = new Set(["1", "true", "on", "yes", "enabled"])' in script
    assert 'const taskManagerMcelDisableValues = new Set(["0", "false", "off", "no", "disabled"])' in script
    assert "if (taskManagerMcelDisableValues.has(queryValue))" in script
    assert "return false;" in script[script.index("function taskManagerMcelAppEnabled"):script.index("function applyTaskManagerMcelAppSemantics")]
    assert "return true;" in script[script.index("function taskManagerMcelAppEnabled"):script.index("function applyTaskManagerMcelAppSemantics")]
    assert 'localStorage.getItem(key)' in script
    assert '"taskManagerMcelDisabled"' in script
    assert '"taskManagerMcelEnabled"' not in script[script.index("function taskManagerMcelAppEnabled"):script.index("function applyTaskManagerMcelAppSemantics")]


def test_task_manager_mcel_app_enrichment_is_passive_and_scheduled() -> None:
    script = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "task-manager.js").read_text(encoding="utf-8")
    terminal = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "terminal.js").read_text(encoding="utf-8")

    assert "function applyTaskManagerMcelAppSemantics(reason = \"app-refresh\")" in script
    assert "adapter.applyTaskManagerMcelSemantics({" in script
    assert 'mode: "app"' in script
    assert 'report: false' in script
    assert 'taskManagerApp?.setAttribute?.("data-task-manager-mcel-mode", "passive")' in script
    assert "window.taskManagerMcelStatus = function taskManagerMcelStatus()" in script
    assert 'scheduleTaskManagerMcelAppSemantics("init")' in script
    assert 'scheduleTaskManagerMcelAppSemantics(firstLoad ? "first-snapshot" : "refresh")' in terminal
    assert 'scheduleTaskManagerMcelAppSemantics("refresh-error")' in terminal
    assert ".click(" not in script[script.index("function applyTaskManagerMcelAppSemantics"):script.index("function initTaskManagerApp")]
    assert "addEventListener" not in script[script.index("function applyTaskManagerMcelAppSemantics"):script.index("function initTaskManagerApp")]


def test_task_manager_refresh_hold_overlay_does_not_expose_scrollbars() -> None:
    css = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "styles" / "task-manager.css").read_text(encoding="utf-8")

    hold_block_start = css.index(".task-process-refresh-hold {")
    hold_block_end = css.index("}", hold_block_start)
    hold_block = css[hold_block_start:hold_block_end]

    assert "overflow: hidden;" in hold_block
    assert "scrollbar-width: none;" in hold_block
    assert ".task-process-refresh-hold::-webkit-scrollbar" in css
    assert "display: none;" in css[css.index(".task-process-refresh-hold::-webkit-scrollbar"):]


if __name__ == "__main__":
    unittest.main()

    def test_heartbeat_control_restart_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = HeartbeatConfig(workspace=Path(tmp), bind_host="127.0.0.1", server_port=8765, heartbeat_port=0, verbose=False)
            server = HeartbeatServer(("127.0.0.1", 0), config, verbose=False)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with mock.patch("main_computer.heartbeat.stop_viewport", return_value={"ok": True, "server": {"running": False}}) as stop_mock, mock.patch(
                    "main_computer.heartbeat.start_viewport",
                    return_value={"ok": True, "server": {"running": True, "pid": 24680}, "heartbeat": {"running": True}},
                ) as start_mock:
                    port = server.server_address[1]
                    request = Request(
                        f"http://127.0.0.1:{port}/api/heartbeat/control",
                        data=json.dumps({"action": "restart"}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=5) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)
                self.assertEqual(payload["message"], "Viewport restart requested through heartbeat.")
                stop_mock.assert_called_once_with(config)
                start_mock.assert_called_once_with(config)
            finally:
                server.shutdown()
                thread.join(timeout=5)
