from __future__ import annotations

import json
import subprocess
from pathlib import Path

from main_computer.mcel_counter_candidate_evidence import CandidateEvidenceResult
from main_computer.mcel_counter_promotion_rehearsal import (
    _apply_plan,
    _build_promotion_plan,
    _promotion_authority_source_snapshot,
    _rollback_plan,
    _snapshot_changes,
    _source_tree_snapshot,
    _tree_snapshot,
    rehearse_counter_promotion,
)
from main_computer.mcel_counter_candidate_projection import project_counter_candidate
from main_computer.mcel_dsl_compiler import compile_dsl_application

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "mcel_apps/contract-counter"
DSL = ROOT / "mcel_apps/contract-counter/application.js"
FIXTURE = ROOT / "tests/fixtures/mcel_application_ir/contract-counter.ir.json"
SEMANTIC = "sha256:a9dbe6b7ec49978d313f18836b30c3394539c18f29430c3a7553837bc46eb0ef"
SOURCE = "sha256:4fecea5fc1242165a82ce2aaa16199807bcda9ca3d8ac4c506fcdb0eb59c595d"


def _receipt(*, ok: bool, status: str, code: str) -> dict:
    return {"ok": ok, "status": status, "code": code}


def _node_probe() -> dict:
    return {
        "schema": "mcel.counter-effect-probe.v1",
        "operations": [
            {"operationId": "candidate-increment", "before": {"count": 0, "revision": 0}, "result": _receipt(ok=True, status="committed", code="APPLICATION_OPERATION_COMMITTED"), "after": {"count": 1, "revision": 1}},
            {"operationId": "candidate-stale", "before": {"count": 1, "revision": 1}, "result": _receipt(ok=False, status="refused", code="SCM_STALE_REVISION"), "after": {"count": 1, "revision": 1}},
            {"operationId": "candidate-direct-set", "before": {"count": 1, "revision": 1}, "result": _receipt(ok=False, status="refused", code="INTENT_PROHIBITED"), "after": {"count": 1, "revision": 1}},
            {"operationId": "candidate-reset", "before": {"count": 1, "revision": 1}, "result": _receipt(ok=True, status="committed", code="APPLICATION_OPERATION_COMMITTED"), "after": {"count": 0, "revision": 2}},
        ],
    }


def _browser_probe() -> dict:
    passing = {"status": "pass", "comparison": {"surfaceMatches": True}}
    return {
        "schema": "mcel.counter-browser-effect-probe.v1",
        "operations": [
            {"operationId": "candidate-browser-increment", "before": {"count": 0, "revision": 0}, "result": _receipt(ok=True, status="committed", code="APPLICATION_OPERATION_COMMITTED"), "after": {"count": 1, "revision": 1}, "visible": "1", "observation": passing},
            {"operationId": "candidate-browser-stale", "before": {"count": 1, "revision": 1}, "result": _receipt(ok=False, status="refused", code="SCM_STALE_REVISION"), "after": {"count": 1, "revision": 1}, "visible": "1"},
            {"operationId": "candidate-browser-direct-set", "before": {"count": 1, "revision": 1}, "result": _receipt(ok=False, status="refused", code="INTENT_PROHIBITED"), "after": {"count": 1, "revision": 1}, "visible": "1"},
            {"operationId": "candidate-browser-reset", "before": {"count": 1, "revision": 1}, "result": _receipt(ok=True, status="committed", code="APPLICATION_OPERATION_COMMITTED"), "after": {"count": 0, "revision": 2}, "visible": "0", "observation": passing},
        ],
    }


def _fake_evidence(**_kwargs):
    report = {
        "schema": "mcel.counter-candidate-evidence-report.v1",
        "valid": True,
        "status": "pass",
        "truthStatus": "semantic-runtime-proven",
        "candidate": {"semanticFingerprint": SEMANTIC, "sourceBindingFingerprint": SOURCE},
        "authority": {"evidenceReused": False},
    }
    return CandidateEvidenceResult(True, "pass", report, ())


