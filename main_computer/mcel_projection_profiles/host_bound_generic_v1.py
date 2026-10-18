"""Single IR-to-contract projector for validated host-bound MCEL applications.

This is a projection of *declared* semantics, not proof of host readiness.
App-specific compatibility validators may wrap this projector; they do not
reimplement its generated modules.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping
from main_computer.mcel_application_ir import canonical_json_bytes

NORMALIZED_DEFINITION = "generated/mcel.application.normalized.json"
SURFACE_BUNDLE_SCHEMA = "mcel.application-surface-bundle.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")

class HostBoundIrProjectionError(ValueError):
    """Invalid or unsupported host-bound IR. No output is emitted."""

@dataclass(frozen=True)
class HostBoundIrProjection:
    profile_id: str
    files: Mapping[str, bytes]
    file_hashes: Mapping[str, str]


def project_host_bound_ir(
    application_ir: Mapping[str, Any], *,
    profile_id: str | None = None,
    export_prefix: str | None = None,
    contract_id: str | None = None,
    include_invariants: bool = True,
) -> HostBoundIrProjection:
    """Render one validated host-bound application, failing closed on missing semantics."""
    app = _mapping(application_ir.get("application"))
    app_id = str(app.get("appId") or "")
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", app_id):
        raise HostBoundIrProjectionError("Application IR must provide a safe appId.")
    surfaces = [item for item in application_ir.get("surfaces") or [] if isinstance(item, Mapping)]
    if len(surfaces) != 1:
        raise HostBoundIrProjectionError("Generic host-bound projection requires exactly one surface.")
    surface = surfaces[0]
    host_route = str(surface.get("route") or "")
    root_selector = str(surface.get("root") or "")
    runtime_facade = str(surface.get("runtimeFacade") or "")
    if (surface.get("presentationAuthority") != "existing-host-html"
            or not host_route.startswith("/applications/")
            or host_route != f"/applications/{app_id}"
            or not root_selector.startswith("#")
            or not _IDENTIFIER.fullmatch(runtime_facade)):
        raise HostBoundIrProjectionError("Host route, root, facade or presentation authority is unsupported.")
    semantic_surface = _plain_object(surface.get("semanticSurface"), "semantic surface")
    static_surface_id = str(semantic_surface.get("surfaceId") or "")
    if not static_surface_id or not semantic_surface.get("regions") or not semantic_surface.get("controls"):
        raise HostBoundIrProjectionError("A surfaceId, regions and controls must be declared.")
    layouts = [item for item in application_ir.get("layouts") or [] if isinstance(item, Mapping)]
    if len(layouts) != 1 or _mapping(layouts[0].get("surface")).get("ref") != surface.get("id"):
        raise HostBoundIrProjectionError("Exactly one layout linked to the declared surface is required.")
    layout = layouts[0]
    layout_grammar = _plain_object(layout.get("layoutGrammar"), "layout grammar")
    if not layout_grammar.get("rootSelector") or not layout_grammar.get("regions") or not layout_grammar.get("constraints"):
        raise HostBoundIrProjectionError("Layout root, regions and constraints are required.")
    intents_list = application_ir.get("intents") or []
    if not isinstance(intents_list, list) or not intents_list:
        raise HostBoundIrProjectionError("At least one declared intent is required.")
    intents: dict[str, Mapping[str, Any]] = {}
    for intent in intents_list:
        if not isinstance(intent, Mapping) or not intent.get("sourceName") or not intent.get("runtimeMethod"):
            raise HostBoundIrProjectionError("Every intent requires a sourceName and runtimeMethod.")
        name = str(intent["sourceName"])
        if name in intents or not _IDENTIFIER.fullmatch(str(intent["runtimeMethod"])):
            raise HostBoundIrProjectionError(f"Duplicate intent or unsafe runtimeMethod: {name!r}.")
        if intent.get("writes") or intent.get("transition"):
            raise HostBoundIrProjectionError(f"Host-bound intent {name!r} cannot claim canonical state writes.")
        intents[name] = intent
    profile_id = profile_id or f"mcel.{app_id}.host-bound-projection.v1"
    export_prefix = export_prefix or "".join(part.capitalize() for part in app_id.split("-"))
    if not _IDENTIFIER.fullmatch(export_prefix):
        raise HostBoundIrProjectionError("Unsafe module export prefix.")
    contract_id = contract_id or str(semantic_surface.get("contractId") or "")
    if not contract_id:
        raise HostBoundIrProjectionError("Generic application must declare a semanticSurface.contractId.")
    # Everything below is derived solely from normalized IR plus explicit
    # backward-compatibility output labels.
    domain_payload = {
        "schema": "mcel.application-domain.v1",
        "appId": app_id,
        "projectionProfile": profile_id,
        "semanticVersion": str(app.get("semanticVersion") or "1"),
        "presentationAuthority": "existing-host-html",
        "runtimeFacade": runtime_facade,
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
            **({"invariants": [str(_mapping(value).get("ref") or "") for value in intent.get("invariants") or []]} if include_invariants else {}),
        }
        for source_name, intent in sorted(intents.items())
    }

    surface_payload = {
        "schema": "mcel.application-surface.v1",
        "appId": app_id,
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

    layout_payload = {
        "schema": "mcel.application-layout.v1",
        "appId": app_id,
        "layoutId": str(layout.get("id") or ""),
        "surface": str(_mapping(layout.get("surface")).get("ref") or ""),
        "layoutGrammar": layout_grammar,
        **({"layoutTree": layout["layoutTree"]} if "layoutTree" in layout else {}),
        "orderedChildren": [
            str(_mapping(item).get("ref") or "") for item in layout.get("orderedChildren") or []
        ],
        "zones": [str(value) for value in layout.get("zones") or []],
    }
    surface_bundle_payload = {
        "schema": SURFACE_BUNDLE_SCHEMA,
        "appId": app_id,
        "surfaceId": static_surface_id,
        "workspaceSurface": str(surface.get("id")),
        "contractId": contract_id,
        "route": str(surface.get("route")),
        "rootSelector": str(surface.get("root")),
        "presentationAuthority": str(surface.get("presentationAuthority")),
        "runtimeFacade": str(surface.get("runtimeFacade")),
        "projectionProfile": profile_id,
        "semanticSurface": semantic_surface,
        "layoutGrammar": layout_grammar,
    }

    observation_payload = {
        "schema": "mcel.application-observation.v1",
        "appId": app_id,
        "hostRoute": host_route,
        "rootSelector": root_selector,
        "runtimeFacade": runtime_facade,
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
        "appId": app_id,
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
        "contracts/domain.js": _data_module(f"{export_prefix}Domain", domain_payload),
        "contracts/intents.js": _data_module(f"{export_prefix}Intents", intent_payload),
        "contracts/adapter.js": _adapter_module(bindings, app_id=app_id, runtime_facade=runtime_facade, export_prefix=export_prefix),
        "contracts/surface.js": _data_module(f"{export_prefix}Surface", surface_payload),
        "contracts/layout.js": _data_module(f"{export_prefix}Layout", layout_payload),
        "contracts/surface-bundle.json": canonical_json_bytes(surface_bundle_payload) + b"\n",
        "contracts/observation.js": _data_module(f"{export_prefix}Observation", observation_payload),
        "contracts/acceptance.js": _data_module(f"{export_prefix}Acceptance", acceptance_payload),
        NORMALIZED_DEFINITION: canonical_json_bytes(application_ir) + b"\n",
    }
    hashes = {
        path: "sha256:" + hashlib.sha256(content).hexdigest()
        for path, content in sorted(files.items())
    }
    return HostBoundIrProjection(profile_id, files, hashes)



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


def _adapter_module(bindings: Mapping[str, Any], *, app_id: str, runtime_facade: str, export_prefix: str) -> bytes:
    encoded = json.dumps(bindings, indent=2, sort_keys=True, ensure_ascii=False)
    display_name = " ".join(part.capitalize() for part in app_id.split("-"))
    text = f"""const BINDINGS = Object.freeze({encoded});

function resolveRuntime() {{
  const runtime = globalThis.{runtime_facade};
  if (!runtime || typeof runtime !== "object") {{
    throw new Error("{runtime_facade} is unavailable.");
  }}
  return runtime;
}}

function bindingFor(intentName) {{
  const binding = BINDINGS[String(intentName || "")];
  if (!binding) throw new Error(`Unknown {display_name} intent: ${{intentName}}`);
  return binding;
}}

export const {export_prefix}Adapter = Object.freeze({{
  schema: "mcel.semantic-adapter.v1",
  appId: "{app_id}",
  adapterId: "{app_id}.dsl-authoritative-adapter.v1",
  bindings: BINDINGS,
  invoke(intentName, ...args) {{
    const binding = bindingFor(intentName);
    const method = resolveRuntime()[binding.runtimeMethod];
    if (typeof method !== "function") {{
      throw new Error(`{display_name} runtime method is unavailable: ${{binding.runtimeMethod}}`);
    }}
    return method(...args);
  }}
}});
"""
    return text.encode("utf-8")


def _plain_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HostBoundIrProjectionError(f"{label} must be an object.")
    return json.loads(json.dumps(value, sort_keys=True))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
