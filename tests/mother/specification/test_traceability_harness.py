from __future__ import annotations

from dataclasses import replace

import pytest

from tests.mother.support.fixtures import OpenContractGuard
from tests.mother.support.traceability import (
    ContractTrace,
    MotherDocuments,
    faultpoint_bearing_functionalities,
    functionality_method_rows,
    functionality_module_rows,
    module_public_method_rows,
    validate_contract_trace,
)


pytestmark = pytest.mark.mother_specification


def test_valid_trace_preserves_real_ancestry() -> None:
    docs = MotherDocuments.load()
    trace = ContractTrace(
        requirements=("MOTHER-REQ-002",),
        operations=("MOTHER-OP-DIAGNOSE",),
        functionalities=("MOTHER-OF-OBS-001",),
        modules=("MOTHER-OFM-CORE-011", "MOTHER-OFM-STATE-001"),
        methods=("MOTHER-OFM-STATE-001.read_stable_head",),
    )
    assert validate_contract_trace(trace, docs) == []


def test_existing_but_unrelated_identifiers_are_rejected() -> None:
    docs = MotherDocuments.load()
    trace = ContractTrace(
        requirements=("MOTHER-REQ-026",),
        operations=("MOTHER-OP-DIAGNOSE",),
        functionalities=("MOTHER-OF-RSL-015",),
        modules=("MOTHER-OFM-CORE-003",),
    )
    errors = validate_contract_trace(trace, docs)
    assert any("not in any claimed operation pipeline" in error for error in errors)
    assert any("not in any claimed functionality chain" in error for error in errors)


def test_out_of_order_functionality_metadata_is_rejected() -> None:
    docs = MotherDocuments.load()
    trace = ContractTrace(
        requirements=("MOTHER-REQ-026",),
        operations=("MOTHER-OP-RESEAL-STATE",),
        functionalities=("MOTHER-OF-RSL-014", "MOTHER-OF-RSL-008"),
        modules=("MOTHER-OFM-REC-003",),
    )
    errors = validate_contract_trace(trace, docs)
    assert any("functionalities are out of order" in error for error in errors)


def test_duplicate_functionality_to_module_rows_are_rejected() -> None:
    docs = MotherDocuments.load()
    composition = docs.modules[
        docs.modules.index("## 7. Functionality-to-module composition"):
        docs.modules.index("## 8. Operation and stage binding")
    ]
    duplicate_row = next(
        line for line in composition.splitlines()
        if line.startswith("| `MOTHER-OF-OBS-001`")
    )
    bad_modules = docs.modules.replace(
        "## 8. Operation and stage binding",
        duplicate_row + "\n\n## 8. Operation and stage binding",
        1,
    )
    with pytest.raises(
        AssertionError,
        match="duplicate functionality-to-module row: MOTHER-OF-OBS-001",
    ):
        functionality_module_rows(replace(docs, modules=bad_modules))


def test_contract_open_mutation_requires_guard_fixture_and_exact_error() -> None:
    docs = MotherDocuments.load()
    trace = ContractTrace(
        requirements=("MOTHER-REQ-001",),
        operations=("MOTHER-OP-SCHEMA-MIGRATION",),
        functionalities=("MOTHER-OF-MIG-007",),
        modules=("MOTHER-OFM-APP-015",),
        mutating=True,
        open_error="MOTHER_OPEN_MIGRATION_AUTHORITY",
    )
    errors = validate_contract_trace(trace, docs)
    assert any("must request mother_open_contract_guard" in error for error in errors)
    assert validate_contract_trace(
        trace,
        docs,
        fixture_names=("mother_open_contract_guard",),
    ) == []


def test_contract_open_guard_proves_exact_zero_effect_rejection() -> None:
    guard = OpenContractGuard("MOTHER_OPEN_MIGRATION_AUTHORITY")
    guard.record_error({"code": "MOTHER_OPEN_MIGRATION_AUTHORITY"})
    guard.verify()


def test_contract_open_guard_rejects_any_side_effect() -> None:
    guard = OpenContractGuard("MOTHER_OPEN_ROTATION_AUTHORITY")
    guard.record_error("MOTHER_OPEN_ROTATION_AUTHORITY")
    guard.record_lock_acquisition()
    with pytest.raises(AssertionError, match="acquired 1 lock"):
        guard.verify()


def test_documented_shared_core_dependencies_are_valid_ancestry() -> None:
    docs = MotherDocuments.load()
    observation_trace = ContractTrace(
        requirements=("MOTHER-REQ-002",),
        operations=("MOTHER-OP-DIAGNOSE",),
        functionalities=("MOTHER-OF-OBS-001",),
        modules=(
            "MOTHER-OFM-CORE-001",
            "MOTHER-OFM-CORE-002",
            "MOTHER-OFM-CORE-005",
        ),
    )
    authority_trace = ContractTrace(
        requirements=("MOTHER-REQ-005",),
        operations=("MOTHER-OP-ADD-NODE",),
        functionalities=("MOTHER-OF-AUTH-004",),
        modules=("MOTHER-OFM-CORE-013",),
    )
    assert validate_contract_trace(observation_trace, docs) == []
    assert validate_contract_trace(authority_trace, docs) == []

