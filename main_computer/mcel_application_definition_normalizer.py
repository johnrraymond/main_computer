"""Deterministic normalization authority for high-level MCEL applications.

The human-owned ``application.js`` definition is evaluated with Node.js, reduced
into a canonical data model, and materialized as the explicit MCEL contracts
consumed by the existing package, browser projection, runtime, and proof tools.
This module preserves the application definition's declared proof maturity while
materializing the explicit contracts consumed by package, runtime, and proof tools.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from main_computer.mcel_application_packages import build_application_package_catalog, repository_root
from main_computer.mcel_node_runtime import resolve_node_executable


NORMALIZER_SCHEMA = "mcel.application-definition-normalization.v1"
NORMALIZED_SCHEMA = "mcel.application-definition.normalized.v1"
RESULT_SCHEMA = "mcel.application-definition-normalizer-result.v1"
NORMALIZER_VERSION = "mcel-application-definition-normalizer-v1"
NORMALIZED_PATH = "generated/mcel.application.normalized.json"
GENERATED_CONTRACT_KEYS = ("domain", "intents", "adapter", "surface", "layout", "acceptance", "observation")


class ApplicationDefinitionNormalizationError(RuntimeError):
    exit_code = 5
    result_code = "normalization_failed"


class ApplicationDefinitionNotFound(ApplicationDefinitionNormalizationError):
    exit_code = 3
    result_code = "application_definition_not_found"


class StaleApplicationDefinition(ApplicationDefinitionNormalizationError):
    exit_code = 4
    result_code = "application_definition_stale"


@dataclass(frozen=True)
class NormalizationPlan:
    app_id: str
    package_root: Path
    definition_path: Path
    definition_reference: str
    normalized_reference: str
    definition_fingerprint: str
    source_sha256: str
    files: Mapping[str, bytes]

    @property
    def generated_contract_count(self) -> int:
        return len(GENERATED_CONTRACT_KEYS)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _class_name(app_id: str) -> str:
    return "".join(part.capitalize() for part in app_id.split("-"))


def _function_source(value: Any) -> str | None:
    if isinstance(value, Mapping) and isinstance(value.get("$function"), str):
        return str(value["$function"])
    return None


def _function_hash(value: Any) -> str | None:
    if isinstance(value, Mapping) and isinstance(value.get("sha256"), str):
        return "sha256:" + str(value["sha256"])
    return None


def _without_function_sources(value: Any) -> Any:
    source = _function_source(value)
    if source is not None:
        return {"kind": "function", "sha256": _function_hash(value)}
    if isinstance(value, Mapping):
        return {str(key): _without_function_sources(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, list):
        return [_without_function_sources(item) for item in value]
    return value


def _proof_contract(app: Mapping[str, Any]) -> Mapping[str, Any]:
    value = app.get("proof")
    return value if isinstance(value, Mapping) else {}


def _runtime_status(app: Mapping[str, Any]) -> str:
    value = str(_proof_contract(app).get("runtimeStatus") or "forward-specification").strip()
    if value not in {"forward-specification", "semantic-runtime-proven"}:
        raise ApplicationDefinitionNormalizationError(
            f"Unsupported application proof runtimeStatus: {value!r}."
        )
    return value


def _acceptance_status(app: Mapping[str, Any]) -> str:
    value = str(_proof_contract(app).get("acceptanceStatus") or _runtime_status(app)).strip()
    if value not in {"forward-specification", "verified", "semantic-runtime-proven"}:
        raise ApplicationDefinitionNormalizationError(
            f"Unsupported application proof acceptanceStatus: {value!r}."
        )
    return value


def _export_definition(repo: Path, definition_path: Path) -> Mapping[str, Any]:
    node = resolve_node_executable()
    exporter = repo / "tools/mcel_application_definition_export.js"
    completed = subprocess.run(
        [node, str(exporter), str(definition_path)],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        timeout=45,
        env={**os.environ},
    )
    if completed.returncode != 0:
        raise ApplicationDefinitionNormalizationError(
            "Could not evaluate MCEL application definition: "
            + (completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}")
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ApplicationDefinitionNormalizationError("Application definition exporter returned invalid JSON.") from exc
    if not isinstance(payload, dict) or payload.get("schema") != "mcel.application-definition.v1":
        raise ApplicationDefinitionNormalizationError("Application definition exporter returned an unsupported definition.")
    return payload


def _js_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _js_value(value: Any, indent: int = 0) -> str:
    function = _function_source(value)
    if function is not None:
        return f"({function})"
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, str):
        return _js_string(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        inner = ",\n".join(" " * (indent + 2) + _js_value(item, indent + 2) for item in value)
        return "[\n" + inner + "\n" + " " * indent + "]"
    if isinstance(value, Mapping):
        if value.get("$undefined") is True:
            return "undefined"
        if not value:
            return "{}"
        parts = []
        for key in sorted(value, key=str):
            if key in {"$function", "sha256"}:
                continue
            parts.append(" " * (indent + 2) + f"{_js_string(str(key))}: " + _js_value(value[key], indent + 2))
        return "{\n" + ",\n".join(parts) + "\n" + " " * indent + "}"
    raise TypeError(f"Unsupported JavaScript value: {type(value)!r}")


def _header(app_id: str, fingerprint: str) -> str:
    return (
        "// Generated by mcel-application-definition-normalizer-v1.\n"
        f"// Source authority: mcel_apps/{app_id}/application.js\n"
        f"// Definition fingerprint: {fingerprint}\n"
        "// Do not edit this file directly.\n\n"
        "function deepFreeze(value, seen = new Set()) {\n"
        "  if (!value || (typeof value !== \"object\" && typeof value !== \"function\")) return value;\n"
        "  if (seen.has(value)) return value;\n"
        "  seen.add(value);\n"
        "  Reflect.ownKeys(value).forEach((key) => deepFreeze(value[key], seen));\n"
        "  return Object.freeze(value);\n"
        "}\n\n"
    )


def _state_path(path: str, state: Mapping[str, Any]) -> str:
    root = path.split(".", 1)[0]
    authority = (state.get(root) or {}).get("authority")
    prefix = {"canonical": "state", "provisional": "provisional", "renderer-local": "local", "derived": "derived"}.get(authority, "state")
    return f"{prefix}.{path}"


def _payload_source(value: Mapping[str, Any]) -> Mapping[str, Any]:
    kind = value.get("sourceKind")
    if kind == "node-property":
        result: dict[str, Any] = {"fromNode": value.get("nodeId"), "property": value.get("property") or "value"}
        if value.get("parse"): result["parse"] = value["parse"]
        if value.get("normalize"): result["normalize"] = value["normalize"]
        return result
    if kind == "item-key":
        return {"fromItemKey": True}
    if kind == "item-field":
        result = {"fromItemField": value.get("path"), "property": value.get("property") or "value"}
        if value.get("parse"): result["parse"] = value["parse"]
        if value.get("normalize"): result["normalize"] = value["normalize"]
        return result
    if kind == "state-path":
        return {"fromStatePath": value.get("path")}
    if kind == "literal":
        return {"literal": value.get("value")}
    return dict(value)


def _schema_summary(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return {"name": value.get("name", "any"), "describe": value.get("describe", {})}


def _render_domain(app: Mapping[str, Any], export_name: str, fingerprint: str) -> bytes:
    state = app.get("state", {})
    canonical = {key: entry.get("initial") for key, entry in state.items() if entry.get("authority") == "canonical"}
    local = {key: entry.get("initial") for key, entry in state.items() if entry.get("authority") == "renderer-local"}
    local_definitions = [
        {"id": key, "initial": entry.get("initial"), "schema": _schema_summary(entry.get("schema", {}))}
        for key, entry in state.items() if entry.get("authority") == "renderer-local"
    ]
    provisional = {key: entry.get("initial") for key, entry in state.items() if entry.get("authority") == "provisional"}
    provisional_definitions = [
        {"id": key, "initial": entry.get("initial"), "schema": _schema_summary(entry.get("schema", {}))}
        for key, entry in state.items() if entry.get("authority") == "provisional"
    ]
    capabilities = {}
    for alias, entry in sorted((app.get("capabilities") or {}).items()):
        capabilities[alias] = {
            "id": entry.get("id"),
            "risk": entry.get("risk"),
            "description": entry.get("description", ""),
            "operations": {
                name: {
                    "stream": operation.get("stream") is True,
                    "cancellable": operation.get("cancellable") is True,
                    "request": _schema_summary(operation.get("request", {})),
                    "response": _schema_summary(operation.get("response", {})),
                }
                for name, operation in sorted((entry.get("operations") or {}).items())
            },
        }
    derived = []
    for key, entry in state.items():
        if entry.get("authority") == "derived":
            derived.append({
                "id": key,
                "reads": entry.get("reads", []),
                "schema": _schema_summary(entry.get("schema", {})),
                "compute": entry.get("compute"),
                "computeFingerprint": _function_hash(entry.get("compute")),
            })
    invariants = []
    for entry in app.get("invariants", []):
        invariants.append({
            "id": entry.get("id"),
            "reads": [_state_path(path, state) for path in entry.get("reads", [])],
            "check": entry.get("check"),
            "description": entry.get("description", ""),
        })
    payload = {
        "schema": "mcel.application-domain.v1",
        "appId": app["id"],
        "currentRuntimeStatus": _runtime_status(app),
        "definitionFingerprint": fingerprint,
        "initialState": canonical,
        "rendererLocalState": local,
        "rendererLocalStateDefinitions": local_definitions,
        "provisionalState": provisional,
        "provisionalStateDefinitions": provisional_definitions,
        "capabilities": capabilities,
        "derivedState": derived,
        "invariantReads": sorted({path for item in invariants for path in item["reads"]}),
        "invariants": invariants,
    }
    source = _header(app["id"], fingerprint) + f"export const {export_name} = deepFreeze(" + _js_value(payload) + ");\n"
    return source.encode("utf-8")


def _render_intents(app: Mapping[str, Any], export_name: str, fingerprint: str) -> bytes:
    state = app.get("state", {})
    result: dict[str, Any] = {}
    kind_map = {"mutation": "mutation", "async": "async-capability", "cancel": "cancel-operation", "prohibited": "prohibited"}
    for op_id, entry in sorted(app.get("operations", {}).items()):
        item: dict[str, Any] = {
            "id": op_id,
            "kind": kind_map.get(entry.get("operationKind"), entry.get("operationKind")),
            "risk": entry.get("risk"),
            "payload": {key: _payload_source(value) for key, value in sorted((entry.get("payload") or {}).items())},
            "reads": [_state_path(path, state) for path in entry.get("reads", [])],
            "writes": [_state_path(path, state) for path in entry.get("writes", [])],
        }
        if entry.get("uses"): item["uses"] = list(entry["uses"])
        if entry.get("provisionalPath"):
            item["provisionalPath"] = entry["provisionalPath"]
            item["writes"] = [f"provisional.{entry['provisionalPath']}", *item["writes"]]
        if entry.get("concurrency") and entry.get("concurrency") != "serial-per-application": item["concurrency"] = entry["concurrency"]
        if entry.get("cancellable"): item["cancellable"] = True
        if entry.get("cancels"): item["cancels"] = entry["cancels"]
        if entry.get("reason"): item["reason"] = entry["reason"]
        item["implementation"] = {
            key: _function_hash(entry.get(key))
            for key in ("preflight", "transition", "ensures", "run", "receive", "commit", "cancel")
            if _function_hash(entry.get(key))
        }
        result[op_id] = item
    payload = {"schema": "mcel.application-intents.v1", "appId": app["id"], "definitionFingerprint": fingerprint, "intents": result}
    source = _header(app["id"], fingerprint) + f"const definition = deepFreeze({_js_value(payload)});\n\nexport const {export_name} = definition.intents;\nexport const {export_name}Contract = definition;\n"
    return source.encode("utf-8")


def _render_adapter(app: Mapping[str, Any], export_name: str, fingerprint: str) -> bytes:
    implementations: dict[str, Any] = {}
    metadata: dict[str, Any] = {}
    for op_id, entry in sorted(app.get("operations", {}).items()):
        implementations[op_id] = {
            key: entry[key]
            for key in ("preflight", "transition", "ensures", "run", "receive", "commit", "cancel")
            if _function_source(entry.get(key)) is not None
        }
        metadata[op_id] = {
            "operationKind": entry.get("operationKind"),
            "risk": entry.get("risk"),
            "reason": entry.get("reason", ""),
            "cancels": entry.get("cancels", ""),
        }
    preamble = _header(app["id"], fingerprint)
    source = preamble + f"const operationMetadata = deepFreeze({_js_value(metadata)});\nconst operationImplementations = deepFreeze({_js_value(implementations)});\n\n"
    source += '''function operation(intentId) {\n  return {metadata: operationMetadata[intentId] || null, implementation: operationImplementations[intentId] || null};\n}\n\n'''
    source += f'''export const {export_name} = deepFreeze({{\n  schema: "mcel.semantic-adapter.v1",\n  appId: {_js_string(app['id'])},\n  adapterId: {_js_string(app['id'] + '.adapter.v1')},\n  currentRuntimeStatus: {_js_string(_runtime_status(app))},\n  targetRuntimeStatus: "fullApplicationSemanticReady",\n  definitionFingerprint: {_js_string(fingerprint)},\n\n  preflight({{intentId, input, state}}) {{\n    const declared = operation(intentId);\n    if (!declared.metadata) return {{ok: false, code: "INTENT_UNKNOWN"}};\n    if (declared.metadata.operationKind === "prohibited") return {{ok: false, code: "INTENT_PROHIBITED", message: declared.metadata.reason}};\n    if (Object.prototype.hasOwnProperty.call(state || {{}}, "revision") && input?.expectedRevision !== state.revision) {{\n      return {{ok: false, code: "REVISION_STALE"}};\n    }}\n    const payload = input?.payload || {{}};\n    return typeof declared.implementation?.preflight === "function"\n      ? declared.implementation.preflight({{intentId, input, payload, state}})\n      : {{ok: true}};\n  }},\n\n  transition({{intentId, input, state}}) {{\n    const declared = operation(intentId);\n    if (!declared.metadata) throw Object.assign(new Error(`Unknown intent ${{intentId}}.`), {{code: "INTENT_UNKNOWN"}});\n    if (declared.metadata.operationKind === "async") throw Object.assign(new Error("Capability operation runtime is not implemented."), {{code: "MCEL_CAPABILITY_OPERATION_UNSUPPORTED"}});\n    if (declared.metadata.operationKind === "cancel") throw Object.assign(new Error("Operation cancellation runtime is not implemented."), {{code: "MCEL_OPERATION_CANCELLATION_UNSUPPORTED"}});\n    if (typeof declared.implementation?.transition !== "function") throw Object.assign(new Error(`Intent ${{intentId}} has no transition.`), {{code: "INTENT_TRANSITION_UNAVAILABLE"}});\n    return declared.implementation.transition({{intentId, input, payload: input?.payload || {{}}, state}});\n  }},\n\n  validateEffects({{intentId, before, after, input}}) {{\n    const declared = operation(intentId);\n    if (typeof declared.implementation?.ensures !== "function") return true;\n    return declared.implementation.ensures({{intentId, before, after, input, payload: input?.payload || {{}}}});\n  }},\n\n  runCapabilityOperation(context) {{\n    const declared = operation(context?.intentId);\n    if (typeof declared.implementation?.run !== "function") throw Object.assign(new Error("Capability operation is unavailable."), {{code: "MCEL_CAPABILITY_OPERATION_UNSUPPORTED"}});\n    return declared.implementation.run(context);\n  }},\n\n  receiveProvisional(context) {{\n    const declared = operation(context?.intentId);\n    if (typeof declared.implementation?.receive !== "function") throw Object.assign(new Error("Provisional reconciliation is unavailable."), {{code: "MCEL_PROVISIONAL_COMMIT_RUNTIME_UNSUPPORTED"}});\n    return declared.implementation.receive(context);\n  }},\n\n  commitCapabilityOperation(context) {{\n    const declared = operation(context?.intentId);\n    if (typeof declared.implementation?.commit !== "function") throw Object.assign(new Error("Capability commit is unavailable."), {{code: "MCEL_CAPABILITY_OPERATION_UNSUPPORTED"}});\n    return declared.implementation.commit(context);\n  }},\n\n  cancelOperation(context) {{\n    const declared = operation(context?.intentId);\n    if (typeof declared.implementation?.cancel !== "function") throw Object.assign(new Error("Operation cancellation is unavailable."), {{code: "MCEL_OPERATION_CANCELLATION_UNSUPPORTED"}});\n    return declared.implementation.cancel(context);\n  }}\n}});\n'''
    return source.encode("utf-8")


def _surface_source(value: Any) -> Any:
    if not isinstance(value, Mapping): return value
    kind = value.get("sourceKind")
    if kind == "latest-receipt": return {"fromLatestReceipt": value.get("path")}
    return {str(key): _surface_source(value[key]) for key in sorted(value, key=str) if key not in {"kind", "$undefined"}}


def _render_surface(app: Mapping[str, Any], export_name: str, fingerprint: str) -> bytes:
    surface = app["surface"]
    nodes = []
    for entry in surface.get("nodes", []):
        node: dict[str, Any] = {
            "id": entry.get("id"), "kind": entry.get("nodeKind"), "regionId": entry.get("regionId")
        }
        for key in ("statePath", "property", "transform", "inputType", "localPath", "intentId", "templateId", "keyPath"):
            if entry.get(key) not in {None, ""}: node[key] = entry[key]
        if entry.get("payload"): node["payload"] = {k: _payload_source(v) for k, v in sorted(entry["payload"].items())}
        if entry.get("properties"): node["properties"] = entry["properties"]
        if entry.get("source"): node["source"] = _surface_source(entry["source"])
        for key in ("when", "content", "accessibility"):
            if entry.get(key): node[key] = entry[key]
        if entry.get("item"):
            item = entry["item"]
            controls = {}
            for name, control in sorted((item.get("controls") or {}).items()):
                controls[name] = dict(control)
            node["item"] = {"fields": item.get("fields", {}), "controls": controls}
        nodes.append(node)
    payload = {
        "schema": "mcel.semantic-surface-ir.v1", "appId": app["id"], "surfaceId": surface["id"],
        "currentRuntimeStatus": _runtime_status(app), "definitionFingerprint": fingerprint,
        "regions": surface.get("regions", []), "nodes": nodes,
    }
    source = _header(app["id"], fingerprint) + f"export const {export_name} = deepFreeze(" + _js_value(payload) + ");\n"
    return source.encode("utf-8")


def _render_simple(app: Mapping[str, Any], export_name: str, fingerprint: str, kind: str) -> bytes:
    if kind == "layout":
        payload = dict(app.get("layout", {})); payload.setdefault("schema", "mcel.layout-grammar.v1"); payload["definitionFingerprint"] = fingerprint
    elif kind == "acceptance":
        payload = {"schema": "mcel.acceptance-suite.v1", "appId": app["id"], "currentStatus": _acceptance_status(app), "definitionFingerprint": fingerprint,
                   "scenarios": [{"id": e.get("id"), "kind": e.get("acceptanceKind"), "operationId": e.get("operationId", ""), "given": e.get("given", {}), "when": e.get("when", {}), "expect": e.get("expect", {})} for e in app.get("acceptance", [])]}
    elif kind == "observation":
        observations=[]
        for e in app.get("observations", []):
            item={"id":e.get("id"),"kind":e.get("observationKind"),"source":e.get("source") or "browser-dom"}
            mapping={"nodeId":"semanticNodeId","statePath":"compareToStatePath","property":"property","normalization":"normalization","keyPath":"keyPath","fields":"fields","requireOrderMatch":"requireOrderMatch","requireItemControls":"requireItemControls","compareToLatestReceiptPath":"compareToLatestReceiptPath","compareToStatePredicate":"compareToStatePredicate","compareToProvisionalStatePath":"compareToProvisionalStatePath","compareToOperationReceipt":"compareToOperationReceipt","minimumInstances":"minimumInstances","requireIsolated":"requireIsolated","expect":"expect"}
            for src,dst in mapping.items():
                value=e.get(src)
                if value not in (None,"",False,0,[],{}): item[dst]=value
            observations.append(item)
        proof = app.get("proof") if isinstance(app.get("proof"), dict) else {}
        observation_status = str(proof.get("browserObservation") or "forward-specification")
        payload={"schema":"mcel.observation-contract.v1","appId":app["id"],"currentStatus":observation_status,"definitionFingerprint":fingerprint,"observations":observations}
    else:
        raise AssertionError(kind)
    source = _header(app["id"], fingerprint) + f"export const {export_name} = deepFreeze(" + _js_value(payload) + ");\n"
    return source.encode("utf-8")


def build_normalization_plan(app_id: str, repo_root: Path | None = None) -> NormalizationPlan:
    repo = (Path(repo_root) if repo_root is not None else repository_root()).resolve()
    catalog = build_application_package_catalog(repo)
    record = next((item for item in catalog.packages if item.app_id == app_id), None)
    if record is None:
        raise ApplicationDefinitionNotFound(f"MCEL application package {app_id!r} was not found.")
    definition_reference = str(record.authoring.get("definition") or "")
    if not definition_reference:
        raise ApplicationDefinitionNotFound(f"Application {app_id!r} does not declare authoring.definition.")
    definition_path = repo / PurePosixPath(definition_reference)
    package_root = repo / PurePosixPath(record.package_root)
    exported = _export_definition(repo, definition_path)
    if exported.get("id") != app_id:
        raise ApplicationDefinitionNormalizationError("Application definition identity disagrees with package identity.")
    source_bytes = definition_path.read_bytes()
    source_sha = _sha256(source_bytes)
    normalized_body = {
        "schema": NORMALIZED_SCHEMA,
        "normalizer": NORMALIZER_VERSION,
        "appId": app_id,
        "source": definition_reference,
        "sourceSha256": source_sha,
        "definition": _without_function_sources(exported),
    }
    fingerprint = "sha256:" + _sha256(_canonical_json(normalized_body))
    normalized = {**normalized_body, "definitionFingerprint": fingerprint}
    class_name = _class_name(app_id)
    files: dict[str, bytes] = {
        NORMALIZED_PATH: _canonical_json(normalized),
        "contracts/domain.js": _render_domain(exported, f"{class_name}Domain", fingerprint),
        "contracts/intents.js": _render_intents(exported, f"{class_name}Intents", fingerprint),
        "contracts/adapter.js": _render_adapter(exported, f"{class_name}Adapter", fingerprint),
        "contracts/surface.js": _render_surface(exported, f"{class_name}Surface", fingerprint),
        "contracts/layout.js": _render_simple(exported, f"{class_name}Layout", fingerprint, "layout"),
        "contracts/acceptance.js": _render_simple(exported, f"{class_name}Acceptance", fingerprint, "acceptance"),
        "contracts/observation.js": _render_simple(exported, f"{class_name}Observation", fingerprint, "observation"),
    }
    manifest_path = package_root / "mcel.app.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    authoring = dict(manifest.get("authoring") or {})
    authoring["normalizedDefinition"] = NORMALIZED_PATH
    runtime_status = _runtime_status(exported)
    if runtime_status == "semantic-runtime-proven":
        authoring["status"] = "semantic-runtime-proven"
    manifest["authoring"] = authoring
    manifest["normalization"] = {
        "schema": NORMALIZER_SCHEMA,
        "normalizer": NORMALIZER_VERSION,
        "definitionFingerprint": fingerprint,
        "sourceSha256": source_sha,
        "generatedContracts": list(GENERATED_CONTRACT_KEYS),
    }
    conformance = dict(manifest.get("conformance") or {})
    bridges = list(conformance.get("missingBridges") or [])
    if runtime_status == "semantic-runtime-proven":
        conformance["currentMode"] = "semantic-runtime-proven"
        conformance["targetMode"] = "semantic-runtime-proven"
        conformance["missingBridges"] = []
    else:
        conformance["missingBridges"] = [item for item in bridges if item != "application-definition-normalization"]
    manifest["conformance"] = conformance
    files["mcel.app.json"] = _canonical_json(manifest)
    return NormalizationPlan(app_id, package_root, definition_path, definition_reference, NORMALIZED_PATH, fingerprint, source_sha, files)


def check_normalization(plan: NormalizationPlan) -> tuple[bool, tuple[str, ...]]:
    stale=[]
    for relative, expected in sorted(plan.files.items()):
        path=plan.package_root / PurePosixPath(relative)
        if not path.is_file() or path.read_bytes()!=expected:
            stale.append(relative)
    return not stale, tuple(stale)


def write_normalization(plan: NormalizationPlan) -> tuple[str, ...]:
    _, stale = check_normalization(plan)
    for relative in stale:
        target=plan.package_root / PurePosixPath(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd,temp_name=tempfile.mkstemp(prefix=f".{target.name}.",suffix=".tmp",dir=target.parent)
        try:
            with os.fdopen(fd,"wb") as handle: handle.write(plan.files[relative])
            os.replace(temp_name,target)
        finally:
            if os.path.exists(temp_name): os.unlink(temp_name)
    return stale
