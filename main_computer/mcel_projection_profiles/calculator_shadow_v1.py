"""Deterministic host-bound projection for the Calculator DSL authority.

The profile projects canonical Calculator Application IR into browser contract
modules without reading or copying the live Calculator HTML, CSS, or runtime.
Those stable host interfaces remain the durable presentation boundary while the
DSL is the semantic authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from main_computer.mcel_projection_profiles.host_bound_generic_v1 import project_host_bound_ir


PROFILE_ID = "mcel.calculator.host-bound-projection.v1"
APP_ID = "calculator"
NORMALIZED_DEFINITION = "generated/mcel.application.normalized.json"
RUNTIME_FACADE = "MainComputerCalculatorRuntime"
HOST_ROUTE = "/applications/calculator"
ROOT_SELECTOR = "#calculator-app"
STATIC_SURFACE_ID = "calculator.surface.workspace"
DEFAULT_CONTRACT_ID = "calculator.contract.default.app-health"
SURFACE_BUNDLE_SCHEMA = "mcel.application-surface-bundle.v1"
EXPECTED_INTENTS = {
    "askModelForExpression": "askModelForExpression",
    "askModelForGraphExpression": "askModelForGraphExpression",
    "askModelForMathicsExpression": "askModelForMathicsExpression",
    "askResultQuestion": "askResultQuestion",
    "clearExpression": "clearExpression",
    "drawGraph": "drawGraph",
    "enterToken": "enterToken",
    "evaluateExpression": "evaluateExpression",
    "evaluateMathics": "evaluateMathics",
    "resetGraph": "resetGraph",
    "switchMode": "switchMode",
}
EXPECTED_CAPABILITIES = {
    "capability:calculator.mathics",
    "capability:calculator.model-assistance",
    "capability:calculator.result-qa",
}


class CalculatorProjectionProfileError(ValueError):
    """Raised when Calculator IR cannot be projected without guessing."""


@dataclass(frozen=True)
class CalculatorProjection:
    profile_id: str
    files: Mapping[str, bytes]
    file_hashes: Mapping[str, str]


def project_calculator_ir(application_ir: Mapping[str, Any]) -> CalculatorProjection:
    """Project one validated Calculator IR document into deterministic files."""

    app = _mapping(application_ir.get("application"))
    if app.get("appId") != APP_ID:
        raise CalculatorProjectionProfileError(
            f"Calculator projection requires appId {APP_ID!r}; observed {app.get('appId')!r}."
        )

    intents = {
        str(item.get("sourceName")): item
        for item in application_ir.get("intents") or []
        if isinstance(item, Mapping) and isinstance(item.get("sourceName"), str)
    }
    observed_intents = set(intents)
    if observed_intents != set(EXPECTED_INTENTS):
        raise CalculatorProjectionProfileError(
            "Calculator projection requires the eleven stable runtime intents; "
            f"missing={sorted(set(EXPECTED_INTENTS) - observed_intents)!r}, "
            f"extra={sorted(observed_intents - set(EXPECTED_INTENTS))!r}."
        )
    for source_name, expected_method in sorted(EXPECTED_INTENTS.items()):
        intent = intents[source_name]
        if intent.get("runtimeMethod") != expected_method:
            raise CalculatorProjectionProfileError(
                f"Intent {source_name!r} must bind runtime method {expected_method!r}."
            )
        if intent.get("writes"):
            raise CalculatorProjectionProfileError(
                f"Calculator intent {source_name!r} may not claim canonical writes."
            )
        if intent.get("transition"):
            raise CalculatorProjectionProfileError(
                f"Calculator intent {source_name!r} may not embed a canonical transition."
            )

    capability_ids = {
        str(item.get("id"))
        for item in application_ir.get("capabilities") or []
        if isinstance(item, Mapping)
    }
    if capability_ids != EXPECTED_CAPABILITIES:
        raise CalculatorProjectionProfileError(
            "Calculator capability set drifted from the bounded model/Mathics/result-QA lanes."
        )

    surfaces = [
        item for item in application_ir.get("surfaces") or []
        if isinstance(item, Mapping) and item.get("id") == "surface:calculator.workspace"
    ]
    if len(surfaces) != 1:
        raise CalculatorProjectionProfileError("Calculator workspace surface is missing or duplicated.")
    surface = surfaces[0]
    if surface.get("root") != ROOT_SELECTOR or surface.get("route") != HOST_ROUTE:
        raise CalculatorProjectionProfileError(
            "Calculator projection must remain bound to /applications/calculator and #calculator-app."
        )
    if surface.get("presentationAuthority") != "existing-host-html":
        raise CalculatorProjectionProfileError(
            "Calculator HTML must remain the declared presentation authority during shadow projection."
        )
    if surface.get("runtimeFacade") != RUNTIME_FACADE:
        raise CalculatorProjectionProfileError(
            f"Calculator projection requires canonical runtime facade {RUNTIME_FACADE!r}."
        )

    projected = project_host_bound_ir(application_ir, profile_id=PROFILE_ID, export_prefix="Calculator", contract_id=DEFAULT_CONTRACT_ID, include_invariants=False)
    return CalculatorProjection(projected.profile_id, projected.files, projected.file_hashes)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}

def _plain_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CalculatorProjectionProfileError(f"{label} must be an object.")
    return json.loads(json.dumps(value, sort_keys=True))
