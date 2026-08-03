from __future__ import annotations

import json
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1]


def test_browser_entrypoint_declares_proven_runtime_without_hidden_feature_blockers() -> None:
    source = (PACKAGE / "src/app.js").read_text(encoding="utf-8")
    manifest = json.loads((PACKAGE / "mcel.app.json").read_text(encoding="utf-8"))
    assert "__MCEL_APPLICATION_PACKAGE_MOUNT_OPTIONS__" in source
    assert "mountApplicationPackage" in source
    assert 'conformanceMode: "semantic-runtime-proven"' in source
    assert "requiredFeatures: Object.freeze([])" in source
    assert manifest["conformance"] == {
        "currentMode": "semantic-runtime-proven",
        "missingBridges": [],
        "targetMode": "semantic-runtime-proven",
    }


def test_observation_contract_is_scenario_linked_and_complete() -> None:
    observation = (PACKAGE / "contracts/observation.js").read_text(encoding="utf-8")
    acceptance = (PACKAGE / "contracts/acceptance.js").read_text(encoding="utf-8")
    assert '"currentStatus": "scenario-linked"' in observation
    assert '"contract-workbench.acceptance.clear-all"' in acceptance
    assert acceptance.count('"kind": "workflow"') == 14
