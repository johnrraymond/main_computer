from __future__ import annotations

from pathlib import Path

from mcel_dsl_authoring_harness import (
    authoring_profile_for_case,
    package_file_snapshot,
    require_profile_backed_surface_bundle_case,
)


ROOT = Path(__file__).resolve().parents[1]


def test_generic_candidate_projection_dispatches_profile_backed_app_without_fixture_names(tmp_path: Path) -> None:
    case = require_profile_backed_surface_bundle_case(ROOT)
    profile = authoring_profile_for_case(case)
    package_path = ROOT / case.record.package_root

    result = profile.project_candidate(
        dsl_source_path=package_path / "application.js",
        live_package_root=package_path,
        candidate_root=tmp_path / "candidates",
        write_candidate=False,
    )
    payload = result.to_dict()

    assert result.valid is True
    assert payload["appId"] == case.app_id
    assert payload["projectionProfile"] == profile.projection_profile
    assert payload["source"]["semanticFingerprint"].startswith("sha256:")
    assert payload["projection"]["fileCount"] >= 1
    assert payload["diagnosticCount"] == 0


def test_generic_candidate_projection_write_mode_is_non_mutating(tmp_path: Path) -> None:
    case = require_profile_backed_surface_bundle_case(ROOT)
    profile = authoring_profile_for_case(case)
    package_path = ROOT / case.record.package_root
    before = package_file_snapshot(package_path)

    first = profile.project_candidate(
        dsl_source_path=package_path / "application.js",
        live_package_root=package_path,
        candidate_root=tmp_path / "candidates",
        write_candidate=True,
    )
    second = profile.project_candidate(
        dsl_source_path=package_path / "application.js",
        live_package_root=package_path,
        candidate_root=tmp_path / "candidates",
        write_candidate=True,
    )
    after = package_file_snapshot(package_path)

    assert first.valid is True
    assert second.valid is True
    assert first.to_dict()["projection"] == second.to_dict()["projection"]
    assert first.candidate_directory is not None
    assert (first.candidate_directory / "projections").is_dir()
    assert before == after


def test_generic_candidate_projection_report_only_mode_does_not_write_candidate(tmp_path: Path) -> None:
    case = require_profile_backed_surface_bundle_case(ROOT)
    profile = authoring_profile_for_case(case)
    package_path = ROOT / case.record.package_root

    result = profile.project_candidate(
        dsl_source_path=package_path / "application.js",
        live_package_root=package_path,
        candidate_root=tmp_path / "candidates",
        write_candidate=False,
    )

    assert result.valid is True
    assert result.candidate_directory is None
    assert not (tmp_path / "candidates").exists()
