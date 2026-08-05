from __future__ import annotations

import json
import subprocess
from pathlib import Path

from main_computer.mcel_app_promote import rehearse_application_promotion
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_workbench_candidate_evidence import WorkbenchCandidateEvidenceResult
from main_computer.mcel_workbench_candidate_projection import (
    DEFAULT_DSL_SOURCE,
    DEFAULT_FIXTURE_IR,
    project_workbench_candidate,
)
from main_computer.mcel_workbench_ir_native_proof import run_workbench_ir_native_intent_proof
from main_computer.mcel_workbench_promotion_rehearsal import (
    _apply_plan,
    _build_promotion_plan,
    _restore_live_package,
    _tree_snapshot,
    _verify_generated_ownership,
    rehearse_workbench_promotion,
)

REPO = Path(__file__).resolve().parents[1]
LIVE = REPO / "mcel_apps/contract-workbench"
SEMANTIC = "sha256:3450eddcd5b67687fc09ff7589221fff5ef176efcc2d54231a9b43e2268ca78e"
SOURCE = "sha256:dd87dd149bff1e2271ecd926611f297f209d570358ab28613110b44a35633197"


def _fake_evidence(**_kwargs):
    report = {
        "schema": "mcel.workbench-candidate-evidence-report.v1",
        "valid": True,
        "status": "pass",
        "truthStatus": "semantic-runtime-proven",
        "candidate": {"semanticFingerprint": SEMANTIC, "sourceBindingFingerprint": SOURCE},
        "authority": {"evidenceReused": False, "liveAuthority": "legacy-explicit-package"},
    }
    return WorkbenchCandidateEvidenceResult(True, "pass", report, ())


