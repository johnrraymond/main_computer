from __future__ import annotations

from pathlib import Path

import pytest

from main_computer.config import MainComputerConfig
from main_computer.docker_executor import DockerExecutor
from main_computer.executor_backend import create_executor_backend, normalize_executor_backend
from main_computer.executor_models import ExecutorRequest, build_executor_runtime_command
from main_computer.wsl_executor import WslExecutor


def test_normalize_executor_backend_accepts_docker_and_wsl_aliases() -> None:
    assert normalize_executor_backend(None) == "docker"
    assert normalize_executor_backend("docker") == "docker"
    assert normalize_executor_backend("container") == "docker"
    assert normalize_executor_backend("wsl") == "wsl"
    assert normalize_executor_backend("wsl2") == "wsl"


def test_normalize_executor_backend_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="Unsupported executor backend"):
        normalize_executor_backend("ssh")


def test_executor_factory_keeps_docker_as_default(tmp_path: Path) -> None:
    config = MainComputerConfig(
        workspace=tmp_path,
        executor_enabled=False,
        executor_backend="docker",
        executor_image="main-computer-executor:test",
    )

    backend = create_executor_backend(config, runtime_root=tmp_path / "runtime")

    assert isinstance(backend, DockerExecutor)
    assert backend.backend_name == "docker"
    assert backend.image == "main-computer-executor:test"


def test_executor_factory_can_select_wsl_test_distribution(tmp_path: Path) -> None:
    config = MainComputerConfig(
        workspace=tmp_path,
        executor_enabled=False,
        executor_backend="wsl",
        executor_wsl_distribution="MainComputerExecutorTest",
        executor_wsl_command="wsl.exe",
    )

    backend = create_executor_backend(config, runtime_root=tmp_path / "runtime")

    assert isinstance(backend, WslExecutor)
    assert backend.backend_name == "wsl"
    assert backend.distribution == "MainComputerExecutorTest"


def test_config_from_env_exposes_backend_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAIN_COMPUTER_EXECUTOR_BACKEND", "wsl")
    monkeypatch.setenv("MAIN_COMPUTER_EXECUTOR_WSL_DISTRIBUTION", "MainComputerExecutor")
    monkeypatch.setenv("MAIN_COMPUTER_EXECUTOR_WSL_COMMAND", "wsl.exe")

    config = MainComputerConfig.from_env()

    assert config.executor_backend == "wsl"
    assert config.executor_wsl_distribution == "MainComputerExecutor"
    assert config.executor_wsl_command == "wsl.exe"


def test_shared_runtime_command_contract_is_backend_neutral() -> None:
    request = ExecutorRequest(command="python -c 'print(123)'", cwd="/workspace/project", timeout_s=5)

    command = build_executor_runtime_command(request, timeout_s=5, artifact_dir="/outputs")

    assert command == [
        "/usr/local/bin/main-computer-exec",
        "run",
        "--cwd",
        "/workspace/project",
        "--timeout-ms",
        "5000",
        "--artifact-dir",
        "/outputs",
        "--",
        "python -c 'print(123)'",
    ]
