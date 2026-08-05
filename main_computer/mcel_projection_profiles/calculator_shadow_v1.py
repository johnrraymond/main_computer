"""Deterministic compatibility projection for the shadow Calculator DSL.

The profile projects canonical Calculator Application IR into browser contract
modules without reading or copying the live Calculator HTML, CSS, or runtime.
Those stable host interfaces remain authoritative until a later promotion.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from main_computer.mcel_application_ir import canonical_json_bytes


PROFILE_ID = "mcel.calculator.shadow-projection.v1"
APP_ID = "calculator"
NORMALIZED_DEFINITION = "generated/mcel.application.normalized.json"
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
                f"Shadow Calculator intent {source_name!r} may not claim canonical writes."
            )
        if intent.get("transition"):
            raise CalculatorProjectionProfileError(
                f"Shadow Calculator intent {source_name!r} may not embed a canonical transition."
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
    if surface.get("root") != "#calculator-app" or surface.get("route") != "/applications/calculator":
        raise CalculatorProjectionProfileError(
            "Calculator projection must remain bound to /applications/calculator and #calculator-app."
        )
    if surface.get("presentationAuthority") != "existing-host-html":
        raise CalculatorProjectionProfileError(
            "Calculator HTML must remain the declared presentation authority during shadow projection."
        )

    domain_payload = {
        "schema": "mcel.application-domain.v1",
        "appId": APP_ID,
        "projectionProfile": PROFILE_ID,
        "semanticVersion": str(app.get("semanticVersion") or "1"),
        "presentationAuthority": "existing-host-html",
        "states": [
            {
                "id": str(item.get("id")),
                "sourceName": str(item.get("sourceName") or ""),
                "authority": str(item.get("authority") or ""),
                "schema": item.get("schema") or {},
                "initial": item.get("initial"),
            }
            for item in application_ir.get("states") or []
            if isinstance(item, Mapping)
        ],
        "capabilities": [
            {
                "id": str(item.get("id")),
                "sourceName": str(item.get("sourceName") or ""),
                "risk": str(item.get("risk") or ""),
                "operations": list(item.get("operations") or []),
            }
            for item in application_ir.get("capabilities") or []
            if isinstance(item, Mapping)
        ],
    }
    intent_payload = {
        source_name: {
            "id": str(intent.get("id")),
            "sourceName": source_name,
            "label": str(intent.get("label") or source_name),
            "operationKind": str(intent.get("operationKind") or ""),
            "risk": str(intent.get("risk") or ""),
            "lane": str(intent.get("lane") or ""),
            "executionBinding": str(intent.get("executionBinding") or ""),
            "runtimeMethod": str(intent.get("runtimeMethod") or ""),
            "reads": [str(_mapping(value).get("ref") or "") for value in intent.get("reads") or []],
            "effectRefs": [str(_mapping(value).get("ref") or "") for value in intent.get("effectRefs") or []],
        }
        for source_name, intent in sorted(intents.items())
    }
    surface_payload = {
        "schema": "mcel.application-surface.v1",
        "appId": APP_ID,
        "surfaceId": str(surface.get("id")),
        "route": str(surface.get("route")),
        "rootSelector": str(surface.get("root")),
        "presentationAuthority": str(surface.get("presentationAuthority")),
        "nodes": [
            {
                "id": str(item.get("id")),
                "sourceName": str(item.get("sourceName") or ""),
                "nodeKind": str(item.get("nodeKind") or ""),
                "intent": str(_mapping(item.get("intent")).get("ref") or ""),
            }
            for item in surface.get("nodes") or []
            if isinstance(item, Mapping)
        ],
    }
    layouts = [
        item for item in application_ir.get("layouts") or []
        if isinstance(item, Mapping) and item.get("id") == "layout:calculator.workspace"
    ]
    layout = layouts[0] if layouts else {}
    layout_payload = {
        "schema": "mcel.application-layout.v1",
        "appId": APP_ID,
        "layoutId": str(layout.get("id") or ""),
        "surface": str(_mapping(layout.get("surface")).get("ref") or ""),
        "orderedChildren": [
            str(_mapping(item).get("ref") or "") for item in layout.get("orderedChildren") or []
        ],
        "zones": [str(value) for value in layout.get("zones") or []],
    }
    observation_payload = {
        "schema": "mcel.application-observation.v1",
        "appId": APP_ID,
        "hostRoute": "/applications/calculator",
        "rootSelector": "#calculator-app",
        "runtimeFacade": "MainComputerCalculatorRuntime",
        "operations": [
            {
                "intentId": value["id"],
                "sourceName": source_name,
                "runtimeMethod": value["runtimeMethod"],
                "lane": value["lane"],
                "risk": value["risk"],
            }
            for source_name, value in sorted(intent_payload.items())
        ],
    }
    acceptance_payload = {
        "schema": "mcel.application-acceptance.v1",
        "appId": APP_ID,
        "targetTruthStatus": str(_mapping(application_ir.get("proof")).get("targetTruthStatus") or ""),
        "requiredAuthorities": list(_mapping(application_ir.get("proof")).get("requiredAuthorities") or []),
        "scenarios": [
            {
                "id": str(item.get("id")),
                "intent": str(_mapping(item.get("intent")).get("ref") or ""),
                "steps": list(item.get("steps") or []),
            }
            for item in application_ir.get("scenarios") or []
            if isinstance(item, Mapping)
        ],
    }

    bindings = {
        source_name: {
            "intentId": value["id"],
            "runtimeMethod": value["runtimeMethod"],
            "executionBinding": value["executionBinding"],
        }
        for source_name, value in sorted(intent_payload.items())
    }
    files = {
        "contracts/domain.js": _data_module("CalculatorDomain", domain_payload),
        "contracts/intents.js": _data_module("CalculatorIntents", intent_payload),
        "contracts/adapter.js": _adapter_module(bindings),
        "contracts/surface.js": _data_module("CalculatorSurface", surface_payload),
        "contracts/layout.js": _data_module("CalculatorLayout", layout_payload),
        "contracts/observation.js": _data_module("CalculatorObservation", observation_payload),
        "contracts/acceptance.js": _data_module("CalculatorAcceptance", acceptance_payload),
        NORMALIZED_DEFINITION: canonical_json_bytes(application_ir) + b"\n",
    }
    hashes = {
        path: "sha256:" + hashlib.sha256(content).hexdigest()
        for path, content in sorted(files.items())
    }
    return CalculatorProjection(PROFILE_ID, files, hashes)


def _data_module(export_name: str, payload: Mapping[str, Any]) -> bytes:
    encoded = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    text = f"""function deepFreeze(value) {{
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.freeze(value);
  Object.keys(value).forEach((key) => deepFreeze(value[key]));
  return value;
}}

