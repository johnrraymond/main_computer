"""Patch 20: portable release-mode proof, atomic activation and fail-closed controls."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from main_computer import mcel_portable_certification as cert
from main_computer import mcel_portable_promotion as promote
from main_computer import mcel_truth_audit as truth

SEMANTIC = "sha256:" + "a" * 64


def fake_witness(app, fingerprint="fixed"):
    keys = ("freshChromiumObservation", "generatedAdapterMounted",
            "generatedAdapterModuleExecuted", "allIntentsPassed", "generatedRuntimeParityStatus",
            "localProviderFree", "capabilityAccountingClosed", "sourceTreeUnchanged") if app == "calculator" else (
                "freshBrowserParity", "independentBytesAfterSave", "staleSourceRefused",
                "previewNonMutating", "unapprovedPatchRefused", "reviewedApplyWritesVerified",
                "realGeneratedAdapterMounted")
    observations = {"schema": (promote.CALCULATOR_OBSERVATION_SCHEMA if app == "calculator"
                               else "mcel.code-editor-real-effects-ir-proof.v1"),
                    "checks": {key: True for key in keys},
                    "intents": sorted(cert.APP_INTENTS[app]),
                    "semanticFingerprint":SEMANTIC}
    if app == "calculator":
        observations["genericSchema"] = promote.GENERIC_HOST_OBSERVATION_SCHEMA
    if app == "code-editor":
        observations["effects"] = {
            "saveAfterSha256": hashlib.sha256(b"after();\n").hexdigest(),
            "reviewedApplyAfterSha256": hashlib.sha256(b"reviewed();\n").hexdigest(),
        }
    return {"schema": promote.HOST_SCHEMA, "appId": app, "status": "pass",
            "repositoryFingerprint": fingerprint,
            "coverage": "bounded-common-intent-core-and-host-effect-fixtures",
            "hostObservation": observations, "generatedAt": "2026-10-18T20:00:00Z"}


@pytest.fixture
def setup_release(tmp_path, monkeypatch):
    # Simulated browser and package producers. Production calls the real host
    # Chromium and actual deterministic standalone compiler instead.
    (tmp_path / cert.DEFAULT_CERTIFICATE).parent.mkdir(parents=True)
    (tmp_path / cert.DEFAULT_CERTIFICATE).write_text('{"test":true}', encoding="utf-8")
    source = {"semanticFingerprint": SEMANTIC, "sourceSha256": "sha256:source",
              "packageFingerprint": "sha256:package", "hostBoundProjectionFingerprint": "sha256:host"}
    data = b"executable portable code\n"
    core = b"trusted shared host core\n"
    host_path = tmp_path / "main_computer/web/applications/scripts/calculator-core.js"
    host_path.parent.mkdir(parents=True)
    host_path.write_bytes(core)
    manifest = {"portableMode":True, "source":source, "declaredIntents":sorted(cert.APP_INTENTS["calculator"]),
                "assetDigests":{"portable.js":promote._sha(data),
                                "calculator-core.js": promote._sha(core)}}
    presentation = promote._json(manifest)
    binding = {"source": source, "assetDigests":manifest["assetDigests"],
               "presentationSha256":promote._sha(presentation),
               "declaredIntents":manifest["declaredIntents"]}

    def build(repo, app, directory, portable):
        assert portable and app == "calculator"
        path = directory / app
        path.mkdir(parents=True)
        (path / "portable.js").write_bytes(data)
        (path / "calculator-core.js").write_bytes(core)
        (path / "presentation.json").write_bytes(presentation)
        return manifest

    monkeypatch.setattr(promote, "build_standalone_package", build)
    monkeypatch.setattr(cert, "current_bindings", lambda _: {"calculator":binding})
    monkeypatch.setattr(promote, "build_repository_provenance", lambda _: {"fingerprint":"fixed"})
    monkeypatch.setattr(promote, "prerequisites", lambda _repo, _app: ({"status":"pass"},{"fingerprint":"fixed"}))
    monkeypatch.setattr(promote, "capture_host_witness", lambda _repo, app: fake_witness(app))
    monkeypatch.setattr(promote, "_certificate_gate", lambda _repo: ({},{"passed":True}))
    return tmp_path, manifest


def test_witness_requires_full_intent_and_host_effect_checks():
    w = fake_witness("code-editor")
    assert promote.verify_witness(w, "code-editor", "fixed")
    w["hostObservation"]["checks"]["unapprovedPatchRefused"] = False
    assert not promote.verify_witness(w, "code-editor", "fixed")
    w = fake_witness("calculator")
    assert promote.verify_witness(w, "calculator", "fixed")
    w["hostObservation"]["intents"].remove("drawGraph")
    assert not promote.verify_witness(w, "calculator", "fixed")


def test_real_calculator_observation_schema_pair_is_required(monkeypatch, tmp_path):
    # This pair comes from the actual host-bound observer, not a synthetic
    # generic-only schema (the original Patch 20 verifier rejected it).
    from main_computer import mcel_calculator_parity
    from main_computer.mcel_calculator_browser_observation import CALCULATOR_BROWSER_OBSERVATION_PROFILE
    from main_computer.mcel_host_bound_browser_observation import REPORT_SCHEMA

    assert CALCULATOR_BROWSER_OBSERVATION_PROFILE.report_schema == promote.CALCULATOR_OBSERVATION_SCHEMA
    assert REPORT_SCHEMA == promote.GENERIC_HOST_OBSERVATION_SCHEMA
    monkeypatch.setattr(promote, "build_repository_provenance", lambda _: {"fingerprint": "fixed"})
    observation = fake_witness("calculator")["hostObservation"]
    observation.update({"valid": True, "status": "pass", "intentResults": [
        {"intentName": name, "status": "pass"} for name in observation["intents"]]})
    monkeypatch.setattr(mcel_calculator_parity, "run_calculator_browser_parity_probe",
                        lambda **kwargs: {"valid": True, "status": "pass",
                                          "semanticFingerprint": SEMANTIC,
                                          "browserObservation": observation})
    witness = promote.capture_host_witness(tmp_path, "calculator")
    assert witness["hostObservation"]["schema"] == promote.CALCULATOR_OBSERVATION_SCHEMA
    assert witness["hostObservation"]["genericSchema"] == REPORT_SCHEMA
    assert promote.verify_witness(witness, "calculator", "fixed")

    missing_generic = json.loads(json.dumps(witness))
    del missing_generic["hostObservation"]["genericSchema"]
    assert not promote.verify_witness(missing_generic, "calculator", "fixed")
    wrong_app_schema = json.loads(json.dumps(witness))
    wrong_app_schema["hostObservation"]["schema"] = REPORT_SCHEMA
    assert not promote.verify_witness(wrong_app_schema, "calculator", "fixed")
    observation["genericSchema"] = "mcel.unsupported.v1"
    with pytest.raises(promote.PortablePromotionRefused, match="schema-mismatch"):
        promote.capture_host_witness(tmp_path, "calculator")


def test_rehearsal_does_not_publish(setup_release):
    repo, _ = setup_release
    result = promote.promote(repo, "calculator")
    assert result["status"] == "rehearsed" and result["valid"]
    assert not (repo / promote.SLOT).exists()


def test_release_activation_tamper_refusal_and_rollback(setup_release):
    repo, _ = setup_release
    result = promote.promote(repo, "calculator", execute=True)
    assert result["status"] == "promoted" and not result["productionHostRouteChanged"]
    active = promote.validate_active(repo, "calculator", require_certificate=False)
    assert active["valid"] and active["transactionId"] == result["transactionId"]
    package_file = repo / result["portableRelease"] / "package" / "portable.js"
    package_file.write_bytes(b"malicious")
    assert not promote.validate_active(repo, "calculator", require_certificate=False)["valid"]
    package_file.write_bytes(b"executable portable code\n")
    assert promote.validate_active(repo, "calculator", require_certificate=False)["valid"]
    with pytest.raises(promote.PortablePromotionRefused, match="rollback-not-current"):
        promote.rollback(repo, "calculator", "0" * 32)
    assert promote.rollback(repo, "calculator", result["transactionId"])["valid"]
    assert promote.validate_active(repo, "calculator")["status"] == "inactive"


def test_releasing_twice_then_rollback_restores_prior(setup_release):
    repo, _ = setup_release
    a = promote.promote(repo, "calculator", execute=True)
    b = promote.promote(repo, "calculator", execute=True)
    assert promote.validate_active(repo, "calculator", require_certificate=False)["transactionId"] == b["transactionId"]
    promote.rollback(repo, "calculator", b["transactionId"])
    assert promote.validate_active(repo, "calculator", require_certificate=False)["transactionId"] == a["transactionId"]


def test_invalid_host_proof_leaves_release_slot_untouched(setup_release, monkeypatch):
    repo, _ = setup_release
    invalid = fake_witness("calculator")
    invalid["hostObservation"]["checks"]["localProviderFree"] = False
    monkeypatch.setattr(promote, "capture_host_witness", lambda *_: invalid)
    with pytest.raises(promote.PortablePromotionRefused, match="host-witness"):
        promote.promote(repo, "calculator", execute=True)
    assert not (repo / promote.SLOT).exists()


def test_canonical_audit_independently_enforces_active_slots(monkeypatch, tmp_path):
    report = {"apps":[{"appId":"calculator"},{"appId":"code-editor"}],
              "summary":{"status":"pass","blockingReasonCount":0,
                         "evidenceBindingStatuses":{"runtime":"exact","acceptance":"exact"}},
              "portableCertification":{"passed":True},
              "auditLevelReasons":[],"configuration":{}}
    monkeypatch.setattr(promote, "validate_active", lambda _repo, app_id: {
        "status":"active" if app_id=="calculator" else "inactive",
        "valid":app_id=="calculator", "appId":app_id})
    result = truth.apply_active_portable_promotion(report,repo=tmp_path,required=True)
    assert result["summary"]["status"] == "fail"
    assert result["summary"]["portableActivePromotionAppIds"] == ["calculator"]
    assert result["auditLevelReasons"][0]["code"] == "audit-portable-active-promotion-incomplete"
    assert result["apps"][0]["portableRelease"]["productionHostRouteChanged"] is False


def test_missing_certificate_and_audit_fail_closed(tmp_path, monkeypatch):
    with pytest.raises(promote.PortablePromotionRefused, match="promotion-evidence-unreadable"):
        promote._certificate_gate(tmp_path)
    monkeypatch.setattr(truth, "run_audit", lambda *args, **kwargs: {
        "summary":{"status":"fail", "evidenceBindingStatuses":{"runtime":"exact","acceptance":"exact"}}})
    with pytest.raises(promote.PortablePromotionRefused, match="canonical-truth-audit-failed"):
        promote._audit_gate(tmp_path)


def test_refuses_release_outside_repo(tmp_path):
    with pytest.raises(promote.PortablePromotionRefused, match="outside-repository"):
        promote._root(tmp_path, tmp_path.parent / "elsewhere")


def test_fresh_calculator_host_capture_consumes_actual_observation_shape(monkeypatch,tmp_path):
    from main_computer import mcel_calculator_parity
    monkeypatch.setattr(promote, "build_repository_provenance", lambda _: {"fingerprint":"fixed"})
    observation = fake_witness("calculator")["hostObservation"]
    observation.update({"valid":True,"status":"pass","intentResults":[
        {"intentName":name,"status":"pass"} for name in observation["intents"]]})
    monkeypatch.setattr(mcel_calculator_parity, "run_calculator_browser_parity_probe",
                        lambda **kwargs: {"valid":True,"status":"pass",
                                          "semanticFingerprint": SEMANTIC,
                                          "browserObservation":observation})
    witness = promote.capture_host_witness(tmp_path,"calculator")
    assert promote.verify_witness(witness,"calculator","fixed")
    observation["intentResults"].pop()
    with pytest.raises(promote.PortablePromotionRefused, match="intent-set-mismatch"):
        promote.capture_host_witness(tmp_path,"calculator")


def test_fresh_code_editor_host_capture_requires_independent_write_evidence(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from main_computer import mcel_application_packages, mcel_code_editor_ir_native_proof
    monkeypatch.setattr(promote, "build_repository_provenance",lambda _: {"fingerprint":"fixed"})
    monkeypatch.setattr(mcel_application_packages,"build_application_package_catalog",
        lambda _repo: SimpleNamespace(packages=[SimpleNamespace(app_id="code-editor")]))
    observation = fake_witness("code-editor")["hostObservation"]
    report = {"schema":observation["schema"],"passed":True,"status":"ir-native-authoritative",
        "crossCuttingChecks":observation["checks"],
        "intents":{name:{"passed":True} for name in observation["intents"]},
        "browserEffectEvidence":observation["effects"],
        "semanticFingerprint":observation["semanticFingerprint"]}
    monkeypatch.setattr(mcel_code_editor_ir_native_proof,"run_code_editor_ir_native_intent_proof",
        lambda **kwargs:report)
    witness = promote.capture_host_witness(tmp_path,"code-editor")
    assert promote.verify_witness(witness,"code-editor","fixed")
    report["browserEffectEvidence"] = {"saveAfterSha256":"forged",
        "reviewedApplyAfterSha256":observation["effects"]["reviewedApplyAfterSha256"]}
    with pytest.raises(promote.PortablePromotionRefused,match="write-effects-invalid"):
        promote.capture_host_witness(tmp_path,"code-editor")


def test_canonical_dispatch_does_not_use_code_editor_dsl_profile(monkeypatch,tmp_path):
    from main_computer import mcel_app_promote
    monkeypatch.setattr(promote,"validate_active",lambda *_args,**_kwargs:
                        {"status":"inactive","valid":False,"reasons":["no-active-release"]})
    report=mcel_app_promote.dispatch_portable_package_promotion(
        app_id="code-editor",repo_root=tmp_path,operation="check")
    assert not report.valid
    assert report.report["portableReleaseSlot"] is True
    assert report.report["sourceAuthorityChanged"] is False


def test_calculator_compiled_host_fingerprint_binds_empty_browser_catalog_field(monkeypatch, tmp_path):
    # Real browser catalog records do not publish a top-level semantic fingerprint.
    # The separately compiled host parity report is the source authority.
    from main_computer import mcel_calculator_parity

    monkeypatch.setattr(promote, "build_repository_provenance", lambda _: {"fingerprint": "fixed"})
    observation = fake_witness("calculator")["hostObservation"]
    observation.update({"valid": True, "status": "pass", "semanticFingerprint": "",
                        "intentResults": [{"intentName": name, "status": "pass"}
                                          for name in observation["intents"]]})
    host = {"valid": True, "status": "pass", "semanticFingerprint": SEMANTIC,
            "browserObservation": observation}
    monkeypatch.setattr(mcel_calculator_parity, "run_calculator_browser_parity_probe",
                        lambda **kwargs: host)
    witness = promote.capture_host_witness(tmp_path, "calculator")
    assert witness["hostObservation"]["semanticFingerprint"] == SEMANTIC
    assert witness["hostObservation"]["browserSemanticFingerprint"] is None
    assert promote.verify_witness(witness, "calculator", "fixed")

    host["semanticFingerprint"] = ""
    with pytest.raises(promote.PortablePromotionRefused,
                       match="host-compiled-semantic-fingerprint-missing"):
        promote.capture_host_witness(tmp_path, "calculator")
    host["semanticFingerprint"] = SEMANTIC
    observation["semanticFingerprint"] = "sha256:" + "b" * 64
    with pytest.raises(promote.PortablePromotionRefused,
                       match="browser-semantic-fingerprint-mismatch"):
        promote.capture_host_witness(tmp_path, "calculator")


def test_rehearsal_refuses_semantic_drift_before_staging(setup_release, monkeypatch):
    repo, _ = setup_release
    witness = fake_witness("calculator")
    witness["hostObservation"]["semanticFingerprint"] = "sha256:" + "b" * 64
    monkeypatch.setattr(promote, "capture_host_witness", lambda *_: witness)
    for execute in (False, True):
        with pytest.raises(promote.PortablePromotionRefused,
                           match="host-and-portable-semantic-fingerprint-mismatch"):
            promote.promote(repo, "calculator", execute=execute)
    assert not (repo / promote.SLOT).exists()


def test_real_calculator_catalog_and_portable_compile_semantic_binding(tmp_path):
    # Verify the exact producer contract, not just a browser-result mock.
    from main_computer.mcel_application_package_browser_catalog import build_repository_browser_catalog_payload
    from main_computer.mcel_dsl_compiler import compile_dsl_application
    from main_computer.mcel_standalone_package import build_standalone_package

    repo = Path(__file__).resolve().parents[1]
    record = next(item for item in build_repository_browser_catalog_payload(repo)["packages"]
                  if item["appId"] == "calculator")
    assert "semanticFingerprint" not in record
    compiled = compile_dsl_application(repo / "mcel_apps/calculator/application.js", write_candidate=False)
    assert compiled.valid
    manifest = build_standalone_package(repo, "calculator", tmp_path, portable=True)
    assert manifest["source"]["semanticFingerprint"] == compiled.semantic_fingerprint
