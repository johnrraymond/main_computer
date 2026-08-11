from __future__ import annotations

from pathlib import Path

from main_computer.mcel_app_promote import (
    inspect_application_authority,
    rehearse_application_promotion,
)
from mcel_dsl_authoring_harness import require_profile_backed_surface_bundle_case


ROOT = Path(__file__).resolve().parents[1]


def _package_snapshot(package: Path) -> dict[str, bytes]:
    return {
        path.relative_to(package).as_posix(): path.read_bytes()
        for path in package.rglob("*")
        if path.is_file() and path.suffix not in {".pyc", ".pyo"}
    }


def test_generic_promotion_dispatch_inspects_promoted_profile_backed_app() -> None:
    case = require_profile_backed_surface_bundle_case(ROOT)
    inspected = inspect_application_authority(app_id=case.app_id, repo_root=ROOT)
    payload = inspected.to_dict()

    assert inspected.valid is True
    assert payload["status"] == "promoted"
    assert payload["appId"] == case.app_id
    assert payload["sourceAuthority"] == "mcel.dsl.v1"
    assert payload["genericPipeline"] is True
    assert payload["promotionSupported"] is True
    assert payload["promotionRehearsalSupported"] is True
    assert payload["promotionExecuted"] is True


def test_generic_promotion_rehearsal_for_promoted_app_is_non_mutating(tmp_path: Path) -> None:
    case = require_profile_backed_surface_bundle_case(ROOT)
    package = ROOT / case.record.package_root
    before = _package_snapshot(package)

    result = rehearse_application_promotion(
        app_id=case.app_id,
        repo_root=ROOT,
        candidate_root=tmp_path / "candidates",
        evidence_report_root=tmp_path / "evidence",
        report_root=tmp_path / "reports",
    )
    after = _package_snapshot(package)
    payload = result.to_dict()["result"]

    assert result.valid is True
    assert payload["status"] == "already-promoted"
    assert payload["promotionExecuted"] is True
    assert payload["rollbackRestoration"] == "exact"
    assert payload["promotionMaterial"]["plan"]["files"] == []
    assert before == after
