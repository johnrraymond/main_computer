from __future__ import annotations

import hashlib

import pytest

from tests.mother.support.implementation import require_mother_module


pytestmark = pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-026"],
    operations=["MOTHER-OP-RESEAL-STATE"],
    functionalities=["MOTHER-OF-RSL-003"],
    modules=["MOTHER-OFM-CORE-004"],
)


def _hashing():
    return require_mother_module("common.hashing", "MOTHER-OFM-CORE-004")


def test_sha256_matches_the_standard_known_vector() -> None:
    hashing = _hashing()
    result = hashing.sha256(b"abc")
    assert result.algorithm == "sha256"
    assert result.digest == hashlib.sha256(b"abc").hexdigest()


def test_hash_file_matches_hashing_the_exact_bytes(tmp_path) -> None:
    hashing = _hashing()
    target = tmp_path / "payload.bin"
    target.write_bytes(b"mother\x00payload\n")
    assert hashing.hash_file(target) == hashing.sha256(target.read_bytes())


def test_ordered_root_is_order_sensitive_and_deterministic() -> None:
    hashing = _hashing()
    first = hashing.sha256(b"first")
    second = hashing.sha256(b"second")
    assert hashing.ordered_root([first, second]) == hashing.ordered_root(
        [first, second]
    )
    assert hashing.ordered_root([first, second]) != hashing.ordered_root(
        [second, first]
    )


def test_set_root_is_order_independent_and_domain_separated() -> None:
    hashing = _hashing()
    first = hashing.sha256(b"first")
    second = hashing.sha256(b"second")
    assert hashing.set_root([first, second]) == hashing.set_root([second, first])
    assert hashing.set_root([first, second]) != hashing.ordered_root([first, second])


def test_set_root_rejects_duplicate_members() -> None:
    hashing = _hashing()
    member = hashing.sha256(b"same")
    with pytest.raises((TypeError, ValueError)):
        hashing.set_root([member, member])


def test_roots_reject_mixed_or_unknown_hash_algorithms() -> None:
    hashing = _hashing()
    valid = hashing.sha256(b"valid")
    fake = type(valid)(algorithm="sha999", digest="a" * 64)
    with pytest.raises((TypeError, ValueError)):
        hashing.ordered_root([valid, fake])
    with pytest.raises((TypeError, ValueError)):
        hashing.set_root([valid, fake])
