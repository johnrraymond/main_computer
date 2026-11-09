"""P1 opt-in, pure semantic-IR -> generated UI composition.

This is NOT Application IR or authority. It derives a read-only presentation
structure from an already validated executable IR; it neither defines effects
nor consults an app name, display label, DOM, source markup, or snapshot data.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json

from .interface_intent import PURPOSES

SCHEMA = "mcel.generated-composition.v1"
POLICY = "mcel.semantic-structure.p1"
P3_POLICY = "mcel.semantic-structure.p3"
RECIPES = frozenset({"root", "queries", "collection", "selection-echo", "evidence", "results", "controls", "operations", "document"})
TAGS = {"root": "main", "queries": "section", "collection": "section", "selection-echo": "aside",
        "evidence": "footer", "results": "section", "controls": "section", "operations": "section", "document": "section"}
ROLE_META = {
    "queries": ("query-controls", "secondary"),
    "collection": ("workspace", "primary"),
    "selection-echo": ("selection", "supporting"),
    "evidence": ("status", "supporting"),
    "results": ("results", "secondary"),
    "controls": ("query-controls", "secondary"),
    "operations": ("commands", "secondary"),
    "document": ("workspace", "primary"),
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha(value):
    return "sha256:" + hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _ref(value):
    return value.get("ref") if isinstance(value, Mapping) else None


def synthesize_composition(ir: Mapping, *, policy: str = POLICY) -> dict:
    """Make organizational choices from purpose, node kinds and verified dataflow.

    Layout/visibility/scheduling is NOT inferred from labels, process names,
    descriptions or app IDs. A selected key is only an echo, not an inspector.
    """
    if policy not in {POLICY, P3_POLICY}:
        raise ValueError("Unknown synthesis policy")
    if ir.get("schema") != "mcel.application-ir.v1" or len(ir.get("surfaces", [])) != 1:
        raise ValueError("P1 requires one validated canonical Application IR surface")
    surface = ir["surfaces"][0]
    if ir.get("layouts") or surface.get("htmlBindings") or surface.get("interfaceIntent", {}).get("hierarchy"):
        raise ValueError("P1 refuses authored presentation, HTML bindings, and layout trees")
    purpose = surface.get("purpose")
    if not isinstance(purpose, str) or purpose not in PURPOSES:
        raise ValueError("P1 requires a validated authored application purpose")
    intents = {i["id"]: i for i in ir.get("intents", [])}
    states = {s["id"]: s for s in ir.get("states", [])}
    if policy == POLICY and (any(c.get("risk") != "external-read" or
           any(op.get("risk") != "external-read" for op in c.get("operations", []))
           for c in ir.get("capabilities", [])) or any(e.get("risk") != "external-read"
           for e in ir.get("effects", []))):
        raise ValueError("P1 cannot synthesize a consequential or source-write capability")
    nodes = surface.get("nodes", [])
    # Operational applicability is a typed relationship to observed state,
    # not an authored layout, a permission or an inferred backend effect.
    mode_transitions = {t.get("viewTransition", {}).get("target", {}).get("ref")
                        for t in intents.values() if isinstance(t.get("viewTransition"), Mapping)}
    def check_condition(when, depth=0):
        if not isinstance(when, Mapping) or depth > 4:
            raise ValueError("Invalid semantic visibility condition")
        if set(when) in ({"all"}, {"any"}):
            clauses = next(iter(when.values()))
            if not isinstance(clauses, list) or not 1 <= len(clauses) <= 12:
                raise ValueError("Invalid compound visibility condition")
            for clause in clauses:
                check_condition(clause, depth+1)
            return
        if not {"state", "equals"} <= set(when) or set(when)-{"state","path","equals","maxAgeSeconds"}:
            raise ValueError("Unknown visibility condition fields")
        state_id = _ref(when["state"])
        target = states.get(state_id, {})
        schema = target.get("schema", {})
        authority = target.get("authority")
        if authority not in {"derived", "renderer-local"}:
            raise ValueError("Visibility requires a declared derived or local state")
        if "maxAgeSeconds" in when:
            age=when["maxAgeSeconds"]
            if (target.get("authority") != "derived" or schema.get("kind") != "record" or
                schema.get("fields", {}).get("observedAt", {}).get("kind") != "string" or
                type(age) is not int or not 1 <= age <= 600):
                raise ValueError("Observation freshness must reference a derived timestamped record")
        if "path" in when:
            if schema.get("kind") != "record" or when["path"] not in schema.get("fields", {}):
                raise ValueError("Visibility requires a declared record field")
            schema = schema["fields"][when["path"]]
        value = when["equals"]
        valid = (schema.get("kind") == "string" and isinstance(value, str) and
                 ("enum" not in schema or value in schema["enum"])) or (
                 schema.get("kind") == "boolean" and type(value) is bool) or (
                 schema.get("kind") == "integer" and type(value) is int)
        if not valid:
            raise ValueError("Visibility condition type mismatch")
        if authority == "renderer-local" and (state_id not in mode_transitions or
                "path" in when or schema.get("kind") != "string" or
                value not in schema.get("enum", [])):
            raise ValueError("Local visibility requires a verified view transition")
    for node in nodes:
        if "visibleWhen" in node:
            check_condition(node["visibleWhen"])
    if not nodes or len({n["id"] for n in nodes}) != len(nodes):
        raise ValueError("P1 requires uniquely identified executable nodes")
    if policy == POLICY and any(i.get("risk") not in {"local-state", "external-read"} or
           (i.get("operationKind") == "capability" and i.get("risk") != "external-read")
           for i in intents.values()):
        raise ValueError("P1 read-only slice refuses any consequential or source-write effect")
    groups = {k: [] for k in ROLE_META}
    source_reasons = {}
    collection_targets = {}
    for n in nodes:
        if n["nodeKind"] == "collection" and n.get("selectionIntent"):
            selection = intents.get(_ref(n["selectionIntent"]))
            contract = selection.get("collectionSelection") if selection else None
            if (contract and _ref(contract.get("source")) == _ref(n.get("state")) and
                _ref(contract.get("target")) in states):
                collection_targets[_ref(contract["target"])] = n["id"]
    for n in sorted(nodes, key=lambda x: x["id"]):
        kind = n.get("nodeKind")
        if kind == "collection": recipe = "collection"
        elif kind == "form": recipe = "queries"
        elif kind == "control": recipe = "controls"
        elif kind == "operation-evidence": recipe = "evidence"
        elif kind == "document-editor": recipe = "document"
        elif kind == "state-value":
            state_ref = _ref((n.get("value") or {}).get("state"))
            recipe = "selection-echo" if state_ref in collection_targets else "results"
        else:
            raise ValueError("P1 unsupported semantic node kind: " + str(kind))
        if n.get("intent") and _ref(n["intent"]) not in intents:
            raise ValueError("P1 control references unavailable intent")
        # Consequential operations are identified only through the validated
        # referenced intent risk; labels or application names never create them.
        if policy == P3_POLICY and kind in {"form", "control"} and (
                intents.get(_ref(n.get("intent")), {}).get("risk") == "consequential"):
            recipe = "operations"
        groups[recipe].append(n["id"])
        source_reasons[n["id"]] = "node-kind:" + str(kind)
    if not groups["collection"]:
        raise ValueError("P1 vertical slice requires an authentic keyed collection")
    # Application purpose controls organization, but cannot create reads, timers, or actions.
    order = ("controls", "queries", "collection", "selection-echo", "operations", "results", "evidence") if purpose == "monitor" else (
        "controls", "queries", "selection-echo", "collection", "operations", "results", "evidence")
    root = "interface-region:generated-root"
    regions = [{"id": root, "parentId": None, "ordinal": 0, "recipe": "root", "nativeTag": "main",
                "members": [], "priority": "primary", "purpose": "application", "tasks": [],
                "reasons": ["whole-application-synthesis", "purpose:" + purpose]}]
    for recipe in order:
        if not groups[recipe]: continue
        meaning, priority = ROLE_META[recipe]
        regions.append({"id": "interface-region:generated-" + recipe, "parentId": root,
             "ordinal": len(regions) - 1, "recipe": recipe, "nativeTag": TAGS[recipe],
             "purpose": meaning, "priority": priority,
             # Operational refresh policy is still owned by the runtime;
             # this derived monitor task only identifies a semantic observation.
             "tasks": ["monitor"] if policy == P3_POLICY and purpose == "monitor" and
                        recipe in {"collection", "results"} else [],
             "members": [{"ref": ident} for ident in groups[recipe]],
             "reasons": ["purpose:" + purpose, "verified-node-kinds", *sorted(set(source_reasons[i] for i in groups[recipe]))]})
    # No generated 'inspection' role: a selected key is not a verified selected record.
    result = {"schema": SCHEMA, "policy": policy,
        "sourceIrHash": ir.get("fingerprints", {}).get("semantic"),
        "purpose": purpose, "rootId": root, "regions": regions,
        "relations": [], "unresolved": [],
        "variants": ["compact", "standard", "wide"]}
    if not isinstance(result["sourceIrHash"], str) or not result["sourceIrHash"].startswith("sha256:"):
        raise ValueError("P1 requires normalized source fingerprint")
    result["fingerprint"] = sha(result)
    return result


def verify_composition(ir: Mapping, plan: Mapping) -> None:
    """Reject tampered or stale composition; recompute the pure decision."""
    expected = synthesize_composition(ir, policy=plan.get("policy", ""))
    if plan != expected:
        raise ValueError("P1 composition differs from source-derived verified policy")
