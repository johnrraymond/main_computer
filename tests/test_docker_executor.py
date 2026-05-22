from __future__ import annotations

import io
import subprocess
from pathlib import Path

from main_computer.docker_executor import DockerExecutor
from main_computer.executor_models import ExecutorRequest


def test_executor_request_validates_workspace_cwd() -> None:
    request = ExecutorRequest.from_mapping(
        {
            "command": "python - <<'PY'\nprint('hi')\nPY",
            "cwd": "/workspace/project",
            "timeout_s": 999,
            "network": "false",
            "env": {"SAFE_NAME": "value", "BAD-NAME": "ignored"},
        },
        max_timeout_s=45,
    )

    assert request.cwd == "/workspace/project"
    assert request.timeout_s == 45
    assert request.network is False
    assert request.env == {"SAFE_NAME": "value"}


def test_docker_executor_builds_locked_down_docker_run_and_collects_artifacts(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        output_mount = next(command[index + 1] for index, item in enumerate(command) if item == "-v" and command[index + 1].endswith(":/outputs:rw"))
        # Docker volume specs on Windows look like C:\\path\\to\\outputs:/outputs:rw.
        # Split from the right so the drive-letter colon stays part of the host path.
        output_dir = Path(output_mount.rsplit(":", 2)[0])
        (output_dir / "result.txt").write_text("artifact\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="hello\n", stderr="")

    executor = DockerExecutor(
        image="main-computer-executor:test",
        runtime_root=tmp_path / "runtime",
        enabled=True,
        runner=fake_runner,
    )

    result = executor.run(ExecutorRequest(command="python -c \"print('hello')\"", network=False))

    assert result.ok is True
    assert result.exit_code == 0
    assert result.stdout == "hello\n"
    assert result.backend == "docker"
    assert result.artifacts
    assert result.artifacts[0].download_url.endswith("/result.txt")

    command = calls[0]
    assert command[:2] == ["docker", "run"]
    assert "--rm" in command
    assert "--network" in command
    assert command[command.index("--network") + 1] == "none"
    assert "--cap-drop" in command
    assert "ALL" in command
    assert "--security-opt" in command
    assert "no-new-privileges:true" in command
    assert "main-computer-executor:test" in command

    image_index = command.index("main-computer-executor:test")
    runtime_argv = command[image_index + 1 :]
    assert runtime_argv[:2] == ["/usr/local/bin/main-computer-exec", "run"]
    assert runtime_argv[runtime_argv.index("--cwd") + 1] == "/workspace"
    assert runtime_argv[runtime_argv.index("--timeout-ms") + 1] == "60000"
    assert runtime_argv[runtime_argv.index("--artifact-dir") + 1] == "/outputs"
    assert runtime_argv[runtime_argv.index("--") + 1] == "python -c \"print('hello')\""


def test_docker_executor_uploads_raw_stream_and_records_container_path(tmp_path: Path) -> None:
    executor = DockerExecutor(
        image="main-computer-executor:test",
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
    assert record.size == 8
    assert record.container_path == f"/inputs/{record.id}/payload.bin"
    assert (executor.inputs_root / record.id / "payload.bin").read_bytes() == b"a,b\n1,2\n"

    uploads = executor.list_uploads()
    assert uploads[0]["id"] == record.id
    assert uploads[0]["filename"] == "unsafe name.csv"
