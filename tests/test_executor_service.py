from __future__ import annotations

import json
import subprocess
from pathlib import Path

from main_computer.executor_service import (
    SERVICE_NAME,
    ExecutorService,
    _parse_wsl_distribution_names,
    load_executor_service_state,
)
from main_computer.viewport_route_dispatch import _control_panel_executor_graphical_status


class FakeRunner:
    def __init__(self, *, distro: str = "MainComputerExecutorTest") -> None:
        self.distro = distro
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        joined = " ".join(command)
        if "--list --quiet" in joined:
            return subprocess.CompletedProcess(command, 0, stdout=f"{self.distro}\n", stderr="")
        if command[:1] and command[0].endswith("wsl.exe") and "/bin/sh" in command:
            script = command[-1]
            if "cat > /usr/local/bin/main-computer-exec" in script:
                return subprocess.CompletedProcess(command, 0, stdout="main-computer-exec 1\n", stderr="")
            if "entrypoint-contract-ok" in script:
                return subprocess.CompletedProcess(command, 0, stdout="entrypoint-contract-ok\n", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:2] and command[0].endswith("docker") and command[1] == "version":
            return subprocess.CompletedProcess(command, 0, stdout="Docker version ok\n", stderr="")
        if command[:3] and command[0].endswith("docker") and command[1:3] == ["compose", "version"]:
            return subprocess.CompletedProcess(command, 0, stdout="Docker Compose version ok\n", stderr="")
        if command[:2] and command[0].endswith("docker") and command[1] == "ps":
            return subprocess.CompletedProcess(command, 0, stdout="main-computer-dev-hub-1\n", stderr="")
        if "compose" in command and "up" in command and "-d" in command:
            return subprocess.CompletedProcess(command, 0, stdout="started\n", stderr="")
        if "compose" in command and "ps" in command:
            return subprocess.CompletedProcess(command, 0, stdout="hub\nexecutor-image\n", stderr="")
        return subprocess.CompletedProcess(command, 99, stdout="", stderr=f"unexpected command: {command!r}")


def make_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    executor = repo / "docker" / "executor" / "main-computer-exec"
    executor.parent.mkdir(parents=True)
    executor.write_text("#!/usr/bin/env bash\necho main-computer-exec 1\n", encoding="utf-8")
    compose = repo / "docker-compose.dev.yml"
    compose.write_text("services:\n  hub:\n    image: test\n", encoding="utf-8")
    fake_wsl = tmp_path / "wsl.exe"
    fake_wsl.write_text("", encoding="utf-8")
    fake_wsl.chmod(0o755)
    fake_docker = tmp_path / "docker"
    fake_docker.write_text("", encoding="utf-8")
    fake_docker.chmod(0o755)
    return repo, fake_wsl, fake_docker


