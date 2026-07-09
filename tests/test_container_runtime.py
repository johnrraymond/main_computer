from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from main_computer.container_runtime import ContainerRuntimeResolutionError, podman_command_cwd, resolve_container_runtime, split_command_override


class FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _runner_with_available(*available_prefixes: tuple[str, ...]):
    available = {tuple(prefix) for prefix in available_prefixes}
    calls: list[list[str]] = []
    kwargs_list: list[dict[str, object]] = []

    def runner(command, **kwargs):
        command = [str(part) for part in command]
        calls.append(command)
        kwargs_list.append(dict(kwargs))
        prefix = tuple(command[:-1]) if command[-1:] == ["version"] else tuple(command)
        return FakeCompleted(0 if prefix in available else 1, stdout="ok" if prefix in available else "", stderr="missing")

    runner.calls = calls  # type: ignore[attr-defined]
    runner.kwargs_list = kwargs_list  # type: ignore[attr-defined]
    return runner


def _runner_with_results(results: dict[tuple[str, ...], FakeCompleted]):
    calls: list[list[str]] = []
    kwargs_list: list[dict[str, object]] = []

    def runner(command, **kwargs):
        command = [str(part) for part in command]
        calls.append(command)
        kwargs_list.append(dict(kwargs))
        prefix = tuple(command[:-1]) if command[-1:] == ["version"] else tuple(command)
        return results.get(prefix, FakeCompleted(1, stderr="missing"))

    runner.calls = calls  # type: ignore[attr-defined]
    runner.kwargs_list = kwargs_list  # type: ignore[attr-defined]
    return runner


def test_split_command_override_preserves_quoted_arguments() -> None:
    assert split_command_override('podman compose --log-level "debug info"') == [
        "podman",
        "compose",
        "--log-level",
        "debug info",
    ]


def test_container_runtime_auto_prefers_working_docker_compose(tmp_path: Path) -> None:
    runner = _runner_with_available(("docker", "compose"))

    runtime = resolve_container_runtime(cwd=tmp_path, runner=runner, environ={})

    assert runtime.runtime == "docker"
    assert runtime.container_command == ("docker",)
    assert runtime.compose_command == ("docker", "compose")


def test_container_runtime_auto_falls_back_to_podman_when_docker_compose_missing(tmp_path: Path) -> None:
    runner = _runner_with_available(("podman-compose",))

    runtime = resolve_container_runtime(cwd=tmp_path, runner=runner, environ={})

    assert runtime.runtime == "podman"
    assert runtime.container_command == ("podman",)
    assert runtime.compose_command == ("podman-compose",)


def test_container_runtime_podman_preference_uses_podman_direct_and_compose(tmp_path: Path) -> None:
    runner = _runner_with_available(("podman",), ("podman-compose",))

    runtime = resolve_container_runtime(
        cwd=tmp_path,
        runner=runner,
        environ={"MAIN_COMPUTER_CONTAINER_RUNTIME": "podman"},
    )

    assert runtime.runtime == "podman"
    assert runtime.container_command == ("podman",)
    assert runtime.compose_command == ("podman-compose",)


def test_container_runtime_compose_override_infers_matching_podman_direct_command(tmp_path: Path) -> None:
    runtime = resolve_container_runtime(
        cwd=tmp_path,
        runner=_runner_with_available(),
        environ={"MAIN_COMPUTER_CONTAINER_COMPOSE_COMMAND": "podman-compose"},
    )

    assert runtime.runtime == "podman"
    assert runtime.container_command == ("podman",)
    assert runtime.compose_command == ("podman-compose",)


def test_container_runtime_legacy_docker_overrides_still_work(tmp_path: Path) -> None:
    runtime = resolve_container_runtime(
        cwd=tmp_path,
        runner=_runner_with_available(),
        environ={
            "MAIN_COMPUTER_DOCKER": "docker --context desktop-linux",
            "MAIN_COMPUTER_DOCKER_COMPOSE": "docker compose --context desktop-linux",
        },
    )

    assert runtime.runtime == "docker"
    assert runtime.container_command == ("docker", "--context", "desktop-linux")
    assert runtime.compose_command == ("docker", "compose", "--context", "desktop-linux")


def test_container_runtime_uses_standalone_podman_compose_for_custom_podman_command_without_probe(tmp_path: Path) -> None:
    runtime = resolve_container_runtime(
        cwd=tmp_path,
        container_command="podman --remote",
        environ={},
        probe=False,
    )

    assert runtime.runtime == "podman"
    assert runtime.container_command == ("podman", "--remote")
    assert runtime.compose_command == ("podman-compose",)


def test_container_runtime_explicit_podman_fails_fast_when_cli_missing_even_without_probe(tmp_path: Path) -> None:
    runner = _runner_with_available()

    with pytest.raises(ContainerRuntimeResolutionError) as excinfo:
        resolve_container_runtime(
            cwd=tmp_path,
            runner=runner,
            environ={"MAIN_COMPUTER_CONTAINER_RUNTIME": "podman"},
            probe=False,
        )

    assert "MAIN_COMPUTER_CONTAINER_RUNTIME=podman requires a working container CLI command" in str(excinfo.value)
    assert "`podman version` failed" in str(excinfo.value)


