from __future__ import annotations

from pathlib import Path

from main_computer.mcel_app_compile import compile_application
from main_computer.mcel_app_project import project_application
from main_computer.mcel_app_promote import inspect_application_authority

from mcel_dsl_authoring_harness import require_profile_backed_surface_bundle_case


ROOT = Path(__file__).resolve().parents[1]


def _generic_authoring_app_id() -> str:
    return require_profile_backed_surface_bundle_case(ROOT).app_id


def test_generic_compile_uses_live_authoritative_source_without_fixture_app_name() -> None:
    app_id = _generic_authoring_app_id()

    result = compile_application(app_id=app_id, repo_root=ROOT)
    payload = result.to_dict()

    assert result.valid
    assert payload["appId"] == app_id
    assert payload["genericPipeline"] is True
    assert payload["counterSpecificExecutionPathRequired"] is False
    assert payload["semanticFingerprint"].startswith("sha256:")
    assert payload["source"] == f"mcel_apps/{app_id}/application.js"


def test_generic_projection_uses_registered_application_profile_without_fixture_app_name() -> None:
    app_id = _generic_authoring_app_id()

    result = project_application(app_id=app_id, repo_root=ROOT)
    payload = result.to_dict()

    assert result.valid
    assert payload["appId"] == app_id
    assert payload["projectionProfile"].startswith("mcel.")
    assert payload["counterSpecificExecutionPathRequired"] is False
    assert payload["semanticFingerprint"].startswith("sha256:")


def test_generic_promotion_inspection_reports_promoted_authority_without_fixture_app_name() -> None:
    app_id = _generic_authoring_app_id()

    result = inspect_application_authority(app_id=app_id, repo_root=ROOT)
    payload = result.to_dict()

    assert result.valid
    assert payload["appId"] == app_id
    assert payload["status"] == "promoted"
    assert payload["sourceAuthority"] == "mcel.dsl.v1"
    assert payload["counterSpecificExecutionPathRequired"] is False
