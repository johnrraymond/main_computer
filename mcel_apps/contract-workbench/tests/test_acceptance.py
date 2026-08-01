from __future__ import annotations

from pathlib import Path

import pytest


PACKAGE = Path(__file__).resolve().parents[1]


def test_forward_acceptance_contract_is_complete() -> None:
    requirements = (PACKAGE / "requirements.md").read_text(encoding="utf-8")
    acceptance = (PACKAGE / "contracts/acceptance.js").read_text(encoding="utf-8")
    assert "contract-workbench.acceptance.complete-application" in requirements
    assert "status: planned" in requirements
    for scenario in (
        "add",
        "validation",
        "remove",
        "update",
        "filter-sort",
        "quote",
        "cancel",
        "stale",
        "duplicate",
        "prohibited",
        "multi-instance",
    ):
        assert f"contract-workbench.acceptance.{scenario}" in acceptance


@pytest.mark.xfail(strict=True, reason="MCEL_INTENT_COMPLETE_PROOF_UNSUPPORTED")
def test_complete_application_acceptance_is_not_yet_executable() -> None:
    pytest.fail("MCEL_INTENT_COMPLETE_PROOF_UNSUPPORTED")
