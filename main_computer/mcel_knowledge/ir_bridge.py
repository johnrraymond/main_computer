"""Patch 12: knowledge -> existing MCEL Application IR -> read-only semantic consumer.

No GUI, host IO, writes, capabilities or authorization. A schema binding says *where*
data comes from; the computer catalog says *what it means*. Semantic node ids and
concepts remain independent of producer field names.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Mapping

from main_computer.mcel_application_ir import (canonical_json_bytes, validate_application_ir)
from . import SemanticCatalogError, _path_get, ABSENT, interpret, load_catalog
from .foundations import format_spec, validate_envelope_spec

BRIDGE_SCHEMA = "mcel.knowledge-application-bridge.v1"
VIEW_SCHEMA = "mcel.knowledge-consumer-projection.v1"
COLLECTION_SCHEMA = "mcel.semantic-collection-spec.v2"
ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
FIELD = re.compile(r"^[a-zA-Z][\w-]*$")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _checked_path(path: str) -> str:
    # Use the same finite dotted-path language as the computer-knowledge interpreter.
    _path_get({}, path)
    return path


def _binding_catalog(catalog: Mapping[str, Any], selected: tuple[str, ...], bindings: Mapping[str, Any]) -> dict:
    if set(bindings) - set(selected):
        raise SemanticCatalogError("Binding references an unselected observation")
    cat = deepcopy(catalog)
    for ident in selected:
        obs = cat["observations"][ident]
        overlay = bindings.get(ident, {})
        if not isinstance(overlay, Mapping) or set(overlay) - {"source", "properties", "context", "envelope"}:
            raise SemanticCatalogError(f"Unsupported binding keys for {ident}")
        if "source" in overlay:
            obs["source"] = _checked_path(overlay["source"])
            obs["sourceAlternates"] = []  # Explicit binding cannot silently fall back to another feed.
        fields = overlay.get("properties", {})
        contexts = overlay.get("context", {})
        if not isinstance(fields, Mapping) or not isinstance(contexts, Mapping):
            raise SemanticCatalogError(f"Invalid property/context bindings: {ident}")
        by_name = {p["name"]: p for p in obs["properties"]}
        if set(fields) - set(by_name) or set(contexts) - set(obs.get("context", {})):
            raise SemanticCatalogError(f"Unknown bound property/context: {ident}")
        source_remap = {}
        for name, path in fields.items():
            orig = by_name[name]["source"]
            try:
                spec = format_spec(path)
            except ValueError as exc:
                raise SemanticCatalogError(f"Invalid binding {ident}.{name}: {exc}") from exc
            new = _checked_path(spec['path'])
            if 'format' in spec or 'formats' in spec:
                if by_name[name]['type'] != 'mc.timestamp':
                    raise SemanticCatalogError('Source format only applies to timestamp concepts')
                by_name[name]['sourceFormat'] = spec.get('format',spec.get('formats'))
            by_name[name]["source"] = new
            source_remap[orig] = new
        for name, path in contexts.items():
            obs["context"][name] = _checked_path(path)
        if 'envelope' in overlay:
            try:
                obs['envelope'] = validate_envelope_spec(overlay['envelope'], checked_path=_checked_path)
            except (ValueError, TypeError, KeyError) as exc:
                raise SemanticCatalogError(f'Invalid observation envelope for {ident}: {exc}') from exc
        # Identity and availability track renamed fields, never field-name guesses.
        if "identity" in obs:
            obs["identity"]["fields"] = [source_remap.get(k, k) for k in obs["identity"]["fields"]]
        for prop in obs["properties"]:
            for gate in ("availableIf", "availabilityFrom"):
                if prop.get(gate) in source_remap:
                    prop[gate] = source_remap[prop[gate]]
            when = prop.get("availableWhen")
            if isinstance(when, dict) and when.get("source") in source_remap:
                when["source"] = source_remap[when["source"]]
    return cat


def compile_knowledge_ir(app_id: str, *, observation_ids: list[str] | None = None,
                         bindings: Mapping[str, Any] | None = None,
                         catalog: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Compile native concepts to *the existing* validated Application IR models.

    Semantic model/field identities and IR semantic fingerprint do not change
    under producer remapping; source bindings live in IR provenance.
    """
    if not isinstance(app_id, str) or not ID.fullmatch(app_id):
        raise SemanticCatalogError("Invalid application id")
    base = catalog if catalog is not None else load_catalog()
    selection = sorted(base["observations"] if observation_ids is None else observation_ids)
    if not selection or len(set(selection)) != len(selection) or set(selection) - set(base["observations"]):
        raise SemanticCatalogError("Unknown, duplicate or empty observations")
    overlays = deepcopy(dict(bindings or {}))
    bound = _binding_catalog(base, tuple(selection), overlays)
    models, node_bindings = [], []
    for ident in selection:
        o = base["observations"][ident]
        b = bound["observations"][ident]
        mid = "model:knowledge." + ident
        fields = []
        for index, (p, bp) in enumerate(zip(o["properties"], b["properties"])):
            fid = "field:knowledge." + ident + "." + p["name"]
            fields.append({"id": fid, "kind": "field", "conceptRef": p["type"],
                           "propertyName": p["name"], "requiredObservation": p.get("required", False)})
            node_bindings.append({"id": "binding:knowledge." + ident + "." + p["name"],
                                  "semanticId": fid, "sourcePath": bp["source"],
                                  "sourceOrigin": bp.get("fromContext", "record"),
                                  **({'sourceFormat': bp['sourceFormat']} if 'sourceFormat' in bp else {})})
        for deriv in o.get('derivedProperties', []):
            fid = 'field:knowledge.' + ident + '.' + deriv['name']
            fields.append({'id':fid,'kind':'field','conceptRef':deriv['type'],
                           'propertyName':deriv['name'], 'semanticDerivation':deepcopy(deriv['rule']),
                           'evidence':deriv['evidence']})
        models.append({"id": mid, "kind": "model", "conceptRef": o["concept"],
                       "observationRef": ident, "cardinality": o["cardinality"],
                       "identityScope": o.get("identity", {}).get("scope"), "fields": fields})
        node_bindings.append({"id": "binding:knowledge." + ident, "semanticId": mid,
                              "sourcePath": b["source"], "context": b.get("context", {}),
                              **({'observationEnvelope': b['envelope']} if 'envelope' in b else {})})
    candidate = {"schema": "mcel.application-ir.v1", "application": {
        "id": "app:" + app_id, "kind": "application", "appId": app_id,
        "semanticVersion": "1", "title": "Computer semantic observations"},
        "models": models, "provenance": {"frontend": {"id": "mcel.knowledge.v1", "version": "1"},
                                        "nodeBindings": node_bindings}}
    validation = validate_application_ir(candidate)
    if not validation.valid or not validation.normalized:
        raise SemanticCatalogError("Existing Application IR rejected knowledge: " +
                                   "; ".join(d.code for d in validation.diagnostics))
    normalized = validation.normalized
    return {"schema": BRIDGE_SCHEMA, "applicationIr": normalized,
            "observations": selection, "bindings": overlays,
            "catalogFingerprint": _digest({"concepts": base["concepts"],
                                             "observations": {k: base["observations"][k] for k in selection},
                                             "operations": {k: o for k, o in base["operations"].items() if o["target"] in selection}}),
            "sourceBindingFingerprint": normalized["fingerprints"]["sourceBinding"],
            "semanticFingerprint": normalized["fingerprints"]["semantic"]}


