from __future__ import annotations

from pathlib import Path

from main_computer.bootstrap.cli import _launcher_environment, _service_env, read_local_coolify_token_file


def _profile(tmp_path: Path) -> dict[str, object]:
    state = tmp_path / "state"
    return {
        "key": "debug",
        "label": "Debug",
        "guidance_level": "debug",
        "state_root": state,
        "control_root": state / "control",
        "port": 28865,
        "heartbeat_port": 28866,
        "distribution": "MainComputer-test-debug",
        "executor_root": state / "executor",
        "local_server_project": "main-computer-local-platform-test-debug",
        "local_server_registry": state / "local-platform" / "sites.json",
        "local_server_compose": state / "local-platform" / "docker-compose.websites.yml",
        "local_server_port_start": 28080,
        "local_server_generated_port_start": 28100,
        "local_server_generated_port_end": 28199,
        "coolify_project": "main-computer-coolify-test-debug",
        "coolify_state_root": state / "coolify-local-docker",
        "coolify_port": 27066,
        "coolify_soketi_port": 27166,
        "coolify_soketi_terminal_port": 27266,
        "onlyoffice_port": 28084,
        "onlyoffice_project": "main-computer-onlyoffice-debug",
    }


def test_local_coolify_token_file_reader_extracts_token_value_only(tmp_path: Path) -> None:
    token_file = tmp_path / "api-token.txt"
    token_file.write_text(
        "\n".join(
            [
                "# Main Computer local Coolify API token",
                "dashboard=http://127.0.0.1:27066",
                "name=main-computer-local-smoke",
                "token=123|abcdef",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert read_local_coolify_token_file(token_file) == "123|abcdef"


def test_service_env_exposes_local_coolify_token_value_for_remote_prod_path(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    token_file = Path(profile["coolify_state_root"]) / "api-token.txt"
    token_file.parent.mkdir(parents=True)
    token_file.write_text(
        "# generated\n"
        "dashboard=http://127.0.0.1:27066\n"
        "token=999|local-coolify-token\n",
        encoding="utf-8",
    )

    env = _service_env(
        base_env={},
        profile=profile,
        workspace=tmp_path,
        wsl_command="wsl.exe",
        onlyoffice_mode="disabled",
        container_runtime="docker",
        local_server_mode="auto",
        local_coolify_mode="auto",
    )

    assert env["MAIN_COMPUTER_COOLIFY_LOCAL_URL"] == "http://127.0.0.1:27066"
    assert env["MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_REF"] == "MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN"
    assert env["MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN"] == "999|local-coolify-token"
    assert "dashboard=" not in env["MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN"]


def test_service_env_respects_empty_base_env_without_dev_shell_leakage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "dev-venv"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "dev-pythonpath"))

    env = _service_env(
        base_env={},
        profile=_profile(tmp_path),
        workspace=tmp_path,
        wsl_command="wsl.exe",
        onlyoffice_mode="disabled",
        container_runtime="docker",
        local_server_mode="disabled",
        local_coolify_mode="disabled",
    )

    assert "VIRTUAL_ENV" not in env
    assert "PYTHONPATH" not in env
    assert env["MAIN_COMPUTER_CONTROL_PORT"] == "28865"
    assert env["MAIN_COMPUTER_CONTAINER_RUNTIME"] == "docker"


def test_launcher_environment_records_managed_python_without_dev_shell_leakage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "dev-venv"))

    managed_python = tmp_path / "managed" / "Scripts" / "python.exe"
    env = _launcher_environment(
        profile=_profile(tmp_path),
        workspace=tmp_path,
        venv_python=managed_python,
        wsl_command="wsl.exe",
        onlyoffice_mode="disabled",
        container_runtime="docker",
        local_server_mode="disabled",
        local_coolify_mode="disabled",
    )

    assert env["MAIN_COMPUTER_PYTHON_COMMAND"] == str(managed_python)
    assert env["MAIN_COMPUTER_CONTAINER_RUNTIME"] == "docker"
    assert "VIRTUAL_ENV" not in env
