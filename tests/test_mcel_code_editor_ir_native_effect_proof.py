"""Code Editor's promoted proof must use real generated-adapter/browser/file effects."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from main_computer.mcel_app_authoring_profiles import AppAuthoringProfileError, get_app_authoring_profile
from main_computer.mcel_app_ir_native_proof import run_app_ir_native_intent_proof
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer import mcel_code_editor_ir_native_proof as actual

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {"inspectWorkspace", "openFile", "editDraft", "saveFile", "discardDraft",
            "closeFile", "previewAiderPlan", "applyReviewedPatch"}


def _record():
    return next(r for r in build_application_package_catalog(ROOT).packages if r.app_id == "code-editor")


@pytest.fixture(scope="module")
def fresh_witness():
    pytest.importorskip("playwright.sync_api")
    saved = {}
    original = actual._fresh_browser_effects

    def capture(*args, **kwargs):
        evidence = original(*args, **kwargs)
        saved["evidence"] = copy.deepcopy(evidence)
        return evidence

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(actual, "_fresh_browser_effects", capture)
        report = run_app_ir_native_intent_proof(
            app_id="code-editor", repo=ROOT, record=_record(), acceptance={}, observation={})
    return report, saved["evidence"]


def test_real_effect_proof_is_registered_and_covers_all_dsl_intents(fresh_witness):
    report, evidence = fresh_witness
    with pytest.raises(AppAuthoringProfileError):
        get_app_authoring_profile("code-editor")  # authoring remains profile-free
    assert report["applicationProfile"] == "mcel.code-editor.real-effects-proof-profile.v1"
    assert report["genericPipeline"] is True
    assert report["passed"] and report["status"] == "ir-native-authoritative"
    assert report["declaredIntentCount"] == report["coveredIntentCount"] == 8
    assert report["observedScenarioCount"] == report["declaredScenarioCount"] == 13
    assert set(report["intents"]) == EXPECTED
    assert all(item["passed"] for item in report["scenarios"].values())
    assert report["effectAccounting"]["status"] == "closed"
    assert report["parityEvidence"]["freshChromiumObservation"] is True
    assert report["browserEffectEvidence"]["afterReadOnlyPreviewSha256"] == actual._sha(b"concurrent();\n")
    assert report["browserEffectEvidence"]["reviewedApplyAfterSha256"] == actual._sha(b"reviewed();\n")
    assert evidence["afterSaveSha256"] == actual._sha(b"after();\n")
    assert evidence["applied"]["receipt"]["raw"]["receipt"]["files"][0]["written_sha256"] == actual._sha(b"reviewed();\n")
    assert report["promotionEligible"] is False


def test_proof_refuses_forged_write_receipt(fresh_witness, monkeypatch):
    _report, witness = fresh_witness
    forged = copy.deepcopy(witness)
    forged["afterSaveSha256"] = "0" * 64
    monkeypatch.setattr(actual, "_fresh_browser_effects", lambda *_args, **_kwargs: forged)
    with pytest.raises(actual.CodeEditorIrNativeProofError, match="transaction receipt"):
        actual.run_code_editor_ir_native_intent_proof(repo=ROOT, record=_record())


def test_proof_refuses_missing_real_apply_and_bypassed_review(fresh_witness, monkeypatch):
    _report, witness = fresh_witness
    missing = copy.deepcopy(witness)
    missing["requestPaths"] = [p for p in missing["requestPaths"] if not p.endswith("/transaction/apply")]
    monkeypatch.setattr(actual, "_fresh_browser_effects", lambda *_args, **_kwargs: missing)
    with pytest.raises(actual.CodeEditorIrNativeProofError, match="service requests"):
        actual.run_code_editor_ir_native_intent_proof(repo=ROOT, record=_record())
    bypassed = copy.deepcopy(witness)
    bypassed["prepared"]["snapshots"]["unapproved"]["receipt"]["ok"] = True
    monkeypatch.setattr(actual, "_fresh_browser_effects", lambda *_args, **_kwargs: bypassed)
    with pytest.raises(actual.CodeEditorIrNativeProofError, match="Unapproved"):
        actual.run_code_editor_ir_native_intent_proof(repo=ROOT, record=_record())


def test_proof_rejects_wrong_package_and_substituted_browser():
    record = _record()
    with pytest.raises(actual.CodeEditorIrNativeProofError, match="substituted browser"):
        actual.run_code_editor_ir_native_intent_proof(
            repo=ROOT, record=record, browser_probe_runner=lambda *_args: {"status":"pass"})
