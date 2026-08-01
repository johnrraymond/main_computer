from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_forward_specification_cannot_be_false_promoted() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "main_computer/mcel_app_prove.py",
            "--app",
            "contract-workbench",
            "--check",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=45,
    )
    assert completed.returncode == 1
    output = completed.stdout + completed.stderr
    assert "forward specification" in output
    assert "unresolved bridges" in output
