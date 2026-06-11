from __future__ import annotations

import json
from pathlib import Path
import sys

from main_computer.service_control import enqueue_supervisor_action, pending_control_requests
from main_computer.service_supervisor import (
    MAIN_LOG_CHILD_NAME,
    SERVICE_NAME,
    SERVICE_SUPERVISOR_PID_FILENAME,
    ServiceSupervisor,
    resolve_python_command,
    load_service_supervisor_state,
    render_service_supervisor_summary,
)


class FakeProcess:
    _next_pid = 20000

    def __init__(self, polls: list[int | None] | None = None) -> None:
        FakeProcess._next_pid += 1
        self._pid = FakeProcess._next_pid
        self.polls = list(polls or [None])

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        if len(self.polls) > 1:
            return self.polls.pop(0)
        return self.polls[0]


def test_resolve_python_command_reuses_current_interpreter_for_generic_python_names(monkeypatch) -> None:
    monkeypatch.setenv("MAIN_COMPUTER_PYTHON_COMMAND", "python")

    assert resolve_python_command("python") == sys.executable
    assert resolve_python_command("python.exe") == sys.executable
    assert resolve_python_command("") == sys.executable
    assert resolve_python_command(None) == sys.executable
    assert resolve_python_command("C:/Tools/Python/python.exe") == "C:/Tools/Python/python.exe"


def test_supervisor_starts_app_executor_applications_and_blockchain_with_python_and_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    starts: list[tuple[list[str], Path, Path, Path]] = []

    def factory(command: list[str], cwd: Path, stdout: Path, stderr: Path) -> FakeProcess:
        starts.append((command, cwd, stdout, stderr))
        return FakeProcess([None])

    supervisor = ServiceSupervisor(
        root=repo,
        python_command="python",
        sleep_func=lambda _: None,
        output_func=None,
        process_factory=factory,
    )

    state = supervisor.supervise(max_loops=1)

    commands = [item[0] for item in starts]
    assert [sys.executable, "-m", "main_computer.main_log_service", "--root", str(repo.resolve()), "serve"] in commands
    assert [sys.executable, "-m", "main_computer.app_control", "--root", str(repo.resolve()), "run"] in commands
    assert [sys.executable, "-m", "main_computer.executor_service", "--root", str(repo.resolve()), "boot", "--watch"] in commands
    assert [sys.executable, "-m", "main_computer.applications_service", "--root", str(repo.resolve()), "boot", "--watch"] in commands
    assert [sys.executable, "-m", "main_computer.blockchain_service", "--root", str(repo.resolve()), "boot", "--watch"] in commands
    assert state["ok"] is True
    assert list(state["children"]).index(MAIN_LOG_CHILD_NAME) < list(state["children"]).index("app")
    assert state["children"][MAIN_LOG_CHILD_NAME]["role"] == "logging"
    assert state["children"][MAIN_LOG_CHILD_NAME]["state"] == "running"
    assert state["children"]["app"]["state"] == "running"
    assert state["children"]["executor"]["state"] == "running"
    assert state["children"]["applications"]["state"] == "running"
    assert state["children"]["blockchain"]["state"] == "running"
    assert state["children"]["executor"]["stdout"].endswith(".stdout.log")
    assert load_service_supervisor_state(repo)["ok"] is True


def test_supervisor_restarts_child_process_when_it_exits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    starts_by_module: dict[str, int] = {}

    def factory(command: list[str], cwd: Path, stdout: Path, stderr: Path) -> FakeProcess:
        module = command[2]
        starts_by_module[module] = starts_by_module.get(module, 0) + 1
        if module == "main_computer.executor_service" and starts_by_module[module] == 1:
            return FakeProcess([None, 7])
        return FakeProcess([None])

    supervisor = ServiceSupervisor(
        root=repo,
        python_command="python",
        poll_interval_s=5,
        sleep_func=lambda _: None,
        output_func=None,
        process_factory=factory,
    )

    state = supervisor.supervise(max_loops=3)

    assert starts_by_module["main_computer.app_control"] == 1
    assert starts_by_module["main_computer.app_control"] == 1
    assert starts_by_module["main_computer.executor_service"] == 2
    assert starts_by_module["main_computer.applications_service"] == 1
    assert starts_by_module["main_computer.blockchain_service"] == 1
    assert state["children"]["executor"]["restart_count"] == 1
    assert state["children"]["executor"]["last_exit_code"] == 7
    assert state["children"]["executor"]["state"] == "running"


