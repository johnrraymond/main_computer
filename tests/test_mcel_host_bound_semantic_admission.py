"""Host-bound operational evidence is not a replacement for independent truth."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer import mcel_truth_audit as audit
from tools import mcel_phase1_host_bound_prove as producer


def _artifacts(tmp_path: Path, monkeypatch, app: str = "calculator"):
    names = sorted((
        ["switchMode", "enterToken", "clearExpression", "evaluateExpression", "drawGraph",
         "resetGraph", "askModelForExpression", "askModelForGraphExpression",
         "askModelForMathicsExpression", "evaluateMathics", "askResultQuestion"]
        if app == "calculator" else
        ["inspectWorkspace", "openFile", "editDraft", "saveFile", "discardDraft",
         "closeFile", "previewAiderPlan", "applyReviewedPatch"]
    ))
    source = tmp_path / f"mcel_apps/{app}/application.js"
    source.parent.mkdir(parents=True)
    source.write_text("// synthetic DSL fixture\n", encoding="utf-8")
    manifest = source.parent / "mcel.app.json"
    manifest.write_text(json.dumps({"authoring": {"status": "dsl-authoritative"}}), encoding="utf-8")
    native = {
        "schema": "mcel.app-ir-native-intent-complete-proof.v1", "appId": app,
        "passed": True, "status": "ir-native-authoritative", "semanticFingerprint": "semantic-sha",
        "sourceBindingFingerprint": "binding-sha",
        "declaredIntentCount": len(names), "coveredIntentCount": len(names),
        "declaredScenarioCount": len(names), "observedScenarioCount": len(names),
        "failedIntentIds": [], "failedScenarioIds": [],
        "scenarios": {f"scenario:{name}": {"passed": True} for name in names},
        "crossCuttingChecks": {"freshBrowserParity": True, "generatedRuntimeBindingsExact": True},
        "effectAccounting": {"status": "closed"},
        "intents": {name: {"passed": True} for name in names},
        "parityEvidence": {"freshChromiumObservation": True, "browserObservation": {
            "status": "pass", "checks": {"sourceTreeUnchanged": True},
            "intentSemanticChecks": {name: True for name in names},
        }},
    }
    scope = "real-host-dom-and-receipts; provider-transport-mocked"
    if app == "code-editor":
        scope = "real-disposable-workspace-byte-effects-and-refusals"
        for key in ("independentBytesAfterSave", "staleSourceRefused", "previewNonMutating",
                    "unapprovedPatchRefused", "reviewedApplyWritesVerified"):
            native["crossCuttingChecks"][key] = True
        native["parityEvidence"]["browserObservation"]["checks"].update(
            realFilesystemEffects=True, unapprovedEffectRefused=True)
        native["browserEffectEvidence"] = {
            "saveAfterSha256": hashlib.sha256(b"after();\n").hexdigest(),
            "reviewedApplyAfterSha256": hashlib.sha256(b"reviewed();\n").hexdigest(),
            "afterReadOnlyPreviewSha256": hashlib.sha256(b"concurrent();\n").hexdigest(),
            "serverRequestPaths": ["/api/applications/editor/files", "/api/applications/editor/read",
                "/api/applications/editor/project/file/save", "/api/applications/editor/project/transaction/prepare",
                "/api/applications/editor/project/transaction/apply"],
        }
    rel = audit.HOST_BOUND_SEMANTIC_ROOT / app
    directory = tmp_path / rel
    directory.mkdir(parents=True)
    native_path = directory / "mcel-ir-native-proof.json"
    def store_native():
        native_path.write_text(json.dumps(native), encoding="utf-8")
        return hashlib.sha256(native_path.read_bytes()).hexdigest()
    now = datetime.now(timezone.utc)
    witness = {
        "schema": audit.HOST_BOUND_SEMANTIC_SCHEMA, "appId": app,
        "generatedAt": now.isoformat(), "status": "pass", "passed": True,
        "truthStatus": "host-bound-operational-semantics-witnessed", "canonicalTruthGateDecides": True,
        "phaseOneTruthStatus": "host-bound-ir-native-authoritative",
        "packageFingerprint": "package-sha", "runtimeProjectionFingerprint": "projection-sha",
        "semanticFingerprint": "semantic-sha", "repositoryFingerprint": "repository-sha",
        "intentNames": names, "declaredIntentCount": len(names), "observedIntentCount": len(names),
        "effectEvidenceScope": scope,
        "nativeProofArtifact": {"path": (rel / native_path.name).as_posix(), "sha256": store_native()},
    }
    witness_path = directory / "mcel-semantic-witness.json"
    def store_witness():
        witness_path.write_text(json.dumps(witness), encoding="utf-8")
    store_witness()
    import main_computer.mcel_application_packages as packages
    import main_computer.mcel_application_runtime_projection as projections
    import main_computer.mcel_dsl_compiler as compiler
    record = SimpleNamespace(app_id=app, valid=True, manifest=manifest.relative_to(tmp_path),
        authoring={"source": source.relative_to(tmp_path).as_posix()}, fingerprint="package-sha")
    projection = SimpleNamespace(app_id=app, mount_mode="host-bound", fingerprint="projection-sha")
    monkeypatch.setattr(packages, "build_application_package_catalog", lambda _: SimpleNamespace(packages=[record]))
    monkeypatch.setattr(projections, "build_runtime_projection_set", lambda _: SimpleNamespace(projections=[projection]))
    monkeypatch.setattr(compiler, "compile_dsl_application", lambda *a, **k: SimpleNamespace(
        valid=True, semantic_fingerprint="semantic-sha", source_binding_fingerprint="binding-sha", normalized_ir={"intents": [
            {"sourceName": name} for name in names]}))
    def admit():
        return audit._admitted_host_bound_semantic_proofs(tmp_path,
            repository_provenance={"fingerprint": "repository-sha"}, now=now.isoformat(), max_age_hours=168)
    return witness, native, store_native, store_witness, admit


@pytest.mark.parametrize("app", ["calculator", "code-editor"])
def test_host_bound_admission_requires_complete_attested_real_effects(tmp_path, monkeypatch, app):
    witness, native, store_native, store_witness, admit = _artifacts(tmp_path, monkeypatch, app)
    assert app in admit()
    # Tampering with referenced evidence without a new digest is refused.
    native["crossCuttingChecks"]["freshBrowserParity"] = False
    store_native()
    assert admit() == {}
    native["crossCuttingChecks"]["freshBrowserParity"] = True
    witness["nativeProofArtifact"]["sha256"] = store_native()
    store_witness()
    assert app in admit()
    # An intact artifact whose operational observation is false also refuses.
    if app == "calculator":
        native["parityEvidence"]["browserObservation"]["intentSemanticChecks"]["switchMode"] = False
    else:
        native["crossCuttingChecks"]["unapprovedPatchRefused"] = False
    witness["nativeProofArtifact"]["sha256"] = store_native()
    store_witness()
    assert admit() == {}


def test_host_bound_admission_refuses_stale_source_age_and_incomplete_intent(tmp_path, monkeypatch):
    witness, native, store_native, store_witness, admit = _artifacts(tmp_path, monkeypatch)
    witness["repositoryFingerprint"] = "older-repository"
    store_witness()
    assert admit() == {}
    witness["repositoryFingerprint"] = "repository-sha"
    witness["generatedAt"] = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    store_witness()
    assert admit() == {}
    witness["generatedAt"] = datetime.now(timezone.utc).isoformat()
    native["intents"].pop("switchMode")
    witness["nativeProofArtifact"]["sha256"] = store_native()
    store_witness()
    assert admit() == {}


def test_phase_one_is_not_automatically_semantic_proof():
    proof = {"crossCuttingChecks": {"freshBrowserParity": True},
             "intents": {"switchMode": {"passed": True}},
             "parityEvidence": {"browserObservation": {"status": "pass",
                                                   "checks": {"sourceTreeUnchanged": True}}},
             "effectAccounting": {"status": "closed"}}
    with pytest.raises(producer.HostBoundPhaseOneError, match="Calculator"):
        producer._validate_semantic_execution("calculator", proof)
