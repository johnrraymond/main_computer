"""Resolve a usable Node.js runtime for MCEL Python tooling.

MCEL repository tools should not assume that a system ``node`` binary is on
``PATH``.  Browser/runtime tooling often has Playwright installed, and
Playwright ships its own Node runtime.  This module centralizes the small,
side-effect-free discovery used by tests and evidence runners.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path


def is_usable_node_file(path: Path) -> bool:
    """Return whether *path* can be invoked as a Node executable."""

    return path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))


def resolve_node_candidate(value: str | os.PathLike[str] | None) -> str | None:
    """Resolve a command name or filesystem path to a Node executable."""

    raw = "" if value is None else str(value).strip()
    if not raw:
        return None

    expanded = os.path.expandvars(os.path.expanduser(raw))
    discovered = shutil.which(expanded)
    if discovered:
        return str(Path(discovered).resolve())

    path = Path(expanded)
    if is_usable_node_file(path):
        return str(path.resolve())
    return None


def playwright_bundled_node_candidates() -> tuple[Path, ...]:
    """Return likely Playwright-bundled Node paths without importing Playwright."""

    try:
        spec = importlib.util.find_spec("playwright")
    except (ImportError, AttributeError, ValueError):
        return ()

    if spec is None or not spec.origin:
        return ()

    driver_dir = Path(spec.origin).resolve().parent / "driver"
    names = ("node.exe", "node") if os.name == "nt" else ("node", "node.exe")
    return tuple(driver_dir / name for name in names)


def resolve_node_executable(node_executable: str | os.PathLike[str] | None = None) -> str | None:
    """Resolve Node from an explicit override, PATH, or Playwright's driver."""

    explicit = resolve_node_candidate(node_executable)
    if explicit:
        return explicit

    env_override = (
        os.environ.get("MCEL_NODE_EXECUTABLE")
        or os.environ.get("NODE_BINARY")
        or os.environ.get("NODE_EXE")
    )
    env_node = resolve_node_candidate(env_override)
    if env_node:
        return env_node

    system_node = shutil.which("node")
    if system_node:
        return str(Path(system_node).resolve())

    for candidate in playwright_bundled_node_candidates():
        if is_usable_node_file(candidate):
            return str(candidate.resolve())
    return None


def prepend_node_to_path(env: dict[str, str], node_executable: str | os.PathLike[str]) -> dict[str, str]:
    """Return an environment with the Node executable's directory on ``PATH``."""

    updated = dict(env)
    node_path = Path(node_executable).resolve()
    node_dir = str(node_path.parent)
    parts = [part for part in updated.get("PATH", "").split(os.pathsep) if part]
    if node_dir not in parts:
        updated["PATH"] = os.pathsep.join([node_dir, *parts])
    updated["MCEL_NODE_EXECUTABLE"] = str(node_path)
    return updated
