from __future__ import annotations

import json
from pathlib import Path

from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_package_test_support import logical_package_text


PACKAGE = Path(__file__).resolve().parents[1]


def test_semantic_runtime_package_is_structurally_valid() -> None:
    catalog = build_application_package_catalog(Path(__file__).resolve().parents[3])
    record = next(item for item in catalog.packages if item.app_id == "contract-workbench")
    assert record.valid, [issue.to_dict() for issue in record.errors]


def test_package_declares_human_authoring_authority_and_zero_bridges() -> None:
    manifest = json.loads((PACKAGE / "mcel.app.json").read_text(encoding="utf-8"))
    authoring = manifest["authoring"]
    if authoring.get("status") == "dsl-authoritative":
        assert authoring == {
            "schema": "mcel.application-authoring.v1",
            "status": "dsl-authoritative",
            "source": "application.js",
            "ownership": "mcel.generated.json",
            "normalizedDefinition": "generated/mcel.application.normalized.json",
        }
        ownership = json.loads(logical_package_text("contract-workbench", "mcel.generated.json"))
        assert ownership["sourceAuthority"]["kind"] == "mcel.dsl.v1"
        assert ownership["manualEditsProhibited"] is True
        assert len(ownership["generatedFiles"]) == 8
    else:
        assert authoring == {
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
