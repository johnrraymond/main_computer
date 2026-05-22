from __future__ import annotations

import io
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from main_computer.executor_models import ExecutorRequest
from main_computer.wsl_executor import WslExecutor


DEFAULT_DISTRIBUTION = "MainComputerExecutorTest"
DISTRIBUTION_ENV = "MAIN_COMPUTER_WSL_INTEGRATION_DISTRIBUTION"
WSL_COMMAND_ENV = "MAIN_COMPUTER_WSL_INTEGRATION_COMMAND"


def _configured_distribution() -> str:
    return os.environ.get(DISTRIBUTION_ENV, DEFAULT_DISTRIBUTION).strip() or DEFAULT_DISTRIBUTION


def _configured_wsl_command() -> str:
    return os.environ.get(WSL_COMMAND_ENV, "wsl.exe").strip() or "wsl.exe"


def _require_real_wsl_test_distribution() -> tuple[str, str]:
    """Return the WSL command and distro name, or skip when the local test executor is absent."""

    wsl_command = _configured_wsl_command()
    distribution = _configured_distribution()

    if shutil.which(wsl_command) is None and not Path(wsl_command).exists():
        pytest.skip(f"{wsl_command!r} is not available; skipping real WSL executor integration test.")

    try:
        probe = subprocess.run(
            [
                wsl_command,
                "--distribution",
                distribution,
                "--exec",
                "/bin/sh",
                "-lc",
                "echo main-computer-wsl-integration-probe",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"{distribution} could not be probed through {wsl_command}: {exc}")

    if probe.returncode != 0 or "main-computer-wsl-integration-probe" not in (probe.stdout or ""):
        details = (probe.stderr or probe.stdout or "").strip()
        if len(details) > 500:
            details = details[:500] + "..."
        pytest.skip(
            f"{distribution} is not available as a real WSL test executor. "
            f"Create/import MainComputerExecutorTest, or set {DISTRIBUTION_ENV} to another explicit test distro. "
            f"Probe output: {details}"
        )

    return wsl_command, distribution


def test_wsl_executor_runs_real_command_and_collects_artifact(tmp_path: Path) -> None:
    """Exercise the shared executor contract against a real test WSL distro.

    This test is intentionally skipped unless MainComputerExecutorTest exists. It
    gives developers a real WSL executor smoke without making normal Docker or CI
    test runs depend on WSL.
    """

    wsl_command, distribution = _require_real_wsl_test_distribution()
    executor = WslExecutor(
        distribution=distribution,
        wsl_command=wsl_command,
        runtime_root=tmp_path / "runtime",
        enabled=True,
        max_timeout_s=20,
    )

    upload = executor.save_upload(
        filename="input.txt",
        stream=io.BytesIO(b"hello from uploaded input\n"),
        content_length=len(b"hello from uploaded input\n"),
        mime_type="text/plain",
    )

    result = executor.run(
        ExecutorRequest(
            command=(
                "set -e\n"
                "python3 - <<'PY'\n"
                "import os\n"
                "from pathlib import Path\n"
                "payload = Path('/inputs/{upload_id}/payload.bin').read_text(encoding='utf-8').strip()\n"
                "assert payload == 'hello from uploaded input'\n"
                "assert os.environ['MC_EXECUTOR_INTEGRATION'] == 'wsl'\n"
                "Path('/outputs/result.txt').write_text('artifact-ok\\n', encoding='utf-8')\n"
                "print('wsl-executor-integration-ok')\n"
                "print(payload)\n"
                "PY\n"
            ).format(upload_id=upload.id),
            input_ids=[upload.id],
            env={"MC_EXECUTOR_INTEGRATION": "wsl"},
            timeout_s=20,
            network=False,
        )
    )

    assert result.ok is True, result.stderr or result.error
    assert result.exit_code == 0
    assert "wsl-executor-integration-ok" in result.stdout
    assert "hello from uploaded input" in result.stdout
    assert result.artifacts

    artifact_path = executor.artifact_path(result.job_id, "result.txt")
    assert artifact_path.read_text(encoding="utf-8") == "artifact-ok\n"
    assert any(item.relative_path == "result.txt" for item in result.artifacts)
