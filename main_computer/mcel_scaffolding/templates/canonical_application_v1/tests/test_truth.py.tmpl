from __future__ import annotations

import json
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_generated_package_does_not_claim_unearned_semantic_runtime_proof() -> None:
    manifest = json.loads((PACKAGE_ROOT / "mcel.app.json").read_text(encoding="utf-8"))
    requirements = (PACKAGE_ROOT / "requirements.md").read_text(encoding="utf-8")

    assert manifest["conformance"]["currentMode"] == "structural-only"
    assert manifest["conformance"]["missingBridges"]
    assert "current_runtime_status: structural-only" in requirements
    assert "semantic-runtime-proven" == manifest["conformance"]["targetMode"]