def _fake_command_runner(command, *, cwd, **_kwargs):
    workspace = Path(cwd)
    joined = " ".join(str(item) for item in command)
    if "mcel_acceptance_runner.py" in joined:
        path = workspace / "runtime/reports/mcel-acceptance/apps/contract-counter/mcel-acceptance-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"status": "pass", "passed": True}), encoding="utf-8")
    elif "mcel_application_observation_runner.py" in joined:
        path = workspace / "runtime/reports/mcel-observation/apps/contract-counter/mcel-operation-observation-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"status": "pass", "ok": True}), encoding="utf-8")
    elif "mcel_app_prove.py" in joined:
        path = workspace / "runtime/reports/mcel-app-proof/apps/contract-counter/mcel-app-proof-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "status": "pass", "truthStatus": "semantic-runtime-proven",
            "stages": {"generatedArtifacts": {"status": "pass"}, "repositoryBinding": {"status": "exact"}},
        }), encoding="utf-8")
    return subprocess.CompletedProcess(command, 0, stdout="pass\n", stderr=None)


def test_promotion_plan_declares_dsl_authority_and_generated_ownership(tmp_path: Path) -> None:
    dsl = compile_dsl_application(DSL, compare_ir_path=FIXTURE)
    projection = project_counter_candidate(candidate_root=tmp_path / "candidates", write_candidate=True)
    assert dsl.valid and projection.valid and projection.candidate_directory
    plan, promoted = _build_promotion_plan(
        repo=ROOT,
        live_package=LIVE,
        candidate_package=projection.candidate_directory / "package/mcel_apps/contract-counter",
        dsl_source_path=DSL,
        semantic_fingerprint=SEMANTIC,
        source_binding_fingerprint=SOURCE,
        evidence_payload=_fake_evidence().to_dict(),
    )
    assert plan["sourceAuthorityAfter"] == "mcel.dsl.v1"
    assert plan["promotionExecuted"] is False
    assert "mcel_apps/contract-counter/application.js" in promoted
    ownership = json.loads(promoted["mcel_apps/contract-counter/mcel.generated.json"])
    assert ownership["manualEditsProhibited"] is True
    assert len(ownership["generatedFiles"]) == 7
    manifest = json.loads(promoted["mcel_apps/contract-counter/mcel.app.json"])
    assert manifest["authoring"]["status"] == "dsl-authoritative"


def test_apply_and_rollback_restore_package_exactly(tmp_path: Path) -> None:
    dsl = compile_dsl_application(DSL, compare_ir_path=FIXTURE)
    projection = project_counter_candidate(candidate_root=tmp_path / "candidates", write_candidate=True)
    assert dsl.valid and projection.valid and projection.candidate_directory
    plan, promoted = _build_promotion_plan(
        repo=ROOT, live_package=LIVE,
        candidate_package=projection.candidate_directory / "package/mcel_apps/contract-counter",
        dsl_source_path=DSL, semantic_fingerprint=SEMANTIC, source_binding_fingerprint=SOURCE,
        evidence_payload=_fake_evidence().to_dict(),
    )
    workspace = tmp_path / "workspace"
    (workspace / "mcel_apps").mkdir(parents=True)
    import shutil
    shutil.copytree(LIVE, workspace / "mcel_apps/contract-counter")
    before = _tree_snapshot(workspace / "mcel_apps/contract-counter")
    _apply_plan(workspace, plan, promoted)
    assert (workspace / "mcel_apps/contract-counter/application.js").is_file()
    _rollback_plan(workspace, plan, LIVE)
    assert _tree_snapshot(workspace / "mcel_apps/contract-counter") == before


