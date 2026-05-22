from __future__ import annotations

from pathlib import Path
from typing import Iterable


PROD_LOCK_FILENAME = ".prod.lock"


class ProductionLockError(RuntimeError):
    """Raised when a command would mutate a protected production runtime tree."""


def resolve_under_root(root: Path, path: Path) -> Path:
    candidate = path if path.is_absolute() else root / path
    return candidate.resolve(strict=False)


def _lock_search_starts(root: Path, paths: Iterable[Path]) -> list[Path]:
    starts: list[Path] = [root.resolve(strict=False)]
    for raw_path in paths:
        path = resolve_under_root(root, raw_path)
        if path.name == PROD_LOCK_FILENAME:
            starts.append(path.parent)
            continue
        if path.exists() and path.is_dir():
            starts.append(path)
            continue
        starts.append(path.parent)
    return starts


def find_production_lock(root: Path, *paths: Path) -> Path | None:
    """Return the first .prod.lock that protects root or any target path.

    The guard checks the repository root and every ancestor of supplied target
    paths. This makes command-line overrides safe too: a dev command cannot
    dodge a lock by writing runtime/deployment output into another locked tree.
    """

    seen: set[Path] = set()
    for start in _lock_search_starts(root, paths):
        current = start
        for candidate_root in (current, *current.parents):
            if candidate_root in seen:
                continue
            seen.add(candidate_root)
            lock_path = candidate_root / PROD_LOCK_FILENAME
            if lock_path.exists():
                return lock_path
    return None


def require_unlocked_production_state(root: Path, *paths: Path, action: str) -> None:
    """Fail closed before a destructive command mutates a locked production tree."""

    lock_path = find_production_lock(root, *paths)
    if lock_path is None:
        return
    raise ProductionLockError(
        f"refusing to {action}; production lock exists at {lock_path}. "
        "Attach/status/read-only commands may run, but reset, deploy, overwrite, "
        "ledger-bridge, and payout-flow commands must not mutate locked production state."
    )
