from __future__ import annotations

import json
from pathlib import Path
from main_computer.mcel_package_test_support import logical_package_text


PACKAGE = Path(__file__).resolve().parents[1]


def _normalized_definition() -> dict:
    payload = json.loads(logical_package_text("contract-workbench", "generated/mcel.application.normalized.json"))
    return payload["definition"]


def test_complete_application_acceptance_is_enforceable() -> None:
    requirements = (PACKAGE / "requirements.md").read_text(encoding="utf-8")
    acceptance = logical_package_text("contract-workbench", "contracts/acceptance.js")
    bindings = json.loads((PACKAGE / "tests/mcel_acceptance_bindings.json").read_text(encoding="utf-8"))
    assert "contract-workbench.acceptance.complete-application" in requirements
    assert "status: verified" in requirements
    assert '"currentStatus": "verified"' in acceptance
    assert bindings["bindings"][0]["acceptanceContractId"] == "contract-workbench.acceptance.complete-application"
    assert len(bindings["bindings"][0]["selectors"]) >= 4


def test_every_declared_intent_has_explicit_acceptance_coverage() -> None:
    definition = _normalized_definition()
    operations = definition["operations"]
    scenarios = definition["acceptance"]
    by_intent: dict[str, list[dict]] = {intent_id: [] for intent_id in operations}
    for scenario in scenarios:
        intent_id = (scenario.get("when") or {}).get("intentId")
        if intent_id in by_intent:
            by_intent[intent_id].append(scenario)

    assert set(by_intent) == {
        "add-contract",
        "update-quantity",
        "remove-contract",
        "clear-all",
        "request-quote",
        "cancel-quote",
        "direct-set",
    }
    assert all(by_intent.values())
    assert any(item["id"] == "contract-workbench.acceptance.clear-all" for item in by_intent["clear-all"])
    assert any((item.get("expect") or {}).get("operationStatus") == "refused" for item in by_intent["add-contract"])
    assert any((item.get("expect") or {}).get("olderOperationStatus") == "superseded" for item in by_intent["request-quote"])
    assert any((item.get("expect") or {}).get("independentItemKeys") is True for item in by_intent["request-quote"])
    assert any((item.get("expect") or {}).get("operationStatus") == "cancelled" for item in by_intent["cancel-quote"])
    assert any((item.get("expect") or {}).get("code") == "INTENT_PROHIBITED" for item in by_intent["direct-set"])


def test_complete_acceptance_declares_cross_cutting_browser_proofs() -> None:
    scenario_ids = {entry["id"] for entry in _normalized_definition()["acceptance"]}
    assert {
        "contract-workbench.acceptance.filter-sort",
        "contract-workbench.acceptance.multi-instance",
        "contract-workbench.acceptance.quote",
        "contract-workbench.acceptance.quote-supersession",
        "contract-workbench.acceptance.quote-parallel",
        "contract-workbench.acceptance.clear-all",
    } <= scenario_ids
    assert len(scenario_ids) == 14
