from __future__ import annotations

import json
from pathlib import Path

from main_computer.mcel_scaffolding.package_validator import validate_package_path


PACKAGE = Path(__file__).resolve().parents[1]


def test_semantic_runtime_package_is_structurally_valid() -> None:
    result = validate_package_path(PACKAGE, expected_app_id="contract-workbench")
    assert result.ok, [issue.to_dict() for issue in result.errors]


def test_package_declares_human_authoring_authority_and_zero_bridges() -> None:
    manifest = json.loads((PACKAGE / "mcel.app.json").read_text(encoding="utf-8"))
    assert manifest["authoring"] == {
        "schema": "mcel.application-definition.v1",
        "status": "semantic-runtime-proven",
        "definition": "application.js",
        "featureMatrix": "forward-specification.json",
        "normalizedDefinition": "generated/mcel.application.normalized.json",
    }
    assert manifest["conformance"] == {
        "currentMode": "semantic-runtime-proven",
        "missingBridges": [],
        "targetMode": "semantic-runtime-proven",
    }
    assert manifest["normalization"]["schema"] == "mcel.application-definition-normalization.v1"
