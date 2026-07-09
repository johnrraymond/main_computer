from __future__ import annotations

import json
import subprocess
from pathlib import Path

from main_computer.service_control import enqueue_applications_action, pending_control_requests
from main_computer.applications_service import (
    APPLICATIONS_SERVICE_PID_FILENAME,
    SERVICE_NAME,
    ApplicationsService,
    load_applications_service_state,
)


class FakeApplicationsRunner:
    def __init__(self, *, docker_failures_before_ready: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.kwargs_list: list[dict[str, object]] = []
        self.docker_failures_before_ready = docker_failures_before_ready
        self.docker_version_attempts = 0

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        self.kwargs_list.append(dict(kwargs))
        if len(command) >= 2 and command[0] in {"docker", "podman"} and command[1] == "version":
            self.docker_version_attempts += 1
            if self.docker_version_attempts <= self.docker_failures_before_ready:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="container runtime is still starting")
            return subprocess.CompletedProcess(command, 0, stdout="Container version ok\n", stderr="")
        if len(command) >= 3 and command[0] in {"docker", "podman"} and command[1:3] == ["compose", "version"]:
            return subprocess.CompletedProcess(command, 0, stdout="Container Compose version ok\n", stderr="")
        if len(command) >= 2 and command[0] in {"docker", "podman"} and command[1] == "ps":
            return subprocess.CompletedProcess(command, 0, stdout="mc-applications-coolify\n", stderr="")
        if "compose" in command and "config" in command and "--quiet" in command:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if "compose" in command and "up" in command and "-d" in command:
            return subprocess.CompletedProcess(command, 0, stdout="started\n", stderr="")
        if "compose" in command and "ps" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="\n".join(
                    [
                        '{"Service":"postgres","State":"running"}',
                        '{"Service":"redis","State":"running"}',
                        '{"Service":"soketi","State":"running"}',
                        '{"Service":"coolify","State":"running"}',
                    ]
                )
                + "\n",
                stderr="",
            )
        if len(command) >= 3 and str(command[1]).endswith("coolify-local-docker.py") and command[2] in {"init", "wait", "ensure-infra"}:
            return subprocess.CompletedProcess(command, 0, stdout=f"{command[2]} ok\n", stderr="")
        if len(command) >= 3 and str(command[1]).endswith("setup-local-coolify.py") and command[2] == "ensure":
            return subprocess.CompletedProcess(command, 0, stdout="ensure ok\n", stderr="")
        return subprocess.CompletedProcess(command, 99, stdout="", stderr=f"unexpected command: {command!r}")


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.applications.yml").write_text(
        """services:
  postgres:
    image: postgres:15-alpine
  redis:
    image: redis:7-alpine
  soketi:
    image: quay.io/soketi/soketi:1.4-16-debian
  coolify:
    image: ghcr.io/coollabsio/coolify:latest
""",
        encoding="utf-8",
    )
    tools_dir = repo / "tools" / "local-prod"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / "coolify-local-docker.py").write_text("# test stub\n", encoding="utf-8")
    (tools_dir / "setup-local-coolify.py").write_text("# test stub\n", encoding="utf-8")
    return repo


def test_applications_compose_declares_application_servers_not_executor_or_chain() -> None:
    repo = Path(__file__).resolve().parents[1]
    compose = (repo / "docker-compose.applications.yml").read_text(encoding="utf-8")

    assert "name: main-computer-applications" in compose
    assert "\n  onlyoffice:" not in compose
    assert "onlyoffice/documentserver" not in compose
    assert "\n  gitea:" not in compose
    gitea_compose = (repo / "docker-compose.gitea.yml").read_text(encoding="utf-8")
    assert "name: main-computer-gitea" in gitea_compose
    assert "\n  gitea:" in gitea_compose
    assert "\n  coolify:" in compose
    assert "\n  postgres:" in compose
    assert "\n  redis:" in compose
    assert "\n  soketi:" in compose

    assert "git-server:" not in compose
    assert "executor-image" not in compose
    assert "ethereum-dev" not in compose
    assert "main-computer:" not in compose