def test_supervisor_restarts_running_executor_when_heartbeat_state_is_old_generation(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("MAIN_COMPUTER_EXECUTOR_HEARTBEAT_STARTUP_GRACE_S", "1")
    starts_by_module: dict[str, int] = {}
    terminated: list[int] = []
    clock = {"now": 0.0}

    def fake_time() -> float:
        clock["now"] += 100.0
        return clock["now"]

    def factory(command: list[str], cwd: Path, stdout: Path, stderr: Path) -> FakeProcess:
        module = command[2]
        starts_by_module[module] = starts_by_module.get(module, 0) + 1
        process = FakeProcess([None])
        if module == "main_computer.executor_service" and starts_by_module[module] == 1:
            (repo / "runtime" / "executor_service").mkdir(parents=True, exist_ok=True)
            (repo / ".main_computer_executor_service.pid").write_text(
                json.dumps(
                    {
                        "pid": process.pid,
                        "service": "main-computer-executor-service",
                        "root": str(repo.resolve()),
                        "command_line": "python -m main_computer.executor_service --root repo boot --watch",
                    }
                ),
                encoding="utf-8",
            )
            (repo / "runtime" / "executor_service" / "state.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "state": "ready",
                        "root": str(repo.resolve()),
                        "policy": {"stale_after_s": 600.0},
                        "service": {
                            "pid": process.pid - 1,
                            "state": "watching",
                            "heartbeat_at": "2000-01-01T00:00:00+00:00",
                        },
                    }
                ),
                encoding="utf-8",
            )
        return process

    supervisor = ServiceSupervisor(
        root=repo,
        python_command="python",
        poll_interval_s=5,
        sleep_func=lambda _: None,
        time_func=fake_time,
        output_func=None,
        process_terminator=terminated.append,
        process_factory=factory,
    )

    state = supervisor.supervise(max_loops=2)

    assert starts_by_module["main_computer.executor_service"] == 2
    assert len(terminated) == 1
    assert state["children"]["executor"]["restart_count"] == 1
    assert state["children"]["executor"]["state"] == "running"