def test_core013_is_functionality_specific_not_module_class_inferred() -> None:
    docs = MotherDocuments.load()
    declared = faultpoint_bearing_functionalities(docs)
    assert "MOTHER-OF-AUTH-004" in declared
    assert "MOTHER-OF-AUTH-016" in declared
    assert "MOTHER-OF-SYNC-007" in declared
    assert "MOTHER-OF-REL-004" in declared
    assert "MOTHER-OF-REL-008" in declared
    assert "MOTHER-OF-REL-009" in declared
    assert "MOTHER-OF-OBS-001" not in declared
    assert "MOTHER-OF-OBS-013" not in declared

    read_only_trace = ContractTrace(
        requirements=("MOTHER-REQ-002",),
        operations=("MOTHER-OP-DIAGNOSE",),
        functionalities=("MOTHER-OF-OBS-013",),
        modules=("MOTHER-OFM-CORE-013",),
    )
    errors = validate_contract_trace(read_only_trace, docs)
    assert any(
        "unsupported implicit core ancestry for MOTHER-OF-OBS-013" in error
        or "not in any claimed functionality chain" in error
        for error in errors
    )

    boundary_trace = ContractTrace(
        requirements=("MOTHER-REQ-005",),
        operations=("MOTHER-OP-ADD-NODE",),
        functionalities=("MOTHER-OF-AUTH-004",),
        modules=("MOTHER-OFM-CORE-013",),
    )
    assert validate_contract_trace(boundary_trace, docs) == []

@pytest.mark.parametrize(
    ("requirement", "operation", "functionality"),
    (
        ("MOTHER-REQ-023", "MOTHER-OP-RESEAL-STATE", "MOTHER-OF-AUTH-016"),
        ("MOTHER-REQ-018", "MOTHER-OP-SYNC-STATE", "MOTHER-OF-SYNC-007"),
        ("MOTHER-REQ-027", "MOTHER-OP-UPGRADE-HUB", "MOTHER-OF-REL-009"),
    ),
)
def test_documented_reconciliation_transitions_accept_core013(
    requirement: str,
    operation: str,
    functionality: str,
) -> None:
    docs = MotherDocuments.load()
    trace = ContractTrace(
        requirements=(requirement,),
        operations=(operation,),
        functionalities=(functionality,),
        modules=("MOTHER-OFM-CORE-013",),
    )
    assert validate_contract_trace(trace, docs) == []


def test_method_trace_requires_exact_functionality_chain_membership() -> None:
    docs = MotherDocuments.load()
    trace = ContractTrace(
        requirements=("MOTHER-REQ-015",),
        operations=("MOTHER-OP-DIAGNOSE",),
        functionalities=("MOTHER-OF-OBS-016",),
        modules=("MOTHER-OFM-CORE-007",),
        methods=("MOTHER-OFM-CORE-007.read_capabilities",),
    )
    assert validate_contract_trace(trace, docs) == []

    unrelated = replace(
        trace,
        methods=("MOTHER-OFM-CORE-007.freeze_capability_set",),
    )
    errors = validate_contract_trace(unrelated, docs)
    assert any("not in any claimed functionality method chain" in error for error in errors)




def test_method_qualified_wave1c_modules_require_methods_metadata() -> None:
    docs = MotherDocuments.load()
    trace = ContractTrace(
        requirements=("MOTHER-REQ-015",),
        operations=("MOTHER-OP-DIAGNOSE",),
        functionalities=("MOTHER-OF-OBS-016",),
        modules=("MOTHER-OFM-CORE-007",),
    )

    errors = validate_contract_trace(
        trace,
        docs,
        direct_methods=("MOTHER-OFM-CORE-007.read_capabilities",),
    )

    assert any(
        "requires methods metadata for method-qualified modules" in error
        for error in errors
    )
    assert any(
        "direct public-method call MOTHER-OFM-CORE-007.read_capabilities "
        "is omitted from methods metadata" in error
        for error in errors
    )