def make_runtime_artifacts(repo: Path) -> Path:
    runtime = repo / "runtime"
    image_root = repo.parent / "wsl-runtime"
    image_path = image_root / "images" / "MainComputerExecutorTest-rootfs.tar"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "main-computer-runtime.json").write_text(
        json.dumps(
            {
                "defaultProfile": "test",
                "profiles": {
                    "test": {
                        "distributionName": "MainComputerExecutorTest",
                        "runtimeRoot": str(image_root),
                        "rootfsTar": str(image_path),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    image_path.write_text("fake-rootfs", encoding="utf-8")
    scripts = repo / "scripts" / "windows"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "install-main-computer-runtime.ps1").write_text("# fake installer\n", encoding="utf-8")
    (scripts / "build-main-computer-runtime.ps1").write_text("# fake builder\n", encoding="utf-8")
    return image_root


def test_parse_wsl_distributions_tolerates_nul_and_default_marker() -> None:
    assert _parse_wsl_distribution_names("\x00*\x00 \x00U\x00b\x00u\x00n\x00t\x00u\x00\r\x00\n\x00") == ["Ubuntu"]
    assert _parse_wsl_distribution_names("MainComputerExecutorTest\nUbuntu\n") == [
        "MainComputerExecutorTest",
        "Ubuntu",
    ]


def test_boot_repairs_wsl_shim_to_repo_entrypoint_and_starts_compose(tmp_path: Path) -> None:
    repo, fake_wsl, fake_docker = make_repo(tmp_path)
    runner = FakeRunner()
    service = ExecutorService(
        root=repo,
        wsl_command=str(fake_wsl),
        docker_command=str(fake_docker),
        runner=runner,
        sleep_func=lambda _: None,
    )

    state = service.boot()

    assert state["ok"] is True
    assert state["state"] == "ready"
    assert state["wsl"]["entrypoint_contract_ok"] is True
    assert state["docker"]["engine_available"] is True
    assert state["compose"]["started"] is True
    assert service.state_path.exists()

    shim_commands = [call for call in runner.calls if call[:1] == [str(fake_wsl)] and "cat > /usr/local/bin/main-computer-exec" in call[-1]]
    assert len(shim_commands) == 1
    shim_script = shim_commands[0][-1]
    assert "exec /bin/bash" in shim_script
    assert "docker/executor/main-computer-exec" in shim_script
    assert "$@" in shim_script

    compose_up_calls = [call for call in runner.calls if call[:1] == [str(fake_docker)] and "compose" in call and "up" in call]
    assert len(compose_up_calls) == 1

    loaded = load_executor_service_state(repo)
    assert loaded["ok"] is True
    assert loaded["service_available"] is True


class MissingDistroInstallRunner(FakeRunner):
    def __init__(self) -> None:
        super().__init__(distro="Ubuntu")
        self.installed = False

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        joined = " ".join(command)
        if "--list --quiet" in joined:
            self.calls.append(command)
            names = ["Ubuntu"]
            if self.installed:
                names.append("MainComputerExecutorTest")
            return subprocess.CompletedProcess(command, 0, stdout="\n".join(names) + "\n", stderr="")
        if command[:1] and (command[0].endswith("powershell.exe") or command[0].endswith("powershell")):
            self.calls.append(command)
            assert "-Reset" not in command
            if "install-main-computer-runtime.ps1" in joined:
                self.installed = True
                return subprocess.CompletedProcess(command, 0, stdout="installed without reset\n", stderr="")
            if "build-main-computer-runtime.ps1" in joined:
                return subprocess.CompletedProcess(command, 0, stdout="built\n", stderr="")
        return super().__call__(command, **kwargs)


class DockerVersionTimeoutRunner(FakeRunner):
    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:2] and command[0].endswith("docker") and command[1] == "version":
            raise subprocess.TimeoutExpired(
                cmd=command,
                timeout=float(kwargs.get("timeout") or 0),
                output="partial docker output",
                stderr="docker cli did not answer",
            )
        return super().__call__(command, **kwargs)


def test_boot_reports_docker_timeout_without_traceback(tmp_path: Path) -> None:
    repo, fake_wsl, fake_docker = make_repo(tmp_path)
    runner = DockerVersionTimeoutRunner()
    clock_values = iter([0.0, 999.0])
    service = ExecutorService(
        root=repo,
        wsl_command=str(fake_wsl),
        docker_command=str(fake_docker),
        runner=runner,
        sleep_func=lambda _: None,
        time_func=lambda: next(clock_values),
        docker_start_timeout_s=1,
    )

    state = service.boot()

    assert state["ok"] is False
    assert state["state"] == "down"
    assert state["docker"]["state"] == "down"
    assert "timed out after 12 seconds" in state["docker"]["error"]
    assert state["compose"]["state"] == "blocked"
    assert service.state_path.exists()


class ComposeAddressPoolFailureRunner(FakeRunner):
    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "compose" in command and "up" in command and "-d" in command:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr=(
                    "Network main-computer-dev-test_default Creating\n"
                    "Network main-computer-dev-test_default Error "
                    "Error response from daemon: all predefined address pools have been fully subnetted\n"
                    "failed to create network main-computer-dev-test_default: "
                    "Error response from daemon: all predefined address pools have been fully subnetted\n"
                ),
            )
        return super().__call__(command, **kwargs)


def test_boot_warns_when_compose_stack_cannot_allocate_network(tmp_path: Path) -> None:
    repo, fake_wsl, fake_docker = make_repo(tmp_path)
    output_lines: list[str] = []
    service = ExecutorService(
        root=repo,
        wsl_command=str(fake_wsl),
        docker_command=str(fake_docker),
        runner=ComposeAddressPoolFailureRunner(),
        sleep_func=lambda _: None,
        output_func=output_lines.append,
    )

    state = service.boot()

    assert state["ok"] is False
    assert state["compose"]["state"] == "down"
    assert state["compose"]["started"] is False
    assert "address pool" in state["compose"]["warning"]
    assert "unused Docker networks" in state["compose"]["remediation"]
    assert state["warning"].startswith("compose:")
    assert "address pool" in state["warning"]
    assert "warning=compose:" in "\n".join(output_lines)


class FlakyDockerVersionRunner(FakeRunner):
    def __init__(self, *, failures_before_ready: int = 2) -> None:
        super().__init__()
        self.failures_before_ready = failures_before_ready
        self.docker_version_attempts = 0

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:2] and command[0].endswith("docker") and command[1] == "version":
            self.docker_version_attempts += 1
            if self.docker_version_attempts <= self.failures_before_ready:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="docker engine is still starting")
        return super().__call__(command, **kwargs)


