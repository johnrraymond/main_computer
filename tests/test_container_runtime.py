from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from main_computer.container_runtime import ContainerRuntimeResolutionError, resolve_container_runtime, split_command_override


class FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _runner_with_available(*available_prefixes: tuple[str, ...]):
    available = {tuple(prefix) for prefix in available_prefixes}
    calls: list[list[str]] = []

    def runner(command, **_kwargs):
        command = [str(part) for part in command]
        calls.append(command)
        prefix = tuple(command[:-1]) if command[-1:] == ["version"] else tuple(command)
        return FakeCompleted(0 if prefix in available else 1, stdout="ok" if prefix in available else "", stderr="missing")

    runner.calls = calls  # type: ignore[attr-defined]
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
    runner = _runner_with_available(("podman", "compose"))

    runtime = resolve_container_runtime(cwd=tmp_path, runner=runner, environ={})

    assert runtime.runtime == "podman"
    assert runtime.container_command == ("podman",)
    assert runtime.compose_command == ("podman", "compose")


def test_container_runtime_podman_preference_uses_podman_direct_and_compose(tmp_path: Path) -> None:
    runner = _runner_with_available(("podman",), ("podman", "compose"))

    runtime = resolve_container_runtime(
        cwd=tmp_path,
        runner=runner,
        environ={"MAIN_COMPUTER_CONTAINER_RUNTIME": "podman"},
    )

    assert runtime.runtime == "podman"
    assert runtime.container_command == ("podman",)
    assert runtime.compose_command == ("podman", "compose")


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


def test_container_runtime_derives_compose_from_custom_podman_command_without_probe(tmp_path: Path) -> None:
    runtime = resolve_container_runtime(
        cwd=tmp_path,
        container_command="podman --remote",
        environ={},
        probe=False,
    )

    assert runtime.runtime == "podman"
    assert runtime.container_command == ("podman", "--remote")
    assert runtime.compose_command == ("podman", "--remote", "compose")


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
    assert "`podman compose version` failed" in str(excinfo.value)
