from __future__ import annotations

import pytest

from tests.mother.support.traceability import (
    MotherDocuments,
    design_ids,
    duplicates,
    functionality_ids,
    module_ids,
    operation_ids,
    requirement_ids,
)


pytestmark = pytest.mark.mother_specification


@pytest.mark.parametrize(
    ("loader", "expected"),
    (
        (requirement_ids, 26),
        (design_ids, 29),
        (operation_ids, 16),
        (functionality_ids, 169),
        (module_ids, 80),
    ),
)
def test_canonical_identifier_counts(loader, expected: int) -> None:
    values = loader(MotherDocuments.load())
    assert len(values) == expected
    assert not duplicates(values)


def test_identifier_sequences_are_complete() -> None:
    docs = MotherDocuments.load()
    assert requirement_ids(docs) == [f"MOTHER-REQ-{i:03d}" for i in range(1, 27)]
    assert sorted(design_ids(docs)) == [f"MOTHER-DESIGN-{i:03d}" for i in range(1, 30)]
