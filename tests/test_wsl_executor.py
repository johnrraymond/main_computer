from __future__ import annotations

import io
import subprocess
from pathlib import Path

from main_computer.executor_models import ExecutorRequest
from main_computer.wsl_executor import WslExecutor


def test_wsl_executor_uploads_match_docker_visible_paths(tmp_path: Path) -> None:
    executor = WslExecutor(
        distribution="MainComputerExecutorTest",
        runtime_root=tmp_path / "runtime",
        enabled=False,
        max_upload_bytes=1024,
    )

    record = executor.save_upload(
        filename="../unsafe name.csv",
        stream=io.BytesIO(b"a,b\n1,2\n"),
        content_length=8,
        mime_type="text/csv",
    )

    assert record.id.startswith("upload_")
    assert record.filename == "unsafe name.csv"
    assert record.container_path == f"/inputs/{record.id}/payload.bin"
    assert (executor.inputs_root / record.id / "payload.bin").read_bytes() == b"a,b\n1,2\n"


def test_wsl_status_exercises_runtime_entrypoint_contract(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    fake_wsl = tmp_path / "wsl.exe"
    fake_wsl.write_text("", encoding="utf-8")
    fake_wsl.chmod(0o755)

    def fake_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="main-computer-wsl-ok\nentrypoint-ok\nentrypoint-contract-ok\n",
            stderr="",
        )

    executor = WslExecutor(
        distribution="MainComputerExecutorTest",
        wsl_command=str(fake_wsl),
        runtime_root=tmp_path / "runtime",
        enabled=True,
        runner=fake_runner,
    )

    status = executor.status()

    assert status["ok"] is True
    assert status["distribution_available"] is True
    assert status["entrypoint_available"] is True
    assert status["entrypoint_contract_ok"] is True

    command = calls[0]
    assert command[:3] == [str(fake_wsl), "--distribution", "MainComputerExecutorTest"]
    assert command[3:6] == ["--exec", "/bin/sh", "-lc"]
    shell_script = command[-1]
    assert "/usr/local/bin/main-computer-exec run" in shell_script
    assert "--cwd /workspace" in shell_script
    assert "--timeout-ms 5000" in shell_script
    assert "--artifact-dir /outputs" in shell_script
    assert "echo main-computer-exec-ready" in shell_script
    assert "entrypoint-contract-ok" in shell_script


def test_wsl_status_reports_entrypoint_contract_failure(tmp_path: Path) -> None:
    fake_wsl = tmp_path / "wsl.exe"
    fake_wsl.write_text("", encoding="utf-8")
    fake_wsl.chmod(0o755)

    def fake_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            2,
            stdout="main-computer-wsl-ok\nentrypoint-ok\n",
            stderr="exec: --: invalid option\n",
        )

    executor = WslExecutor(
        distribution="MainComputerExecutorTest",
        wsl_command=str(fake_wsl),
        runtime_root=tmp_path / "runtime",
        enabled=True,
        runner=fake_runner,
    )

    status = executor.status()

    assert status["ok"] is False
    assert status["distribution_available"] is True
    assert status["entrypoint_available"] is True
    assert status["entrypoint_contract_ok"] is False
    assert "invalid option" in status["wsl_error"]


def test_wsl_executor_builds_wsl_command_and_collects_artifacts(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    runtime_root = tmp_path / "runtime"

    def fake_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        output_dirs = list((runtime_root / "outputs").iterdir())
        assert len(output_dirs) == 1
        (output_dirs[0] / "result.txt").write_text("artifact\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="hello\n", stderr="")

    executor = WslExecutor(
        distribution="MainComputerExecutorTest",
        wsl_command="wsl.exe",
        runtime_root=runtime_root,
        enabled=True,
        runner=fake_runner,
    )

    result = executor.run(
        ExecutorRequest(
            command="python - <<'PY'\nprint('hello')\nPY",
            cwd="/workspace",
            env={"SAFE_NAME": "value"},
            network=False,
        )
    )

    assert result.ok is True
    assert result.exit_code == 0
    assert result.stdout == "hello\n"
    assert result.backend == "wsl"
    assert result.artifacts
    assert result.artifacts[0].download_url.endswith("/result.txt")

    command = calls[0]
    assert command[:3] == ["wsl.exe", "--distribution", "MainComputerExecutorTest"]
    assert command[3:6] == ["--exec", "/bin/sh", "-lc"]
    shell_script = command[-1]
    assert "ln -s" in shell_script
    assert "/inputs" in shell_script
    assert "/outputs" in shell_script
    assert "/workspace" in shell_script
    assert "export SAFE_NAME=value" in shell_script
    assert "/usr/local/bin/main-computer-exec run" in shell_script
    assert "--cwd /workspace" in shell_script
    assert "--timeout-ms 60000" in shell_script
    assert "--artifact-dir /outputs" in shell_script
    assert "python -" in shell_script


def test_wsl_executor_disabled_error_mentions_backend_switch(tmp_path: Path) -> None:
    executor = WslExecutor(
        distribution="MainComputerExecutorTest",
        runtime_root=tmp_path / "runtime",
        enabled=False,
    )

    result = executor.run(ExecutorRequest(command="echo hi"))

    assert result.ok is False
    assert result.error is not None
    assert "MAIN_COMPUTER_EXECUTOR_BACKEND=wsl" in result.error
