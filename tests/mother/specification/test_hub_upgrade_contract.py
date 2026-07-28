from __future__ import annotations

import pytest

from tests.mother.support.traceability import (
    MotherDocuments,
    functionality_module_rows,
    module_records,
    operation_functionality_references,
    operation_stage_sequences,
)


pytestmark = pytest.mark.mother_specification


def test_upgrade_hub_uses_ordinary_d026_without_membership_or_reseal() -> None:
    docs = MotherDocuments.load()
    sequence = operation_functionality_references(docs)["MOTHER-OP-UPGRADE-HUB"]
    assert "MOTHER-OF-AUTH-004" in sequence
    assert "MOTHER-OF-AUTH-001" in sequence
    assert "MOTHER-OF-AUTH-002" in sequence
    assert not [item for item in sequence if item.startswith("MOTHER-OF-MEM-")]
    assert not [item for item in sequence if item.startswith("MOTHER-OF-RSL-")]


def test_upgrade_hub_rollout_order_is_authority_then_artifacts_then_frames() -> None:
    docs = MotherDocuments.load()
    stages = operation_stage_sequences(docs)["MOTHER-OP-UPGRADE-HUB"]
    do_stage = next(stage for stage in stages if "`do`" in stage.heading)
    sequence = do_stage.functionalities

    pending_commit = sequence.index("MOTHER-OF-AUTH-006")
    artifact_stage = sequence.index("MOTHER-OF-REL-004")
    availability_progress = sequence.index("MOTHER-OF-AUTH-020")
    prestate = sequence.index("MOTHER-OF-REL-007")
    deploy = sequence.index("MOTHER-OF-REL-008")
    verify = sequence.index("MOTHER-OF-REL-010")
    promote = sequence.index("MOTHER-OF-RB-004")
    participant_progress = sequence.index("MOTHER-OF-AUTH-019", promote)
    convergence = sequence.index("MOTHER-OF-REL-012")

    assert (
        pending_commit
        < artifact_stage
        < availability_progress
        < prestate
        < deploy
        < verify
        < promote
        < participant_progress
        < convergence
    )
    assert sequence.count("MOTHER-OF-AUTH-020") == 1
    assert sequence.count("MOTHER-OF-REL-010") == 1


def test_upgrade_hub_finalization_is_typed_and_topology_preserving() -> None:
    docs = MotherDocuments.load()
    required = (
        "closed typed `authoritative_delta`",
        "leaves topology and topology epoch unchanged",
        "successor_release_generation",
        "legacy-baseline",
        "rollback_artifact_closure_root",
        "operator-approved-outage",
        "descriptor_payload_hash",
        "signature_envelope_hash",
        "validated_signer_policy_hash",
    )
    for term in required:
        assert term in docs.mother
    assert "MOTHER-DESIGN-030: authoritative-schema-preserving-hub-release-rollout" in docs.mother


def test_release_policy_is_pure_and_service_effects_have_one_owner() -> None:
    docs = MotherDocuments.load()
    records = module_records(docs)
    assert "pure" in records["MOTHER-OFM-SVC-002"].contract
    assert "performs no deployment or external effect" in records["MOTHER-OFM-SVC-002"].contract
    assert "sole prepared Coolify/service-effect adapter" in records["MOTHER-OFM-SVC-001"].contract

    rows = functionality_module_rows(docs)
    for functionality in ("MOTHER-OF-REL-004", "MOTHER-OF-REL-008", "MOTHER-OF-REL-011"):
        assert "MOTHER-OFM-SVC-001" in rows[functionality]
    assert "MOTHER-OFM-XPORT-002" in rows["MOTHER-OF-REL-004"]
    assert "MOTHER-OFM-XPORT-002" in rows["MOTHER-OF-REL-008"]


def test_artifact_staging_and_deployment_create_request_before_dispatch() -> None:
    docs = MotherDocuments.load()
    rows = functionality_module_rows(docs)

    for functionality in ("MOTHER-OF-REL-004", "MOTHER-OF-REL-008"):
        chain = rows[functionality]
        request = chain.index("MOTHER-OFM-XPORT-003")
        dispatch = chain.index("MOTHER-OFM-XPORT-002")
        service = chain.index("MOTHER-OFM-SVC-001")
        assert request < dispatch < service

    rel008 = docs.modules[
        docs.modules.index("| `MOTHER-OF-REL-008`"):
        docs.modules.index("\n", docs.modules.index("| `MOTHER-OF-REL-008`"))
    ]
    assert "target_handler=MOTHER-OFM-SVC-001.drain_and_apply_prepared_release" in rel008


