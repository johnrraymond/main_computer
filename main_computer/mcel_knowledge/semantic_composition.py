"""P1 opt-in, pure semantic-IR -> generated UI composition.

This is NOT Application IR or authority. It derives a read-only presentation
structure from an already validated executable IR; it neither defines effects
nor consults an app name, display label, DOM, source markup, or snapshot data.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json

from .interface_intent import PURPOSES, interface_intent_errors

SCHEMA = "mcel.generated-composition.v1"
POLICY = "mcel.semantic-structure.p1"
P3_POLICY = "mcel.semantic-structure.p3"
RECIPES = frozenset({"root", "queries", "collection", "selection-echo", "evidence", "results", "controls", "operations", "document", "navigation", "task", "commands", "inspection"})
TAGS = {"root": "main", "queries": "section", "collection": "section", "selection-echo": "aside",
        "evidence": "footer", "results": "section", "controls": "section", "operations": "section", "document": "section", "navigation": "nav", "task": "section", "commands": "section", "inspection": "aside"}
ROLE_META = {
    "queries": ("query-controls", "secondary"),
    "collection": ("workspace", "primary"),
    "selection-echo": ("selection", "supporting"),
    "evidence": ("status", "supporting"),
    "results": ("results", "secondary"),
    "controls": ("query-controls", "secondary"),
    "operations": ("commands", "secondary"),
    "document": ("workspace", "primary"),
    "navigation": ("navigation", "secondary"),
    "task": ("task", "primary"),
    "commands": ("commands", "supporting"),
    "inspection": ("inspection", "supporting"),
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
    # A role-authored, non-HTML semantic region is the authority for which
    # nodes belong in navigation. Generic control/collection types are not.
    declared_intent = surface.get("interfaceIntent") or {}
    if declared_intent:
        problems = interface_intent_errors(surface)
        if problems:
            raise ValueError("Invalid authored semantic regions: " + "; ".join(problems))
    navigation_owners = {}
    task_owners = {}
    command_owners = {}
    inspector_owners = {}
    status_owners = {}
    by_node = {n["id"]: n for n in nodes}
    for authored in declared_intent.get("regions", []):
        role = authored.get("role")
        if role in {"navigation", "mode-selector"}:
            for member in authored["members"]:
                navigation_owners[member["ref"]] = authored
        elif role == "task":
            # A task is an actionable form with its selected identity/reference.
            # It must be semantically declared and structurally verified; it is
            # not inferred from names, labels, or display geometry.
            members = [by_node[m["ref"]] for m in authored["members"]]
            forms = [n for n in members if n["nodeKind"] == "form"]
            echoes = [n for n in members if n["nodeKind"] == "state-value"]
            if (len(forms) != 1 or len(echoes) != 1 or len(members) != 2 or
                policy != P3_POLICY or
                intents.get(_ref(forms[0].get("intent")), {}).get("risk") != "consequential"):
                raise ValueError("Task requires one consequential form and one selected reference")
            for member in members:
                task_owners[member["id"]] = authored
        elif role == "inspector":
            # An inspector is authored, and each member must present a real
            # derived observation. A renderer-local selected key is not a
            # verified service or selected-record inspector.
            members = [by_node[m["ref"]] for m in authored["members"]]
            if (policy != P3_POLICY or not members or any(
                    n["nodeKind"] != "state-value" or
                    states.get(_ref((n.get("value") or {}).get("state")), {}).get("authority") != "derived"
                    for n in members)):
                raise ValueError("Inspector requires derived observed values")
            for member in members:
                inspector_owners[member["id"]] = authored
        elif role == "status":
            members = [by_node[m["ref"]] for m in authored["members"]]
            if not members or any(n["nodeKind"] != "operation-evidence" for n in members):
                raise ValueError("Activity region requires operation evidence")
            for member in members:
                status_owners[member["id"]] = authored
        elif role == "commands":
            members = [by_node[m["ref"]] for m in authored["members"]]
            if not members or any(n["nodeKind"] != "control" or
                    intents.get(_ref(n.get("intent")), {}).get("risk") != "consequential"
                    for n in members):
                raise ValueError("Commands require explicit consequential controls")
            for member in members:
                command_owners[member["id"]] = authored
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
        if kind in {"collection", "record-notebook"}: recipe = "collection"
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
        if n["id"] in navigation_owners:
            recipe = "navigation"
            reason = "authored-navigation:" + navigation_owners[n["id"]]["id"]
        elif n["id"] in task_owners:
            recipe = "task"
            reason = "authored-task:" + task_owners[n["id"]]["id"]
        elif n["id"] in command_owners:
            recipe = "commands"
            reason = "authored-commands:" + command_owners[n["id"]]["id"]
        elif n["id"] in inspector_owners:
            recipe = "inspection"
            reason = "authored-inspector:" + inspector_owners[n["id"]]["id"]
        elif n["id"] in status_owners:
            recipe = "evidence"
            reason = "authored-status:" + status_owners[n["id"]]["id"]
        else:
            reason = "node-kind:" + str(kind)
        groups[recipe].append(n["id"])
        source_reasons[n["id"]] = reason
    # A task reference must resolve to an actual typed selection, not an
    # arbitrary string presented as an administrator or an entity identity.
    for authored in declared_intent.get("regions", []):
        if authored.get("role") != "task":
            continue
        echoed = next(by_node[m["ref"]] for m in authored["members"]
                      if by_node[m["ref"]]["nodeKind"] == "state-value")
        if _ref((echoed.get("value") or {}).get("state")) not in collection_targets:
            raise ValueError("Task reference requires verified collection selection")
    # Navigation is a semantic hierarchy, not alphabetical source-node order.
    # Target-dependent forms are contextual disclosures before the shared
    # directory, followed by view controls. No app-specific names are used.
    navigation_kinds = {node["id"]:node["nodeKind"] for node in nodes}
    order_in_rail = {"form":0,"collection":1,"control":2,"state-value":3}
    groups["navigation"].sort(key=lambda identifier:(
        order_in_rail.get(navigation_kinds.get(identifier),4),identifier))
    if not any(n.get("nodeKind") in {"collection", "record-notebook"} for n in nodes):
        raise ValueError("P1 vertical slice requires an authentic keyed collection")
    # Application purpose controls organization, but cannot create reads, timers, or actions.
    order = ("navigation", "controls", "queries", "collection", "selection-echo", "operations", "task", "commands", "inspection", "results", "evidence") if purpose == "monitor" else (
        "navigation", "controls", "queries", "selection-echo", "collection", "operations", "task", "commands", "inspection", "results", "evidence")
    # A compound record notebook is downstream of service configuration/commands.
    # Other collections retain their previous synthesized order.
    if groups["collection"] and all(by_node[id]["nodeKind"]=="record-notebook" for id in groups["collection"]):
        order=tuple(x for x in order if x!="collection")
        insert_after=order.index("commands") if "commands" in order else order.index("task")
        order=order[:insert_after+1]+("collection",)+order[insert_after+1:]
    root = "interface-region:generated-root"
    regions = [{"id": root, "parentId": None, "ordinal": 0, "recipe": "root", "nativeTag": "main",
                "members": [], "priority": "primary", "purpose": "application", "tasks": [],
                "reasons": ["whole-application-synthesis", "purpose:" + purpose]}]
    for recipe in order:
        if not groups[recipe]: continue
        if recipe == "navigation":
            authored_nav = [r for r in declared_intent.get("regions", [])
                            if r.get("role") in {"navigation", "mode-selector"}]
            if len(authored_nav) > 1:
                # A target-specific disclosure must never be swallowed by a
                # persistent identity collection heading. Each authored semantic
                # navigation region retains its own generated region, while
                # scroll ownership still belongs to the shared left rail.
                rank = {"form":0, "collection":1, "control":2, "state-value":3}
                kinds = {n["id"]:n["nodeKind"] for n in nodes}
                def region_priority(r):
                    return (min((rank.get(kinds.get(m["ref"]),4) for m in r["members"]),default=4),r["id"])
                for authored in sorted(authored_nav,key=region_priority):
                    member_ids = [ident for ident in groups[recipe]
                                  if navigation_owners[ident]["id"] == authored["id"]]
                    if not member_ids: continue
                    regions.append({"id":"interface-region:generated-navigation-"+
                                    authored["id"].rsplit(".",1)[-1],
                       "parentId":root,"ordinal":len(regions)-1,
                       "recipe":"navigation","nativeTag":"nav","purpose":"navigation",
                       "priority":authored.get("priority","secondary"),"tasks":[],
                       "members":[{"ref":ident} for ident in member_ids],
                       "reasons":["authored-semantic-navigation:"+authored["id"]]})
                continue
        if recipe in {"task", "commands", "inspection"}:
            # Like navigation disclosures, task and command groups preserve
            # authored semantic ownership. Separate tasks must not merge into
            # one giant card merely because their nodes share a node kind.
            for authored in declared_intent.get("regions", []):
                if authored.get("role") != ("inspector" if recipe == "inspection" else recipe):
                    continue
                member_ids = [m["ref"] for m in authored["members"] if m["ref"] in groups[recipe]]
                if not member_ids:
                    continue
                meaning, priority = ROLE_META[recipe]
                regions.append({"id":"interface-region:generated-" + recipe + "-" + authored["id"].rsplit(".",1)[-1],
                    "parentId":root,"ordinal":len(regions)-1,"recipe":recipe,
                    "nativeTag":TAGS[recipe],"purpose":meaning,
                    "priority":authored.get("priority",priority),
                    "semanticTitle":authored["id"].rsplit(".",1)[-1],"tasks":[],
                    "members":[{"ref":ident} for ident in member_ids],
                    "reasons":["authored-semantic-" + recipe + ":" + authored["id"]]})
            continue
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
    # Inspector membership is explicit and validated against derived states;
    # a renderer-local selected key alone never manufactures an inspector.
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