export const {export_name} = deepFreeze({encoded});
"""
    return text.encode("utf-8")


def _adapter_module(bindings: Mapping[str, Any]) -> bytes:
    encoded = json.dumps(bindings, indent=2, sort_keys=True, ensure_ascii=False)
    text = f"""const BINDINGS = Object.freeze({encoded});

function resolveRuntime() {{
  const runtime = globalThis.MainComputerCalculatorRuntime;
  if (!runtime || typeof runtime !== "object") {{
    throw new Error("MainComputerCalculatorRuntime is unavailable.");
  }}
  return runtime;
}}

function bindingFor(intentName) {{
  const binding = BINDINGS[String(intentName || "")];
  if (!binding) throw new Error(`Unknown Calculator intent: ${{intentName}}`);
  return binding;
}}

export const CalculatorAdapter = Object.freeze({{
  schema: "mcel.semantic-adapter.v1",
  appId: "calculator",
  adapterId: "calculator.dsl-shadow-adapter.v1",
  bindings: BINDINGS,
  invoke(intentName, ...args) {{
    const binding = bindingFor(intentName);
    const method = resolveRuntime()[binding.runtimeMethod];
    if (typeof method !== "function") {{
      throw new Error(`Calculator runtime method is unavailable: ${{binding.runtimeMethod}}`);
    }}
    return method(...args);
  }}
}});
"""
    return text.encode("utf-8")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
