"""The read-only SEH contract Twiddle is part of automated regression coverage."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_compiled_seh_deployment_result_matches_real_adapter():
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(root / 'tools/twiddle_seh_deploy_response_contract.py')],
        cwd=root, text=True, capture_output=True, check=True, timeout=35,
    )
    assert 'PASS: 6 operations, actual compiled DSL and JS runtime validator' in completed.stdout
