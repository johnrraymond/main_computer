"""The Phase One entrypoint must never convert partial evidence into proof."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tools.mcel_phase1_gate import run_phase_one

REPO = Path(__file__).resolve().parents[1]
FINGERPRINT = "sha256:" + "a" * 64


def _response(payload, returncode=0):
    return subprocess.CompletedProcess([], returncode, json.dumps(payload), "")


def test_phase_one_success_requires_authoritative_compile_matching_projection_and_proof() -> None:
    calls = []
    payloads = [
        {"appId": "calculator", "valid": True, "status": "pass", "sourceAuthority": "mcel.dsl.v1", "semanticFingerprint": FINGERPRINT},
        {"appId": "calculator", "valid": True, "status": "pass", "semanticFingerprint": FINGERPRINT},
        {"appId": "calculator", "status": "pass", "truthStatus": "semantic-runtime-proven", "intentCoverage": {"passed": True}, "evidence": {"acceptance": {"status": "pass"}}},
    ]

    def invoke(command, _repo):
        calls.append(list(command))
        return _response(payloads[len(calls) - 1])

    result = run_phase_one(repo_root=REPO, app_id="calculator", execute=invoke)
    assert result["passed"] is True
    assert result["status"] == "pass"
    assert [entry["status"] for entry in result["stages"].values()] == ["pass", "pass", "pass"]
    assert "--check" in calls[2]
    assert "--reuse-evidence" not in calls[2]


def test_phase_one_fails_closed_when_projection_fingerprint_differs() -> None:
    calls = []

    def invoke(command, _repo):
        calls.append(list(command))
        if len(calls) == 1:
            return _response({"appId": "calculator", "valid": True, "status": "pass", "sourceAuthority": "mcel.dsl.v1", "semanticFingerprint": FINGERPRINT})
        return _response({"appId": "calculator", "valid": True, "status": "pass", "semanticFingerprint": "sha256:stale"})

    result = run_phase_one(repo_root=REPO, app_id="calculator", execute=invoke)
    assert result["passed"] is False
    assert result["failedStage"] == "project"
    assert result["stages"]["prove"]["status"] == "not-run"
    assert len(calls) == 2


def test_phase_one_does_not_claim_browser_proof_on_environment_failure() -> None:
    payloads = [
        {"appId": "calculator", "valid": True, "status": "pass", "sourceAuthority": "mcel.dsl.v1", "semanticFingerprint": FINGERPRINT},
        {"appId": "calculator", "valid": True, "status": "pass", "semanticFingerprint": FINGERPRINT},
        {"appId": "calculator", "status": "fail", "error": "Chromium navigation blocked"},
    ]
    calls = []

    def invoke(command, _repo):
        calls.append(list(command))
        return _response(payloads[len(calls) - 1], returncode=1 if len(calls) == 3 else 0)

    result = run_phase_one(repo_root=REPO, app_id="calculator", execute=invoke)
    assert result["passed"] is False
    assert result["failedStage"] == "prove"
    assert "Chromium navigation blocked" in result["stages"]["prove"]["error"]
    assert result["stages"]["compile"]["status"] == "pass"
    assert result["stages"]["project"]["status"] == "pass"


def test_phase_one_rejects_shadow_source_even_when_compilation_passes() -> None:
    result = run_phase_one(
        repo_root=REPO,
        app_id="calculator",
        execute=lambda _cmd, _repo: _response({
            "appId": "calculator", "valid": True, "status": "pass",
            "sourceAuthority": "mcel.dsl.shadow.v1", "semanticFingerprint": FINGERPRINT,
        }),
    )
    assert not result["passed"]
    assert result["failedStage"] == "compile"


def test_phase_one_host_bound_uses_native_browser_proof_and_requires_matching_source() -> None:
    calls = []
    payloads = [
        {"appId": "calculator", "valid": True, "status": "pass", "sourceAuthority": "mcel.dsl.v1", "semanticFingerprint": FINGERPRINT},
        {
            "appId": "calculator", "valid": True, "status": "pass", "semanticFingerprint": FINGERPRINT,
            "projection": {
                "authority": {"hostBoundRuntimeActive": True},
                "projection": {"hostBoundRuntimeActive": True, "hostRoute": "/applications/calculator"},
            },
        },
        {
            "appId": "calculator", "status": "pass", "truthStatus": "host-bound-ir-native-authoritative",
            "semanticFingerprint": FINGERPRINT,
            "intentCoverage": {"passed": True}, "evidence": {"browserObservation": {"fresh": True}},
        },
    ]

    def invoke(command, _repo):
        calls.append(list(command))
        return _response(payloads[len(calls) - 1])

    result = run_phase_one(repo_root=REPO, app_id="calculator", execute=invoke)
    assert result["passed"] is True
    assert result["proofScope"] == "host-bound-ir-native-authoritative"
    assert result["fullSemanticRuntimeProven"] is False
    assert calls[2][1].endswith("mcel_phase1_host_bound_prove.py")
    assert "--check" in calls[2]
    assert "--reuse-evidence" not in calls[2]


def test_phase_one_host_bound_rejects_proof_from_another_semantic_revision() -> None:
    calls = []
    payloads = [
        {"appId": "calculator", "valid": True, "status": "pass", "sourceAuthority": "mcel.dsl.v1", "semanticFingerprint": FINGERPRINT},
        {
            "appId": "calculator", "valid": True, "status": "pass", "semanticFingerprint": FINGERPRINT,
            "projection": {"authority": {"hostBoundRuntimeActive": True}, "projection": {"hostBoundRuntimeActive": True}},
        },
        {
            "appId": "calculator", "status": "pass", "truthStatus": "host-bound-ir-native-authoritative",
            "semanticFingerprint": "sha256:" + "b" * 64,
            "intentCoverage": {"passed": True}, "evidence": {"browserObservation": {"fresh": True}},
        },
    ]

    def invoke(command, _repo):
        calls.append(list(command))
        return _response(payloads[len(calls) - 1])

    result = run_phase_one(repo_root=REPO, app_id="calculator", execute=invoke)
    assert result["passed"] is False
    assert result["failedStage"] == "prove"


def test_phase_one_host_bound_requires_both_projection_authorities() -> None:
    payloads = [
        {"appId": "calculator", "valid": True, "status": "pass", "sourceAuthority": "mcel.dsl.v1", "semanticFingerprint": FINGERPRINT},
        {
            "appId": "calculator", "valid": True, "status": "pass", "semanticFingerprint": FINGERPRINT,
            "projection": {"authority": {"hostBoundRuntimeActive": False}, "projection": {"hostBoundRuntimeActive": True}},
        },
        {"appId": "calculator", "status": "pass", "truthStatus": "semantic-runtime-proven", "intentCoverage": {"passed": True}, "evidence": {}},
    ]
    calls = []

    def invoke(command, _repo):
        calls.append(list(command))
        return _response(payloads[len(calls) - 1])

    result = run_phase_one(repo_root=REPO, app_id="calculator", execute=invoke)
    assert result["passed"] is True
    assert calls[2][1].endswith("mcel_app_prove.py")
