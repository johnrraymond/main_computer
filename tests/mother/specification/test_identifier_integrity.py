from __future__ import annotations

import ast
from pathlib import Path
import re

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
        (requirement_ids, 27),
        (design_ids, 30),
        (operation_ids, 17),
        (functionality_ids, 182),
        (module_ids, 82),
    ),
)
def test_canonical_identifier_counts(loader, expected: int) -> None:
    values = loader(MotherDocuments.load())
    assert len(values) == expected
    assert not duplicates(values)


def test_identifier_sequences_are_complete() -> None:
    docs = MotherDocuments.load()
    assert requirement_ids(docs) == [f"MOTHER-REQ-{i:03d}" for i in range(1, 28)]
    assert sorted(design_ids(docs)) == [f"MOTHER-DESIGN-{i:03d}" for i in range(1, 31)]

_EXACT_ERROR_CODE_RE = re.compile(r"^MOTHER_[A-Z][A-Z0-9_]+$")
_ERROR_ROW_RE = re.compile(
    r"^\| `(MOTHER_[A-Z0-9_]+)` \| .*? \| "
    r"`(never|same-request|after-reobserve|operator-decision)` \| "
    r"`(none|ledger-only|live-state-maybe-changed|local-pointer-determined|network-head-determined)` \|"
)


def _contract_test_error_codes(root: Path) -> set[str]:
    codes: set[str] = set()
    for path in sorted((root / "tests" / "mother" / "contracts").rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _EXACT_ERROR_CODE_RE.fullmatch(node.value)
            ):
                codes.add(node.value)
    return codes


def _contains_mother_error_fragment(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return "MOTHER_" in node.value
    if isinstance(node, ast.JoinedStr):
        return any(_contains_mother_error_fragment(value) for value in node.values)
    if isinstance(node, ast.FormattedValue):
        return _contains_mother_error_fragment(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return (
            _contains_mother_error_fragment(node.left)
            or _contains_mother_error_fragment(node.right)
        )
    return False


def _is_error_code_attribute(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "code"


def _contract_error_assertion_violations(root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted((root / "tests" / "mother" / "contracts").rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Add)
                and _contains_mother_error_fragment(node)
            ):
                violations.append(
                    f"{relative}:{node.lineno}: builds a MOTHER_* code from fragments"
                )
            if (
                isinstance(node, ast.JoinedStr)
                and _contains_mother_error_fragment(node)
            ):
                violations.append(
                    f"{relative}:{node.lineno}: formats a MOTHER_* code dynamically"
                )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "startswith"
                and _is_error_code_attribute(node.func.value)
            ):
                violations.append(
                    f"{relative}:{node.lineno}: checks MotherError.code by prefix"
                )
    return violations


def _normative_error_rows(modules_document: str) -> dict[str, tuple[str, str]]:
    heading = "### 4.4 Normative exact error contracts"
    end_heading = "## 5. Stable module registry"
    assert heading in modules_document
    section = modules_document.split(heading, 1)[1].split(end_heading, 1)[0]
    rows: dict[str, tuple[str, str]] = {}
    for line in section.splitlines():
        match = _ERROR_ROW_RE.match(line)
        if not match:
            continue
        code, retry_class, authority_effect = match.groups()
        assert code not in rows, f"duplicate normative exact error row: {code}"
        rows[code] = (retry_class, authority_effect)
    return rows


def test_contract_tests_use_only_normatively_documented_exact_error_codes() -> None:
    docs = MotherDocuments.load()
    documented = _normative_error_rows(docs.modules)
    asserted = _contract_test_error_codes(docs.root)
    assert asserted
    assert asserted - set(documented) == set()
    assert _contract_error_assertion_violations(docs.root) == []

