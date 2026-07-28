from __future__ import annotations

import pytest

from tests.mother.support.traceability import (
    MotherDocuments,
    external_effect_owner_ids,
    functionality_ids,
    functionality_module_rows,
    module_dependency_violations,
    module_ids,
    module_records,
    operation_functionality_references,
    operation_ids,
    operation_stage_bindings,
    operation_stage_sequences,
    section,
    MODULE_RE,
)


pytestmark = pytest.mark.mother_specification


def test_every_operation_has_an_ordered_functionality_pipeline() -> None:
    docs = MotherDocuments.load()
    mapping = operation_functionality_references(docs)
    assert tuple(mapping) == tuple(operation_ids(docs))
    assert all(mapping[operation] for operation in mapping)


def test_every_functionality_has_exactly_one_ordered_module_chain() -> None:
    docs = MotherDocuments.load()
    rows = functionality_module_rows(docs)
    assert set(rows) == set(functionality_ids(docs))
    assert all(rows[functionality] for functionality in rows)


def test_reseal_order_is_preserved_not_reduced_to_sets() -> None:
    docs = MotherDocuments.load()
    sequence = operation_functionality_references(docs)["MOTHER-OP-RESEAL-STATE"]
    expected = (
        "MOTHER-OF-RSL-008",
        "MOTHER-OF-MEM-007",
        "MOTHER-OF-MEM-008",
        "MOTHER-OF-RSL-014",
    )
    positions = [sequence.index(identifier) for identifier in expected]
    assert positions == sorted(positions)


def test_operation_stage_bindings_reference_parent_sections_in_order() -> None:
    docs = MotherDocuments.load()
    stages = operation_stage_sequences(docs)
    bindings = operation_stage_bindings(docs)
    assert set(bindings) == set(operation_ids(docs))
    for operation, references in bindings.items():
        available = [stage.reference for stage in stages[operation]]
        assert all(reference in available for reference in references)
        positions = [available.index(reference) for reference in references]
        assert positions == sorted(positions)


def test_every_referenced_module_is_declared() -> None:
    docs = MotherDocuments.load()
    declared = set(module_ids(docs))
    composition = section(
        docs.modules,
        "## 7. Functionality-to-module composition",
        "## 8. Operation and stage binding",
    )
    assert set(MODULE_RE.findall(composition)) <= declared


def test_path_owner_rows_are_unique_and_reference_declared_modules() -> None:
    docs = MotherDocuments.load()
    declared = set(module_ids(docs))
    ownership = section(docs.modules, "### 9.2 Path-to-owner map", "### 9.3 Mother context")
    state_areas: list[str] = []
    for line in ownership.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        state_areas.append(cells[0])
        assert set(MODULE_RE.findall(cells[1])) <= declared
    assert len(state_areas) == len(set(state_areas))


def test_declared_external_effect_owners_are_unique_adapter_modules() -> None:
    docs = MotherDocuments.load()
    records = module_records(docs)
    owners = external_effect_owner_ids(docs)
    assert owners
    assert len(owners) == len(set(owners))
    paths = [records[owner].path for owner in owners]
    assert len(paths) == len(set(paths))
    for owner in owners:
        assert "adapter" in records[owner].contract.lower()


def test_implemented_mother_imports_follow_dependency_direction() -> None:
    docs = MotherDocuments.load()
    assert module_dependency_violations(docs) == []
