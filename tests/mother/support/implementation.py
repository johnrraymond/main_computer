from __future__ import annotations

import importlib
import re
from types import ModuleType

import pytest


_PHASE_RE = re.compile(r"WAVE[0-9]+[A-Z]*")


def require_mother_module(
    relative_name: str,
    module_id: str,
    *,
    phase: str = "WAVE1A",
) -> ModuleType:
    """Import a production Mother module only when a contract test executes."""

    if not _PHASE_RE.fullmatch(phase):
        raise ValueError(f"invalid Mother implementation phase: {phase!r}")

    qualified = f"tools.mother.{relative_name}"
    try:
        return importlib.import_module(qualified)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing == qualified or qualified.startswith(f"{missing}."):
            pytest.fail(
                f"MOTHER_{phase}_IMPLEMENTATION_MISSING: {module_id} "
                f"requires production module {qualified}",
                pytrace=False,
            )
        raise
