"""Deterministic, source-bound workspace composition for paired semantic MCEL IR.

The plan changes only presentation. Hosts verify it against the compiled IR;
the operational runtime independently validates coverage before mounting.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from .semantic_composition import sha

SCHEMA = "mcel.workspace-plan.v1"
POLICY = "mcel.workspace-planning.v1"
SCHEME = "workspace_v1"


def _ref(value):
    return value.get("ref") if isinstance(value, Mapping) else None


def _references(items):
    return {_ref(x) for x in (items or []) if _ref(x)}


def _region_facts(region, nodes, intents):
    reads, writes, displays, selection_targets, results = set(), set(), set(), set(), set()
    controls = 0
    for member in region["members"]:
        node = nodes[member["ref"]]
        if node.get("nodeKind") == "control":
            controls += 1
        # A collection's selectionIntent is distinct from a control's intent;
        # ignoring it would lose precisely the navigator -> anchor dependency.
        for intent_ref in (_ref(node.get("intent")), _ref(node.get("selectionIntent"))):
            intent = intents.get(intent_ref, {})
            reads |= _references(intent.get("reads"))
            reads |= {_ref(item.get("state")) for item in intent.get("requestArguments", []) if _ref(item.get("state"))}
            writes |= _references(intent.get("writes")) | _references(intent.get("viewWrites"))
            selection = intent.get("collectionSelection") or {}
            target = _ref(selection.get("target"))
            if target:
                selection_targets.add(target)
                writes.add(target)
            for binding in intent.get("resultBindings", []):
                target = _ref(binding.get("target"))
                if target:
                    results.add(target)
        value = node.get("value") or {}
        if _ref(value.get("state")):
            displays.add(_ref(value["state"]))
        if _ref(node.get("state")):
            displays.add(_ref(node["state"]))
    content = region.get("content") or {}
    if content.get("ref", "").startswith("state:"):
        displays.add(content["ref"])
    return dict(reads=sorted(reads), writes=sorted(writes), displays=sorted(displays),
                selectionTargets=sorted(selection_targets), results=sorted(results),
                memberCount=len(region["members"]), controlCount=controls)


def plan_workspace(ir: Mapping, *, scheme: str = SCHEME) -> dict:
    """Derive a source-bound presentation plan. Fail closed on malformed IR.

    A decision is driven only by normalized semantics, never application names,
    node labels, DOM selectors, geometry observations, or snapshot pixels.
    """
    if scheme != SCHEME:
        raise ValueError("Unsupported workspace scheme")
    if ir.get("schema") != "mcel.application-ir.v1" or len(ir.get("surfaces", [])) != 1:
        raise ValueError("Workspace planner requires one normalized Application IR surface")
    source_hash = (ir.get("fingerprints") or {}).get("semantic")
    if not isinstance(source_hash, str) or not source_hash.startswith("sha256:"):
        raise ValueError("Workspace planner requires an authenticated semantic fingerprint")
    surface = ir["surfaces"][0]
    interface = surface.get("interfaceIntent") or {}
    if not interface.get("hierarchy") or interface.get("schema") != "mcel.interface-intent.v1":
        raise ValueError("S1 requires a paired semantic HTML hierarchy")
    regions = interface.get("regions") or []
    if not regions or not isinstance(regions, list):
        raise ValueError("Missing semantic regions")
    ids = [r["id"] for r in regions]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate semantic region")
    by_id = {r["id"]: r for r in regions}
    root_id = interface["hierarchy"]["rootId"]
    if root_id not in by_id or by_id[root_id].get("parentId") is not None:
        raise ValueError("Unresolved workspace root")
    # Compiler retains HTML declaration order; ordinals are sibling-local.
    children = [r for r in regions if r["id"] != root_id]
    if any(r.get("parentId") != root_id for r in children):
        raise ValueError("S1 cannot flatten nested regions; nested hierarchy requires explicit planning")
    nodes = {n["id"]: n for n in surface.get("nodes", [])}
    intents = {i["id"]: i for i in ir.get("intents", [])}
    owned = [m["ref"] for r in regions for m in r.get("members", [])]
    if set(owned) != set(nodes) or len(owned) != len(set(owned)):
        raise ValueError("S1 requires exactly-once node ownership")
    facts = {r["id"]: _region_facts(r, nodes, intents) for r in children}

    # Explicit authored mode semantics are stronger than region purpose. When
    # a view selector governs multiple alternate regions, those regions share
    # the workspace slot without changing authored visibleWhen conditions.
    conditionals = [r for r in children if isinstance(r.get("visibleWhen"), Mapping)]
    modes_by_state = {}
    for r in conditionals:
        when = r["visibleWhen"]
        state = _ref(when.get("state"))
        if state and isinstance(when.get("equals"), str):
            modes_by_state.setdefault(state, set()).add(when["equals"])
    mode_state = next((state for state, modes in sorted(modes_by_state.items(),
                       key=lambda x: (-len(x[1]), x[0])) if len(modes) > 1), None)

    primary = [r for r in children if r.get("priority") == "primary" and
               r.get("purpose") not in {"status", "navigation", "query-controls", "mode-control"}]
    if not primary:
        raise ValueError("S1 cannot derive an anchor without authored primary priority")
    # Primary is an anchor for non-conditional workspaces; for mode-based apps,
    # every region governed by the same mode state shares the anchor slot.
    mode_members = [r for r in children if _ref((r.get("visibleWhen") or {}).get("state")) == mode_state] if mode_state else []
    anchor_members = list(dict.fromkeys(r["id"] for r in primary + mode_members))
    anchored = set(anchor_members)
    navigation, controls, status, tools = [], [], [], []
    for r in children:
        rid = r["id"]
        if rid in anchored:
            continue
        purpose = r.get("purpose")
        if purpose == "navigation": navigation.append(rid)
        elif purpose in {"mode-control", "query-controls"}: controls.append(rid)
        elif purpose == "status": status.append(rid)
        else: tools.append(rid)

    # Authoring's soft priorities are not silently rewritten. If alternate
    # regions might be too coarse to organize internally, return an explicit
    # review requirement rather than assuming a human-quality result.
    unresolved = []
    for r in children:
        f = facts[r["id"]]
        if f["memberCount"] >= 14 or f["controlCount"] >= 10:
            unresolved.append({"kind": "coarse-region", "region": r["id"],
                               "memberCount": f["memberCount"], "controlCount": f["controlCount"],
                               "reason": "S1 cannot infer independent subworkflows inside one region"})
    if len(tools) > 1:
        unresolved.append({"kind": "tool-default-requires-review", "regions": tools,
                           "reason": "Author order is deterministic, not proof of user-preferred default"})
    # Rationale is inspectable; width-dependent ladders are hypotheses, not a
    # runtime instruction. The S2/S3 stages must validate minimum capacity.
    zones = []
    if controls:
        zones.append({"id": "controls", "kind": "toolbar", "members": controls,
                      "ladder": {"wide": "top", "medium": "top", "narrow": "top"},
                      "reasons": ["purpose:query-controls-or-mode-control"]})
    if navigation:
        zones.append({"id": "navigator", "kind": "dock", "members": navigation,
                      "ladder": {"wide": "dock-start", "medium": "dock-start", "narrow": "disclosure-before-anchor"},
                      "reasons": ["purpose:navigation"]})
    variants = []
    if mode_state:
        for mode in sorted(modes_by_state[mode_state]):
            m = [r["id"] for r in children if (r.get("visibleWhen") or {}).get("equals") == mode
                 and _ref((r.get("visibleWhen") or {}).get("state")) == mode_state]
            variants.append({"state": mode_state, "equals": mode, "members": m,
                             "rule": "authored-visibleWhen"})
    # A result belongs beside the authored producer that writes its displayed
    # state, and only within the SAME mode. Never infer a pairing from adjacent
    # English labels, application names, or DOM coordinates. Unmatched regions
    # remain visible in their authored mode rather than getting silently lost.
    mode_pairs = []
    if mode_state:
        for variant in variants:
            members = variant["members"]
            sources = [rid for rid in members if by_id[rid].get("purpose") != "results"]
            results = [rid for rid in members if by_id[rid].get("purpose") == "results"]
            unused = set(sources)
            pairs = []
            for result_id in results:
                matched = [rid for rid in sources if rid in unused and
                           set(facts[rid]["results"]) & set(facts[result_id]["displays"])]
                if len(matched) == 1:
                    source_id = matched[0]
                    unused.remove(source_id)
                    pairs.append({"source": source_id, "result": result_id,
                                  "evidenceStates": sorted(set(facts[source_id]["results"]) &
                                                           set(facts[result_id]["displays"]))})
            paired = {id for pair in pairs for id in (pair["source"], pair["result"])}
            mode_pairs.append({"state": variant["state"], "equals": variant["equals"],
                               "pairs": pairs, "unpaired": [id for id in members if id not in paired]})
    zones.append({"id": "workspace", "kind": "protected-anchor", "members": anchor_members,
                  "modeVariants": variants, "modePairs": mode_pairs,
                  "ladder": {"wide": "anchor", "medium": "anchor", "narrow": "anchor"},
                  "reasons": ["authored-primary", *(["authored-mode-alternates"] if mode_state else [])]})
    if tools:
        selected_states = set().union(*(set(f["selectionTargets"]) for f in facts.values()))
        selection_hints = [rid for rid in tools if by_id[rid].get("purpose") == "inspection"
                           and set(facts[rid]["displays"]) & selected_states]
        zones.append({"id": "tools", "kind": "tab-group" if len(tools) > 1 else "inspector",
                      "members": tools, "default": tools[0],
                      "hintUntilSelected": selection_hints,
                      "ladder": {"wide": "dock-end", "medium": "dock-bottom", "narrow": "disclosure-after-anchor"},
                      "reasons": ["supporting-or-unassigned", "fallback-preserves-bound-region"]})
    if status:
        zones.append({"id": "status", "kind": "pinned-compact", "members": status,
                      "ladder": {"wide": "bottom", "medium": "bottom", "narrow": "bottom"},
                      "reasons": ["purpose:status"]})
    assigned = [r for zone in zones for r in zone["members"]]
    if len(assigned) != len(set(assigned)) or set(assigned) != set(ids) - {root_id}:
        raise ValueError("Workspace plan must cover each nonroot region exactly once")

    derived = []
    anchor_refs = set().union(*(set(facts[r]["reads"]) | set(facts[r]["displays"]) for r in anchor_members))
    authored = {(_ref(x["from"]), x["kind"], _ref(x["to"])) for x in interface.get("relations", [])}
    # A result displayed in the same mode slot can be paired with its input
    # without manufacturing a new runtime binding.
    for source_id in anchor_members:
        produced = set(facts[source_id]["results"])
        for target_id in anchor_members:
            if target_id == source_id:
                continue
            # Never associate results across two different authored modes.
            if by_id[source_id].get("visibleWhen") != by_id[target_id].get("visibleWhen"):
                continue
            matched = sorted(produced & set(facts[target_id]["displays"]))
            if matched and (target_id, "reports-for", source_id) not in authored:
                derived.append({"from": target_id, "kind": "reports-for", "to": source_id,
                                "evidenceStates": matched, "reason": "verified-ir-result-binding"})
    for r in children:
        rid = r["id"]
        if rid in anchored:
            continue
        evidence = facts[rid]
        kind, signals = None, []
        if set(evidence["selectionTargets"]) & anchor_refs:
            kind, signals = "selects", sorted(set(evidence["selectionTargets"]) & anchor_refs)
        elif set(evidence["reads"]) & anchor_refs:
            kind, signals = "contextual-to", sorted(set(evidence["reads"]) & anchor_refs)
        if kind and (rid, kind, anchor_members[0]) not in authored:
            derived.append({"from": rid, "kind": kind, "to": anchor_members[0],
                            "evidenceStates": signals, "reason": "verified-ir-dataflow"})
    result = {"schema": SCHEMA, "policy": POLICY, "scheme": {"requested": "workspace", "resolved": scheme},
              "sourceIrHash": source_hash, "rootId": root_id, "zones": zones,
              "authoredRelations": interface.get("relations", []), "derivedRelations": derived,
              "regionFacts": [{"id": r["id"], **facts[r["id"]]} for r in children],
              "unresolved": unresolved, "runtimeEnabled": True}
    result["fingerprint"] = sha(result)
    return result


def verify_workspace_plan(ir: Mapping, plan: Mapping) -> None:
    """Recompute the entire plan from verified semantic IR before host emission."""
    if not isinstance(plan, Mapping) or plan != plan_workspace(ir, scheme=SCHEME):
        raise ValueError("Workspace plan disagrees with the verified semantic IR and policy")
