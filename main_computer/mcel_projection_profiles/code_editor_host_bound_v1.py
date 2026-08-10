"""Deterministic host-bound projection for the Code Editor DSL authority.

The profile projects canonical Code Editor Application IR into browser contract
modules without copying the existing Code Editor HTML, CSS, or runtime shell.
The stable host route, root selector, and canonical runtime facade remain the
durable boundary while the DSL becomes the package authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from main_computer.mcel_application_ir import canonical_json_bytes


PROFILE_ID = "mcel.code-editor.host-bound-projection.v1"
APP_ID = "code-editor"
NORMALIZED_DEFINITION = "generated/mcel.application.normalized.json"
RUNTIME_FACADE = "MainComputerCodeEditorRuntime"
HOST_ROUTE = "/applications/code-editor"
ROOT_SELECTOR = "#code-editor-app"
STATIC_SURFACE_ID = "code-editor.surface.monaco-selected-file-editor"
AUTHORING_CONTRACT_ID = "code-editor.contract.authoring.monaco-golden-path"
SURFACE_BUNDLE_SCHEMA = "mcel.application-surface-bundle.v1"
EXPECTED_INTENTS = {
    "applyReviewedPatch": "applyReviewedPatch",
    "closeFile": "closeFile",
    "discardDraft": "discardDraft",
    "editDraft": "editDraft",
    "inspectWorkspace": "inspectWorkspace",
    "openFile": "openFile",
    "previewAiderPlan": "previewAiderPlan",
    "saveFile": "saveFile",
}
EXPECTED_CAPABILITIES = {
    "capability:code-editor.aider-planning",
    "capability:code-editor.reviewed-patch-application",
    "capability:code-editor.source-workspace",
}


class CodeEditorProjectionProfileError(ValueError):
    """Raised when Code Editor IR cannot be projected without guessing."""


@dataclass(frozen=True)
class CodeEditorProjection:
    profile_id: str
    files: Mapping[str, bytes]
    file_hashes: Mapping[str, str]


def project_code_editor_ir(application_ir: Mapping[str, Any]) -> CodeEditorProjection:
    """Project one validated Code Editor IR document into deterministic files."""

    app = _mapping(application_ir.get("application"))
    if app.get("appId") != APP_ID:
        raise CodeEditorProjectionProfileError(
            f"Code Editor projection requires appId {APP_ID!r}; observed {app.get('appId')!r}."
        )

    intents = {
        str(item.get("sourceName")): item
        for item in application_ir.get("intents") or []
        if isinstance(item, Mapping) and isinstance(item.get("sourceName"), str)
    }
    observed_intents = set(intents)
    if observed_intents != set(EXPECTED_INTENTS):
        raise CodeEditorProjectionProfileError(
            "Code Editor projection requires the stable DSL-native runtime intents; "
            f"missing={sorted(set(EXPECTED_INTENTS) - observed_intents)!r}, "
            f"extra={sorted(observed_intents - set(EXPECTED_INTENTS))!r}."
        )
    for source_name, expected_method in sorted(EXPECTED_INTENTS.items()):
        intent = intents[source_name]
        if intent.get("runtimeMethod") != expected_method:
            raise CodeEditorProjectionProfileError(
                f"Intent {source_name!r} must bind runtime method {expected_method!r}."
            )
        if intent.get("writes"):
            raise CodeEditorProjectionProfileError(
                f"Code Editor host-bound intent {source_name!r} may not claim canonical writes."
            )
        if intent.get("transition"):
            raise CodeEditorProjectionProfileError(
                f"Code Editor host-bound intent {source_name!r} may not embed a canonical transition."
            )

    capability_ids = {
        str(item.get("id"))
        for item in application_ir.get("capabilities") or []
        if isinstance(item, Mapping)
    }
    if capability_ids != EXPECTED_CAPABILITIES:
        raise CodeEditorProjectionProfileError(
            "Code Editor capability set drifted from the source-workspace, Aider planning, and reviewed-patch lanes."
        )

    surfaces = [
        item for item in application_ir.get("surfaces") or []
        if isinstance(item, Mapping) and item.get("id") == "surface:code-editor.workspace"
    ]
    if len(surfaces) != 1:
        raise CodeEditorProjectionProfileError("Code Editor workspace surface is missing or duplicated.")
    surface = surfaces[0]
    if surface.get("root") != ROOT_SELECTOR or surface.get("route") != HOST_ROUTE:
        raise CodeEditorProjectionProfileError(
            "Code Editor projection must remain bound to /applications/code-editor and #code-editor-app."
        )
    if surface.get("presentationAuthority") != "existing-host-html":
        raise CodeEditorProjectionProfileError(
            "Code Editor HTML must remain the declared presentation authority for this host-bound projection."
        )
    if surface.get("runtimeFacade") != RUNTIME_FACADE:
        raise CodeEditorProjectionProfileError(
            f"Code Editor projection requires canonical runtime facade {RUNTIME_FACADE!r}."
        )

    domain_payload = {
        "schema": "mcel.application-domain.v1",
        "appId": APP_ID,
        "projectionProfile": PROFILE_ID,
        "semanticVersion": str(app.get("semanticVersion") or "1"),
        "presentationAuthority": "existing-host-html",
        "runtimeFacade": RUNTIME_FACADE,
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
            "invariants": [str(_mapping(value).get("ref") or "") for value in intent.get("invariants") or []],
        }
        for source_name, intent in sorted(intents.items())
    }
    semantic_surface = _plain_object(surface.get("semanticSurface"), "Code Editor semantic surface declaration")
    if semantic_surface.get("surfaceId") != STATIC_SURFACE_ID:
        raise CodeEditorProjectionProfileError(
            f"Code Editor semantic surface must target {STATIC_SURFACE_ID!r}."
        )
    if not semantic_surface.get("regions") or not semantic_surface.get("controls"):
        raise CodeEditorProjectionProfileError("Code Editor semantic surface must declare regions and controls.")

    surface_payload = {
        "schema": "mcel.application-surface.v1",
        "appId": APP_ID,
        "surfaceId": str(surface.get("id")),
        "route": str(surface.get("route")),
        "rootSelector": str(surface.get("root")),
        "presentationAuthority": str(surface.get("presentationAuthority")),
        "runtimeFacade": str(surface.get("runtimeFacade")),
        "semanticSurface": semantic_surface,
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
        if isinstance(item, Mapping) and item.get("id") == "layout:code-editor.workspace"
    ]
    layout = layouts[0] if layouts else {}
    layout_grammar = _plain_object(layout.get("layoutGrammar"), "Code Editor layout grammar declaration")
    if layout_grammar.get("rootSelector") != ".code-studio-shell":
        raise CodeEditorProjectionProfileError("Code Editor layout grammar must remain rooted at the legacy shell.")
    if not layout_grammar.get("regions") or not layout_grammar.get("constraints"):
        raise CodeEditorProjectionProfileError("Code Editor layout grammar must declare regions and constraints.")

    layout_payload = {
        "schema": "mcel.application-layout.v1",
        "appId": APP_ID,
        "layoutId": str(layout.get("id") or ""),
        "surface": str(_mapping(layout.get("surface")).get("ref") or ""),
        "layoutGrammar": layout_grammar,
        "orderedChildren": [
            str(_mapping(item).get("ref") or "") for item in layout.get("orderedChildren") or []
        ],
        "zones": [str(value) for value in layout.get("zones") or []],
    }
    surface_bundle_payload = {
        "schema": SURFACE_BUNDLE_SCHEMA,
        "appId": APP_ID,
        "surfaceId": STATIC_SURFACE_ID,
        "workspaceSurface": str(surface.get("id")),
        "contractId": AUTHORING_CONTRACT_ID,
        "route": str(surface.get("route")),
        "rootSelector": str(surface.get("root")),
        "presentationAuthority": str(surface.get("presentationAuthority")),
        "runtimeFacade": str(surface.get("runtimeFacade")),
        "projectionProfile": PROFILE_ID,
        "semanticSurface": semantic_surface,
        "layoutGrammar": layout_grammar,
    }

    observation_payload = {
        "schema": "mcel.application-observation.v1",
        "appId": APP_ID,
        "hostRoute": HOST_ROUTE,
        "rootSelector": ROOT_SELECTOR,
        "runtimeFacade": RUNTIME_FACADE,
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
        "contracts/domain.js": _data_module("CodeEditorDomain", domain_payload),
        "contracts/intents.js": _data_module("CodeEditorIntents", intent_payload),
        "contracts/adapter.js": _adapter_module(bindings),
        "contracts/surface.js": _data_module("CodeEditorSurface", surface_payload),
        "contracts/layout.js": _data_module("CodeEditorLayout", layout_payload),
        "contracts/surface-bundle.json": canonical_json_bytes(surface_bundle_payload) + b"\n",
        "contracts/observation.js": _data_module("CodeEditorObservation", observation_payload),
        "contracts/acceptance.js": _data_module("CodeEditorAcceptance", acceptance_payload),
        NORMALIZED_DEFINITION: canonical_json_bytes(application_ir) + b"\n",
    }
    hashes = {
        path: "sha256:" + hashlib.sha256(content).hexdigest()
        for path, content in sorted(files.items())
    }
    return CodeEditorProjection(PROFILE_ID, files, hashes)


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
  const runtime = globalThis.MainComputerCodeEditorRuntime;
  if (!runtime || typeof runtime !== "object") {{
    throw new Error("MainComputerCodeEditorRuntime is unavailable.");
  }}
  return runtime;
}}

function bindingFor(intentName) {{
  const binding = BINDINGS[String(intentName || "")];
  if (!binding) throw new Error(`Unknown Code Editor intent: ${{intentName}}`);
  return binding;
}}

export const CodeEditorAdapter = Object.freeze({{
  schema: "mcel.semantic-adapter.v1",
  appId: "code-editor",
  adapterId: "code-editor.dsl-authoritative-adapter.v1",
  bindings: BINDINGS,
  invoke(intentName, ...args) {{
    const binding = bindingFor(intentName);
    const method = resolveRuntime()[binding.runtimeMethod];
    if (typeof method !== "function") {{
      throw new Error(`Code Editor runtime method is unavailable: ${{binding.runtimeMethod}}`);
    }}
    return method(...args);
  }}
}});
"""
    return text.encode("utf-8")


def _plain_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CodeEditorProjectionProfileError(f"{label} must be an object.")
    return json.loads(json.dumps(value, sort_keys=True))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
