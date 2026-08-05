from __future__ import annotations

import json
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1]


def test_calculator_shadow_truth_status_remains_explicitly_unpromoted() -> None:
    manifest = json.loads((PACKAGE / "mcel.app.json").read_text(encoding="utf-8"))
    conformance = manifest["conformance"]
    assert conformance["currentMode"] == "forward-specification"
    assert conformance["targetMode"] == "semantic-runtime-proven"
    assert conformance["shadow"] is True
    assert {
        "host-bound-runtime-projection",
        "browser-observation",
        "promotion-rehearsal",
        "legacy-semantic-adapter-retirement",
    }.issubset(set(conformance["missingBridges"]))