def test_full_rehearsal_passes_without_mutating_live_repository(tmp_path: Path) -> None:
    before = _tree_snapshot(LIVE)
    result = rehearse_counter_promotion(
        repo_root=ROOT,
        candidate_root=tmp_path / "candidates",
        evidence_report_root=tmp_path / "evidence",
        report_root=tmp_path / "reports",
        command_runner=_fake_command_runner,
        evidence_runner=_fake_evidence,
        node_probe_runner=lambda _workspace: _node_probe(),
        browser_probe_runner=lambda _workspace, _headed: _browser_probe(),
    )
    payload = result.to_dict()
    assert result.valid is True
    assert payload["promotionRehearsal"] == "pass"
    assert payload["postPromotionTruthStatus"] == "semantic-runtime-proven"
    assert payload["rollbackRehearsal"] == "pass"
    assert payload["rollbackRestoration"] == "exact"
    assert payload["promotionEligible"] is True
    assert payload["authority"]["liveApplicationChanged"] is False
    assert payload["authority"]["promotionExecuted"] is False
    assert _tree_snapshot(LIVE) == before
    assert result.output_directory
    assert (result.output_directory / "mcel-counter-promotion-rehearsal-report.json").is_file()


def test_stale_candidate_evidence_blocks_rehearsal(tmp_path: Path) -> None:
    def stale(**_kwargs):
        report = _fake_evidence().to_dict()
        report["candidate"]["sourceBindingFingerprint"] = "sha256:stale"
        return CandidateEvidenceResult(True, "pass", report, ())
    result = rehearse_counter_promotion(
        repo_root=ROOT, candidate_root=tmp_path / "candidates",
        report_root=tmp_path / "reports", evidence_runner=stale,
        command_runner=_fake_command_runner,
        node_probe_runner=lambda _workspace: _node_probe(),
        browser_probe_runner=lambda _workspace, _headed: _browser_probe(),
    )
    assert result.valid is False
    assert result.status == "stale-evidence"
    assert any(item["code"] == "MCEL_COUNTER_PROMOTION_EVIDENCE_BINDING_CONFLICT" for item in result.diagnostics)


def test_repository_guard_ignores_unrelated_source_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "main_computer").mkdir(parents=True)
    (repo / "tools/mother").mkdir(parents=True)
    (repo / "runtime/build/mcel/web/applications/mcel-packages/contract-counter/contracts").mkdir(parents=True)
    (repo / "mcel_apps/contract-counter").mkdir(parents=True)
    (repo / "tests/fixtures/mcel_dsl").mkdir(parents=True)
    (repo / "tests/fixtures/mcel_application_ir").mkdir(parents=True)
    (repo / "main_computer/mcel_counter_promotion_rehearsal.py").write_text("authority\n", encoding="utf-8")
    (repo / "tools/mother/unrelated.py").write_text("before\n", encoding="utf-8")
    (repo / "runtime/build/mcel/web/applications/mcel-packages/contract-counter/contracts/domain.js").write_text("counter\n", encoding="utf-8")
    (repo / "mcel_apps/contract-counter/application.js").write_text("dsl\n", encoding="utf-8")
    (repo / "tests/fixtures/mcel_application_ir/contract-counter.ir.json").write_text("{}\n", encoding="utf-8")

    full_before = _source_tree_snapshot(repo)
    protected_before = _promotion_authority_source_snapshot(repo)
    (repo / "tools/mother/unrelated.py").write_text("after\n", encoding="utf-8")
    full_after = _source_tree_snapshot(repo)
    protected_after = _promotion_authority_source_snapshot(repo)

    assert _snapshot_changes(full_before, full_after) == ["tools/mother/unrelated.py"]
    assert _snapshot_changes(protected_before, protected_after) == []


def test_repository_guard_detects_counter_or_shared_mcel_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "main_computer").mkdir(parents=True)
    (repo / "runtime/build/mcel/web/applications/mcel-packages/contract-counter/contracts").mkdir(parents=True)
    authority = repo / "main_computer/mcel_counter_promotion_rehearsal.py"
    contract = repo / "runtime/build/mcel/web/applications/mcel-packages/contract-counter/contracts/domain.js"
    authority.write_text("before\n", encoding="utf-8")
    contract.write_text("before\n", encoding="utf-8")
    before = _promotion_authority_source_snapshot(repo)

    authority.write_text("after\n", encoding="utf-8")
    contract.write_text("after\n", encoding="utf-8")
    after = _promotion_authority_source_snapshot(repo)

    assert _snapshot_changes(before, after) == [
        "main_computer/mcel_counter_promotion_rehearsal.py",
    ]