def test_watch_retries_full_boot_on_heartbeat_until_boot_is_proven(tmp_path: Path) -> None:
    repo, fake_wsl, fake_docker = make_repo(tmp_path)
    runner = FlakyDockerVersionRunner(failures_before_ready=2)
    sleep_calls: list[float] = []
    service = ExecutorService(
        root=repo,
        wsl_command=str(fake_wsl),
        docker_command=str(fake_docker),
        runner=runner,
        sleep_func=sleep_calls.append,
        heartbeat_interval_s=30,
        light_check_interval_s=999,
    )

    state = service.boot(watch=True, max_watch_loops=3)

    assert state["ok"] is True
    assert state["boot_proven"] is True
    assert runner.docker_version_attempts == 3
    assert [value for value in sleep_calls if value == 30] == [30, 30]
    compose_up_calls = [call for call in runner.calls if call[:1] == [str(fake_docker)] and "compose" in call and "up" in call]
    assert len(compose_up_calls) == 1


def test_watch_mode_heartbeats_without_repeating_expensive_boot_work(tmp_path: Path) -> None:
    repo, fake_wsl, fake_docker = make_repo(tmp_path)
    runner = FakeRunner()
    service = ExecutorService(
        root=repo,
        wsl_command=str(fake_wsl),
        docker_command=str(fake_docker),
        runner=runner,
        sleep_func=lambda _: None,
        heartbeat_interval_s=30,
        light_check_interval_s=300,
    )

    state = service.boot(watch=True, max_watch_loops=2)

    assert state["ok"] is True
    shim_calls = [call for call in runner.calls if call[:1] == [str(fake_wsl)] and "cat > /usr/local/bin/main-computer-exec" in call[-1]]
    compose_up_calls = [call for call in runner.calls if call[:1] == [str(fake_docker)] and "compose" in call and "up" in call]
    docker_ps_calls = [call for call in runner.calls if call[:2] == [str(fake_docker), "ps"]]
    assert len(shim_calls) == 1
    assert len(compose_up_calls) == 1
    assert docker_ps_calls == []


def test_missing_wsl_distro_installs_without_reset_then_finishes_boot(tmp_path: Path) -> None:
    repo, fake_wsl, fake_docker = make_repo(tmp_path)
    image_root = make_runtime_artifacts(repo)
    fake_powershell = tmp_path / "powershell.exe"
    fake_powershell.write_text("", encoding="utf-8")
    fake_powershell.chmod(0o755)
    runner = MissingDistroInstallRunner()
    service = ExecutorService(
        root=repo,
        wsl_command=str(fake_wsl),
        docker_command=str(fake_docker),
        powershell_command=str(fake_powershell),
        runner=runner,
        sleep_func=lambda _: None,
    )

    state = service.boot()

    assert state["ok"] is True
    assert state["wsl"]["state"] == "ready"
    assert state["wsl"]["entrypoint_contract_ok"] is True
    install_calls = [call for call in runner.calls if call[:1] == [str(fake_powershell)] and "install-main-computer-runtime.ps1" in " ".join(call)]
    assert len(install_calls) == 1
    assert "-Reset" not in install_calls[0]
    assert "-DistributionName" in install_calls[0]
    assert "MainComputerExecutorTest" in install_calls[0]
    assert "-RuntimeImagePath" in install_calls[0]
    assert str(image_root / "images" / "MainComputerExecutorTest-rootfs.tar") in install_calls[0]
    assert str(repo / "runtime" / "main-computer-executor-test-rootfs.tar") not in install_calls[0]


def test_graphical_executor_tile_prefers_executor_service_state() -> None:
    service_payload = {
        "ok": True,
        "state": "ready",
        "service_available": True,
        "wsl": {"state": "ready"},
        "docker": {"state": "ready"},
        "compose": {"state": "ready"},
        "service": {"heartbeat_at": "now"},
    }

    graphical = _control_panel_executor_graphical_status(
        executor_payload={"ok": False, "error": "backend disabled"},
        service_payload=service_payload,
        executor_enabled=True,
        executor_backend="wsl",
    )

    assert graphical["state"] == "healthy"
    assert graphical["required"] is True
    assert "service ready" in graphical["summary"]
    assert "wsl ready" in graphical["summary"]


def test_graphical_executor_tile_marks_missing_service_down_when_executor_enabled() -> None:
    graphical = _control_panel_executor_graphical_status(
        executor_payload={"ok": False},
        service_payload={"ok": False, "state": "missing", "service_available": False},
        executor_enabled=True,
        executor_backend="docker",
    )

    assert graphical["state"] == "down"

