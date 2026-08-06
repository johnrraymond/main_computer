from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from main_computer.mcel_app_promote import rehearse_application_promotion
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_calculator_candidate_evidence import CalculatorCandidateEvidenceResult
from main_computer.mcel_calculator_promotion_rehearsal import (
    _apply_plan,
    _restore_live_package,
    _tree_snapshot,
    rehearse_calculator_promotion,
)
from main_computer.mcel_dsl_compiler import compile_dsl_application


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "mcel_apps/calculator"


def _fake_evidence(**kwargs: Any) -> CalculatorCandidateEvidenceResult:
    repo = Path(kwargs.get("repo_root") or ROOT).resolve()
    compiled = compile_dsl_application(repo / "mcel_apps/calculator/application.js", write_candidate=False)
    assert compiled.valid and compiled.normalized_ir is not None
    output = Path(kwargs.get("report_root") or repo / "runtime/reports/mcel-compiler-candidates") / "calculator/fake"
    report = {
        "schema": "mcel.calculator-candidate-evidence-report.v1",
        "version": "test-double",
        "appId": "calculator",
        "valid": True,
        "status": "pass",
        "truthStatus": "fresh-browser-shadow-ir-native-parity",
        "candidate": {
            "semanticFingerprint": compiled.semantic_fingerprint,
            "sourceBindingFingerprint": compiled.source_binding_fingerprint,
            "repositoryProvenance": {"fingerprint": "sha256:test"},
            "packageFingerprint": "sha256:test-package",
            "catalogFingerprint": "sha256:test-catalog",
            "candidateDirectory": "runtime/state/mcel/compiler-candidates/calculator/test",
        },
        "stages": {
            "dslCompilation": {"status": "pass"},
            "candidateProjection": {"status": "pass"},
            "packageValidation": {"status": "pass"},
            "runtimeProjection": {"status": "pass"},
            "generatedLegacyParity": {"status": "pass"},
            "freshBrowserParity": {"status": "pass"},
            "irNativeShadowProof": {"status": "pass"},
            "repositoryBinding": {"status": "pass"},
            "livePackageUnchanged": {"status": "pass"},
        },
        "authority": {
            "liveAuthority": "existing-html-calculator-runtime",
            "candidateAuthority": "mcel.dsl.shadow.v1",
            "hostBoundRuntimeActive": True,
            "legacySemanticAdapterRemainsLive": True,
            "liveCalculatorChanged": False,
            "contractsGeneratedInCandidate": True,
            "candidatePromoted": False,
            "promotionEligible": False,
            "freshChromiumObservation": True,
        },
    }
    return CalculatorCandidateEvidenceResult(True, "pass", report, (), output)


def _package_snapshot() -> dict[str, bytes]:
    return {
        path.relative_to(PACKAGE).as_posix(): path.read_bytes()
        for path in PACKAGE.rglob("*")
        if path.is_file() and path.suffix not in {".pyc", ".pyo"}
    }


def test_calculator_promotion_rehearsal_builds_non_mutating_plan(tmp_path: Path) -> None:
    before = _package_snapshot()
    result = rehearse_calculator_promotion(
        repo_root=ROOT,
        candidate_root=tmp_path / "candidates",
        evidence_report_root=tmp_path / "evidence",
        report_root=tmp_path / "reports",
        evidence_runner=_fake_evidence,
    )
    after = _package_snapshot()
    payload = result.to_dict()

    assert result.valid is True
    assert payload["promotionRehearsal"] == "pass"
    assert payload["postPromotionTruthStatus"] == "semantic-runtime-proven"
    assert payload["promotionEligible"] is True
    assert payload["promotionExecuted"] is False
    assert payload["rollbackRehearsal"] == "pass"
    assert payload["rollbackRestoration"] == "exact"
    assert payload["liveRepositoryChanged"] is False
    assert payload["authority"]["sourceAuthorityAfter"] == "mcel.dsl.v1"
    assert payload["authority"]["legacySemanticAdapterRemainsLive"] is True
    assert payload["authority"]["contractsWrittenToSourceTree"] is False
    assert len(payload["promotionMaterial"]["plan"]["files"]) == 1
    assert payload["promotionMaterial"]["plan"]["files"][0]["path"] == "mcel_apps/calculator/mcel.app.json"
    assert before == after
    assert result.output_directory is not None
    assert (result.output_directory / "mcel-calculator-promotion-rehearsal-report.json").is_file()
    assert (result.output_directory / "promotion-plan.json").is_file()


