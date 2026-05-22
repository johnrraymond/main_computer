from __future__ import annotations

from pathlib import Path
from typing import Any


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def pytest_ignore_collect(collection_path: Any, config: Any) -> bool:
    """Only collect the live repo test suite.

    The repository can contain historical snapshots, patch bundles, undo bundles,
    executor workspaces, and other generated artifacts with files named test_*.py.
    Those files are not active tests.  The live suite is the top-level tests/
    directory only.
    """

    root = Path(str(config.rootpath)).resolve()
    path = Path(str(collection_path)).resolve()
    live_tests = root / "tests"

    if path == live_tests or _is_relative_to(path, live_tests):
        return False

    # Keep this root conftest importable, but do not let pytest collect any other
    # top-level or generated test-looking files outside tests/.
    if path == root / "conftest.py":
        return False

    return True
