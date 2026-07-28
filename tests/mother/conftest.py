from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.mother.support.fixtures import OpenContractGuard
from tests.mother.support.traceability import (
    ContractTrace,
    MotherDocuments,
    validate_contract_trace,
)


def _as_tuple(marker: pytest.Mark, key: str) -> tuple[str, ...]:
    value = marker.kwargs.get(key, ())
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def pytest_collection_modifyitems(config: Any, items: list[pytest.Item]) -> None:
    docs = MotherDocuments.load(Path(str(config.rootpath)))
    errors: list[str] = []

    for item in items:
        marker = item.get_closest_marker("mother_contract")
        if marker is None:
            continue

        trace = ContractTrace(
            requirements=_as_tuple(marker, "requirements"),
            operations=_as_tuple(marker, "operations"),
            functionalities=_as_tuple(marker, "functionalities"),
            modules=_as_tuple(marker, "modules"),
            mutating=bool(marker.kwargs.get("mutating", False)),
            open_error=marker.kwargs.get("open_error"),
        )
        item_errors = validate_contract_trace(
            trace,
            docs,
            fixture_names=getattr(item, "fixturenames", ()),
        )
        errors.extend(f"{item.nodeid}: {message}" for message in item_errors)

    if errors:
        raise pytest.UsageError(
            "Mother traceability collection errors:\n- " + "\n- ".join(errors)
        )


@pytest.fixture
def mother_open_contract_guard(request: pytest.FixtureRequest) -> OpenContractGuard:
    marker = request.node.get_closest_marker("mother_contract")
    if marker is None:
        raise AssertionError(
            "mother_open_contract_guard requires a mother_contract marker"
        )
    expected_error = marker.kwargs.get("open_error")
    if not isinstance(expected_error, str):
        raise AssertionError(
            "mother_open_contract_guard requires marker open_error=<exact code>"
        )
    guard = OpenContractGuard(expected_error=expected_error)
    yield guard
    guard.verify()
