from __future__ import annotations

import json
from pathlib import Path

import pytest


PACKAGE = Path(__file__).resolve().parents[1]


def test_browser_entrypoint_reports_forward_blockers_truthfully() -> None:
    source = (PACKAGE / "src/app.js").read_text(encoding="utf-8")
    assert "forward-specification-blocked" in source
    assert "__MCEL_CONTRACT_WORKBENCH_BLOCKER__" in source
    assert "mountApplicationPackage" in source


@pytest.mark.parametrize("feature", json.loads((PACKAGE / "forward-specification.json").read_text(encoding="utf-8"))["features"])
def test_each_forward_runtime_feature_remains_an_explicit_tdd_blocker(feature: dict) -> None:
    pytest.xfail(feature["code"])