def test_container_runtime_explicit_podman_fails_when_compose_missing(tmp_path: Path) -> None:
    runner = _runner_with_available(("podman",))

    with pytest.raises(ContainerRuntimeResolutionError) as excinfo:
        resolve_container_runtime(
            cwd=tmp_path,
            runner=runner,
            environ={"MAIN_COMPUTER_CONTAINER_RUNTIME": "podman"},
        )

    assert "MAIN_COMPUTER_CONTAINER_RUNTIME=podman requires a working Compose CLI command" in str(excinfo.value)
    assert "`podman-compose version` failed" in str(excinfo.value)


def test_container_runtime_explicit_podman_direct_only_allows_missing_compose(tmp_path: Path) -> None:
    runner = _runner_with_available(("podman",))

    runtime = resolve_container_runtime(
        cwd=tmp_path,
        runner=runner,
        environ={"MAIN_COMPUTER_CONTAINER_RUNTIME": "podman"},
        require_compose=False,
    )

    assert runtime.runtime == "podman"
    assert runtime.container_command == ("podman",)
    assert runtime.compose_command == ("podman-compose",)
    assert ["podman", "version"] in runner.calls  # type: ignore[attr-defined]
    assert ["podman-compose", "version"] not in runner.calls  # type: ignore[attr-defined]


def test_container_runtime_finds_per_user_podman_when_path_is_stale(tmp_path: Path) -> None:
    podman = tmp_path / "Programs" / "Podman" / "podman.exe"
    podman.parent.mkdir(parents=True)
    podman.write_text("", encoding="utf-8")
    runner = _runner_with_available((str(podman),), ("podman-compose",))

    runtime = resolve_container_runtime(
        cwd=tmp_path,
        runner=runner,
        environ={
            "MAIN_COMPUTER_CONTAINER_RUNTIME": "podman",
            "LOCALAPPDATA": str(tmp_path),
        },
    )

    assert runtime.runtime == "podman"
    assert runtime.container_command == (str(podman),)
    assert runtime.compose_command == ("podman-compose",)


def test_container_runtime_infers_runtime_from_windows_style_absolute_path() -> None:
    runtime = resolve_container_runtime(
        container_command=r"C:\Users\subsi\AppData\Local\Programs\Podman\podman.exe",
        environ={},
        probe=False,
    )

    assert runtime.runtime == "podman"
    assert runtime.compose_command == ("podman-compose",)


def test_container_runtime_rejects_podman_compose_when_it_delegates_to_docker(tmp_path: Path) -> None:
    runner = _runner_with_results(
        {
            ("podman",): FakeCompleted(0, stdout="podman version 6.0.0"),
            ("podman", "compose"): FakeCompleted(
                0,
                stdout="podman-compose version 1.6.0",
                stderr='>>>> Executing external compose provider "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker-compose.exe". <<<<',
            ),
        }
    )

    with pytest.raises(ContainerRuntimeResolutionError) as excinfo:
        resolve_container_runtime(
            cwd=tmp_path,
            runner=runner,
            environ={"MAIN_COMPUTER_CONTAINER_RUNTIME": "podman"},
            compose_command="podman compose",
        )

    assert "delegated to Docker Compose" in str(excinfo.value)


def test_podman_command_cwd_uses_parent_of_protected_workdir(tmp_path: Path) -> None:
    protected = tmp_path / "main_computer_test-test-debug"
    protected.mkdir()

    assert podman_command_cwd(protected) == tmp_path


def test_container_runtime_podman_probes_outside_requested_workdir(tmp_path: Path) -> None:
    protected = tmp_path / "main_computer_test-test-debug"
    protected.mkdir()
    runner = _runner_with_available(("podman",), ("podman-compose",))

    runtime = resolve_container_runtime(
        cwd=protected,
        runner=runner,
        environ={"MAIN_COMPUTER_CONTAINER_RUNTIME": "podman"},
    )

    assert runtime.runtime == "podman"
    podman_cwds = [
        Path(str(kwargs.get("cwd")))
        for command, kwargs in zip(runner.calls, runner.kwargs_list)  # type: ignore[attr-defined]
        if command and command[0] == "podman"
    ]
    assert podman_cwds
    assert all(cwd == tmp_path for cwd in podman_cwds)
    assert protected not in podman_cwds


def test_container_runtime_docker_keeps_requested_workdir(tmp_path: Path) -> None:
    protected = tmp_path / "main_computer_test-test-debug"
    protected.mkdir()
    runner = _runner_with_available(("docker", "compose"))

    runtime = resolve_container_runtime(cwd=protected, runner=runner, environ={})

    assert runtime.runtime == "docker"
    docker_cwds = [
        Path(str(kwargs.get("cwd")))
        for command, kwargs in zip(runner.calls, runner.kwargs_list)  # type: ignore[attr-defined]
        if command and command[0] == "docker"
    ]
    assert docker_cwds
    assert all(cwd == protected for cwd in docker_cwds)
