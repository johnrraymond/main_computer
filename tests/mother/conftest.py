from __future__ import annotations

import ast
import inspect
from pathlib import Path
import textwrap
from typing import Any

import pytest

from tests.mother.support.fixtures import OpenContractGuard
from tests.mother.support.traceability import (
    ContractTrace,
    METHOD_QUALIFIED_CONTRACT_MODULES,
    MotherDocuments,
    module_public_method_rows,
    validate_contract_trace,
)


def _as_tuple(marker: pytest.Mark, key: str) -> tuple[str, ...]:
    value = marker.kwargs.get(key, ())
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def _direct_public_method_calls(
    item: pytest.Item,
    trace: ContractTrace,
    docs: MotherDocuments,
) -> tuple[str, ...]:
    """Infer direct calls to documented public methods in one test function."""

    obj = getattr(item, "obj", None)
    if obj is None:
        return ()
    try:
        source = textwrap.dedent(inspect.getsource(obj))
        tree = ast.parse(source)
    except (OSError, TypeError, SyntaxError):
        return ()

    called_names = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    public_rows = module_public_method_rows(docs)
    result: list[str] = []
    for module_id in trace.modules:
        for method_name in public_rows.get(module_id, ()):
            if method_name in called_names:
                result.append(f"{module_id}.{method_name}")
    return tuple(result)


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
            methods=_as_tuple(marker, "methods"),
            mutating=bool(marker.kwargs.get("mutating", False)),
            open_error=marker.kwargs.get("open_error"),
        )
        item_errors = validate_contract_trace(
            trace,
            docs,
            fixture_names=getattr(item, "fixturenames", ()),
            direct_methods=(
                _direct_public_method_calls(item, trace, docs)
                if (
                    trace.methods
                    or any(
                        module in METHOD_QUALIFIED_CONTRACT_MODULES
                        for module in trace.modules
                    )
                )
                else ()
            ),
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