def test_documented_method_rows_include_wave1c_chains() -> None:
    rows = functionality_method_rows(MotherDocuments.load())
    assert rows["MOTHER-OF-OBS-016"] == (
        "MOTHER-OFM-CORE-006.validate_object",
        "MOTHER-OFM-CORE-007.read_capabilities",
        "MOTHER-OFM-CORE-007.require_capabilities",
        "MOTHER-OFM-CORE-010.decode_compatibility_report",
        "MOTHER-OFM-CORE-010.check_peer_compatibility",
    )
    assert rows["MOTHER-OF-OBS-015"] == (
        "MOTHER-OFM-OBS-006.classify",
        "MOTHER-OFM-CORE-009.build_allowed_commands_report",
        "MOTHER-OFM-CORE-009.render_json",
        "MOTHER-OFM-CORE-009.render_text",
        "MOTHER-OFM-CORE-009.render_allowed_commands",
    )
    assert rows["MOTHER-OF-OBS-018"] == (
        "MOTHER-OFM-CORE-008.store_evidence",
        "MOTHER-OFM-CORE-008.load_evidence",
        "MOTHER-OFM-CORE-008.redact_copy",
        "MOTHER-OFM-CORE-008.export_manifest",
        "MOTHER-OFM-CORE-008.load_export_result",
        "MOTHER-OFM-CORE-009.build_evidence_report",
        "MOTHER-OFM-CORE-009.render_json",
        "MOTHER-OFM-CORE-009.render_text",
    )
    assert rows["MOTHER-OF-CTL-010"] == (
        "MOTHER-OFM-CORE-010.freeze_contract_versions",
        "MOTHER-OFM-CORE-007.freeze_capability_set",
    )
    assert rows["MOTHER-OF-MIG-001"] == (
        "MOTHER-OFM-CORE-006.decode_schema_catalog",
        "MOTHER-OFM-CORE-006.load_schema",
        "MOTHER-OFM-CORE-006.validate_schema_transition",
        "MOTHER-OFM-CORE-007.require_capabilities",
    )


def test_direct_public_method_calls_must_be_declared_in_metadata() -> None:
    docs = MotherDocuments.load()
    public = module_public_method_rows(docs)
    assert "decode_schema_catalog" in public["MOTHER-OFM-CORE-006"]
    trace = ContractTrace(
        requirements=("MOTHER-REQ-015",),
        operations=("MOTHER-OP-SCHEMA-MIGRATION",),
        functionalities=("MOTHER-OF-MIG-001",),
        modules=("MOTHER-OFM-CORE-006",),
        methods=("MOTHER-OFM-CORE-006.load_schema",),
    )

    errors = validate_contract_trace(
        trace,
        docs,
        direct_methods=("MOTHER-OFM-CORE-006.decode_schema_catalog",),
    )

    assert any(
        "direct public-method call MOTHER-OFM-CORE-006.decode_schema_catalog "
        "is omitted from methods metadata" in error
        for error in errors
    )


def test_method_metadata_is_required_for_core008_and_core009() -> None:
    docs = MotherDocuments.load()
    for module_id, functionality, operation in (
        ("MOTHER-OFM-CORE-008", "MOTHER-OF-OBS-018", "MOTHER-OP-EVIDENCE-EXPORT"),
        ("MOTHER-OFM-CORE-009", "MOTHER-OF-OBS-015", "MOTHER-OP-DIAGNOSE"),
    ):
        errors = validate_contract_trace(
            ContractTrace(
                requirements=("MOTHER-REQ-002",),
                operations=(operation,),
                functionalities=(functionality,),
                modules=(module_id,),
                methods=(),
            ),
            docs,
        )
        assert any(
            "requires methods metadata for method-qualified modules" in error
            for error in errors
        )


def test_method_metadata_is_required_for_state001_and_state002() -> None:
    docs = MotherDocuments.load()
    cases = (
        (
            "MOTHER-OFM-STATE-001",
            "MOTHER-OF-OBS-003",
            "MOTHER-OP-DIAGNOSE",
            "MOTHER-OFM-STATE-001.validate_lineage",
        ),
        (
            "MOTHER-OFM-STATE-002",
            "MOTHER-OF-OBS-003",
            "MOTHER-OP-DIAGNOSE",
            "MOTHER-OFM-STATE-002.prepare_replay",
        ),
    )
    for module_id, functionality, operation, direct_method in cases:
        errors = validate_contract_trace(
            ContractTrace(
                requirements=("MOTHER-REQ-002",),
                operations=(operation,),
                functionalities=(functionality,),
                modules=(module_id,),
                methods=(),
            ),
            docs,
            direct_methods=(direct_method,),
        )
        assert any(
            "requires methods metadata for method-qualified modules" in error
            for error in errors
        )
        assert any(
            f"direct public-method call {direct_method} "
            "is omitted from methods metadata" in error
            for error in errors
        )


def test_state_replay_functionality_rows_include_proof_factories() -> None:
    rows = functionality_method_rows(MotherDocuments.load())
    expected_validation_tail = (
        "MOTHER-OFM-STATE-001.validate_lineage",
        "MOTHER-OFM-STATE-001.authorize_lineage",
        "MOTHER-OFM-AUTH-003.validate_bundle",
        "MOTHER-OFM-STATE-002.validate_checkpoint",
        "MOTHER-OFM-STATE-002.state_closure",
        "MOTHER-OFM-STATE-002.prepare_replay",
        "MOTHER-OFM-STATE-001.replay_forward",
    )
    for functionality in (
        "MOTHER-OF-SYNC-004",
        "MOTHER-OF-REC-005",
        "MOTHER-OF-PRJ-002",
    ):
        row = rows[functionality]
        positions = tuple(row.index(method) for method in expected_validation_tail)
        assert positions == tuple(sorted(positions))

    assert "MOTHER-OFM-STATE-002.build_checkpoint" in rows["MOTHER-OF-RSL-006"]
    assert "MOTHER-OFM-STATE-002.build_checkpoint" in rows["MOTHER-OF-MIG-005"]