def test_graphical_executor_tile_surfaces_compose_warning_over_heartbeat() -> None:
    graphical = _control_panel_executor_graphical_status(
        executor_payload={"ok": False},
        service_payload={
            "ok": False,
            "state": "down",
            "service_available": True,
            "heartbeat_age_s": 12.3,
            "wsl": {"ok": True, "state": "ready"},
            "docker": {"ok": True, "state": "ready"},
            "compose": {
                "ok": False,
                "state": "down",
                "warning": "Docker could not allocate a new Compose network subnet.",
            },
        },
        executor_enabled=True,
        executor_backend="wsl",
    )

    assert graphical["state"] == "down"
    assert graphical["detail"].startswith("warning: compose:")
    assert "Compose network subnet" in graphical["detail"]
    assert "heartbeat 12.3s ago" not in graphical["detail"]


def test_watch_claims_pid_file_and_prints_boot_status(tmp_path: Path) -> None:
    repo, fake_wsl, fake_docker = make_repo(tmp_path)
    output_lines: list[str] = []
    service = ExecutorService(
        root=repo,
        wsl_command=str(fake_wsl),
        docker_command=str(fake_docker),
        runner=FakeRunner(),
        sleep_func=lambda _: None,
        output_func=output_lines.append,
        heartbeat_interval_s=30,
        light_check_interval_s=300,
    )

    state = service.boot(watch=True, max_watch_loops=1)

    assert state["ok"] is True
    assert state["service"]["pid_file"] == str(repo / ".main_computer_executor_service.pid")
    assert state["service"]["pid_claim"]["written"] is True
    assert "complete; executor infrastructure is ready" in "\n".join(output_lines)
    assert not service.pid_path.exists()


def test_pid_claim_terminates_prior_service_only_when_live_command_matches_pid_file_and_root(tmp_path: Path) -> None:
    repo, fake_wsl, fake_docker = make_repo(tmp_path)
    prior_pid = 43210
    prior_command = f"python -m main_computer.executor_service --root {repo} boot --watch"
    (repo / ".main_computer_executor_service.pid").write_text(
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
    service = ExecutorService(
        root=repo,
        wsl_command=str(fake_wsl),
        docker_command=str(fake_docker),
        runner=FakeRunner(),
        output_func=None,
        process_command_reader=lambda pid: prior_command if pid == prior_pid else None,
        process_terminator=terminated.append,
    )

    claim = service._claim_pid_file()

    assert claim["state"] == "terminated"
    assert terminated == [prior_pid]
    written = json.loads(service.pid_path.read_text(encoding="utf-8"))
    assert written["pid"] != prior_pid
    assert written["service"] == SERVICE_NAME


def test_pid_mismatch_does_not_block_watch_boot(tmp_path: Path) -> None:
    repo, fake_wsl, fake_docker = make_repo(tmp_path)
    prior_pid = 54321
    prior_command = f"python -m main_computer.executor_service --root {repo} boot --watch"
    (repo / ".main_computer_executor_service.pid").write_text(
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
    service = ExecutorService(
        root=repo,
        wsl_command=str(fake_wsl),
        docker_command=str(fake_docker),
        runner=FakeRunner(),
        sleep_func=lambda _: None,
        output_func=None,
        process_command_reader=lambda pid: "notepad.exe",
        process_terminator=lambda pid: (_ for _ in ()).throw(AssertionError("should not kill mismatched process")),
    )

    state = service.boot(watch=True, max_watch_loops=1)

    assert state["ok"] is True
    assert state["service"]["pid_claim"]["state"] == "not-terminated"
    assert state["service"]["pid_claim"]["prior_command_matches_pid_file"] is False



def test_executor_compose_project_env_scopes_dev_template_for_installed_modes(monkeypatch, tmp_path: Path) -> None:
    repo, fake_wsl, fake_docker = make_repo(tmp_path)
    monkeypatch.setenv("MAIN_COMPUTER_DEV_COMPOSE_PROJECT", "main-computer-dev-main-computer-test-debug")
    runner = FakeRunner()
    service = ExecutorService(
        root=repo,
        wsl_command=str(fake_wsl),
        docker_command=str(fake_docker),
        runner=runner,
        sleep_func=lambda _: None,
    )

    state = service.boot()

    expected = [
        str(fake_docker),
        "compose",
        "--project-name",
        "main-computer-dev-main-computer-test-debug",
        "-f",
        str(repo / "docker-compose.dev.yml"),
        "up",
        "-d",
    ]
    assert expected in runner.calls
    assert state["compose"]["compose_project"] == "main-computer-dev-main-computer-test-debug"
