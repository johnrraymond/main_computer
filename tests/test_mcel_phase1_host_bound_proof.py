"""Fail-closed host-bound proof adapter tests; never substitute synthetic browser truth."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools import mcel_phase1_host_bound_prove as bridge
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_evidence_provenance import build_repository_provenance

ROOT = Path(__file__).resolve().parents[1]


def acceptance_for_repo():
    record = next(r for r in build_application_package_catalog(ROOT).packages if r.app_id == "calculator")
    return {
        "status": "pass",
        "passed": True,
        "evidenceScope": {"kind": "app-scoped", "selectedApps": ["calculator"]},
        "results": [{"appId": "calculator", "status": "pass"}],
        "applicationPackages": [{"appId": "calculator", "packageFingerprint": record.fingerprint}],
        "repositoryProvenance": build_repository_provenance(ROOT),
    }


def passing_native_proof(*, repo, record, **kwargs):
    from main_computer.mcel_dsl_compiler import compile_dsl_application
    compiled = compile_dsl_application(repo / record.authoring["source"], write_candidate=False)
    return {
        "schema": "mcel.app-ir-native-intent-complete-proof.v1",
        "appId": "calculator",
        "passed": True,
        "status": "ir-native-authoritative",
        "semanticFingerprint": compiled.semantic_fingerprint,
        "declaredIntentCount": 11,
        "coveredIntentCount": 11,
        "declaredScenarioCount": 11,
        "observedScenarioCount": 11,
        "crossCuttingChecks": {"freshBrowserParity": True, "generatedRuntimeBindingsExact": True},
        "effectAccounting": {"status": "closed"},
        "parityEvidence": {"freshChromiumObservation": True, "browserObservation": {
            "status": "pass", "checks": {"sourceTreeUnchanged": True},
        }},
    }


def test_host_bound_proof_requires_scoped_acceptance_and_fresh_native_browser(monkeypatch):
    monkeypatch.setattr(bridge, "_load_acceptance", lambda *_: acceptance_for_repo())
    monkeypatch.setattr(bridge, "run_app_ir_native_intent_proof", passing_native_proof)
    report = bridge.run_host_bound_proof(repo=ROOT, app_id="calculator")
    assert report["status"] == "pass"
    assert report["truthStatus"] == "host-bound-ir-native-authoritative"
    assert report["fullSemanticRuntimeProven"] is False
    assert report["intentCoverage"]["coveredIntentCount"] == 11
    assert report["evidence"]["browserObservation"]["fresh"] is True


def test_host_bound_proof_rejects_stale_acceptance(monkeypatch):
    acceptance = acceptance_for_repo()
    acceptance["repositoryProvenance"] = {"fingerprint": "sha256:stale"}
    monkeypatch.setattr(bridge, "_load_acceptance", lambda *_: acceptance)
    with pytest.raises(bridge.HostBoundPhaseOneError, match="provenance"):
        bridge.run_host_bound_proof(repo=ROOT, app_id="calculator")


def test_host_bound_proof_rejects_unproven_browser(monkeypatch):
    monkeypatch.setattr(bridge, "_load_acceptance", lambda *_: acceptance_for_repo())

    def not_fresh(**kwargs):
        proof = passing_native_proof(**kwargs)
        proof["parityEvidence"]["freshChromiumObservation"] = False
        return proof

    monkeypatch.setattr(bridge, "run_app_ir_native_intent_proof", not_fresh)
    with pytest.raises(bridge.HostBoundPhaseOneError, match="complete, fresh"):
        bridge.run_host_bound_proof(repo=ROOT, app_id="calculator")
