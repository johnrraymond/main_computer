from __future__ import annotations

import json
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_generated_package_declares_the_completed_semantic_runtime_template() -> None:
    manifest = json.loads((PACKAGE_ROOT / "mcel.app.json").read_text(encoding="utf-8"))
    requirements = (PACKAGE_ROOT / "requirements.md").read_text(encoding="utf-8")

    assert manifest["conformance"]["currentMode"] == "semantic-runtime-proven"
    assert manifest["conformance"]["missingBridges"] == []
    assert "package-local-acceptance-discovery" not in manifest["conformance"]["missingBridges"]
    assert "operation-linked-browser-observation" not in manifest["conformance"]["missingBridges"]
    assert "current_runtime_status: semantic-runtime-proven" in requirements
    assert "semantic-runtime-proven" == manifest["conformance"]["targetMode"]