def test_supervisor_pid_takeover_only_kills_matching_prior_process_and_still_boots(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    prior_pid = 34567
    prior_command = f"python -m main_computer.service_supervisor --root {repo.resolve()} supervise"
    (repo / SERVICE_SUPERVISOR_PID_FILENAME).write_text(
        json.dumps(
            {
                "pid": prior_pid,
                "service": SERVICE_NAME,
                "root": str(repo.resolve()),
                "command_line": prior_command,
            }
        ),
        encoding="utf-8",
    )
    terminated: list[int] = []

    def factory(command: list[str], cwd: Path, stdout: Path, stderr: Path) -> FakeProcess:
        return FakeProcess([None])

    supervisor = ServiceSupervisor(
        root=repo,
        sleep_func=lambda _: None,
        output_func=None,
        process_command_reader=lambda pid: prior_command if pid == prior_pid else None,
        process_terminator=terminated.append,
        process_factory=factory,
    )

    state = supervisor.supervise(max_loops=1)

    assert terminated == [prior_pid]
    assert state["ok"] is True
    assert state["service"]["pid_claim"]["state"] == "terminated"


def test_supervisor_pid_mismatch_does_not_block_boot(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    prior_pid = 45678
    prior_command = f"python -m main_computer.service_supervisor --root {repo.resolve()} supervise"
    (repo / SERVICE_SUPERVISOR_PID_FILENAME).write_text(
        json.dumps(
            {
                "pid": prior_pid,
                "service": SERVICE_NAME,
                "root": str(repo.resolve()),
                "command_line": prior_command,
            }
        ),
        encoding="utf-8",
    )

    def factory(command: list[str], cwd: Path, stdout: Path, stderr: Path) -> FakeProcess:
        return FakeProcess([None])

    supervisor = ServiceSupervisor(
        root=repo,
        sleep_func=lambda _: None,
        output_func=None,
        process_command_reader=lambda pid: "python -m something_else" if pid == prior_pid else None,
        process_terminator=lambda pid: (_ for _ in ()).throw(AssertionError("should not kill mismatched process")),
        process_factory=factory,
    )

    state = supervisor.supervise(max_loops=1)

    assert state["ok"] is True
    assert state["service"]["pid_claim"]["state"] == "not-terminated"
    assert state["service"]["pid_claim"]["prior_command_matches_pid_file"] is False


def test_start_and_stop_bat_use_source_tree_start_stop_session() -> None:
    root = Path(__file__).resolve().parents[1]
    start = (root / "start.bat").read_text(encoding="utf-8")
    stop = (root / "stop.bat").read_text(encoding="utf-8")
    helper = (root / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")

    assert "run-main-computer.ps1" not in start
    assert "main-computer-install.json" not in start
    assert "scripts\\main-computer-start-stop.ps1" in start
    assert "-Action start" in start
    assert "main_computer.app_control" not in start
    assert "status --summary --wait-s 30 --interval-s 2" in start
    assert "Waiting briefly for startup status" in start

    assert "scripts\\main-computer-start-stop.ps1" in stop
    assert "-Action stop" in stop
    assert "runtime\\start_stop" in stop

    assert "start-session.json" in helper
    assert "source-tree-supervisor" in helper
    assert "main_computer.app_control" in helper
    assert ".main_computer_service_supervisor.pid" in helper
    assert "runtime\\service_supervisor\\state.json" in helper
    assert "docker-compose.dev.yml" in helper
    assert "docker-compose.applications.yml" in helper
    assert '"down", "--remove-orphans", "--timeout", "30"' in helper
    assert '"ps", "-a", "-q"' in helper
    assert "Wait-DockerComposeStackGone" in helper
    assert "down-after-wait" in helper
    assert "taskkill.exe" in helper


def test_service_supervisor_summary_includes_child_and_boot_component_states(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "runtime" / "service_supervisor").mkdir(parents=True)
    (repo / "runtime" / "executor_service").mkdir(parents=True)
    (repo / "runtime" / "applications_service").mkdir(parents=True)
    (repo / "runtime" / "blockchain_service").mkdir(parents=True)

    (repo / "runtime" / "service_supervisor" / "state.json").write_text(
        json.dumps(
            {
                "ok": True,
                "state": "supervising",
                "service": {"pid": 111},
                "children": {
                    "app": {
                        "state": "running",
                        "pid": 1110,
                        "restart_count": 0,
                        "stdout": "app.out",
                        "stderr": "app.err",
                    },
                    "executor": {
                        "state": "running",
                        "pid": 222,
                        "restart_count": 1,
                        "stdout": "executor.out",
                        "stderr": "executor.err",
                    },
                    "applications": {
                        "state": "running",
                        "pid": 333,
                        "restart_count": 0,
                        "stdout": "applications.out",
                        "stderr": "applications.err",
                    },
                    "blockchain": {
                        "state": "running",
                        "pid": 444,
                        "restart_count": 0,
                        "stdout": "blockchain.out",
                        "stderr": "blockchain.err",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (repo / "runtime" / "executor_service" / "state.json").write_text(
        json.dumps(
            {
                "ok": False,
                "state": "down",
                "boot_proven": False,
                "message": "docker still starting",
                "service": {"pid": 222},
                "components": {
                    "wsl": {"state": "ready"},
                    "docker": {"state": "down"},
                    "compose": {"state": "blocked"},
                },
            }
        ),
        encoding="utf-8",
    )
    (repo / "runtime" / "applications_service" / "state.json").write_text(
        json.dumps(
            {
                "ok": True,
                "state": "ready",
                "boot_proven": True,
                "service": {"pid": 333},
                "components": {
                    "env": {"state": "ready"},
                    "docker": {"state": "ready"},
                    "compose": {"state": "ready"},
                    "applications": {"state": "ready"},
                },
            }
        ),
        encoding="utf-8",
    )
    (repo / "runtime" / "blockchain_service" / "state.json").write_text(
        json.dumps(
            {
                "ok": True,
                "state": "ready",
                "boot_proven": True,
                "service": {"pid": 444},
                "components": {
                    "config": {"state": "configured"},
                    "runtime": {"state": "ready"},
                    "docker": {"state": "ready"},
                    "compose": {"state": "ready"},
                    "rpc": {"state": "ready"},
                    "dev_chain": {"state": "not-required"},
                },
            }
        ),
        encoding="utf-8",
    )

    summary = render_service_supervisor_summary(repo)

    assert "Supervisor: state=supervising ok=True" in summary
    assert "child app: state=running pid=1110 restarts=0" in summary
    assert "child executor: state=running pid=222 restarts=1" in summary
    assert "child blockchain: state=running pid=444 restarts=0" in summary
    assert "Executor service: state=down ok=False boot_proven=False" in summary
    assert "executor components: wsl=ready docker=down compose=blocked" in summary
    assert "Applications service: state=ready ok=True boot_proven=True" in summary
    assert "applications components: env=ready docker=ready compose=ready applications=ready" in summary
    assert "Blockchain service: state=ready ok=True boot_proven=True" in summary
    assert "blockchain components: config=configured runtime=ready docker=ready compose=ready rpc=ready dev_chain=not-required" in summary

def test_supervisor_control_queue_can_restart_executor_child(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    starts_by_module: dict[str, int] = {}

    def factory(command: list[str], cwd: Path, stdout: Path, stderr: Path) -> FakeProcess:
        module = command[2]
        starts_by_module[module] = starts_by_module.get(module, 0) + 1
        return FakeProcess([None])

    enqueue_supervisor_action(repo, action="restart", target="executor", source="test")

    supervisor = ServiceSupervisor(
        root=repo,
        python_command="python",
        sleep_func=lambda _: None,
        output_func=None,
        process_factory=factory,
    )
    state = supervisor.supervise(max_loops=2)

    assert starts_by_module["main_computer.executor_service"] == 2
    assert starts_by_module["main_computer.applications_service"] == 1
    assert starts_by_module["main_computer.blockchain_service"] == 1
    assert state["children"]["executor"]["restart_count"] == 1
    assert pending_control_requests(repo, channel="supervisor") == []


def test_supervisor_control_queue_can_restart_blockchain_child(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    starts_by_module: dict[str, int] = {}

    def factory(command: list[str], cwd: Path, stdout: Path, stderr: Path) -> FakeProcess:
        module = command[2]
        starts_by_module[module] = starts_by_module.get(module, 0) + 1
        return FakeProcess([None])

    enqueue_supervisor_action(repo, action="restart", target="blockchain", source="test")

    supervisor = ServiceSupervisor(
        root=repo,
        python_command="python",
        sleep_func=lambda _: None,
        output_func=None,
        process_factory=factory,
    )
    state = supervisor.supervise(max_loops=2)

    assert starts_by_module["main_computer.blockchain_service"] == 2
    assert state["children"]["blockchain"]["restart_count"] == 1
    assert pending_control_requests(repo, channel="supervisor") == []


def test_supervisor_control_queue_proxies_named_application_restart(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def factory(command: list[str], cwd: Path, stdout: Path, stderr: Path) -> FakeProcess:
        return FakeProcess([None])

    enqueue_supervisor_action(repo, action="restart", target="onlyoffice", source="test")

    supervisor = ServiceSupervisor(
        root=repo,
        python_command="python",
        sleep_func=lambda _: None,
        output_func=None,
        process_factory=factory,
    )
    state = supervisor.supervise(max_loops=1)

    assert state["ok"] is True
    app_requests = pending_control_requests(repo, channel="applications")
    assert len(app_requests) == 1
    assert app_requests[0].action == "restart"
    assert app_requests[0].target == "onlyoffice"


def test_supervisor_control_queue_accepts_self_restart(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    starts: list[list[str]] = []

    def factory(command: list[str], cwd: Path, stdout: Path, stderr: Path) -> FakeProcess:
        starts.append(command)
        return FakeProcess([None])

    enqueue_supervisor_action(repo, action="restart", target="supervisor", source="test")

    supervisor = ServiceSupervisor(
        root=repo,
        python_command="python",
        sleep_func=lambda _: None,
        output_func=None,
        process_factory=factory,
    )
    state = supervisor.supervise(max_loops=1)

    assert state["state"] == "restarting"
    assert any(
        command[:3] == [sys.executable, "-m", "main_computer.app_control"]
        and ["--python-command", sys.executable] == command[-3:-1]
        and command[-1:] == ["bootstrap"]
        for command in starts
    )



def test_supervisor_control_queue_can_shutdown_all_children_without_restart(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    starts_by_module: dict[str, int] = {}
    pid_by_module: dict[str, int] = {}
    terminated: list[int] = []

    def factory(command: list[str], cwd: Path, stdout: Path, stderr: Path) -> FakeProcess:
        module = command[2]
        starts_by_module[module] = starts_by_module.get(module, 0) + 1
        process = FakeProcess([None])
        pid_by_module[module] = process.pid
        return process

    enqueue_supervisor_action(repo, action="shutdown", target="system", source="test")

    supervisor = ServiceSupervisor(
        root=repo,
        python_command="python",
        sleep_func=lambda _: None,
        output_func=None,
        process_terminator=terminated.append,
        process_factory=factory,
    )
    state = supervisor.supervise(max_loops=3)

    assert state["ok"] is True
    assert state["state"] == "stopped"
    assert state["children"] == {}
    assert starts_by_module["main_computer.main_log_service"] == 1
    assert starts_by_module["main_computer.app_control"] == 1
    assert starts_by_module["main_computer.executor_service"] == 1
    assert starts_by_module["main_computer.applications_service"] == 1
    assert starts_by_module["main_computer.blockchain_service"] == 1
    assert len(terminated) == 5
    assert terminated[-1] == pid_by_module["main_computer.main_log_service"]
    assert pending_control_requests(repo, channel="supervisor") == []



def test_supervisor_passes_environment_control_port_to_app_child(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    starts: list[tuple[list[str], Path, Path, Path]] = []

    def factory(command: list[str], cwd: Path, stdout: Path, stderr: Path) -> FakeProcess:
        starts.append((command, cwd, stdout, stderr))
        return FakeProcess([None])

    monkeypatch.setenv("MAIN_COMPUTER_CONTROL_PORT", "28865")
    supervisor = ServiceSupervisor(
        root=repo,
        python_command="python",
        sleep_func=lambda _: None,
        output_func=None,
        process_factory=factory,
    )

    supervisor.supervise(max_loops=1)

    commands = [item[0] for item in starts]
    assert [
        sys.executable,
        "-m",
        "main_computer.app_control",
        "--root",
        str(repo.resolve()),
        "--port",
        "28865",
        "run",
    ] in commands


def test_supervisor_restart_all_restarts_main_log_last_down_first_up(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    starts: list[tuple[str, int]] = []
    terminated: list[int] = []
    module_by_pid: dict[int, str] = {}

    def factory(command: list[str], cwd: Path, stdout: Path, stderr: Path) -> FakeProcess:
        module = command[2]
        process = FakeProcess([None])
        starts.append((module, process.pid))
        module_by_pid[process.pid] = module
        return process

    enqueue_supervisor_action(repo, action="restart", target="all", source="test")
    supervisor = ServiceSupervisor(
        root=repo,
        python_command="python",
        sleep_func=lambda _: None,
        output_func=None,
        process_terminator=terminated.append,
        process_factory=factory,
    )

    state = supervisor.supervise(max_loops=2)

    assert state["ok"] is True
    started_modules = [module for module, _pid in starts]
    assert started_modules[:5] == [
        "main_computer.main_log_service",
        "main_computer.app_control",
        "main_computer.executor_service",
        "main_computer.applications_service",
        "main_computer.blockchain_service",
    ]
    assert started_modules[5:] == [
        "main_computer.main_log_service",
        "main_computer.app_control",
        "main_computer.executor_service",
        "main_computer.applications_service",
        "main_computer.blockchain_service",
    ]
    terminated_modules = [module_by_pid[pid] for pid in terminated]
    assert terminated_modules == [
        "main_computer.app_control",
        "main_computer.executor_service",
        "main_computer.applications_service",
        "main_computer.blockchain_service",
        "main_computer.main_log_service",
    ]
    assert state["children"][MAIN_LOG_CHILD_NAME]["restart_count"] == 1
    assert state["children"]["app"]["restart_count"] == 1


def test_supervisor_pid_takeover_uses_prior_state_to_kill_logger_last_and_fail_open(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    prior_pid = 34567
    prior_command = f"python -m main_computer.service_supervisor --root {repo.resolve()} supervise"
    (repo / SERVICE_SUPERVISOR_PID_FILENAME).write_text(
        json.dumps(
            {
                "pid": prior_pid,
                "service": SERVICE_NAME,
                "root": str(repo.resolve()),
                "command_line": prior_command,
            }
        ),
        encoding="utf-8",
    )
    runtime_dir = repo / "runtime" / "service_supervisor"
    runtime_dir.mkdir(parents=True)
    runtime_dir.joinpath("state.json").write_text(
        json.dumps(
            {
                "main_log": {"url": "http://127.0.0.1:1"},
                "children": {
                    MAIN_LOG_CHILD_NAME: {"pid": 100, "role": "logging"},
                    "app": {"pid": 101, "role": "service"},
                    "executor": {"pid": 102, "role": "service"},
                },
            }
        ),
        encoding="utf-8",
    )
    terminated: list[int] = []
    output: list[str] = []

    def factory(command: list[str], cwd: Path, stdout: Path, stderr: Path) -> FakeProcess:
        return FakeProcess([None])

    supervisor = ServiceSupervisor(
        root=repo,
        sleep_func=lambda _: None,
        output_func=output.append,
        process_command_reader=lambda pid: prior_command if pid == prior_pid else None,
        process_terminator=terminated.append,
        process_factory=factory,
    )

    state = supervisor.supervise(max_loops=1)

    assert state["ok"] is True
    assert terminated[:3] == [101, 102, 100]
    assert terminated[-1] == prior_pid
    claim = state["service"]["pid_claim"]
    assert claim["state"] == "terminated"
    assert claim["ordered_takeover_attempted"] is True
    assert any("main_log_emit_failed" in line for line in output)
