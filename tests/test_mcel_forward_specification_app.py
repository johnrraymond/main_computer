from __future__ import annotations

import json
from pathlib import Path

from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_scaffolding.package_validator import validate_package_path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "mcel_apps/contract-workbench"


def test_former_forward_specification_is_now_a_valid_proven_package() -> None:
    result = validate_package_path(PACKAGE, expected_app_id="contract-workbench")
    assert result.ok, [issue.to_dict() for issue in result.errors]
    manifest = json.loads((PACKAGE / "mcel.app.json").read_text(encoding="utf-8"))
    assert manifest["conformance"] == {
        "currentMode": "semantic-runtime-proven",
        "missingBridges": [],
        "targetMode": "semantic-runtime-proven",
    }


def test_repository_catalog_preserves_human_authoring_authority_after_promotion() -> None:
    catalog = build_application_package_catalog(ROOT)
    record = next(item for item in catalog.packages if item.app_id == "contract-workbench")
    assert record.valid is True
    assert record.authoring == {
        "definition": "mcel_apps/contract-workbench/application.js",
        "featureMatrix": "mcel_apps/contract-workbench/forward-specification.json",
        "normalizedDefinition": "mcel_apps/contract-workbench/generated/mcel.application.normalized.json",
    }
    assert record.conformance["currentMode"] == "semantic-runtime-proven"
    assert record.conformance["missingBridges"] == []


def test_promoted_app_projects_as_semantic_runtime() -> None:
    projection_set = build_runtime_projection_set(ROOT)
    projection = next(item for item in projection_set.projections if item.app_id == "contract-workbench")
    assert projection.manifest["conformance"]["currentMode"] == "semantic-runtime-proven"
    assert projection.manifest["conformance"]["missingBridges"] == []
