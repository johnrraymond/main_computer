from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.mother.support.implementation import require_mother_module


pytestmark = pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-001"],
    modules=["MOTHER-OFM-CORE-005"],
)


def _paths():
    return require_mother_module("common.paths", "MOTHER-OFM-CORE-005")


def _resolver(paths, tmp_path: Path):
    return paths.MotherPaths(runtime_state_root=tmp_path / "runtime" / "state")


def test_canonical_root_is_runtime_state_mother(tmp_path) -> None:
    paths = _paths()
    resolver = _resolver(paths, tmp_path)
    assert resolver.root == (tmp_path / "runtime" / "state" / "mother").resolve()


def test_typed_identifiers_resolve_to_contained_roots(tmp_path) -> None:
    paths = _paths()
    resolver = _resolver(paths, tmp_path)
    assert resolver.action_root("operation-1") == resolver.root / "actions" / "operation-1"
    assert resolver.network_root("network-a") == resolver.root / "networks" / "network-a"
    assert resolver.generation_root("network-a", "generation-1") == (
        resolver.root / "generations" / "network-a" / "generation-1"
    )


@pytest.mark.parametrize(
    "identifier",
    [
        "../escape",
        "/absolute",
        "C:\\absolute",
        "network/child",
        "network\\child",
        ".",
        "",
    ],
)
def test_identifier_injection_and_traversal_are_rejected(
    tmp_path,
    identifier: str,
) -> None:
    paths = _paths()
    resolver = _resolver(paths, tmp_path)
    with pytest.raises((TypeError, ValueError)):
        resolver.network_root(identifier)


def test_backslashes_are_not_silently_reinterpreted(tmp_path) -> None:
    paths = _paths()
    resolver = _resolver(paths, tmp_path)
    with pytest.raises((TypeError, ValueError)):
        resolver.action_root("operation\\nested")


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_symlink_escape_is_rejected(tmp_path) -> None:
    paths = _paths()
    resolver = _resolver(paths, tmp_path)
    resolver.root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = resolver.root / "escaped"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises((TypeError, ValueError)):
        resolver.validate_contained(link / "secret.json")


def test_wrong_network_substitution_is_rejected(tmp_path) -> None:
    paths = _paths()
    resolver = _resolver(paths, tmp_path)
    candidate = resolver.network_root("network-a")
    with pytest.raises((TypeError, ValueError)):
        resolver.validate_network_path(candidate, expected_network="network-b")


def test_wrong_generation_substitution_is_rejected(tmp_path) -> None:
    paths = _paths()
    resolver = _resolver(paths, tmp_path)
    candidate = resolver.generation_root("network-a", "generation-a")
    with pytest.raises((TypeError, ValueError)):
        resolver.validate_generation_path(
            candidate,
            expected_network="network-a",
            expected_generation="generation-b",
        )


def test_resolve_network_head_paths_returns_canonical_contained_paths(tmp_path) -> None:
    paths = _paths()
    resolver = _resolver(paths, tmp_path)

    resolved = resolver.resolve_network_head_paths("network-a")

    network_root = resolver.network_root("network-a")
    assert type(resolved).__name__ == "NetworkHeadPaths"
    assert resolved.journal_head == network_root / "journal" / "head.json"
    assert resolved.committed_state == network_root / "committed-state.json"
    assert resolver.validate_network_path(
        resolved.journal_head,
        expected_network="network-a",
    ) == resolved.journal_head
    assert resolver.validate_network_path(
        resolved.committed_state,
        expected_network="network-a",
    ) == resolved.committed_state
