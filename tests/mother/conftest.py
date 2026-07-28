from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.mother.support.traceability import (
    MotherDocuments,
    blocked_module_ids,
    functionality_ids,
    gap_ids,
    hard_contract_open_operations,
    module_ids,
    operation_ids,
    requirement_ids,
)


def _as_list(marker: pytest.Mark, key: str) -> list[str]:
    value = marker.kwargs.get(key, [])
    if isinstance(value, str):
        return [value]
    return list(value)


def pytest_collection_modifyitems(config: Any, items: list[pytest.Item]) -> None:
    docs = MotherDocuments.load(Path(str(config.rootpath)))
    known = {
        "requirements": set(requirement_ids(docs)),
        "operations": set(operation_ids(docs)),
        "functionalities": set(functionality_ids(docs)),
        "modules": set(module_ids(docs)),
    }
    gaps = gap_ids(docs)
    blocked_modules = blocked_module_ids(docs)
    hard_open_operations = hard_contract_open_operations(docs)
    errors: list[str] = []

    for item in items:
        marker = item.get_closest_marker("mother_contract")
        if marker is None:
            continue

        metadata = {key: _as_list(marker, key) for key in known}
        if not metadata["modules"]:
            errors.append(f"{item.nodeid}: mother_contract requires at least one module ID")

        for key, identifiers in metadata.items():
            unknown = set(identifiers) - known[key]
            if unknown:
                errors.append(f"{item.nodeid}: unknown {key}: {sorted(unknown)}")

        gap_refs = set(metadata["functionalities"]) & gaps
        if gap_refs:
            errors.append(
                f"{item.nodeid}: documented gaps cannot be referenced as resolved "
                f"functionalities: {sorted(gap_refs)}"
            )

        mutating = bool(marker.kwargs.get("mutating", False))
        proves_open_gate = bool(marker.kwargs.get("proves_open_gate", False))
        touches_open = bool(
            set(metadata["operations"]) & hard_open_operations
            or set(metadata["modules"]) & blocked_modules
        )
        if mutating and touches_open and not proves_open_gate:
            errors.append(
                f"{item.nodeid}: contract-open mutation tests must set "
                "proves_open_gate=True and prove failure before locks/effects"
            )

    if errors:
        raise pytest.UsageError("Mother traceability collection errors:\n- " + "\n- ".join(errors))
