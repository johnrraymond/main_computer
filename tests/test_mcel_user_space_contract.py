from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"

EXPECTED_USER_CONTRACT_CLAUSES = {
    "mcel.user.source-traits-are-planning-surface.v1",
    "mcel.user.runtime-generation-is-discardable.v1",
    "mcel.user.serialization-is-source-firewall.v1",
    "mcel.user.repair-is-bounded-regeneration.v1",
    "mcel.user.validation-is-evidence-not-trust.v1",
    "mcel.user.browser-facts-are-snapshots.v1",
    "mcel.user.adoption-is-narrow-and-reversible.v1",
}

EXPECTED_EXECUTABLE_GUARANTEES = {
    "mcel.contract.source-intent-is-input.v1",
    "mcel.contract.generated-runtime-is-discardable.v1",
    "mcel.contract.serializer-cleans-runtime-state.v1",
    "mcel.contract.repair-is-schema-bounded.v1",
    "mcel.contract.validation-is-reporting-not-trust.v1",
    "mcel.contract.browser-facts-are-runtime-only.v1",
}


def _object_block(text: str, marker: str) -> str:
    start = text.index(marker)
    next_object = text.find("        Object.freeze({", start + 1)
    end = next_object if next_object != -1 else text.find("      ]);", start)
    assert end != -1
    return text[start:end]


def test_mcel_user_space_contract_manifest_is_first_class_and_bounded() -> None:
    contract = (SCRIPTS / "mcel-contract.js").read_text(encoding="utf-8")

    assert "const userSpaceContract = Object.freeze([" in contract
    assert "function listUserContractClauses()" in contract
    assert "function userContractClauseById(id)" in contract
    assert "function buildUserSpaceContract()" in contract
    assert 'kind: "mcel-user-space-contract"' in contract
    assert "stableEntrypoints: Object.freeze([" in contract

    ids = set(re.findall(r'id: "(mcel\.user\.[^"]+)"', contract))
    assert ids == EXPECTED_USER_CONTRACT_CLAUSES

    for clause_id in EXPECTED_USER_CONTRACT_CLAUSES:
        block = _object_block(contract, f'id: "{clause_id}"')
        assert "stableSurface:" in block
        assert "userCanRelyOn: Object.freeze([" in block
        assert "userMustProvide: Object.freeze([" in block
        assert "userMustNotAssume: Object.freeze([" in block
        assert "failClosedSignal:" in block
        assert "evidenceGuarantees: Object.freeze([" in block

        guarantees = set(re.findall(r'"(mcel\.contract\.[^"]+)"', block))
        assert guarantees
        assert guarantees <= EXPECTED_EXECUTABLE_GUARANTEES


def test_mcel_public_facade_exposes_user_space_contract() -> None:
    core = (SCRIPTS / "mcel-core.js").read_text(encoding="utf-8")

    assert "function buildUserSpaceContract()" in core
    assert "function listUserContractClauses()" in core
    assert "contract.buildUserSpaceContract()" in core
    assert "contract.listUserContractClauses()" in core

    return_block = core[core.index("return Object.freeze({") :]
    assert "buildUserSpaceContract," in return_block
    assert "listUserContractClauses," in return_block


def test_mcel_user_space_contract_is_registered_and_documented() -> None:
    index = json.loads((ROOT / "pretty_docs" / "index.json").read_text(encoding="utf-8"))
    documents = index.get("documents", [])

    paths = {item.get("path"): item for item in documents}
    assert paths["mcel-user-space-contract.md"]["title"] == "MCEL User-Space Contract"
    assert paths["mcel-user-space-contract.md"]["kind"] == "markdown"
    assert isinstance(paths["mcel-user-space-contract.md"]["order"], int)
    assert paths["mcel-contract-guarantees.md"]["title"] == "MCEL Contract Guarantees"

    doc = (ROOT / "pretty_docs" / "mcel-user-space-contract.md").read_text(encoding="utf-8")
    required_phrases = [
        "The short contract",
        "The planning model",
        "Stable user-space clauses",
        "React can be explained",
        "A law module is implementation detail",
        "What MCEL users can rely on",
        "what they must not assume",
        "fail-closed signal",
        "Every user-space clause maps back to one or more executable contract guarantees",
        "Do not persist runtime innerHTML",
        "Adoption is narrow and reversible",
    ]
    for phrase in required_phrases:
        assert phrase in doc

    for clause_id in EXPECTED_USER_CONTRACT_CLAUSES:
        assert clause_id in doc


def test_readme_and_system_guide_point_to_user_space_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    system_guide = (ROOT / "pretty_docs" / "mcel-system-guide.md").read_text(encoding="utf-8")

    assert "pretty_docs/mcel-user-space-contract.md" in readme
    assert "user-space contract is the planning surface" in readme
    assert "McelLabContract.buildUserSpaceContract()" in system_guide
    assert "MCEL.buildUserSpaceContract()" in system_guide
    assert "source traits are the durable input" in system_guide
