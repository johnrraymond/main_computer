from __future__ import annotations

import sys
import time

from main_computer.bootstrap.process import run_command


def test_run_command_returns_when_parent_exits_even_if_descendant_keeps_stdout_open() -> None:
    """Detached app processes can inherit stdout; the installer must not wait on them."""

    child_code = (
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-S', '-c', 'import time; time.sleep(3)'])\n"
        "print('parent done', flush=True)\n"
    )

    started = time.monotonic()
    result = run_command([sys.executable, "-S", "-c", child_code], timeout_seconds=10)
    elapsed = time.monotonic() - started

    assert result.exit_code == 0
    assert "parent done" in result.output
    assert elapsed < 2.0
