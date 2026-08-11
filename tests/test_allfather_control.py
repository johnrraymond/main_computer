from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RETIREMENT_MESSAGE = (
    "ERROR: Allfather scripts are retired. Allfather has been replaced by the Mother scripts. "
    "Use tools/mother_deploy.py and tools/mother/* for lifecycle operations."
)


@pytest.mark.parametrize(
    "script",
    [
        "tools/allfather_control.py",
        "tools/allfather_control_1_2.py",
        "tools/hub_propagate_checkpointed.py",
    ],
)
def test_allfather_control_entrypoints_fail_closed(script: str) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / script), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert RETIREMENT_MESSAGE in result.stderr
