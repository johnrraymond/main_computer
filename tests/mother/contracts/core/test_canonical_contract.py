from __future__ import annotations

import pytest

from tests.mother.support.implementation import require_mother_module


pytestmark = pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-026"],
    operations=["MOTHER-OP-RESEAL-STATE"],
    functionalities=["MOTHER-OF-RSL-005"],
    modules=["MOTHER-OFM-CORE-003"],
)


def _canonical():
    return require_mother_module("common.canonical", "MOTHER-OFM-CORE-003")


def test_canonical_json_is_deterministic_compact_utf8() -> None:
    canonical = _canonical()
    left = canonical.canonical_json({"b": 2, "a": "é"})
    right = canonical.canonical_json({"a": "é", "b": 2})
    assert left == right == b'{"a":"\xc3\xa9","b":2}'


def test_canonical_json_normalizes_unicode_keys() -> None:
    canonical = _canonical()
    assert canonical.canonical_json({"é": 1}) == canonical.canonical_json(
        {"e\u0301": 1}
    )


def test_canonical_json_rejects_duplicate_keys_after_normalization() -> None:
    canonical = _canonical()
    with pytest.raises((TypeError, ValueError)):
        canonical.canonical_json({"é": 1, "e\u0301": 2})


@pytest.mark.parametrize(
    "value",
    [
        1.0,
        {"nested": [1, 2.5]},
        {"nan": float("nan")},
        {"infinity": float("inf")},
    ],
)
def test_hashed_canonical_values_reject_floats(value) -> None:
    canonical = _canonical()
    with pytest.raises((TypeError, ValueError)):
        canonical.canonical_json(value)
    with pytest.raises((TypeError, ValueError)):
        canonical.canonical_yaml(value)


def test_canonical_yaml_is_deterministic_and_preserves_ambiguous_strings() -> None:
    canonical = _canonical()
    value = {
        "yes_value": "yes",
        "null_value": "null",
        "number_value": "01",
        "date_value": "2026-07-27",
        "boolean_value": True,
    }
    left = canonical.canonical_yaml(value)
    right = canonical.canonical_yaml(dict(reversed(tuple(value.items()))))
    assert left == right
    assert b"\r" not in left
    assert canonical.canonical_yaml({"value": "yes"}) != canonical.canonical_yaml(
        {"value": True}
    )


def test_canonical_encoders_reject_non_string_mapping_keys() -> None:
    canonical = _canonical()
    with pytest.raises((TypeError, ValueError)):
        canonical.canonical_json({1: "not-a-canonical-key"})
    with pytest.raises((TypeError, ValueError)):
        canonical.canonical_yaml({1: "not-a-canonical-key"})
