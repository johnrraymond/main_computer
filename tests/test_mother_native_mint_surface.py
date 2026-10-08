from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_mother_harness_delegates_native_mint_help_to_native_surface() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "mother_mutate_harness.py"), "native-mint", "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "--amount-native" in proc.stdout
    assert "--yes-i-know-this-mutates-target-host" in proc.stdout
    assert "--node" not in proc.stdout


def test_mother_harness_exposes_native_mint_status() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "mother_mutate_harness.py"), "native-mint-status", "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "native-mint-status" in proc.stdout


def test_mother_harness_exposes_native_mint_cleanup_without_native_harness_module() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "mother_mutate_harness.py"), "native-mint", "--cleanup", "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "Delete leftover ephemeral Mother native-mint helper services" in proc.stdout
    assert "--dry-run" in proc.stdout
    assert "--network" in proc.stdout
