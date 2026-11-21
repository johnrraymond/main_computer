"""Deterministic template applicability AND argument binding from verified semantic IR.

No metric thresholds, app names, DOM order, JSON override, or spectral gate.
The template's semantic signature is a reusable graph pattern, not a per-app
policy. Invalid source bindings fail closed; no effect is executed here.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
import copy

from .semantic_argument_contract import verify_argument_sources


def _ref(value):
    return value.get("ref") if isinstance(value, Mapping) else None


def match_code_workbench(ir: Mapping, plan: Mapping) -> dict | None:
    """Match a reusable document workbench's *entire* required graph.

    Returns None for applications without this kind of workbench, raises for
    ambiguous/malformed would-be matches, and returns the complete role map
    only when every member has a unique supported assignment.
    """
    verify_argument_sources(ir, plan)
    d = plan.get("documentWorkbench")
    if not isinstance(d, Mapping):
        return None
    if d.get("schema") != "mcel.document-workbench.v1":
        raise ValueError("Unknown workbench graph schema")
    surface = ir["surfaces"][0]
    regions = {r["id"]: r for r in surface["interfaceIntent"]["regions"]}
    nodes = {n["id"]: n for n in surface["nodes"]}
    intents = {i["id"]: i for i in ir["intents"]}
    def zones_match(ids, purpose=None, side=None):
        return all((purpose is None or regions[rid]["purpose"] == purpose)
                   and (side is None or regions[rid].get("preferences", {}).get("side") == side)
                   for rid in ids)
    document_nodes = [n for n in nodes.values() if n["nodeKind"] == "document-editor"]
    if not document_nodes:
        return None
    if len(document_nodes) != 1:
        raise ValueError("Ambiguous document-editor anchor")
    # Check the entire dock topology, not the application name or appearance.
    if (len(d.get("navigation", ())) != 1 or len(d.get("status", ())) != 1
        or not d.get("companion") or not d.get("bottom")
        or not zones_match(d["navigation"], "navigation", "start")
        or not zones_match(d["status"], "status", "bottom")
        or not zones_match(d["companion"], side="end")
        or not zones_match(d["bottom"], side="bottom")):
        raise ValueError("Document workbench lacks required semantic dock relationships")
    anchor = regions[d["document"]]
    if (anchor.get("priority") != "primary" or
        [m["ref"] for m in anchor.get("members", ()) if nodes[m["ref"]]["nodeKind"] == "document-editor"]
            != [document_nodes[0]["id"]]):
        raise ValueError("No unique primary document-editor anchor")
    # A document workbench is editable only if an actual source write exists;
    # verified effect/capability links are checked by verify_argument_sources.
    if not any(i.get("risk") == "source-write" and i.get("operationKind") == "capability"
               for i in intents.values()):
        return None
    assistance = [regions[rid] for rid in d["companion"]
                  if regions[rid].get("purpose") == "assistance"]
    if not assistance:
        return None
    if len(assistance) != 1:
        raise ValueError("Ambiguous assistance region")
    region = assistance[0]
    if any(r.get("parentId") == region["id"] for r in regions.values()):
        # Author-declared nested relationships outrank inferred presentation.
        # No second hierarchy or grouping algorithm may rewrite them.
        return {"profile": "code", "docks": copy.deepcopy(dict(d)),
                "grouping": "authored", "groups": {}}
    groups: dict[str, list[str]] = {"execution": [], "preview": [], "session": []}
    produced: dict[str, set[str]] = defaultdict(set)
    anchors: dict[str, list[str]] = {"execution": [], "preview": []}
    state_nodes: list[dict] = []
    for member in region.get("members", ()):
        node = nodes[member["ref"]]
        if node["nodeKind"] == "state-value":
            state_nodes.append(node)
            continue
        intent = intents.get(_ref(node.get("intent")))
        if intent is None or intent.get("operationKind") != "capability":
            raise ValueError("Assistance node lacks a verified capability operation")
        typed = intent.get("typedOperation") or {}
        kind, target, risk = typed.get("kind"), typed.get("target"), intent.get("risk")
        form = node["nodeKind"] == "form"
        if form and kind == "run" and target == "task" and risk == "consequential":
            role = "execution"
            anchors[role].append(node["id"])
        elif form and kind == "inspect" and target == "document" and risk == "external-read":
            role = "preview"
            anchors[role].append(node["id"])
        elif ((target == "session" and kind in {"inspect", "save", "reset", "open"}) or
              (target == "task" and kind == "refresh")) and node["nodeKind"] in {"control", "form"}:
            role = "session"
        else:
            raise ValueError("Assistance operation does not fit template roles")
        groups[role].append(node["id"])
        for binding in intent.get("resultBindings") or ():
            state = _ref(binding.get("target"))
            if state:
                produced[state].add(role)
    if any(len(anchors[role]) != 1 for role in anchors) or not groups["session"]:
        raise ValueError("Document workbench has missing or ambiguous role anchors")
    for node in state_nodes:
        state = _ref((node.get("value") or {}).get("state")) or _ref(node.get("state"))
        owners = produced.get(state, set())
        if not owners:
            raise ValueError("Result state has no verified producer")
        if len(owners) == 1:
            owner = next(iter(owners))
        elif owners == {"execution", "session"}:
            # The single primary run form owns its own result; session controls
            # may additionally write it. This is a graph-level template rule,
            # not a per-app precedence file or DOM-position assumption.
            owner = "execution"
        else:
            raise ValueError("Result ownership is genuinely ambiguous")
        groups[owner].append(node["id"])
    actual = [node for members in groups.values() for node in members]
    expected = [member["ref"] for member in region["members"]]
    if len(actual) != len(set(actual)) or sorted(actual) != sorted(expected):
        raise ValueError("Argument binding lacks exact coverage")
    return {"profile": "code", "docks": copy.deepcopy(dict(d)),
            "grouping": "inferred", "region": region["id"],
            "regionPurpose": region["purpose"],
            "groups": {name: sorted(members) for name, members in groups.items()}}


def resolve_code_arguments(ir: Mapping, plan: Mapping, policy=None) -> dict:
    """Public matcher for the reusable workbench template; policy-free."""
    if policy is not None:
        raise ValueError("Legacy override policy is no longer accepted")
    result = match_code_workbench(ir, plan)
    if result is None:
        raise ValueError("Semantic graph does not match code workbench")
    return result