def resolve_knowledge(snapshot: Mapping[str, Any], bridge: Mapping[str, Any], *,
                      catalog: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Fail closed on tampered IR or bindings, then interpret source data once."""
    if bridge.get("schema") != BRIDGE_SCHEMA:
        raise SemanticCatalogError("Unknown bridge schema")
    base = catalog if catalog is not None else load_catalog()
    compiled = compile_knowledge_ir(bridge["applicationIr"]["application"]["appId"],
                                    observation_ids=bridge["observations"],
                                    bindings=bridge["bindings"], catalog=base)
    for key in ("catalogFingerprint", "sourceBindingFingerprint", "semanticFingerprint"):
        if bridge.get(key) != compiled[key]:
            raise SemanticCatalogError(f"Stale/tampered knowledge bridge: {key}")
    ir_check = validate_application_ir(bridge["applicationIr"])
    if not ir_check.valid or ir_check.normalized != compiled["applicationIr"]:
        raise SemanticCatalogError("Knowledge IR changed without recompilation")
    bound = _binding_catalog(base, tuple(bridge["observations"]), bridge["bindings"])
    # Restrict sources and operations to the selected application-facing projections,
    # without changing what concept knowledge is authoritative.
    bound["observations"] = {k: bound["observations"][k] for k in bridge["observations"]}
    bound["operations"] = {k: v for k, v in bound["operations"].items() if v["target"] in bound["observations"]}
    for obs in bound["observations"].values():
        obs["relationships"] = [rel for rel in obs.get("relationships", []) if rel["target"] in bound["observations"]]
    graph = interpret(snapshot, bound)
    return {"schema": VIEW_SCHEMA, "applicationIrFingerprint": compiled["semanticFingerprint"],
            "sourceBindingFingerprint": compiled["sourceBindingFingerprint"],
            "catalogFingerprint": compiled["catalogFingerprint"],
            "sources": graph["sources"], "operations": graph["operations"],
            "diagnostics": graph["diagnostics"], "authority": "read-only; no runtime capabilities"}


def collection_projection(view: Mapping[str, Any], observation_id: str, *,
                          catalog: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Produce a validated v2-consumer *input*, with raw value states intact.

    Ambiguous or incomplete identities are refused; weak process/socket links
    remain candidate-only in view evidence, never translated into authority.
    """
    cat = catalog if catalog is not None else load_catalog()
    if view.get("schema") != VIEW_SCHEMA or observation_id not in view["sources"]:
        raise SemanticCatalogError("Unknown canonical observation projection")
    source = view["sources"][observation_id]
    if source["state"] != "present":
        raise SemanticCatalogError("No live records available for collection projection")
    ob = cat["observations"][observation_id]
    fields = [{"key": "mcelIdentity", "from": "mcelIdentity", "kind": "identifier", "tier": "primary", "label": "Identity"}]
    source_keys = ["mcelIdentity", "mcelCanonicalRecord"]
    rows = []
    quarantined = [{'index':r['index'], 'reason':r['reason'], 'sourceRaw':deepcopy(r['raw'])}
                    for r in source.get('rejectedRecords', [])]
    property_defs = []
    deferred = []
    # Derived semantic properties already carry provenance in canonical records.
    # They are first-class typed values; do not make each application restate
    # the derivation in a browser-only semantic collection contract.
    for p in [*ob["properties"], *ob.get("derivedProperties", [])]:
        c = cat["concepts"][p["type"]]
        name = p["name"]
        if not FIELD.fullmatch(name) or name == "mcelIdentity" or name.endswith("Availability"):
            raise SemanticCatalogError(f"Property incompatible with generic v2 consumer: {name}")
        if c["kind"] == "measurement":
            kind = "quantity"
        elif c["id"] == "mc.boolean" or c.get("representation") == "boolean":
            kind = "boolean"
        elif c["kind"] == "state":
            kind = "enum"
        elif c["id"] == "mc.timestamp":
            if any(type(r["properties"][name]["raw"]) in (int, float) and
                   r["properties"][name]["state"] == "present" for r in source["records"]):
                deferred.append({"property": name, "reason": "numeric-timestamp-not-supported-by-v2"})
                continue
            kind = "timestamp"
        elif c["kind"] == "structured-value" and c.get("representation") == "string":
            kind = "long-text"
        elif c["kind"] == "structured-value" or c.get("representation") in {"object", "string-list"}:
            # The existing collection renderer cannot faithfully render objects.
            # Defer to the exact canonical-record inspector rather than
            # quietly JSON-stringifying a structured semantic value.
            deferred.append({"property": name, "reason": "structured-value-requires-inspection"})
            continue
        elif "identifier" in c["id"] or c["id"] in {"mc.process.pid"}:
            kind = "identifier"
        else:
            kind = "text"
        if len(fields) >= 16:
            deferred.append({"property": name, "reason": "v2-field-limit"})
            continue
        availability = name + "Availability"
        field = {"key": name, "from": name, "kind": kind,
                 "tier": "primary" if p.get("required") and kind == "identifier" else "secondary",
                 "label": name.replace("_", " ").capitalize(),
                 "validity": {"availabilityFrom": availability}, "exact": True}
        if kind == "quantity" and c.get("unit") == "bytes":
            field["presentation"] = {"units": "bytes"}
        fields.append(field)
        source_keys.extend([name, availability])
        property_defs.append((name, kind, availability))
    for record_index, record in enumerate(source["records"]):
        ident = record["identity"]
        if ident["status"] != "complete":
            if ident['status'] == 'ambiguous':
                # Duplicate identities invalidate the collection reconciliation
                # namespace; preserve the Patch 12 fail-closed guarantee.
                raise SemanticCatalogError('Identity unsafe for stable live collection: ambiguous')
            quarantined.append({'index':record_index, 'reason':'identity-'+ident['status'],
                                'canonicalRecord':deepcopy(record)})
            continue
        identity = json.dumps([ident["scope"], ident["parts"]], separators=(",", ":"), ensure_ascii=False)
        row = {"mcelIdentity": identity, "mcelCanonicalRecord": deepcopy(record)}
        for name, kind, availability in property_defs:
            val = record["properties"][name]
            state = val["state"]
            row[availability] = state if state in {"present", "missing", "null", "empty", "unavailable", "failed"} else "failed"
            raw = val["raw"]
            if state in {"present", "null", "empty"}:
                if kind == "quantity" and (type(raw) not in (int, float)):
                    row[availability] = "failed"
                elif kind in {"text", "enum", "timestamp", "long-text"} and state == "present" and not isinstance(raw, str):
                    row[availability] = "failed"
                else:
                    row[name] = deepcopy(raw)
            # On failure preserve *source* value in canonical view, never silently
            # fabricate a string/zero in consumer row.
        rows.append(row)
    if not rows and quarantined:
        raise SemanticCatalogError('Identity unsafe for stable live collection: all rows quarantined')
    if len(rows) > 500:
        raise SemanticCatalogError("v2 consumer row limit exceeded")
    # Syntactically complete identity is NOT automatically proof of safe
    # continuity between snapshots: files may be replaced at the same pathname,
    # sockets reused, etc. Only an explicit catalog guarantee may enable a
    # generation-preserving renderer. Default: rebuild rows on updates.
    identity_rule = ob.get("identity", {})
    if identity_rule.get("continuity") not in (None, "verified-generation"):
        raise SemanticCatalogError("Unknown identity continuity guarantee")
    spec = {"schema": COLLECTION_SCHEMA, "identity": "mcelIdentity",
            "sourceFields": source_keys, "fields": fields}
    if identity_rule.get("continuity") == "verified-generation":
        spec["reconciliation"] = {"generationFrom": "mcelIdentity"}
    return {"schema": "mcel.knowledge-collection-adapter.v1", "spec": spec,
            "rows": rows, "observation": observation_id,
            "canonicalFingerprint": view["applicationIrFingerprint"],
            "sourceBindingFingerprint": view["sourceBindingFingerprint"],
            "deferredFields": deferred, "quarantinedRows":quarantined,
            "observationEnvelope":deepcopy(source.get('envelope')), "noOperations": True}
