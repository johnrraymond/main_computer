from __future__ import annotations

import pytest

from tests.mother.support.traceability import (
    MotherDocuments,
    blocked_module_ids,
    hard_contract_open_operations,
    section,
)


pytestmark = pytest.mark.mother_specification


def test_contract_open_operations_remain_declared_open() -> None:
    docs = MotherDocuments.load()
    assert hard_contract_open_operations(docs) == {
        "MOTHER-OP-SCHEMA-MIGRATION",
        "MOTHER-OP-IDENTITY-ROTATION",
    }


def test_contract_open_module_calls_fail_before_locks_or_effects() -> None:
    docs = MotherDocuments.load()
    blocked = blocked_module_ids(docs)
    assert {
        "MOTHER-OFM-APP-011",
        "MOTHER-OFM-MAINT-001",
        "MOTHER-OFM-APP-015",
        "MOTHER-OFM-MAINT-002",
        "MOTHER-OFM-APP-016",
    } <= blocked
    governance = section(docs.modules, "## 16. Test-first contract governance", "## 17.")
    assert (
        "before the first lock, staging action, durable write, or external effect"
        in " ".join(governance.split())
    )
    assert "MOTHER_OPEN_*" in governance
