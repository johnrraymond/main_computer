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
    availability_progress = sequence.index(
        "MOTHER-OF-AUTH-019", artifact_stage
    )
    prestate = sequence.index("MOTHER-OF-REL-007")
    deploy = sequence.index("MOTHER-OF-REL-008")
    verify = sequence.index("MOTHER-OF-REL-010")
    promote = sequence.index("MOTHER-OF-RB-004")
    participant_progress = sequence.index("MOTHER-OF-AUTH-019", promote)

    assert (
        pending_commit
        < artifact_stage
        < availability_progress
        < prestate
        < deploy
        < verify
        < promote
        < participant_progress
    )


def test_upgrade_hub_finalization_is_typed_and_topology_preserving() -> None:
    docs = MotherDocuments.load()
    required = (
        "closed typed `authoritative_delta`",
        "leaves topology and topology epoch unchanged",
        "successor_release_generation",
        "legacy-baseline",
        "rollback_artifact_closure_root",
        "operator-approved-outage",
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
    for functionality in ("MOTHER-OF-REL-008", "MOTHER-OF-REL-011"):
        assert "MOTHER-OFM-SVC-001" in rows[functionality]
        assert "MOTHER-OFM-SVC-002" not in rows[functionality]
