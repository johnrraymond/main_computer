from __future__ import annotations

import json
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1]


def test_calculator_authoritative_truth_status_is_promoted_and_write_free() -> None:
    manifest = json.loads((PACKAGE / "mcel.app.json").read_text(encoding="utf-8"))
    conformance = manifest["conformance"]
    assert manifest["authoring"]["status"] == "dsl-authoritative"
    assert conformance["currentMode"] == "semantic-runtime-proven"
    assert conformance["targetMode"] == "semantic-runtime-proven"
    assert conformance["shadow"] is False
    assert conformance["missingBridges"] == []
    assert conformance["legacySemanticAdapterRetired"] is True
    assert manifest["projection"]["hostBoundRuntimeActive"] is True
    assert manifest["projection"]["mountMode"] == "host-bound"
    assert manifest["promotion"]["promotionEligible"] is True
    assert manifest["promotion"]["promotionExecuted"] is True