def _fake_runner(command, *, cwd, **_kwargs):
    workspace = Path(cwd)
    joined = " ".join(str(value) for value in command)
    if "mcel_acceptance_runner.py" in joined:
        path = workspace / "runtime/reports/mcel-acceptance/apps/contract-workbench/mcel-acceptance-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"status": "pass", "passed": True}) + "\n", encoding="utf-8")
    elif "mcel_application_observation_runner.py" in joined:
        path = workspace / "runtime/reports/mcel-observation/apps/contract-workbench/mcel-operation-observation-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"status": "pass", "ok": True}) + "\n", encoding="utf-8")
    elif "mcel_app_prove.py" in joined:
        path = workspace / "runtime/reports/mcel-app-proof/apps/contract-workbench/mcel-app-proof-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "status": "pass",
                    "truthStatus": "semantic-runtime-proven",
                    "stages": {
                        "acceptanceEvidence": {"status": "pass"},
                        "browserObservation": {"status": "pass"},
                        "repositoryBinding": {"status": "exact"},
                    },
                    "intentCoverage": {
                        "status": "ir-native",
                        "passed": True,
                        "coverageMode": "authoritative-dsl-ir-runtime-convergence",
                        "declaredIntentCount": 7,
                        "coveredIntentCount": 7,
                        "declaredScenarioCount": 14,
                        "observedScenarioCount": 14,
                        "effectAccounting": {"status": "closed", "declaredEffectCount": 18},
                        "capabilityAccounting": {"status": "closed", "declaredCapabilityCount": 1},
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
    return subprocess.CompletedProcess(command, 0, stdout="pass\n")


def test_workbench_promotion_plan_declares_dsl_authority_and_eight_generated_artifacts(tmp_path: Path) -> None:
    dsl = compile_dsl_application(REPO / DEFAULT_DSL_SOURCE, compare_ir_path=REPO / DEFAULT_FIXTURE_IR)
    projection = project_workbench_candidate(candidate_root=tmp_path / "candidates", write_candidate=True)
    assert dsl.valid and projection.valid and projection.candidate_directory
    plan, promoted = _build_promotion_plan(
        live_package=LIVE,
        candidate_package=projection.candidate_directory / "package/mcel_apps/contract-workbench",
        dsl_source=REPO / DEFAULT_DSL_SOURCE,
        semantic_fingerprint=SEMANTIC,
        source_binding_fingerprint=SOURCE,
        evidence=_fake_evidence().to_dict(),
    )
    assert plan["sourceAuthorityAfter"] == "mcel.dsl.v1"
    assert plan["derivedArtifactAuthorityAfter"] == "mcel.workbench.portable-ir-projection.v1"
    ownership = json.loads(promoted["mcel_apps/contract-workbench/mcel.generated.json"])
    assert ownership["manualEditsProhibited"] is True
    assert len(ownership["generatedFiles"]) == 8
    manifest = json.loads(promoted["mcel_apps/contract-workbench/mcel.app.json"])
    assert manifest["authoring"]["status"] == "dsl-authoritative"
    assert len(plan["files"]) == 11


def test_workbench_apply_and_rollback_restore_package_exactly(tmp_path: Path) -> None:
    dsl = compile_dsl_application(REPO / DEFAULT_DSL_SOURCE, compare_ir_path=REPO / DEFAULT_FIXTURE_IR)
    projection = project_workbench_candidate(candidate_root=tmp_path / "candidates", write_candidate=True)
    assert dsl.valid and projection.valid and projection.candidate_directory
    plan, promoted = _build_promotion_plan(
        live_package=LIVE,
        candidate_package=projection.candidate_directory / "package/mcel_apps/contract-workbench",
        dsl_source=REPO / DEFAULT_DSL_SOURCE,
        semantic_fingerprint=SEMANTIC,
        source_binding_fingerprint=SOURCE,
        evidence=_fake_evidence().to_dict(),
    )
    workspace = tmp_path / "workspace"
    (workspace / "mcel_apps").mkdir(parents=True)
    import shutil
    shutil.copytree(LIVE, workspace / "mcel_apps/contract-workbench")
    before = _tree_snapshot(workspace / "mcel_apps/contract-workbench")
    _apply_plan(workspace, plan, promoted)
    assert _verify_generated_ownership(workspace / "mcel_apps/contract-workbench") is True
    _restore_live_package(workspace, LIVE)
    assert _tree_snapshot(workspace / "mcel_apps/contract-workbench") == before


def test_workbench_ir_native_proof_closes_intents_scenarios_effects_and_capability(tmp_path: Path) -> None:
    dsl = compile_dsl_application(REPO / DEFAULT_DSL_SOURCE, compare_ir_path=REPO / DEFAULT_FIXTURE_IR)
    projection = project_workbench_candidate(candidate_root=tmp_path / "candidates", write_candidate=True)
    assert dsl.valid and projection.valid and projection.candidate_directory
    plan, promoted = _build_promotion_plan(
        live_package=LIVE,
        candidate_package=projection.candidate_directory / "package/mcel_apps/contract-workbench",
        dsl_source=REPO / DEFAULT_DSL_SOURCE,
        semantic_fingerprint=SEMANTIC,
        source_binding_fingerprint=SOURCE,
        evidence=_fake_evidence().to_dict(),
    )
    repo = tmp_path / "repo"
    import shutil
    shutil.copytree(REPO, repo, ignore=shutil.ignore_patterns("runtime", "__pycache__", ".pytest_cache", "*.pyc"))
    _apply_plan(repo, plan, promoted)
    catalog = build_application_package_catalog(repo)
    record = next(item for item in catalog.packages if item.app_id == "contract-workbench")
    ir = json.loads((repo / DEFAULT_FIXTURE_IR).read_text(encoding="utf-8"))
    observation = {
        "status": "pass",
        "ok": True,
        "observation": {
            "scenarioResults": [{"id": item["id"].removeprefix("scenario:"), "passed": True} for item in ir["scenarios"]],
            "observationCoverage": [{"id": str(index), "passed": True} for index in range(7)],
        },
    }
    proof = run_workbench_ir_native_intent_proof(
        repo=repo,
        record=record,
        acceptance={"status": "pass", "passed": True},
        observation=observation,
    )
    assert proof["status"] == "ir-native"
    assert proof["coveredIntentCount"] == 7
    assert proof["observedScenarioCount"] == 14
    assert proof["effectAccounting"]["status"] == "closed"
    assert proof["effectAccounting"]["declaredEffectCount"] == 18
    assert proof["capabilityAccounting"]["status"] == "closed"
    assert proof["capabilityAccounting"]["streamedOperationCount"] == 1


def test_full_generic_workbench_rehearsal_passes_without_mutating_live_repository(tmp_path: Path) -> None:
    before = _tree_snapshot(LIVE)
    result = rehearse_application_promotion(
        app_id="contract-workbench",
        repo_root=REPO,
        candidate_root=tmp_path / "candidates",
        evidence_report_root=tmp_path / "evidence",
        report_root=tmp_path / "reports",
        command_runner=_fake_runner,
        evidence_runner=_fake_evidence,
    )
    payload = result.to_dict()["result"]
    assert result.valid is True
    assert payload["promotionRehearsal"] == "pass"
    assert payload["postPromotionTruthStatus"] == "semantic-runtime-proven"
    assert payload["rollbackRehearsal"] == "pass"
    assert payload["rollbackRestoration"] == "exact"
    assert payload["promotionEligible"] is True
    assert payload["liveRepositoryChanged"] is False
    assert payload["promotionExecuted"] is False
    assert _tree_snapshot(LIVE) == before


def test_stale_workbench_candidate_evidence_blocks_rehearsal(tmp_path: Path) -> None:
    def stale(**_kwargs):
        report = _fake_evidence().to_dict()
        report["candidate"]["sourceBindingFingerprint"] = "sha256:stale"
        return WorkbenchCandidateEvidenceResult(True, "pass", report, ())
    result = rehearse_workbench_promotion(
        repo_root=REPO,
        candidate_root=tmp_path / "candidates",
        report_root=tmp_path / "reports",
        evidence_runner=stale,
        command_runner=_fake_runner,
    )
    assert result.valid is False
    assert result.status == "stale-evidence"
    assert any(item["code"] == "MCEL_WORKBENCH_PROMOTION_EVIDENCE_BINDING_CONFLICT" for item in result.diagnostics)
