from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EPISTEMIC = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-epistemic-status.js"
DOC = ROOT / "pretty_docs" / "mcel-observation-and-inference.md"


def run_node_json(body: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; epistemic contract tests cannot run")
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(EPISTEMIC))}, "utf8"),
          sandbox,
          {{filename: "mcel-epistemic-status.js"}}
        );
        const epistemic = sandbox.McelEpistemicStatus;
        {body}
        """
    )
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def source_js(source_id: str, *, value_locator: str) -> str:
    return json.dumps(
        {
            "id": source_id,
            "kind": "browser-accessibility",
            "locator": value_locator,
            "fingerprint": f"sha256:{source_id}",
            "observedAt": "2026-07-30T18:00:00Z",
        }
    )


def test_epistemic_contract_freezes_the_six_states_and_documentation() -> None:
    source = EPISTEMIC.read_text(encoding="utf-8")
    documentation = DOC.read_text(encoding="utf-8")

    assert "mcel.epistemic-status.v1" in source
    assert "mcel.observation-bundle.v1" in documentation
    assert "Only `verified` is truth-gate eligible." in documentation

    result = run_node_json(
        """
        process.stdout.write(JSON.stringify({
          version: epistemic.CONTRACT_VERSION,
          statuses: epistemic.STATUSES,
          eligible: epistemic.TRUTH_GATE_ELIGIBLE_STATUSES,
          frozen: Object.isFrozen(epistemic.STATUSES)
        }));
        """
    )

    assert result == {
        "version": "mcel.epistemic-status.v1",
        "statuses": ["declared", "observed", "inferred", "verified", "rejected", "ambiguous"],
        "eligible": ["verified"],
        "frozen": True,
    }


def test_inferred_claim_carries_provenance_but_cannot_satisfy_truth_gate() -> None:
    result = run_node_json(
        f"""
        const claim = epistemic.createClaim({{
          claimId: "claim.submit.role",
          subject: "control.submit",
          predicate: "semantic-role",
          value: "submit",
          status: "inferred",
          sources: [{source_js("a11y.submit", value_locator="#send")}],
          confidence: 0.96,
          contradictions: [],
          observedAt: "2026-07-30T18:00:00Z",
          repositoryFingerprint: "repo:abc123",
          validatorResults: [],
          requiredForTruthGate: true,
          truthGateRequirementIds: ["demo.semantic.submit-role"]
        }});
        const assessment = epistemic.assessTruthGate({{claims: [claim]}});
        process.stdout.write(JSON.stringify({{
          claim,
          assessment,
          frozen: Object.isFrozen(claim) && Object.isFrozen(claim.sources)
        }}));
        """
    )

    assert result["claim"]["status"] == "inferred"
    assert result["claim"]["confidence"] == 0.96
    assert result["claim"]["sources"][0]["locator"] == "#send"
    assert result["assessment"]["truthGateEligible"] is False
    assert result["assessment"]["blockedClaims"] == [
        {
            "claimId": "claim.submit.role",
            "status": "inferred",
            "subject": "control.submit",
            "predicate": "semantic-role",
        }
    ]
    assert result["frozen"] is True


def test_verified_claim_requires_deterministic_validator_evidence() -> None:
    result = run_node_json(
        f"""
        let invalidCode = "";
        let invalidDiagnostics = [];
        try {{
          epistemic.createClaim({{
            claimId: "claim.submit.verified",
            subject: "control.submit",
            predicate: "semantic-role",
            value: "submit",
            status: "verified",
            sources: [{source_js("a11y.submit", value_locator="#send")}],
            confidence: 1,
            contradictions: [],
            observedAt: "2026-07-30T18:00:00Z",
            repositoryFingerprint: "repo:abc123",
            validatorResults: [],
            requiredForTruthGate: true
          }});
        }} catch (error) {{
          invalidCode = error.code;
          invalidDiagnostics = error.report.diagnosticCodes;
        }}
        const verified = epistemic.createClaim({{
          claimId: "claim.submit.verified",
          subject: "control.submit",
          predicate: "semantic-role",
          value: "submit",
          status: "verified",
          sources: [{source_js("a11y.submit", value_locator="#send")}],
          confidence: 1,
          contradictions: [],
          observedAt: "2026-07-30T18:00:00Z",
          repositoryFingerprint: "repo:abc123",
          validatorResults: [{{
            id: "result.native-form-rule",
            validatorId: "mcel.native-form-rule",
            validatorVersion: "v1",
            status: "pass",
            code: "native-submit-confirmed",
            evidenceFingerprint: "sha256:evidence",
            validatedAt: "2026-07-30T18:01:00Z"
          }}],
          requiredForTruthGate: true,
          truthGateRequirementIds: ["demo.semantic.submit-role"]
        }});
        process.stdout.write(JSON.stringify({{
          invalidCode,
          invalidDiagnostics,
          eligible: epistemic.assessTruthGate({{claims: [verified]}}).truthGateEligible
        }}));
        """
    )

    assert result["invalidCode"] == "MCEL_EPISTEMIC_CLAIM_INVALID"
    assert "verified-claim-validator-proof-missing" in result["invalidDiagnostics"]
    assert result["eligible"] is True


def test_conflicting_sources_resolve_to_ambiguous_without_precedence() -> None:
    result = run_node_json(
        f"""
        function observed(claimId, value, source) {{
          return epistemic.createClaim({{
            claimId,
            subject: "control.primary",
            predicate: "semantic-role",
            value,
            status: "observed",
            sources: [source],
            confidence: null,
            contradictions: [],
            observedAt: "2026-07-30T18:00:00Z",
            repositoryFingerprint: "repo:abc123",
            validatorResults: []
          }});
        }}
        const resolved = epistemic.resolveClaimCandidates([
          observed("claim.role.button", "button", {source_js("dom.primary", value_locator="#primary")}),
          observed("claim.role.link", "link", {source_js("a11y.primary", value_locator="AXNode:17")})
        ]);
        process.stdout.write(JSON.stringify({{
          status: resolved.status,
          value: resolved.value,
          contradictionValues: resolved.contradictions.map((item) => item.value).sort(),
          sourceIds: resolved.sources.map((item) => item.id).sort(),
          eligible: epistemic.assessTruthGate({{
            claims: [{{
              ...resolved,
              requiredForTruthGate: true,
              truthGateRequirementIds: ["demo.semantic.primary-role"]
            }}]
          }}).truthGateEligible
        }}));
        """
    )

    assert result["status"] == "ambiguous"
    assert result["value"] is None
    assert result["contradictionValues"] == ["button", "link"]
    assert result["sourceIds"] == ["a11y.primary", "dom.primary"]
    assert result["eligible"] is False


def test_declared_claim_requires_exact_explicit_authored_fields() -> None:
    result = run_node_json(
        """
        const base = {
          claimId: "claim.ridge.role",
          subject: "surface.demo",
          predicate: "surface-role",
          value: "workbench",
          status: "declared",
          confidence: 1,
          contradictions: [],
          observedAt: "2026-07-30T18:00:00Z",
          repositoryFingerprint: "repo:abc123",
          validatorResults: []
        };
        let invalidDiagnostics = [];
        try {
          epistemic.createClaim({
            ...base,
            sources: [{
              id: "ridge.surface",
              kind: "authored-ridge",
              locator: "[data-mcel-surface-id='surface.demo']",
              fingerprint: "sha256:ridge-surface"
            }]
          });
        } catch (error) {
          invalidDiagnostics = error.report.diagnosticCodes;
        }
        const declared = epistemic.createClaim({
          ...base,
          sources: [{
            id: "ridge.surface",
            kind: "authored-ridge",
            locator: "[data-mcel-surface-id='surface.demo']@data-mcel-surface-role",
            fingerprint: "sha256:ridge-surface",
            explicit: true,
            declaredFields: ["data-mcel-surface-role"]
          }]
        });
        process.stdout.write(JSON.stringify({
          invalidDiagnostics,
          status: declared.status,
          declaredFields: declared.sources[0].declaredFields
        }));
        """
    )

    assert "declared-claim-explicit-source-missing" in result["invalidDiagnostics"]
    assert result["status"] == "declared"
    assert result["declaredFields"] == ["data-mcel-surface-role"]


def test_duplicate_claim_ids_fail_closed_before_truth_evaluation() -> None:
    result = run_node_json(
        f"""
        const base = {{
          claimId: "claim.duplicate",
          subject: "control.primary",
          predicate: "semantic-role",
          value: "button",
          status: "observed",
          sources: [{source_js("dom.primary", value_locator="#primary")}],
          confidence: null,
          contradictions: [],
          observedAt: "2026-07-30T18:00:00Z",
          repositoryFingerprint: "repo:abc123",
          validatorResults: []
        }};
        let code = "";
        let diagnostics = [];
        try {{
          epistemic.assessTruthGate({{claims: [base, {{...base}}]}});
        }} catch (error) {{
          code = error.code;
          diagnostics = error.report.diagnosticCodes;
        }}
        process.stdout.write(JSON.stringify({{code, diagnostics}}));
        """
    )

    assert result["code"] == "MCEL_EPISTEMIC_CLAIM_SET_INVALID"
    assert result["diagnostics"] == ["duplicate-claim-id"]
