from __future__ import annotations

import json
from pathlib import Path

from main_computer import mcel_app_prove
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_scaffolding.package_validator import validate_package_path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "mcel_apps/contract-workbench"


def test_forward_specification_is_a_valid_non_proven_package_mode() -> None:
    result = validate_package_path(PACKAGE, expected_app_id="contract-workbench")
    assert result.ok, [issue.to_dict() for issue in result.errors]
    manifest = json.loads((PACKAGE / "mcel.app.json").read_text(encoding="utf-8"))
    assert manifest["conformance"]["currentMode"] == "forward-specification"
    assert manifest["conformance"]["missingBridges"]


def test_repository_catalog_exposes_the_authoring_source_without_promoting_the_app() -> None:
    catalog = build_application_package_catalog(ROOT)
    record = next(item for item in catalog.packages if item.app_id == "contract-workbench")
    assert record.valid is True
    assert record.authoring == {
        "definition": "mcel_apps/contract-workbench/application.js",
        "featureMatrix": "mcel_apps/contract-workbench/forward-specification.json",
    }
    assert record.conformance["currentMode"] == "forward-specification"


def test_forward_app_can_be_projected_for_inspection_but_not_proven() -> None:
    projection_set = build_runtime_projection_set(ROOT)
    projection = next(item for item in projection_set.projections if item.app_id == "contract-workbench")
    assert projection.manifest["conformance"]["currentMode"] == "forward-specification"
    try:
        mcel_app_prove.run_app_proof(repo=ROOT, app_id="contract-workbench")
    except mcel_app_prove.AppProofError as exc:
        assert "forward specification" in str(exc)
        assert "unresolved bridges" in str(exc)
    else:
        raise AssertionError("Forward specification was incorrectly eligible for proof.")
