"""Live worker.js regression probes using a mocked provider; no secrets/network."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_worker_rpc_transition_with_node() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for the actual Worker JavaScript regression test")
    script = Path(__file__).with_name("worker_rpc_transition.test.cjs")
    result = subprocess.run(
        [node, "--test", str(script)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
