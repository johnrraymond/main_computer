from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
PACKAGE = Path(__file__).resolve().parents[1]
APPLICATION = PACKAGE / "application.js"
LIBRARY = ROOT / "main_computer/web/applications/scripts/mcel-app-definition.js"


def _node() -> str:
    resolved = os.environ.get("MCEL_NODE_EXECUTABLE", "").strip() or shutil.which("node") or ""
    if not resolved:
        pytest.skip("Node.js is unavailable.")
    return resolved


def test_historical_feature_matrix_records_complete_platform_convergence() -> None:
    manifest = json.loads((PACKAGE / "mcel.app.json").read_text(encoding="utf-8"))
    matrix = json.loads((PACKAGE / "forward-specification.json").read_text(encoding="utf-8"))
    completed = subprocess.run(
        [
            _node(),
            "-e",
            f"const m=require({json.dumps(str(LIBRARY))});"
            f"const app=require({json.dumps(str(APPLICATION))});"
            "process.stdout.write(JSON.stringify(m.inspect(app).requiredRuntimeFeatures));",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=45,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    inspected = set(json.loads(completed.stdout))
    implemented_ids = {entry["id"] for entry in matrix["implementedFeatures"]}
    assert matrix["features"] == []
    assert manifest["conformance"]["missingBridges"] == []
    assert inspected <= implemented_ids
    assert "intent-complete-proof" in implemented_ids
    assert len(implemented_ids) == 16