def test_boot_writes_app_env_and_starts_applications_compose(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = FakeApplicationsRunner()
    service = ApplicationsService(root=repo, runner=runner, sleep_func=lambda _: None, output_func=None)

    state = service.boot()

    assert state["ok"] is True
    assert state["state"] == "ready"
    assert state["env"]["state"] == "ready"
    assert state["docker"]["state"] == "ready"
    assert state["compose"]["started"] is True
    assert state["coolify"]["state"] == "ready"
    assert state["applications"] == ["coolify"]

    env_file = repo / "runtime" / "applications_service" / "applications.env"
    coolify_env = repo / "runtime" / "applications_service" / "coolify" / "source" / ".env"
    assert env_file.exists()
    assert coolify_env.exists()

    env_text = env_file.read_text(encoding="utf-8")
    assert "MAIN_COMPUTER_ONLYOFFICE_PORT=18085" in env_text
    assert "MAIN_COMPUTER_GITEA_HTTP_PORT" not in env_text
    assert "COOLIFY_LOCAL_STATE=" in env_text
    assert "APP_PORT=8000" in env_text
    assert "APP_KEY=base64:" in env_text

    compose_up_calls = [call for call in runner.calls if "compose" in call and "up" in call and "-d" in call]
    assert len(compose_up_calls) == 1
    assert "--project-name" in compose_up_calls[0]
    assert "main-computer-applications" in compose_up_calls[0]
    assert "--env-file" in compose_up_calls[0]
    assert str(env_file) in compose_up_calls[0]
    assert str(repo / "docker-compose.applications.yml") in compose_up_calls[0]
    assert compose_up_calls[0][-4:] == ["postgres", "redis", "soketi", "coolify"]
    assert all("onlyoffice" not in call for call in compose_up_calls)

    coolify_boot_calls = [call for call in runner.calls if len(call) >= 3 and str(call[1]).endswith("coolify-local-docker.py")]
    assert [call[2] for call in coolify_boot_calls] == ["init", "wait", "ensure-infra"]

    loaded = load_applications_service_state(repo)
    assert loaded["ok"] is True
    assert loaded["service_available"] is True





def test_applications_service_uses_podman_runtime_when_requested(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAIN_COMPUTER_CONTAINER_RUNTIME", "podman")
    repo = make_repo(tmp_path)
    runner = FakeApplicationsRunner()

    service = ApplicationsService(root=repo, runner=runner, output_func=None)
    state = service._full_boot_reconcile()

    assert state["docker"]["container_runtime"]["runtime"] == "podman"
    assert ["podman", "version"] in runner.calls
    compose_up_calls = [call for call in runner.calls if call[:2] == ["podman", "compose"] and "up" in call]
    assert compose_up_calls

def test_applications_service_runs_podman_subprocesses_outside_install_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAIN_COMPUTER_CONTAINER_RUNTIME", "podman")
    repo = make_repo(tmp_path)
    runner = FakeApplicationsRunner()

    service = ApplicationsService(root=repo, runner=runner, sleep_func=lambda _: None, output_func=None)
    state = service.boot()

    assert state["ok"] is True
    protected_cwd_calls = [
        (command, kwargs)
        for command, kwargs in zip(runner.calls, runner.kwargs_list)
        if Path(str(kwargs.get("cwd"))) == repo
    ]
    assert protected_cwd_calls == []
    podman_or_helper_cwds = [
        Path(str(kwargs.get("cwd")))
        for command, kwargs in zip(runner.calls, runner.kwargs_list)
        if (command and command[0] == "podman")
        or (len(command) >= 2 and str(command[1]).endswith("coolify-local-docker.py"))
    ]
    assert podman_or_helper_cwds
    assert all(cwd == repo.parent for cwd in podman_or_helper_cwds)


def test_boot_does_not_start_docker_onlyoffice_after_coolify(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = FakeApplicationsRunner()
    service = ApplicationsService(root=repo, runner=runner, sleep_func=lambda _: None, output_func=None)

    state = service.boot()

    assert state["ok"] is True
    assert state["coolify"]["state"] == "ready"
    assert state["application_servers"]["state"] == "skipped"
    assert state["application_servers"]["expected_applications"] == []
    compose_up_calls = [call for call in runner.calls if "compose" in call and "up" in call and "-d" in call]
    assert compose_up_calls
    assert all("onlyoffice" not in call for call in compose_up_calls)


def test_applications_boot_ignores_legacy_coolify_project_env_for_stack_name(monkeypatch, tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    state_root = tmp_path / "mode-state" / "coolify-local-docker"
    monkeypatch.setenv("MAIN_COMPUTER_COOLIFY_PROJECT", "main-computer-coolify-debug-1234")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "polluting-compose-project")
    monkeypatch.setenv("COOLIFY_COMPOSE_PROJECT", "polluting-coolify-compose-project")
    monkeypatch.setenv("MAIN_COMPUTER_COOLIFY_STATE_DIR", str(state_root))
    monkeypatch.setenv("MAIN_COMPUTER_COOLIFY_APP_PORT", "27042")
    monkeypatch.setenv("MAIN_COMPUTER_COOLIFY_SOKETI_PORT", "27142")
    monkeypatch.setenv("MAIN_COMPUTER_COOLIFY_SOKETI_TERMINAL_PORT", "27242")

    runner = FakeApplicationsRunner()
    service = ApplicationsService(root=repo, runner=runner, sleep_func=lambda _: None, output_func=None)

    state = service.boot()

    assert state["ok"] is True
    assert state["env"]["compose_project"] == "main-computer-applications"
    env_text = (repo / "runtime" / "applications_service" / "applications.env").read_text(encoding="utf-8")
    assert f"COOLIFY_LOCAL_STATE={state_root}" in env_text
    assert "COOLIFY_COMPOSE_PROJECT=main-computer-applications" in env_text
    assert "COOLIFY_NETWORK_NAME=main-computer-applications" in env_text
    assert "COOLIFY_CONTAINER_NAME=mc-applications-coolify" in env_text
    assert "COOLIFY_POSTGRES_CONTAINER_NAME=mc-applications-coolify-db" in env_text
    assert "COOLIFY_REDIS_CONTAINER_NAME=mc-applications-coolify-redis" in env_text
    assert "COOLIFY_SOKETI_CONTAINER_NAME=mc-applications-coolify-realtime" in env_text
    assert "APP_PORT=27042" in env_text
    assert "SOKETI_PORT=27142" in env_text
    assert "SOKETI_TERMINAL_PORT=27242" in env_text
    assert "main-computer-coolify-debug-1234" not in env_text

    coolify_boot_calls = [call for call in runner.calls if len(call) >= 3 and str(call[1]).endswith("coolify-local-docker.py")]
    assert [call[2] for call in coolify_boot_calls] == ["init", "wait", "ensure-infra"]
    for call in coolify_boot_calls:
        assert "--project-name" in call
        assert "main-computer-applications" in call
        assert "main-computer-coolify-debug-1234" not in call
        assert "--state-dir" in call
        assert str(state_root) in call
        assert "--app-port" in call
        assert "27042" in call


def test_applications_env_heals_bad_prior_compose_project_and_container_names(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    env_dir = repo / "runtime" / "applications_service"
    env_dir.mkdir(parents=True)
    (env_dir / "applications.env").write_text(
        "\n".join(
            [
                "COOLIFY_COMPOSE_PROJECT=main-computer-coolify-main-computer-test-unleashed",
                "COOLIFY_CONTAINER_NAME=mc-coolify-main-computer-test-unleashed",
                "COOLIFY_POSTGRES_CONTAINER_NAME=mc-coolify-main-computer-test-unleashed-db",
                "COOLIFY_REDIS_CONTAINER_NAME=mc-coolify-main-computer-test-unleashed-redis",
                "COOLIFY_SOKETI_CONTAINER_NAME=mc-coolify-main-computer-test-unleashed-realtime",
                "COOLIFY_NETWORK_NAME=main-computer-coolify-main-computer-test-unleashed_default",
                "APP_PORT=18000",
                "",
            ]
        ),
        encoding="utf-8",
    )

    runner = FakeApplicationsRunner()
    service = ApplicationsService(root=repo, runner=runner, sleep_func=lambda _: None, output_func=None)

    state = service.boot()

    assert state["ok"] is True
    assert state["env"]["compose_project"] == "main-computer-applications"
    env_text = (env_dir / "applications.env").read_text(encoding="utf-8")
    assert "COOLIFY_COMPOSE_PROJECT=main-computer-applications" in env_text
    assert "COOLIFY_CONTAINER_NAME=mc-applications-coolify" in env_text
    assert "COOLIFY_POSTGRES_CONTAINER_NAME=mc-applications-coolify-db" in env_text
    assert "COOLIFY_REDIS_CONTAINER_NAME=mc-applications-coolify-redis" in env_text
    assert "COOLIFY_SOKETI_CONTAINER_NAME=mc-applications-coolify-realtime" in env_text
    assert "COOLIFY_NETWORK_NAME=main-computer-applications" in env_text
    assert "APP_PORT=8000" in env_text
    assert "APP_PORT=18000" not in env_text
    assert "main-computer-coolify-main-computer-test-unleashed" not in env_text
    assert "mc-coolify-main-computer-test-unleashed" not in env_text


def test_watch_retries_full_applications_boot_on_heartbeat_until_ready(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = FakeApplicationsRunner(docker_failures_before_ready=2)
    sleep_calls: list[float] = []
    output_lines: list[str] = []
    service = ApplicationsService(
        root=repo,
        runner=runner,
        sleep_func=sleep_calls.append,
        output_func=output_lines.append,
        heartbeat_interval_s=30,
        light_check_interval_s=999,
    )

    state = service.boot(watch=True, max_watch_loops=3)

    assert state["ok"] is True
    assert state["boot_proven"] is True
    assert runner.docker_version_attempts == 3
    assert [value for value in sleep_calls if value == 30] == [30, 30]
    assert "application servers are ready" in "\n".join(output_lines)
    assert not (repo / APPLICATIONS_SERVICE_PID_FILENAME).exists()


def test_applications_pid_claim_only_kills_matching_prior_process_and_boots_anyway(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    prior_pid = 45678
    prior_command = f"python -m main_computer.applications_service --root {repo} boot --watch"
    (repo / APPLICATIONS_SERVICE_PID_FILENAME).write_text(
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
    service = ApplicationsService(
        root=repo,
        runner=FakeApplicationsRunner(),
        sleep_func=lambda _: None,
        output_func=None,
        process_command_reader=lambda pid: prior_command if pid == prior_pid else None,
        process_terminator=terminated.append,
    )

    state = service.boot(watch=True, max_watch_loops=1)

    assert state["ok"] is True
    assert state["service"]["pid_claim"]["state"] == "terminated"
    assert terminated == [prior_pid]


def test_applications_pid_mismatch_does_not_block_boot(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    prior_pid = 56789
    prior_command = f"python -m main_computer.applications_service --root {repo} boot --watch"
    (repo / APPLICATIONS_SERVICE_PID_FILENAME).write_text(
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
    service = ApplicationsService(
        root=repo,
        runner=FakeApplicationsRunner(),
        sleep_func=lambda _: None,
        output_func=None,
        process_command_reader=lambda pid: "python -m something_else" if pid == prior_pid else None,
        process_terminator=lambda pid: (_ for _ in ()).throw(AssertionError("should not kill mismatched process")),
    )

    state = service.boot(watch=True, max_watch_loops=1)

    assert state["ok"] is True
    assert state["service"]["pid_claim"]["state"] == "not-terminated"
    assert state["service"]["pid_claim"]["prior_command_matches_pid_file"] is False

def test_applications_service_processes_named_coolify_restart_request(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = FakeApplicationsRunner()
    service = ApplicationsService(
        root=repo,
        runner=runner,
        sleep_func=lambda _: None,
        output_func=None,
        heartbeat_interval_s=30,
        light_check_interval_s=999,
    )

    enqueue_applications_action(repo, action="restart", target="coolify", source="test")
    state = service.boot(watch=True, max_watch_loops=1)

    assert state["ok"] is True
    assert pending_control_requests(repo, channel="applications") == []
    restart_calls = [call for call in runner.calls if "compose" in call and "up" in call and "--force-recreate" in call]
    assert restart_calls
    assert "coolify" in restart_calls[-1]
    assert "onlyoffice" not in restart_calls[-1]


def test_applications_service_no_longer_accepts_onlyoffice_docker_restart(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = FakeApplicationsRunner()
    service = ApplicationsService(
        root=repo,
        runner=runner,
        sleep_func=lambda _: None,
        output_func=None,
        heartbeat_interval_s=30,
        light_check_interval_s=999,
    )

    enqueue_applications_action(repo, action="restart", target="onlyoffice", source="test")
    state = service.boot(watch=True, max_watch_loops=1)

    assert state["ok"] is True
    assert state["last_control_results"][-1]["result"]["state"] == "unknown-target"


def test_applications_service_rejects_unknown_app_server_restart_target(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runner = FakeApplicationsRunner()
    service = ApplicationsService(
        root=repo,
        runner=runner,
        sleep_func=lambda _: None,
        output_func=None,
        heartbeat_interval_s=30,
        light_check_interval_s=999,
    )

    enqueue_applications_action(repo, action="restart", target="made-up-app", source="test")
    state = service.boot(watch=True, max_watch_loops=1)

    assert state["ok"] is True
    assert state["last_control_results"][-1]["result"]["state"] == "unknown-target"