def test_preparatory_progress_uses_its_own_transition_validator() -> None:
    docs = MotherDocuments.load()
    rows = functionality_module_rows(docs)
    auth020 = docs.modules[
        docs.modules.index("| `MOTHER-OF-AUTH-020`"):
        docs.modules.index("\n", docs.modules.index("| `MOTHER-OF-AUTH-020`"))
    ]
    assert "MOTHER-OFM-RB-002" not in rows["MOTHER-OF-AUTH-020"]
    assert "MOTHER-OFM-CORE-012" in rows["MOTHER-OF-AUTH-020"]
    assert "validate_preparatory_progress_transition" in auth020
    assert "validate_authoritative_delta" not in auth020
    assert "MOTHER-OFM-RB-002" in rows["MOTHER-OF-AUTH-019"]


def test_rel012_is_one_deterministic_calculation_reused_at_finalize() -> None:
    docs = MotherDocuments.load()
    sequence = operation_functionality_references(docs)["MOTHER-OP-UPGRADE-HUB"]
    assert sequence.count("MOTHER-OF-REL-012") == 2
    row = docs.modules[
        docs.modules.index("| `MOTHER-OF-REL-012`"):
        docs.modules.index("\n", docs.modules.index("| `MOTHER-OF-REL-012`"))
    ]
    assert "derive_rollout_convergence_and_delta" in row
    assert "repeated execution MUST be byte-identical" in row
    assert "construct or revalidate" not in row.lower()


def test_detached_signature_construction_is_acyclic_and_policy_is_independent() -> None:
    docs = MotherDocuments.load()
    design = docs.mother[
        docs.mother.index("#### Release descriptor and trust authority"):
        docs.mother.index("#### Authoritative release state and typed delta")
    ]
    assert "descriptor payload bytes without signature-envelope or signer-policy fields" in design
    assert "descriptor_payload_hash" in design
    assert "detached signature envelope signs descriptor_payload_hash" in design
    assert "--signature-envelope <path-or-content-hash>" in design
    assert "--signer-policy <path-or-content-hash>" in design
    payload_block = design[
        design.index("schema: mother.hub-release-descriptor.v1"):
        design.index("```", design.index("schema: mother.hub-release-descriptor.v1"))
    ]
    assert "signature_envelope_hash" not in payload_block
    assert "signer_policy" not in payload_block

def test_upgrade_hub_cli_requires_detached_signature_envelope() -> None:
    docs = MotherDocuments.load()
    operation = docs.operations[
        docs.operations.index("## 12. Hub release upgrade"):
    ]
    assert "--release-descriptor <path-or-content-hash>" in operation
    assert "--signature-envelope <path-or-content-hash>" in operation
    assert "[--signature-envelope" not in operation


def test_rel011_uses_only_the_restoration_handler_chain() -> None:
    docs = MotherDocuments.load()
    rel008 = docs.modules[
        docs.modules.index("| `MOTHER-OF-REL-008`"):
        docs.modules.index("\n", docs.modules.index("| `MOTHER-OF-REL-008`"))
    ]
    rel011 = docs.modules[
        docs.modules.index("| `MOTHER-OF-REL-011`"):
        docs.modules.index("\n", docs.modules.index("| `MOTHER-OF-REL-011`"))
    ]

    assert "drain_and_apply_prepared_release" in rel008
    assert "drain_and_apply_prepared_release" not in rel011
    assert rel011.index("drain_for_restore") < rel011.index("restore_release")
    assert rel011.index("restore_release") < rel011.index("verify_release")
    assert rel011.index("verify_release") < rel011.index("restore_eligibility")
    assert rel011.index("restore_eligibility") < rel011.index("verify_restored")


def test_release_types_separate_payload_signature_authorization_and_state() -> None:
    docs = MotherDocuments.load()
    required_types = (
        "HubReleaseDescriptorPayload",
        "HubReleaseSignatureEnvelope",
        "HubReleaseAuthorization",
        "HubComponentReleaseState",
    )
    for name in required_types:
        assert f"| `{name}` |" in docs.modules

    payload_row = next(
        line for line in docs.modules.splitlines()
        if line.startswith("| `HubReleaseDescriptorPayload` |")
    )
    assert "no signature-envelope or signer-policy field" in payload_row
    assert "| `HubReleaseDescriptor` |" not in docs.modules

