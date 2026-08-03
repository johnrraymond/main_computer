from __future__ import annotations

import json
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1]


def test_package_is_eligible_for_semantic_runtime_proof() -> None:
    manifest = json.loads((PACKAGE / "mcel.app.json").read_text(encoding="utf-8"))
    requirements = (PACKAGE / "requirements.md").read_text(encoding="utf-8")
    matrix = json.loads((PACKAGE / "forward-specification.json").read_text(encoding="utf-8"))
    assert manifest["conformance"]["currentMode"] == "semantic-runtime-proven"
    assert manifest["conformance"]["missingBridges"] == []
    assert "current_runtime_status: semantic-runtime-proven" in requirements
    assert "status: verified" in requirements
    assert matrix["features"] == []
