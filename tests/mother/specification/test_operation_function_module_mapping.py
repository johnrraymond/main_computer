from __future__ import annotations

import re

import pytest

from tests.mother.support.traceability import (
    MODULE_RE,
    MotherDocuments,
    functionality_ids,
    functionality_module_rows,
    module_ids,
    operation_functionality_references,
    operation_ids,
    section,
)


pytestmark = pytest.mark.mother_specification


def test_every_operation_has_a_functionality_pipeline() -> None:
    docs = MotherDocuments.load()
    mapping = operation_functionality_references(docs)
    assert set(mapping) == set(operation_ids(docs))
    assert all(mapping[operation] for operation in mapping)


def test_every_functionality_has_one_module_chain() -> None:
    docs = MotherDocuments.load()
    rows = functionality_module_rows(docs)
    assert set(rows) == set(functionality_ids(docs))
    assert all(rows[functionality] for functionality in rows)


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
