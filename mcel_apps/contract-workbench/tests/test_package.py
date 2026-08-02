from __future__ import annotations

import json
from pathlib import Path

from main_computer.mcel_scaffolding.package_validator import validate_package_path


PACKAGE = Path(__file__).resolve().parents[1]


def test_forward_package_is_structurally_valid() -> None:
    result = validate_package_path(PACKAGE, expected_app_id="contract-workbench")
    assert result.ok, [issue.to_dict() for issue in result.errors]


def test_forward_package_declares_human_authoring_authority() -> None:
    manifest = json.loads((PACKAGE / "mcel.app.json").read_text(encoding="utf-8"))
    assert manifest["authoring"] == {
        "schema": "mcel.application-definition.v1",
        "status": "forward-specification",
        "definition": "application.js",
        "featureMatrix": "forward-specification.json",
        "normalizedDefinition": "generated/mcel.application.normalized.json",
    }
    assert manifest["conformance"]["currentMode"] == "forward-specification"
    assert manifest["conformance"]["targetMode"] == "semantic-runtime-proven"
    assert manifest["conformance"]["missingBridges"]
    assert "application-definition-normalization" not in manifest["conformance"]["missingBridges"]
    assert manifest["normalization"]["schema"] == "mcel.application-definition-normalization.v1"
