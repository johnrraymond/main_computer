"""Read-only, flat-file Main Computer semantic knowledge catalog.

No GUI dependency and no operational execution path. A catalog is a language for
information and declared effects, not a source of capabilities/authorization.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping

from .foundations import TIME_FORMATS, normalize_declared_timestamp, envelope_for_source, validate_envelope_spec

SCHEMA = "mcel.computer-knowledge.v1"
GRAPH_SCHEMA = "mcel.computer-knowledge-observation.v1"
CATALOG = Path(__file__).parent / "catalog"
ABSENT = object()


class SemanticCatalogError(ValueError):
    pass


def _path_get(value: Any, dotted: str) -> Any:
    if not isinstance(dotted, str) or not dotted or any(not part or part.startswith("_") for part in dotted.split(".")):
        raise SemanticCatalogError(f"Invalid source path: {dotted!r}")
    for part in dotted.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return ABSENT
        value = value[part]
    return value


def _distinct(ids: list[str], what: str) -> None:
    if len(ids) != len(set(ids)):
        raise SemanticCatalogError(f"Duplicate {what}: {ids}")


def load_catalog(directory: Path | str = CATALOG) -> dict[str, Any]:
    """Load/validate additive catalog fragments; no code evaluation or plugins."""
    directory = Path(directory)
    fragments = sorted(directory.glob("*.json"))
    if not fragments:
        raise SemanticCatalogError(f"No semantic catalog fragments in {directory}")
    concepts: dict[str, dict] = {}
    observations: dict[str, dict] = {}
    operations: dict[str, dict] = {}
    source_names: set[str] = set()
    for path in fragments:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
            raise SemanticCatalogError(f"Unsupported catalog schema: {path.name}")
        ns = doc.get("namespace")
        if not isinstance(ns, str) or not ns or ns in source_names:
            raise SemanticCatalogError(f"Duplicate/invalid namespace: {ns}")
        source_names.add(ns)
        for label, store in (("concepts", concepts), ("observations", observations), ("operations", operations)):
            entries = doc.get(label, [])
            if not isinstance(entries, list):
                raise SemanticCatalogError(f"{path.name}: {label} must be a list")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise SemanticCatalogError(f"{path.name}: invalid {label} entry")
                ident = entry.get("id")
                if not isinstance(ident, str) or not ident or ident in store:
                    raise SemanticCatalogError(f"Duplicate/invalid {label} id: {ident}")
                store[ident] = deepcopy(entry)
    for concept in concepts.values():
        if concept.get("base") and concept["base"] not in concepts:
            raise SemanticCatalogError(f"Unknown base: {concept['base']}")
        if concept.get("kind") not in {"value", "measurement", "structured-value", "entity", "state", "operation"}:
            raise SemanticCatalogError(f"Bad concept kind: {concept['id']}")
    for obs in observations.values():
        if obs.get("concept") not in concepts:
            raise SemanticCatalogError(f"Unknown observation concept: {obs['id']}")
        if obs.get("cardinality") not in {"one", "many"}:
            raise SemanticCatalogError(f"Bad cardinality: {obs['id']}")
        _path_get({}, obs.get("source"))
        alternates = obs.get("sourceAlternates", [])
        if not isinstance(alternates, list):
            raise SemanticCatalogError(f"Invalid source alternates: {obs['id']}")
        for alternative in alternates:
            _path_get({}, alternative)
        if obs.get("collectionFormat", "list") not in {"list", "map-values"}:
            raise SemanticCatalogError(f"Invalid collection format: {obs['id']}")
        scope_bindings = obs.get("context", {})
        if not isinstance(scope_bindings, dict):
            raise SemanticCatalogError(f"Invalid scope context: {obs['id']}")
        for context_path in scope_bindings.values():
            _path_get({}, context_path)
        props = obs.get("properties", [])
        if not isinstance(props, list) or not props:
            raise SemanticCatalogError(f"Observation without properties: {obs['id']}")
        _distinct([p.get("name") for p in props], f"property for {obs['id']}")
        for prop in props:
            if prop.get("type") not in concepts:
                raise SemanticCatalogError(f"Unknown property type {prop.get('type')} in {obs['id']}")
            _path_get({}, prop.get("source"))
            if prop.get("fromContext", "record") not in {"record", "snapshot", "parent"}:
                raise SemanticCatalogError(f"Invalid property origin: {obs['id']}")
            condition = prop.get("availableWhen")
            if condition is not None:
                if not isinstance(condition, dict) or ("equals" in condition) == ("notEquals" in condition):
                    raise SemanticCatalogError(f"Invalid availability predicate in {obs['id']}")
                _path_get({}, condition.get("source"))
            for ref in ("availabilityFrom", "availableIf"):
                if ref in prop:
                    _path_get({}, prop[ref])
        # Application derivations are semantic facts with explicit, bounded
        # evidence, not a display overlay and not operating-system authority.
        derivations = obs.get("derivedProperties", [])
        if not isinstance(derivations, list):
            raise SemanticCatalogError(f"Invalid semantic derivations: {obs['id']}")
        _distinct([d.get('name') for d in derivations], f"derived property for {obs['id']}")
        native_names = {p['name'] for p in props}
        if native_names.intersection({d.get('name') for d in derivations}):
            raise SemanticCatalogError('Derived properties cannot replace source properties')
        for deriv in derivations:
            if deriv.get('type') not in concepts or not isinstance(deriv.get('evidence'), str) or not deriv['evidence']:
                raise SemanticCatalogError('Derived semantic type and evidence required')
            rule = deriv.get('rule')
            if not isinstance(rule, dict) or set(rule) - {'op','cases','default'} or rule.get('op') != 'first-true':
                raise SemanticCatalogError('Only bounded first-true semantic derivations supported')
            cases = rule.get('cases')
            if not isinstance(cases,list) or not cases:
                raise SemanticCatalogError('Derivation requires cases')
            for case in cases:
                if not isinstance(case,dict) or set(case) != {'property','equals','value'} or case['property'] not in native_names:
                    raise SemanticCatalogError('Derivation refers to unknown source property')
                if not _valid(case['value'], concepts[deriv['type']]):
                    raise SemanticCatalogError('Invalid derived value')
            if 'default' in rule and not _valid(rule['default'], concepts[deriv['type']]):
                raise SemanticCatalogError('Invalid derivation default')
        for prop in props:
            if 'sourceFormat' in prop and (prop['type'] != 'mc.timestamp' or
                (isinstance(prop['sourceFormat'],str) and prop['sourceFormat'] not in TIME_FORMATS) or
                (not isinstance(prop['sourceFormat'],str) and (not isinstance(prop['sourceFormat'],list) or
                 not prop['sourceFormat'] or any(x not in TIME_FORMATS for x in prop['sourceFormat'])))):
                raise SemanticCatalogError('Source timestamp format only valid for timestamp concepts')
        if 'envelope' in obs:
            try:
                validate_envelope_spec(obs['envelope'], checked_path=lambda p: _path_get({},p))
            except (ValueError, TypeError, KeyError) as exc:
                raise SemanticCatalogError(f"Invalid observation envelope: {obs['id']}") from exc
        identity = obs.get("identity", {})
        if identity:
            _distinct(identity.get("fields", []), "identity field")
            if not identity.get("scope") or not identity.get("fields"):
                raise SemanticCatalogError(f"Identity scope/fields missing: {obs['id']}")
            if not isinstance(identity.get("contextFields", []), list) or not set(identity.get("contextFields", [])).issubset(scope_bindings):
                raise SemanticCatalogError(f"Unknown identity context: {obs['id']}")
            if not set(identity["fields"]).issubset({p["source"] for p in props}):
                raise SemanticCatalogError(f"Identity parts aren't source properties: {obs['id']}")
        for rel in obs.get("relationships", []):
            if rel.get("target") not in observations:
                raise SemanticCatalogError(f"Unknown relation target: {obs['id']}")
            if rel.get("certainty") != "candidate-only":
                raise SemanticCatalogError("First cut supports candidate-only observed links; never assert authority")
            matches = rel.get("matches", [{"property": rel.get("property"), "targetProperty": rel.get("targetProperty")}])
            if not isinstance(matches, list) or not matches:
                raise SemanticCatalogError("Missing relation match keys")
            for match in matches:
                if not isinstance(match, dict) or match.get("property") not in {p["name"] for p in props} or match.get("targetProperty") not in {p["name"] for p in observations[rel["target"]]["properties"]}:
                    raise SemanticCatalogError("Unknown relation property")
    for op in operations.values():
        if op.get("concept") != "mc.operation" or op.get("target") not in observations:
            raise SemanticCatalogError(f"Unknown operation concept/target: {op['id']}")
        if op.get("implementedHere") is not False or op.get("authority") != "external-runtime-required":
            raise SemanticCatalogError(f"Operations must remain declared-only: {op['id']}")
        if op.get("effect") not in {"observation", "consequential"}:
            raise SemanticCatalogError(f"Unknown operation effect: {op['id']}")
        if op["effect"] == "consequential" and op.get("confirmationRequired") is not True:
            raise SemanticCatalogError(f"Consequential operation lacks confirmation declaration: {op['id']}")
    return {"schema": SCHEMA, "concepts": concepts, "observations": observations,
            "operations": operations, "fragments": [f.name for f in fragments]}


def _valid(raw: Any, concept: Mapping[str, Any]) -> bool:
    kind = concept["kind"]
    representation = concept.get("representation")
    if representation:
        permitted = {"string", "boolean", "integer", "object", "string-list", "decimal-text"}
        if representation not in permitted:
            return False
        correct = {
            "string": lambda x: isinstance(x, str),
            "boolean": lambda x: type(x) is bool,
            "integer": lambda x: type(x) is int,
            "object": lambda x: isinstance(x, Mapping),
            "string-list": lambda x: isinstance(x, list) and all(isinstance(v, str) for v in x),
            "decimal-text": lambda x: isinstance(x, str) and bool(x.strip()) and all(c in "0123456789." for c in x.strip()),
        }[representation](raw)
        return correct and ("values" not in concept or raw in concept["values"])
    if concept["id"] == "mc.boolean":
        return type(raw) is bool
    if concept["id"] == "mc.quantity.count":
        return type(raw) is int and raw >= 0
    if kind == "measurement":
        if type(raw) not in (int, float) or not math.isfinite(raw):
            return False
        bounds = concept.get("bounds", {})
        return ("min" not in bounds or raw >= bounds["min"]) and ("max" not in bounds or raw <= bounds["max"])
    if concept["id"] in ("mc.process.pid",):
        return type(raw) is int and raw > 0
    if kind in ("value", "state", "structured-value") and concept["id"] != "mc.identifier":
        if concept["id"] == "mc.timestamp":
            return isinstance(raw, (str, int, float)) and not isinstance(raw, bool)
        if concept["id"] in ("mc.text", "mc.process.command_line", "mc.process.state",
                             "mc.network.endpoint", "mc.network.connection.state", "mc.file.path"):
            return isinstance(raw, str)
    return isinstance(raw, (str, int)) and not isinstance(raw, bool)


def _property_value(record: Mapping[str, Any], prop: dict, concepts: dict, *, snapshot: Mapping[str, Any] | None = None, parent: Mapping[str, Any] | None = None) -> dict[str, Any]:
    origin = {"record": record, "snapshot": snapshot or {}, "parent": parent or {}}[prop.get("fromContext", "record")]
    raw = _path_get(origin, prop["source"])
    claimed = _path_get(record, prop["availabilityFrom"]) if prop.get("availabilityFrom") else ABSENT
    available_if = _path_get(record, prop["availableIf"]) if prop.get("availableIf") else ABSENT
    condition = prop.get("availableWhen")
    condition_value = _path_get(record, condition["source"]) if condition else ABSENT
    predicate_failed = condition is not None and (condition_value is ABSENT or ("equals" in condition and condition_value != condition["equals"]) or ("notEquals" in condition and condition_value == condition["notEquals"]))
    if claimed is not ABSENT and claimed not in ("present", "missing", "unavailable", "failed", "null", "empty"):
        state = "invalid-availability"
    elif claimed is not ABSENT and claimed != "present":
        state = claimed
    elif available_if is False or predicate_failed:
        state = "unavailable"
    elif raw is ABSENT:
        state = "contradiction" if claimed == "present" else "missing"
    elif raw is None:
        state = "contradiction" if claimed == "present" else "null"
    elif raw == "":
        state = "empty"
    elif not _valid(raw, concepts[prop["type"]]):
        state = "invalid-value"
    else:
        state = "present"
    concept = concepts[prop["type"]]
    normalized = None
    matched_format = None
    if state == 'present' and prop.get('sourceFormat'):
        try:
            normalized, matched_format = normalize_declared_timestamp(raw, prop['sourceFormat'])
        except ValueError:
            state = 'invalid-value'
    return {"type": prop["type"], "state": state,
            "raw": None if raw is ABSENT else deepcopy(raw),
            "source": prop["source"], "fromContext": prop.get("fromContext", "record"), "availabilityEvidence": None if claimed is ABSENT else claimed,
            "unit": concept.get("unit"), "meaning": concept.get("meaning"), "evidence": prop.get("evidence"),
            "sourceFormat": prop.get("sourceFormat"), "matchedFormat":matched_format, "normalized": normalized}


def _derive_property(properties: Mapping[str, Any], deriv: Mapping[str, Any], concepts: Mapping[str, Any]) -> dict[str, Any]:
    rule = deriv['rule']
    raw = ABSENT
    cause = None
    for case in rule['cases']:
        src = properties[case['property']]
        if src['state'] == 'present' and src['raw'] == case['equals']:
            raw = case['value']
            cause = {'property':case['property'], 'matchedValue':deepcopy(src['raw'])}
            break
    if raw is ABSENT and 'default' in rule and all(
            properties[case['property']]['state'] == 'present' for case in rule['cases']):
        raw = rule['default']; cause={'rule':'default', 'evidence':'all-tested-fields-present'}
    state = 'missing' if raw is ABSENT else 'present' if _valid(raw,concepts[deriv['type']]) else 'invalid-value'
    return {'type':deriv['type'],'state':state,'raw':None if raw is ABSENT else deepcopy(raw),
            'source':'semantic-derivation','fromContext':'derived', 'availabilityEvidence':None,
            'unit':concepts[deriv['type']].get('unit'), 'meaning':concepts[deriv['type']].get('meaning'),
            'evidence':deriv['evidence'], 'derivationEvidence':cause}


def interpret(snapshot: Mapping[str, Any], catalog: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Interpret saved, source-shaped observation envelopes without changing/executing them."""
    cat = catalog if catalog is not None else load_catalog()
    result: dict[str, Any] = {"schema": GRAPH_SCHEMA, "sources": {}, "operations": {}, "diagnostics": []}
    for ident, obs in cat["observations"].items():
        envelope = envelope_for_source(snapshot, obs.get('envelope'), lookup=_path_get, absent=ABSENT)
        source_path = obs["source"]
        source = _path_get(snapshot, source_path)
        if source is ABSENT:
            for path in obs.get("sourceAlternates", []):
                candidate = _path_get(snapshot, path)
                if candidate is not ABSENT:
                    source_path, source = path, candidate
                    break
        if source is ABSENT:
            result["sources"][ident] = {"concept": obs["concept"], "state": "missing-source", "records": [], "envelope": envelope}
            continue
        if obs["cardinality"] == "many":
            if isinstance(source, list):
                records = source
            elif obs.get("collectionFormat") == "map-values" and isinstance(source, Mapping):
                records = list(source.values())
            else:
                result["sources"][ident] = {"concept": obs["concept"], "state": "invalid-source", "records": [], "envelope": envelope}
                continue
        elif isinstance(source, Mapping):
            records = [source]
        elif source is None:
            result["sources"][ident] = {"concept": obs["concept"], "state": "null-source", "records": [], "envelope": envelope}
            continue
        else:
            result["sources"][ident] = {"concept": obs["concept"], "state": "invalid-source", "records": [], "envelope": envelope}
            continue
        context = {name: _path_get(snapshot, path) for name, path in obs.get("context", {}).items()}
        # In an observation envelope, parent properties can be referenced explicitly,
        # without changing the record payload or inventing defaults.
        parent = _path_get(snapshot, source_path.rsplit(".", 1)[0]) if "." in source_path else snapshot
        if not isinstance(parent, Mapping):
            parent = {}
        rendered = []
        rejected = []
        for idx, rec in enumerate(records):
            if not isinstance(rec, Mapping):
                result["diagnostics"].append({"code": "invalid-record", "source": ident, "index": idx})
                rejected.append({'index':idx, 'reason':'invalid-record', 'raw':deepcopy(rec)})
                continue
            fields = {p["name"]: _property_value(rec, p, cat["concepts"], snapshot=snapshot, parent=parent) for p in obs["properties"]}
            for deriv in obs.get('derivedProperties', []):
                fields[deriv['name']] = _derive_property(fields, deriv, cat['concepts'])
            identity = obs.get("identity")
            if identity:
                # Identity uses normalized event-time *only if* an explicit
                # format exists in the binding; original source raw is retained.
                by_source = {p['source']:p['name'] for p in obs['properties']}
                parts = [context.get(f, ABSENT) for f in identity.get('contextFields', [])]
                for identity_source in identity['fields']:
                    key = by_source.get(identity_source)
                    field = fields.get(key) if key else None
                    if field is not None and (field['state'] != 'present' or field['sourceFormat'] and field['normalized'] is None):
                        parts.append(ABSENT)
                    elif field is not None and field.get('normalized') is not None:
                        parts.append(field['normalized'])
                    else:
                        parts.append(_path_get(rec, identity_source))
                verified = all(v is not ABSENT and v is not None and v != '' for v in parts)
                identity_evidence = {"scope": identity["scope"], "status": "complete" if verified else "incomplete",
                                     "parts": None if not verified else deepcopy(parts),
                                     "stability": "observation-only" if "observation" in identity["scope"] else "scope-dependent"}
            else:
                identity_evidence = {"status": "not-declared", "scope": None, "parts": None, "stability": "unknown"}
            for field_name, value in fields.items():
                if value['state'] == 'invalid-value' and value.get('sourceFormat'):
                    result['diagnostics'].append({'code':'invalid-timestamp-format', 'source':ident,
                                                  'index':idx, 'property':field_name})
            rendered.append({"concept": obs["concept"], "identity": identity_evidence,
                             "context": {k: (None if v is ABSENT else deepcopy(v)) for k, v in context.items()},
                             "properties": fields, "relationships": []})
            for prop in obs["properties"]:
                if prop.get("required") and fields[prop["name"]]["state"] != "present":
                    result["diagnostics"].append({"code": "required-field-not-present", "source": ident, "index": idx,
                                                   "property": prop["name"], "state": fields[prop["name"]]["state"]})
        # An identity tuple can be syntactically complete but non-unique within
        # the observation. Never present a duplicated tuple as an unambiguous id.
        signatures: dict[str, list[int]] = {}
        for idx, item in enumerate(rendered):
            evidence = item["identity"]
            if evidence["status"] == "complete":
                marker = json.dumps(evidence["parts"], ensure_ascii=False, sort_keys=True)
                signatures.setdefault(marker, []).append(idx)
        for indices in signatures.values():
            if len(indices) > 1:
                for idx in indices:
                    rendered[idx]["identity"]["status"] = "ambiguous"
                    result["diagnostics"].append({"code": "duplicate-identity", "source": ident, "index": idx})
        result["sources"][ident] = {"concept": obs["concept"], "state": "present", "records": rendered,
                                      "sourcePath": source_path, "cardinality": obs["cardinality"], "envelope":envelope,
                                      "rejectedRecords":rejected}
    for ident, obs in cat["observations"].items():
        for rel in obs.get("relationships", []):
            target = result["sources"][rel["target"]]["records"]
            match_fields = rel.get("matches", [{"property": rel.get("property"), "targetProperty": rel.get("targetProperty")}])
            target_props = cat["observations"][rel["target"]]["properties"]
            for rec in result["sources"][ident]["records"]:
                hits = []
                for idx, candidate in enumerate(target):
                    equal = True
                    for match in match_fields:
                        left = rec["properties"].get(match["property"])
                        tprop = next((p for p in target_props if p["name"] == match["targetProperty"]), None)
                        right = candidate["properties"].get(tprop["name"]) if tprop else None
                        if not left or not right or left["state"] != "present" or right["state"] != "present" or left["raw"] != right["raw"]:
                            equal = False
                            break
                    if equal:
                        hits.append(idx)
                rec["relationships"].append({"name": rel["name"], "target": rel["target"],
                                             "status": "candidate-only" if hits else "unresolved",
                                             "candidateIndices": hits, "reason": rel["reason"]})
    for ident, operation in cat["operations"].items():
        result["operations"][ident] = {"target": operation["target"], "sourceAction": operation["sourceAction"],
                                       "effect": operation["effect"], "confirmationRequired": operation["confirmationRequired"],
                                       "executable": False, "authorized": False, "authority": operation["authority"]}
    return result


def explain(concept_id: str, catalog: Mapping[str, Any] | None = None) -> dict[str, Any]:
    cat = catalog if catalog is not None else load_catalog()
    for key in ("concepts", "observations", "operations"):
        if concept_id in cat[key]:
            return deepcopy(cat[key][concept_id])
    raise KeyError(concept_id)
