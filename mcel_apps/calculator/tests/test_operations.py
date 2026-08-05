from __future__ import annotations

from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application


REPO = Path(__file__).resolve().parents[3]
EXPECTED = {
    "askModelForExpression",
    "askModelForGraphExpression",
    "askModelForMathicsExpression",
    "askResultQuestion",
    "clearExpression",
    "drawGraph",
    "enterToken",
    "evaluateExpression",
    "evaluateMathics",
    "resetGraph",
    "switchMode",
}


def test_calculator_shadow_declares_the_stable_runtime_facade_operations() -> None:
    compiled = compile_dsl_application(REPO / "mcel_apps/calculator/application.js", write_candidate=False)
    assert compiled.valid and compiled.normalized_ir
    intents = {item["sourceName"]: item for item in compiled.normalized_ir["intents"]}
    assert set(intents) == EXPECTED
    assert all(item["runtimeMethod"] == name for name, item in intents.items())
    assert all(item.get("writes") == [] for item in intents.values())
    assert all("transition" not in item for item in intents.values())


def test_provider_and_mathics_lanes_are_explicit_capabilities() -> None:
    compiled = compile_dsl_application(REPO / "mcel_apps/calculator/application.js", write_candidate=False)
    assert compiled.valid and compiled.normalized_ir
    intents = {item["sourceName"]: item for item in compiled.normalized_ir["intents"]}
    capability_names = {
        "askModelForExpression",
        "askModelForGraphExpression",
        "askModelForMathicsExpression",
        "evaluateMathics",
        "askResultQuestion",
    }
    assert all(intents[name]["operationKind"] == "capability" for name in capability_names)
    assert all(len(intents[name]["effectRefs"]) == 1 for name in capability_names)