def test_calculator_promotion_plan_apply_and_rollback_restore_package_exactly(tmp_path: Path) -> None:
    result = rehearse_calculator_promotion(
        repo_root=ROOT,
        candidate_root=tmp_path / "candidates",
        evidence_report_root=tmp_path / "evidence",
        report_root=tmp_path / "reports",
        evidence_runner=_fake_evidence,
    )
    payload = result.to_dict()
    plan = payload["promotionMaterial"]["plan"]
    promoted = {
        relative: (result.output_directory / "promoted-files" / relative).read_bytes()
        for relative in payload["promotionMaterial"]["files"]
    }

    workspace = tmp_path / "workspace"
    (workspace / "mcel_apps").mkdir(parents=True)
    shutil.copytree(PACKAGE, workspace / "mcel_apps/calculator")
    before = _tree_snapshot(workspace / "mcel_apps/calculator")
    _apply_plan(workspace, plan, promoted)

    promoted_manifest = json.loads((workspace / "mcel_apps/calculator/mcel.app.json").read_text(encoding="utf-8"))
    assert promoted_manifest["authoring"]["status"] == "dsl-authoritative"
    assert promoted_manifest["conformance"]["shadow"] is False
    assert promoted_manifest["conformance"]["missingBridges"] == []
    assert promoted_manifest["promotion"]["promotionEligible"] is True
    assert "contracts" not in {path.name for path in (workspace / "mcel_apps/calculator").iterdir()}

    _restore_live_package(workspace / "mcel_apps/calculator", before)
    assert _tree_snapshot(workspace / "mcel_apps/calculator") == before


def test_calculator_promoted_manifest_materializes_host_bound_projection(tmp_path: Path) -> None:
    result = rehearse_calculator_promotion(
        repo_root=ROOT,
        candidate_root=tmp_path / "candidates",
        evidence_report_root=tmp_path / "evidence",
        report_root=tmp_path / "reports",
        evidence_runner=_fake_evidence,
    )
    promoted_manifest = result.output_directory / "promoted-files/mcel_apps/calculator/mcel.app.json"

    workspace = tmp_path / "promoted-repo"
    shutil.copytree(ROOT, workspace, ignore=shutil.ignore_patterns("runtime", "__pycache__", ".pytest_cache", "*.pyc", "*.pyo"))
    target = workspace / "mcel_apps/calculator/mcel.app.json"
    target.write_bytes(promoted_manifest.read_bytes())
    runtime = build_runtime_projection_set(workspace)
    records = [record for record in runtime.projections if record.app_id == "calculator"]

    assert len(records) == 1
    assert records[0].mount_mode == "host-bound"
    assert records[0].host_route == "/applications/calculator"
    assert records[0].root_selector == "#calculator-app"
    assert records[0].runtime_facade == "MainComputerCalculatorRuntime"


def test_generic_application_promotion_dispatches_calculator_rehearsal(tmp_path: Path) -> None:
    result = rehearse_application_promotion(
        app_id="calculator",
        repo_root=ROOT,
        candidate_root=tmp_path / "candidates",
        evidence_report_root=tmp_path / "evidence",
        report_root=tmp_path / "reports",
        evidence_runner=_fake_evidence,
    )
    payload = result.to_dict()["result"]

    assert result.valid is True
    assert payload["promotionRehearsal"] == "pass"
    assert payload["promotionEligible"] is True
    assert payload["promotionExecuted"] is False
