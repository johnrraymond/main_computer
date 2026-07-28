from __future__ import annotations

import importlib
from types import ModuleType

import pytest


def require_mother_module(relative_name: str, module_id: str) -> ModuleType:
    """Import a production Mother module only when a contract test executes."""

    qualified = f"tools.mother.{relative_name}"
    try:
        return importlib.import_module(qualified)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing == qualified or qualified.startswith(f"{missing}."):
            pytest.fail(
                f"MOTHER_WAVE1A_IMPLEMENTATION_MISSING: {module_id} "
                f"requires production module {qualified}",
                pytrace=False,
            )
        raise
