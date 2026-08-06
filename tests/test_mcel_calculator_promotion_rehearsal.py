from __future__ import annotations

import json
from pathlib import Path

from main_computer.mcel_app_promote import inspect_application_authority, rehearse_application_promotion
from main_computer.mcel_calculator_promotion_rehearsal import (
    execute_calculator_promotion,
    rehearse_calculator_promotion,
)
from main_computer.mcel_dsl_compiler import compile_dsl_application


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "mcel_apps/calculator"


def _package_snapshot() -> dict[str, bytes]:
    return {
        path.relative_to(PACKAGE).as_posix(): path.read_bytes()
        for path in PACKAGE.rglob("*")
        if path.is_file() and path.suffix not in {".pyc", ".pyo"}
    }


def test_calculator_manifest_is_authoritative_after_finalization() -> None:
    manifest = json.loads((PACKAGE / "mcel.app.json").read_text(encoding="utf-8"))
    assert manifest["authoring"]["status"] == "dsl-authoritative"
    assert manifest["conformance"]["shadow"] is False
    assert manifest["conformance"]["missingBridges"] == []
    assert manifest["conformance"]["legacySemanticAdapterRetired"] is True
    assert manifest["promotion"]["promotionEligible"] is True
    assert manifest["promotion"]["promotionExecuted"] is True
    assert not (PACKAGE / "contracts").exists()
    assert not (PACKAGE / "generated").exists()


def test_calculator_rehearsal_dispatch_reports_already_promoted_without_mutating_package(tmp_path: Path) -> None:
    before = _package_snapshot()
    result = rehearse_calculator_promotion(
        repo_root=ROOT,
        candidate_root=tmp_path / "candidates",
        evidence_report_root=tmp_path / "evidence",
        report_root=tmp_path / "reports",
    )
    after = _package_snapshot()
    payload = result.to_dict()

    assert result.valid is True
    assert result.status == "already-promoted"
    assert payload["promotionRehearsal"] == "pass"
    assert payload["postPromotionTruthStatus"] == "semantic-runtime-proven"
    assert payload["promotionEligible"] is True
    assert payload["promotionExecuted"] is True
    assert payload["rollbackRestoration"] == "exact"
    assert payload["authority"]["sourceAuthorityBefore"] == "mcel.dsl.v1"
    assert payload["authority"]["sourceAuthorityAfter"] == "mcel.dsl.v1"
    assert payload["authority"]["legacySemanticAdapterRemainsLive"] is False
    assert payload["authority"]["legacySemanticAdapterRetired"] is True
    assert payload["authority"]["contractsWrittenToSourceTree"] is False
    assert payload["promotionMaterial"]["plan"]["files"] == []
    assert before == after
    assert result.output_directory is not None


def test_generic_calculator_promotion_dispatch_inspects_authoritative_state() -> None:
    inspected = inspect_application_authority(app_id="calculator", repo_root=ROOT)
    payload = inspected.to_dict()

    assert inspected.valid is True
    assert payload["status"] == "promoted"
    assert payload["sourceAuthority"] == "mcel.dsl.v1"
    assert payload["promotionExecuted"] is True
    assert payload["promotionSupported"] is True
    assert payload["promotionRehearsalSupported"] is True


def test_generic_rehearsal_dispatch_is_safe_after_authority_flip(tmp_path: Path) -> None:
    result = rehearse_application_promotion(
        app_id="calculator",
        repo_root=ROOT,
        candidate_root=tmp_path / "candidates",
        evidence_report_root=tmp_path / "evidence",
        report_root=tmp_path / "reports",
    )
    payload = result.to_dict()["result"]

    assert result.valid is True
    assert payload["status"] == "already-promoted"
    assert payload["promotionExecuted"] is True
    assert payload["promotionMaterial"]["plan"]["files"] == []


def test_execute_calculator_promotion_reports_live_authoritative_manifest() -> None:
    compiled = compile_dsl_application(ROOT / "mcel_apps/calculator/application.js", write_candidate=False)
    assert compiled.valid is True

    result = execute_calculator_promotion(repo_root=ROOT)
    payload = result.to_dict()

    assert result.valid is True
    assert payload["status"] == "pass"
    assert payload["promotionExecuted"] is True
    assert payload["promotionEligible"] is True
    assert payload["sourceAuthority"] == "mcel.dsl.v1"
    assert payload["legacySemanticAdapterRetired"] is True
    assert payload["contractsWrittenToSourceTree"] is False
