from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"

EXPECTED_GUARANTEES = {
    "mcel.contract.source-intent-is-input.v1",
    "mcel.contract.generated-runtime-is-discardable.v1",
    "mcel.contract.serializer-cleans-runtime-state.v1",
    "mcel.contract.repair-is-schema-bounded.v1",
    "mcel.contract.validation-is-reporting-not-trust.v1",
    "mcel.contract.browser-facts-are-runtime-only.v1",
}


def test_mcel_contract_manifest_is_explicit_and_bounded() -> None:
    contract = (SCRIPTS / "mcel-contract.js").read_text(encoding="utf-8")

    assert "const contractGuarantees = Object.freeze([" in contract
    assert "function listContractGuarantees()" in contract
    assert "function guaranteeById(id)" in contract
    assert "function buildContractEnvelope()" in contract
    assert 'kind: "mcel-contract-envelope"' in contract

    ids = set(re.findall(r'id: "(mcel\.contract\.[^"]+)"', contract))
    assert ids == EXPECTED_GUARANTEES

    for guarantee_id in EXPECTED_GUARANTEES:
        start = contract.index(f'id: "{guarantee_id}"')
        block = contract[start:contract.find("        Object.freeze({", start + 1)]
        if not block:
            block = contract[start:contract.find("      ]);", start)]
        assert 'status: "executable"' in block
        assert "scope:" in block
        assert "guarantee:" in block
        assert "absoluteWhen: Object.freeze([" in block
        assert "nonGuarantees: Object.freeze([" in block
        assert "failureMode:" in block
        assert "evidenceTests: Object.freeze([" in block

    assert "MCEL is a better platform replacement without evidence gates" not in contract


def test_mcel_contract_tests_cover_every_executable_guarantee() -> None:
    engine = (SCRIPTS / "mcel-engine.js").read_text(encoding="utf-8")

    assert "function runContractTests()" in engine
    assert "guaranteeResults" in engine
    assert "failedGuarantees" in engine
    assert "uncoveredGuarantees" in engine
    assert "MCEL_CONTRACT_GUARANTEES_PASSED" in engine
    assert "MCEL_CONTRACT_GUARANTEES_FAILED" in engine

    for guarantee_id in EXPECTED_GUARANTEES:
        assert guarantee_id in engine

    assert 'supportingTests.length > 0 && supportingTests.every((testResult) => testResult.passed)' in engine
    assert 'uncoveredGuarantees.length' in engine


def test_mcel_contract_documentation_rejects_law_as_a_guarantee() -> None:
    doc = (ROOT / "pretty_docs" / "mcel-contract-guarantees.md").read_text(encoding="utf-8")

    assert "a law is only an implementation module" in doc
    assert "not a contract" in doc
    assert "absolute only inside their stated scopes" in doc
    assert "failed guarantee" in doc
    assert "uncovered guarantee" in doc

    for guarantee_id in EXPECTED_GUARANTEES:
        assert guarantee_id in doc
