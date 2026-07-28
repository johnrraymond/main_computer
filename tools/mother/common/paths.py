"""Canonical, contained filesystem path construction for Mother state."""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
import re

from .models import NetworkHeadPaths


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _identifier(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or Path(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or not _IDENTIFIER.fullmatch(value)
    ):
        raise ValueError(f"invalid {name}: {value!r}")
    return value


class MotherPaths:
    """Resolve Mother paths beneath ``runtime/state/mother`` only."""

    def __init__(self, *, runtime_state_root: str | Path) -> None:
        raw = Path(runtime_state_root).expanduser()
        self._root = (raw / "mother").resolve(strict=False)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def actions_root(self) -> Path:
        return self._root / "actions"

    @property
    def networks_root(self) -> Path:
        return self._root / "networks"

    @property
    def generations_root(self) -> Path:
        return self._root / "generations"

    @property
    def objects_root(self) -> Path:
        return self._root / "objects"

    @property
    def evidence_root(self) -> Path:
        return self._root / "evidence"

    @property
    def locks_root(self) -> Path:
        return self._root / "locks"

    def action_root(self, operation_id: str) -> Path:
        return self.validate_contained(
            self.actions_root / _identifier(operation_id, "operation_id")
        )

    def network_root(self, network: str) -> Path:
        return self.validate_contained(
            self.networks_root / _identifier(network, "network")
        )

    def resolve_network_head_paths(self, network: str) -> NetworkHeadPaths:
        """Return the canonical journal head and committed-state projection.

        The journal head is authoritative.  ``committed-state.json`` is the
        replayed projection that must be checked against that stable head.
        """

        root = self.network_root(network)
        head = self.validate_network_path(
            root / "journal" / "head.json",
            expected_network=network,
        )
        committed_state = self.validate_network_path(
            root / "committed-state.json",
            expected_network=network,
        )
        return NetworkHeadPaths(
            journal_head=head,
            committed_state=committed_state,
        )

    def generation_root(self, network: str, generation: str) -> Path:
        return self.validate_contained(
            self.generations_root
            / _identifier(network, "network")
            / _identifier(generation, "generation")
        )

    def validate_contained(self, candidate: str | Path) -> Path:
        """Return the canonical path or reject lexical/symlink escape."""

        if isinstance(candidate, str):
            if "\x00" in candidate:
                raise ValueError("path contains NUL")
            candidate_path = Path(candidate)
        elif isinstance(candidate, Path):
            candidate_path = candidate
        else:
            raise TypeError("candidate must be a path")

        if not candidate_path.is_absolute():
            candidate_path = self._root / candidate_path
        resolved = candidate_path.resolve(strict=False)
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise ValueError(f"path escapes Mother root: {candidate!s}") from exc

        # Resolve every existing prefix independently.  This catches a symlink
        # escape even when the final descendant does not yet exist.
        current = self._root
        relative = resolved.relative_to(self._root)
        lexical = self._root
        for part in relative.parts:
            lexical = lexical / part
            if lexical.exists() or lexical.is_symlink():
                actual = lexical.resolve(strict=False)
                try:
                    actual.relative_to(self._root)
                except ValueError as exc:
                    raise ValueError(f"symlink escapes Mother root: {lexical!s}") from exc
        return resolved

    def validate_network_path(
        self,
        candidate: str | Path,
        *,
        expected_network: str,
    ) -> Path:
        resolved = self.validate_contained(candidate)
        expected = self.network_root(expected_network)
        try:
            resolved.relative_to(expected)
        except ValueError as exc:
            raise ValueError("path does not belong to the expected network") from exc
        return resolved

    def validate_generation_path(
        self,
        candidate: str | Path,
        *,
        expected_network: str,
        expected_generation: str,
    ) -> Path:
        resolved = self.validate_contained(candidate)
        expected = self.generation_root(expected_network, expected_generation)
        try:
            resolved.relative_to(expected)
        except ValueError as exc:
            raise ValueError(
                "path does not belong to the expected network generation"
            ) from exc
        return resolved


__all__ = ["MotherPaths"]
