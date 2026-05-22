from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_blog_runtime_diagnostics_twiddle_help_compiles() -> None:
    script = ROOT / "tools" / "local-platform" / "diagnose-blog-runtime.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "Diagnose Configure Blog Runtime results" in completed.stdout
    assert "--verify-directus" in completed.stdout


def test_blog_runtime_diagnostics_twiddle_reports_missing_site_json() -> None:
    script = ROOT / "tools" / "local-platform" / "diagnose-blog-runtime.py"
    completed = subprocess.run(
        [sys.executable, str(script), "missing-site-for-diagnostics"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["site_id"] == "missing-site-for-diagnostics"
    assert "Unknown website project" in payload["error"]
