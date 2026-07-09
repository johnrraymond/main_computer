#!/usr/bin/env python3
"""FLOG synthetic layout trial smoke with PNG proof.

This script is intentionally not a live-app layout fixer.  It creates eight
MCEL-like synthetic hierarchies that model what real mounted apps should look
like once the source hierarchy is properly described.  For each hierarchy it
tries multiple stable layout families, measures the rendered geometry in
Playwright/Chromium, and writes a PNG proof for every trial.

The human stays in the loop:

* Chromium proves rectangles, clipping, scroll pressure, focus area, and rough
  unclaimed/wasted space.
* FLOG infers which layout family looks best for the hierarchy.
* The report calls out what the script cannot know yet.

Examples:

    python main_computer/flog_layout_snapshot_smoke.py
    python main_computer/flog_layout_snapshot_smoke.py --hierarchies all --viewports desktop=1440x900,narrow=390x844
    python main_computer/flog_layout_snapshot_smoke.py --candidates split-pane,focus-priority --screenshot-mode both
"""

from __future__ import annotations

import argparse
import html
import json
import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageOps
except ImportError:  # pragma: no cover - exercised only when Pillow is missing at runtime.
    Image = None
    ImageDraw = None
    ImageOps = None


DEFAULT_HIERARCHIES = "all"
DEFAULT_VIEWPORTS = "desktop=1440x900"
DEFAULT_CHROME = "mcel-realistic"
DEFAULT_CANDIDATES = "all"
DEFAULT_OUTPUT_DIR = "runtime/reports/flog"
ROLLUP_TOP_N = 8
ROLLUP_COLUMNS = 4
ROLLUP_TILE_WIDTH = 320
ROLLUP_IMAGE_HEIGHT = 180
ROLLUP_TILE_GAP = 12
ROLLUP_CANVAS_MARGIN = 16

SEMANTIC_RELATION_KEYS = (
    "controls",
    "selects",
    "navigates",
    "scopes",
    "reflects",
    "confirms",
    "proves",
    "emits",
    "consumes",
)
SEMANTIC_QUALITY_KEYS = (
    "authority",
    "persistence",
    "growth",
    "layoutAffordance",
    "deferability",
    "scrollPolicy",
    "phase",
    "availability",
    "presentationSet",
    "relationshipStrength",
    "hardConstraints",
    "softPreferences",
)

LAYOUT_CANDIDATES = [
    "source-order-stacked",
    "split-pane",
    "sectioned-sidebar",
    "inspector",
    "dashboard-grid",
    "focus-priority",
    "bounded-drawer",
]

ACCEPTABLE_LAYOUT_STATUSES = {"pass", "watch"}


@dataclass(frozen=True)
class ViewportProfile:
    name: str
    width: int
    height: int


def slugify(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value).strip()).strip("-")
    return clean.lower() or "item"


def parse_viewports(value: str) -> list[ViewportProfile]:
    profiles: list[ViewportProfile] = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if "=" not in raw or "x" not in raw:
            raise ValueError(f"Viewport must look like name=WIDTHxHEIGHT: {raw!r}")
        name, dims = raw.split("=", 1)
        width_text, height_text = dims.lower().split("x", 1)
        width = int(width_text)
        height = int(height_text)
        if width <= 0 or height <= 0:
            raise ValueError(f"Viewport dimensions must be positive: {raw!r}")
        profiles.append(ViewportProfile(name=slugify(name), width=width, height=height))
    if not profiles:
        raise ValueError("At least one viewport is required.")
    return profiles


def parse_candidates(value: str) -> list[str]:
    if value.strip().lower() == "all":
        return list(LAYOUT_CANDIDATES)
    selected = [slugify(part) for part in value.split(",") if part.strip()]
    unknown = [item for item in selected if item not in LAYOUT_CANDIDATES]
    if unknown:
        raise ValueError(f"Unknown layout candidate(s): {', '.join(unknown)}")
    if not selected:
        raise ValueError("At least one layout candidate is required.")
    return selected




def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _add_semantic(semantics: dict[str, list[str]], key: str, values: Any) -> None:
    bucket = semantics.setdefault(key, [])
    for value in _as_list(values):
        if value not in bucket:
            bucket.append(value)


def _focus_growth_for_kind(kind: str) -> str:
    kind = slugify(kind)
    if "grid" in kind:
        return "large-dense-grid"
    if "terminal" in kind:
        return "large-output-stream"
    if "editor" in kind:
        return "large-text-surface"
    if "file-list" in kind or "collection" in kind:
        return "large-collection"
    if "map" in kind:
        return "large-spatial-surface"
    if "canvas" in kind or "workspace" in kind:
        return "large-work-surface"
    return "large-focus-surface"


def _semantic_attr_name(key: str) -> str:
    return "data-mc-" + re.sub(r"(?<!^)([A-Z])", r"-\1", key).lower()


def _relationship_strength_for_primitive(semantics: dict[str, Any], primitive: str) -> str:
    """Return the declared generic strength for a primitive edge.

    Strength is intentionally primitive-level, not app-level.  A node can say
    that a `controls` edge is hard or a `proves` edge is conditional without
    saying what application family it belongs to.
    """

    for value in _as_list(semantics.get("relationshipStrength")):
        if ":" in value:
            name, strength = value.split(":", 1)
            if name == primitive:
                return strength
        elif value in {"hard", "strong", "medium", "weak", "conditional"}:
            return value
    if primitive in {"controls", "confirms"}:
        return "strong"
    if primitive in {"selects", "navigates", "scopes", "reflects"}:
        return "medium"
    if primitive == "proves":
        return "conditional"
    return "weak"


def _strength_multiplier(strength: str) -> float:
    return {
        "hard": 1.45,
        "strong": 1.22,
        "medium": 1.0,
        "conditional": 0.82,
        "weak": 0.68,
    }.get(strength, 1.0)


def _is_hard_semantic_requirement(node: dict[str, Any], primitive: str | None = None) -> bool:
    semantics = node.get("semantics") or {}
    hard_constraints = set(_as_list(semantics.get("hardConstraints")))
    if "must-remain-visible" in hard_constraints or "persistent-visibility" in hard_constraints:
        return True
    if "must-own-readable-space" in hard_constraints and primitive is None:
        return True
    if primitive:
        strength = _relationship_strength_for_primitive(semantics, primitive)
        return strength == "hard" or f"{primitive}:hard" in set(_as_list(semantics.get("relationshipStrength")))
    return False


def semantic_presentation_sets(hierarchy: dict[str, Any]) -> list[dict[str, Any]]:
    """Compose generic presentation sets from phase/availability tags.

    This is the MBUI/PUC-style layer: it says which generic interaction pieces
    must be co-present during a phase, without declaring an app archetype or a
    final layout family.
    """

    focus_slot = hierarchy["focusSlot"]
    phase_slots: dict[str, list[str]] = defaultdict(list)
    required_by_phase: dict[str, list[str]] = defaultdict(list)
    reasons: dict[str, list[str]] = defaultdict(list)

    def add_unique(bucket: dict[str, list[str]], key: str, value: str) -> None:
        if value not in bucket[key]:
            bucket[key].append(value)

    nodes = hierarchy.get("nodes", [])
    always_visible = {
        node["slot"]
        for node in nodes
        if "always-visible" in _as_list((node.get("semantics") or {}).get("persistence"))
        or "must-remain-visible" in _as_list((node.get("semantics") or {}).get("hardConstraints"))
    }

    for node in nodes:
        slot = node["slot"]
        semantics = node.get("semantics") or {}
        phases = _as_list(semantics.get("presentationSet")) or _as_list(semantics.get("phase")) or ["default"]
        for phase in phases:
            add_unique(phase_slots, phase, slot)
            if (
                slot == focus_slot
                or slot in always_visible
                or node.get("visibility") == "required"
                or _is_hard_semantic_requirement(node)
            ):
                add_unique(required_by_phase, phase, slot)
            availability = ", ".join(_as_list(semantics.get("availability")) or ["available"])
            reasons[phase].append(f"{slot} is {availability} during {phase}")

    all_phases = sorted(phase_slots)
    for phase in all_phases:
        add_unique(phase_slots, phase, focus_slot)
        add_unique(required_by_phase, phase, focus_slot)
        for slot in sorted(always_visible):
            add_unique(phase_slots, phase, slot)
            add_unique(required_by_phase, phase, slot)

    sets: list[dict[str, Any]] = []
    for phase in all_phases:
        slots = phase_slots.get(phase, [])
        required = required_by_phase.get(phase, [])
        sets.append(
            {
                "phase": phase,
                "slots": slots,
                "requiredSlots": required,
                "optionalSlots": [slot for slot in slots if slot not in required],
                "reason": "; ".join(reasons.get(phase, [])[:4]),
            }
        )
    return sets


def enrich_nodes_with_semantic_primitives(nodes: list[dict[str, Any]], focus_slot: str) -> list[dict[str, Any]]:
    """Attach generic semantic relationship tags without hard-coding app archetypes.

    The output does not say "spreadsheet", "IDE", or "file explorer".  It only
    declares composable primitives such as controls, selects, reflects,
    confirms, proves, owned scroll, and persistence.  FLOG can then infer a
    layout grammar from those primitives instead of importing a high-level app
    answer.
    """

    role_by_slot = {node["slot"]: node.get("role", "support") for node in nodes}
    enriched: list[dict[str, Any]] = []
    for raw_node in nodes:
        node = dict(raw_node)
        role = node.get("role", "support")
        slot = node["slot"]
        connects = list(node.get("connects") or [])
        connects_focus = focus_slot in connects or slot == focus_slot
        semantics: dict[str, list[str]] = {
            key: list(values)
            for key, values in (node.get("semantics") or {}).items()
        }

        if slot == focus_slot or role == "focus":
            _add_semantic(semantics, "authority", "primary-work")
            _add_semantic(semantics, "emits", [f"{slot}.selection", f"{slot}.state"])
            _add_semantic(semantics, "growth", _focus_growth_for_kind(node.get("kind", "")))
            _add_semantic(semantics, "layoutAffordance", "dominant-surface")
            _add_semantic(semantics, "scrollPolicy", "owned" if node.get("scroll", "allowed") == "allowed" else "fixed")
            command_inputs = [f"{candidate}.intent" for candidate, role_name in role_by_slot.items() if role_name == "command"]
            _add_semantic(semantics, "consumes", command_inputs)

        if role == "command":
            _add_semantic(semantics, "authority", "command")
            _add_semantic(semantics, "layoutAffordance", "command-rail")
            _add_semantic(semantics, "emits", f"{slot}.intent")
            _add_semantic(semantics, "controls", focus_slot if connects_focus else connects[:1])

        if role == "navigation":
            target = focus_slot if connects_focus else connects[:1]
            _add_semantic(semantics, "navigates", target)
            _add_semantic(semantics, "scopes", target)
            _add_semantic(semantics, "selects", f"{focus_slot}.selection" if connects_focus else target)
            _add_semantic(semantics, "growth", "medium-list")
            _add_semantic(semantics, "layoutAffordance", "rail")

        if role == "collection":
            target = focus_slot if connects_focus else connects[:1]
            _add_semantic(semantics, "selects", f"{focus_slot}.selection" if connects_focus else target)
            _add_semantic(semantics, "growth", "medium-collection")
            _add_semantic(semantics, "layoutAffordance", "support-collection")

        if role in {"detail", "inspector"}:
            reflected = f"{focus_slot}.selection" if connects_focus else connects[:1]
            _add_semantic(semantics, "reflects", reflected)
            _add_semantic(semantics, "consumes", reflected)
            _add_semantic(semantics, "layoutAffordance", "inspector")

        if role == "status":
            confirmed = f"{focus_slot}.state" if connects_focus else connects[:1]
            _add_semantic(semantics, "confirms", confirmed)
            _add_semantic(semantics, "consumes", confirmed)
            _add_semantic(semantics, "persistence", "always-visible")
            _add_semantic(semantics, "layoutAffordance", "status-rail")

        if role == "evidence":
            target = focus_slot if connects_focus else (connects[0] if connects else focus_slot)
            claim = f"{target}.claim"
            _add_semantic(semantics, "proves", claim)
            _add_semantic(semantics, "consumes", claim)
            _add_semantic(semantics, "deferability", "secondary-visible" if node.get("visibility") == "deferable" else "visible")
            _add_semantic(semantics, "layoutAffordance", "proof-region")

        if node.get("visibility") == "deferable":
            _add_semantic(semantics, "deferability", "deferable")
        elif node.get("visibility") == "required":
            _add_semantic(semantics, "deferability", "not-deferable")

        if role == "command":
            _add_semantic(semantics, "phase", ["prepare", "commit"])
            _add_semantic(semantics, "presentationSet", ["default", "operation", "confirmation"])
            _add_semantic(semantics, "availability", "active-control")
            _add_semantic(semantics, "relationshipStrength", "controls:strong")
            _add_semantic(semantics, "softPreferences", ["near-controlled-focus", "command-band-or-rail"])
        elif role == "focus":
            _add_semantic(semantics, "phase", ["work", "selection", "review"])
            _add_semantic(semantics, "presentationSet", ["default", "operation", "selection", "confirmation", "proof-review"])
            _add_semantic(semantics, "availability", "primary")
            _add_semantic(semantics, "hardConstraints", "must-own-readable-space")
            _add_semantic(semantics, "softPreferences", "dominant-owned-surface")
        elif role == "navigation":
            _add_semantic(semantics, "phase", ["scope", "selection"])
            _add_semantic(semantics, "presentationSet", ["default", "selection"])
            _add_semantic(semantics, "availability", "selection-scope")
            _add_semantic(semantics, "relationshipStrength", ["navigates:strong", "scopes:strong", "selects:strong"])
            _add_semantic(semantics, "softPreferences", ["side-rail-adjacency", "selection-near-focus"])
        elif role == "collection":
            _add_semantic(semantics, "phase", ["selection", "review"])
            _add_semantic(semantics, "presentationSet", ["selection", "review"])
            _add_semantic(semantics, "availability", "selection-source")
            _add_semantic(semantics, "relationshipStrength", "selects:medium")
            _add_semantic(semantics, "softPreferences", ["supporting-collection-adjacent", "selection-near-focus"])
        elif role in {"detail", "inspector"}:
            _add_semantic(semantics, "phase", ["selection-review", "inspection"])
            _add_semantic(semantics, "presentationSet", ["selection", "review"])
            _add_semantic(semantics, "availability", "selection-dependent")
            _add_semantic(semantics, "relationshipStrength", ["reflects:medium", "consumes:medium"])
            _add_semantic(semantics, "softPreferences", ["inspector-adjacent", "reflection-near-source"])
        elif role == "status":
            _add_semantic(semantics, "phase", ["confirmation", "state-review"])
            _add_semantic(semantics, "presentationSet", ["default", "confirmation", "proof-review"])
            _add_semantic(semantics, "availability", "always")
            _add_semantic(semantics, "relationshipStrength", ["confirms:hard", "consumes:strong"])
            _add_semantic(semantics, "hardConstraints", ["must-remain-visible", "persistent-visibility"])
            _add_semantic(semantics, "softPreferences", "persistent-status-strip")
        elif role == "evidence":
            _add_semantic(semantics, "phase", ["proof-review", "confirmation"])
            _add_semantic(semantics, "presentationSet", ["proof-review", "confirmation"])
            _add_semantic(semantics, "availability", "claim-dependent")
            _add_semantic(semantics, "relationshipStrength", ["proves:conditional", "consumes:conditional"])
            _add_semantic(semantics, "softPreferences", ["secondary-proof-dock", "proof-near-claim"])

        if node.get("visibility") == "required":
            _add_semantic(semantics, "hardConstraints", "must-remain-visible")
        elif node.get("visibility") == "companion":
            _add_semantic(semantics, "softPreferences", "default-visible-companion")

        node["semantics"] = semantics
        enriched.append(node)

    return enriched


def _targets_focus(target: str, focus_slot: str) -> bool:
    return target == focus_slot or target.startswith(f"{focus_slot}.")


def semantic_relationship_edges(hierarchy: dict[str, Any]) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    for node in hierarchy.get("nodes", []):
        semantics = node.get("semantics") or {}
        for primitive in SEMANTIC_RELATION_KEYS:
            for target in _as_list(semantics.get(primitive)):
                edges.append(
                    {
                        "primitive": primitive,
                        "source": node["slot"],
                        "target": target,
                    }
                )
    return edges



def semantic_contract_audit(hierarchy: dict[str, Any]) -> dict[str, Any]:
    focus_slot = hierarchy["focusSlot"]
    nodes = hierarchy.get("nodes", [])
    role_slots: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        role_slots[node.get("role", "support")].append(node["slot"])

    edges = semantic_relationship_edges(hierarchy)
    edge_text = [f"{edge['source']} {edge['primitive']} {edge['target']}" for edge in edges]

    def has_edge(primitive: str, *, source: str | None = None, focus_target: bool = False) -> bool:
        for edge in edges:
            if edge["primitive"] != primitive:
                continue
            if source is not None and edge["source"] != source:
                continue
            if focus_target and not _targets_focus(edge["target"], focus_slot):
                continue
            return True
        return False

    missing: list[str] = []
    focus_node = next((node for node in nodes if node["slot"] == focus_slot), {})
    focus_semantics = focus_node.get("semantics") or {}

    if not has_edge("emits", source=focus_slot):
        missing.append(f"{focus_slot} emits selection/state")
    if not _as_list(focus_semantics.get("growth")):
        missing.append(f"{focus_slot} declares generic growth/density behavior")
    if not _as_list(focus_semantics.get("scrollPolicy")):
        missing.append(f"{focus_slot} declares owned scroll behavior")
    if role_slots.get("command") and not has_edge("consumes", source=focus_slot):
        missing.append(f"{focus_slot} consumes command intent")

    if role_slots.get("command") and not has_edge("controls", focus_target=True):
        missing.append(f"command controls {focus_slot}")

    selector_slots = [
        node["slot"]
        for node in nodes
        if node.get("role") in {"navigation", "collection"} and focus_slot in (node.get("connects") or [])
    ]
    if selector_slots and not any(has_edge(primitive, focus_target=True) for primitive in ("selects", "navigates", "scopes")):
        missing.append(f"navigation/collection selects, navigates, or scopes {focus_slot}")

    if role_slots.get("detail") and not has_edge("reflects", focus_target=True):
        missing.append(f"detail reflects {focus_slot}.selection or {focus_slot}.state")
    if role_slots.get("detail") and not any(has_edge("consumes", source=slot) for slot in role_slots.get("detail", [])):
        missing.append(f"detail consumes {focus_slot}.selection or {focus_slot}.state")

    if role_slots.get("status") and not has_edge("confirms", focus_target=True):
        missing.append(f"status confirms {focus_slot}.state")
    if role_slots.get("status") and not any(has_edge("consumes", source=slot) for slot in role_slots.get("status", [])):
        missing.append(f"status consumes {focus_slot}.state")

    if role_slots.get("evidence") and not has_edge("proves"):
        missing.append("evidence proves a focus, command, or runtime claim")

    primitive_counts = {
        primitive: sum(1 for edge in edges if edge["primitive"] == primitive)
        for primitive in SEMANTIC_RELATION_KEYS
    }
    quality_counts = {
        key: sum(1 for node in nodes if _as_list((node.get("semantics") or {}).get(key)))
        for key in SEMANTIC_QUALITY_KEYS
    }

    presentation_sets = semantic_presentation_sets(hierarchy)
    if not presentation_sets or not any(_as_list((node.get("semantics") or {}).get("presentationSet")) for node in nodes):
        missing.append("generic presentation sets describe which regions co-exist during interaction phases")
    if not any(_as_list((node.get("semantics") or {}).get("phase")) for node in nodes):
        missing.append("nodes declare generic interaction phases")
    if not any(_as_list((node.get("semantics") or {}).get("availability")) for node in nodes):
        missing.append("nodes declare generic availability/dependency state")
    if not any(_as_list((node.get("semantics") or {}).get("relationshipStrength")) for node in nodes):
        missing.append("relationship edges declare generic strength")
    if not any(_as_list((node.get("semantics") or {}).get("hardConstraints")) for node in nodes):
        missing.append("hard visibility/ownership constraints are declared")
    if not any(_as_list((node.get("semantics") or {}).get("softPreferences")) for node in nodes):
        missing.append("soft layout preferences are declared")

    inferred_grammar: list[str] = []
    if has_edge("controls", focus_target=True) and has_edge("confirms", focus_target=True):
        inferred_grammar.append("controller-focus-confirmation")
    if has_edge("scopes", focus_target=True):
        inferred_grammar.append("scoped-focus")
    if any(has_edge(primitive, focus_target=True) for primitive in ("selects", "navigates")):
        inferred_grammar.append("selector-focus")
    if has_edge("reflects", focus_target=True) and any(has_edge("consumes", source=slot) for slot in role_slots.get("detail", [])):
        inferred_grammar.append("focus-reflection")
    if has_edge("proves"):
        inferred_grammar.append("evidence-backed-claim")
    if _as_list(focus_semantics.get("growth")):
        inferred_grammar.append("dominant-owned-work-surface")
    if any(
        "always-visible" in _as_list((node.get("semantics") or {}).get("persistence"))
        for node in nodes
        if node.get("role") == "status"
    ):
        inferred_grammar.append("persistent-state-confirmation")
    if has_edge("emits", source=focus_slot) and has_edge("consumes"):
        inferred_grammar.append("emitted-state-consumption")
    if presentation_sets:
        inferred_grammar.append("phase-based-presentation-sets")
    if any(
        "hard" in " ".join(_as_list((node.get("semantics") or {}).get("relationshipStrength")))
        or _as_list((node.get("semantics") or {}).get("hardConstraints"))
        for node in nodes
    ):
        inferred_grammar.append("hard-soft-contract-split")

    layout_pressures = semantic_layout_pressures(hierarchy)
    affordance_expectations = semantic_affordance_expectations(hierarchy)
    if not affordance_expectations:
        missing.append("generic layout affordances describe how semantic roles should be spatially realized")
    contract_confidence = min(
        1.0,
        ((len(layout_pressures) + len(presentation_sets) + min(6, len(affordance_expectations))) / 17.0) if layout_pressures else 0.0,
    )

    if not missing and contract_confidence >= 0.80:
        state = "complete"
    elif len(missing) <= 3 and len(edges) >= 4 and presentation_sets:
        state = "partial"
    else:
        state = "underspecified"

    return {
        "hierarchyId": hierarchy["id"],
        "focusSlot": focus_slot,
        "state": state,
        "primitiveCounts": primitive_counts,
        "qualityCounts": quality_counts,
        "primitiveEdgeCount": len(edges),
        "relationships": edge_text,
        "missingPrimitives": missing,
        "inferredLayoutGrammar": inferred_grammar,
        "layoutPressures": [pressure["kind"] for pressure in layout_pressures],
        "layoutPressureCount": len(layout_pressures),
        "affordanceExpectations": [expectation["expectation"] for expectation in affordance_expectations],
        "affordanceExpectationCount": len(affordance_expectations),
        "presentationSets": presentation_sets,
        "presentationSetCount": len(presentation_sets),
        "contractConfidence": round(contract_confidence, 3),
        "note": "Inferred from generic MCEL primitives, phase sets, and hard/soft constraints; no high-level app archetype was used.",
    }


def _node_by_slot(hierarchy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["slot"]: node for node in hierarchy.get("nodes", [])}


def _target_slot_for_semantic_target(target: str, hierarchy: dict[str, Any]) -> str:
    slot = str(target).split(".", 1)[0]
    return slot if slot in _node_by_slot(hierarchy) else hierarchy["focusSlot"]


def semantic_layout_pressures(hierarchy: dict[str, Any]) -> list[dict[str, Any]]:
    """Infer layout pressures from generic MCEL primitives.

    This still does not declare an app archetype.  It turns relationships such as
    "controls focus", "selects focus.selection", and "confirms focus.state" into
    spatial questions that candidate layouts can be tested against.  The pressure
    layer also preserves the MBUI/PUC distinction between hard constraints,
    soft preferences, and phase/presentation-set co-presence.
    """

    focus_slot = hierarchy["focusSlot"]
    nodes_by_slot = _node_by_slot(hierarchy)
    pressures: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    focus_node = nodes_by_slot.get(focus_slot, {})
    focus_semantics = focus_node.get("semantics") or {}
    focus_growth = _as_list(focus_semantics.get("growth"))
    if focus_growth:
        pressures.append(
            {
                "kind": "focus-growth",
                "source": focus_slot,
                "target": focus_slot,
                "expectation": "dominant-owned-work-surface",
                "weight": 24 if any(item in {"large-dense-grid", "large-output-stream", "large-text-surface"} for item in focus_growth) else 20,
                "details": focus_growth,
                "phase": _as_list(focus_semantics.get("phase")),
                "requirement": "hard",
                "hard": True,
                "primitive": "growth",
                "strength": "hard",
            }
        )

    for edge in semantic_relationship_edges(hierarchy):
        primitive = edge["primitive"]
        source = edge["source"]
        target_slot = _target_slot_for_semantic_target(edge["target"], hierarchy)
        source_node = nodes_by_slot.get(source, {})
        source_semantics = source_node.get("semantics") or {}
        source_affordances = _as_list(source_semantics.get("layoutAffordance"))
        strength = _relationship_strength_for_primitive(source_semantics, primitive)
        hard = _is_hard_semantic_requirement(source_node, primitive)

        kind = ""
        expectation = ""
        weight = 0

        if primitive == "controls" and target_slot == focus_slot:
            kind = "control-focus-adjacency"
            expectation = "control-adjacent"
            weight = 12
        elif primitive in {"selects", "navigates", "scopes"} and _targets_focus(edge["target"], focus_slot):
            kind = f"{primitive}-focus-adjacency"
            expectation = "selector-adjacent"
            weight = 14 if primitive in {"navigates", "scopes"} else 12
        elif primitive == "reflects" and _targets_focus(edge["target"], focus_slot):
            kind = "reflection-adjacency"
            expectation = "reflection-adjacent"
            weight = 12
        elif primitive == "confirms" and _targets_focus(edge["target"], focus_slot):
            kind = "persistent-confirmation"
            expectation = "persistent-visible"
            weight = 12
            hard = True
        elif primitive == "proves" and _targets_focus(edge["target"], focus_slot):
            kind = "proof-support"
            expectation = "proof-visible"
            weight = 8
        elif primitive == "consumes" and _targets_focus(edge["target"], focus_slot) and source != focus_slot:
            kind = "emitted-state-consumption"
            expectation = "consumer-visible"
            weight = 7

        if not kind:
            continue

        key = (kind, source, target_slot)
        if key in seen:
            continue
        seen.add(key)
        pressures.append(
            {
                "kind": kind,
                "source": source,
                "target": target_slot,
                "expectation": expectation,
                "weight": round(weight * _strength_multiplier(strength), 2),
                "affordances": source_affordances,
                "softPreferences": _as_list(source_semantics.get("softPreferences")),
                "hardConstraints": _as_list(source_semantics.get("hardConstraints")),
                "phase": _as_list(source_semantics.get("phase")),
                "presentationSet": _as_list(source_semantics.get("presentationSet")),
                "availability": _as_list(source_semantics.get("availability")),
                "strength": strength,
                "requirement": "hard" if hard else "soft",
                "hard": hard,
                "primitive": primitive,
            }
        )

    for presentation_set in semantic_presentation_sets(hierarchy):
        required_slots = presentation_set.get("requiredSlots", [])
        if len(required_slots) <= 1:
            continue
        phase = presentation_set["phase"]
        key = ("presentation-set-co-presence", phase, focus_slot)
        if key in seen:
            continue
        seen.add(key)
        hard = phase in {"default", "confirmation"} or any(
            _is_hard_semantic_requirement(nodes_by_slot.get(slot, {}))
            for slot in required_slots
        )
        pressures.append(
            {
                "kind": "presentation-set-co-presence",
                "source": phase,
                "target": focus_slot,
                "expectation": "phase-copresence",
                "weight": 14 if hard else 9,
                "slots": presentation_set.get("slots", []),
                "requiredSlots": required_slots,
                "optionalSlots": presentation_set.get("optionalSlots", []),
                "phase": [phase],
                "strength": "hard" if hard else "medium",
                "requirement": "hard" if hard else "soft",
                "hard": hard,
                "primitive": "presentation-set",
                "reason": presentation_set.get("reason", ""),
            }
        )

    return pressures


def _record_rect(record: dict[str, Any] | None) -> dict[str, float] | None:
    if not record:
        return None
    rect = record.get("rect") or record.get("documentRect")
    if not rect:
        return None
    left = float(rect.get("left", rect.get("x", 0)) or 0)
    top = float(rect.get("top", rect.get("y", 0)) or 0)
    width = float(rect.get("width", 0) or 0)
    height = float(rect.get("height", 0) or 0)
    right = float(rect.get("right", left + width) or (left + width))
    bottom = float(rect.get("bottom", top + height) or (top + height))
    if width <= 0 and right > left:
        width = right - left
    if height <= 0 and bottom > top:
        height = bottom - top
    area = float(rect.get("area", width * height) or (width * height))
    return {"left": left, "right": right, "top": top, "bottom": bottom, "width": width, "height": height, "area": area}


def _overlap_ratio(a: dict[str, float], b: dict[str, float], axis: str) -> float:
    if axis == "x":
        overlap = max(0.0, min(a["right"], b["right"]) - max(a["left"], b["left"]))
        denom = max(1.0, min(a["width"], b["width"]))
    else:
        overlap = max(0.0, min(a["bottom"], b["bottom"]) - max(a["top"], b["top"]))
        denom = max(1.0, min(a["height"], b["height"]))
    return max(0.0, min(1.0, overlap / denom))


def _axis_gap(a: dict[str, float], b: dict[str, float], axis: str) -> float:
    if axis == "x":
        return max(0.0, max(a["left"] - b["right"], b["left"] - a["right"]))
    return max(0.0, max(a["top"] - b["bottom"], b["top"] - a["bottom"]))


def _center_distance_score(source: dict[str, float], target: dict[str, float], root: dict[str, float]) -> tuple[float, float]:
    sx = (source["left"] + source["right"]) / 2.0
    sy = (source["top"] + source["bottom"]) / 2.0
    tx = (target["left"] + target["right"]) / 2.0
    ty = (target["top"] + target["bottom"]) / 2.0
    diagonal = max(1.0, (root["width"] ** 2 + root["height"] ** 2) ** 0.5)
    normalized = (((sx - tx) ** 2 + (sy - ty) ** 2) ** 0.5) / diagonal
    score = max(0.0, min(1.0, 1.0 - (normalized / 0.58)))
    return normalized, score


def _slot_records_by_slot(measurement: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes = ((measurement.get("examples") or {}).get("nodes") or [])
    return {node.get("slot"): node for node in nodes if node.get("slot")}


def _root_rect_from_measurement(measurement: dict[str, Any]) -> dict[str, float] | None:
    facts = measurement.get("geometryFacts") or {}
    root = (facts.get("root") or {}).get("clipped") or (facts.get("root") or {}).get("raw")
    return _record_rect({"rect": root}) if root else None


def _spatial_relation(source_record: dict[str, Any], target_record: dict[str, Any], root_rect: dict[str, float]) -> dict[str, Any]:
    source = _record_rect(source_record)
    target = _record_rect(target_record)
    if not source or not target or source["area"] <= 4 or target["area"] <= 4:
        return {
            "visible": False,
            "sideDocked": False,
            "bandDocked": False,
            "near": False,
            "normalizedDistance": 1.0,
            "distanceScore": 0.0,
            "horizontalGap": 1.0,
            "verticalGap": 1.0,
            "crossAxisOverlap": 0.0,
        }

    horizontal_gap = _axis_gap(source, target, "x") / max(1.0, root_rect["width"])
    vertical_gap = _axis_gap(source, target, "y") / max(1.0, root_rect["height"])
    x_overlap = _overlap_ratio(source, target, "x")
    y_overlap = _overlap_ratio(source, target, "y")
    normalized_distance, distance_score = _center_distance_score(source, target, root_rect)
    side_docked = horizontal_gap <= 0.055 and y_overlap >= 0.22
    band_docked = vertical_gap <= 0.055 and x_overlap >= 0.30
    near = normalized_distance <= 0.58

    return {
        "visible": True,
        "sideDocked": side_docked,
        "bandDocked": band_docked,
        "near": near,
        "normalizedDistance": round(normalized_distance, 4),
        "distanceScore": round(distance_score, 4),
        "horizontalGap": round(horizontal_gap, 4),
        "verticalGap": round(vertical_gap, 4),
        "crossAxisOverlap": round(max(x_overlap, y_overlap), 4),
    }


def _score_spatial_expectation(expectation: str, relation: dict[str, Any], *, affordances: list[str]) -> int:
    if not relation.get("visible"):
        return 0

    side = bool(relation.get("sideDocked"))
    band = bool(relation.get("bandDocked"))
    near = bool(relation.get("near"))
    distance_score = float(relation.get("distanceScore", 0.0) or 0.0)

    if expectation == "selector-adjacent":
        if "rail" in affordances:
            if side:
                return 100
            if band:
                return 66
            if near:
                return max(44, round(distance_score * 64))
            return 28
        if side or band:
            return 94
        if near:
            return max(50, round(distance_score * 76))
        return 32

    if expectation == "reflection-adjacent":
        if side:
            return 100
        if band:
            return 76
        if near:
            return max(48, round(distance_score * 72))
        return 30

    if expectation == "control-adjacent":
        if side or band:
            return 100
        if near:
            return max(60, round(distance_score * 82))
        return 42

    if expectation == "persistent-visible":
        if side or band:
            return 100
        if near:
            return max(66, round(distance_score * 86))
        return 52

    if expectation == "proof-visible":
        if side or band:
            return 94
        if near:
            return max(58, round(distance_score * 78))
        return 46

    if expectation == "consumer-visible":
        if side or band:
            return 88
        if near:
            return max(54, round(distance_score * 72))
        return 44

    return 50 if relation.get("visible") else 0


def _score_focus_growth_contract(hierarchy: dict[str, Any], measurement: dict[str, Any]) -> tuple[int, str]:
    facts = measurement.get("geometryFacts") or {}
    focus_share = float(facts.get("focusShare", 0.0) or 0.0)
    desired = float(facts.get("desiredFocusShare", hierarchy.get("desiredFocusShare", 0.5)) or 0.5)
    min_share = float(facts.get("minFocusShare", hierarchy.get("minFocusShare", max(0.0, desired - 0.14))) or 0.0)
    focus_node = next((node for node in hierarchy.get("nodes", []) if node["slot"] == hierarchy["focusSlot"]), {})
    growth = _as_list((focus_node.get("semantics") or {}).get("growth"))
    strictness = 1.0
    if any(item in {"large-dense-grid", "large-output-stream", "large-text-surface"} for item in growth):
        strictness = 1.25
    deviation = abs(focus_share - desired) * strictness
    score = 100 - round(min(1.0, deviation / 0.22) * 46)
    if focus_share < min_share:
        score -= 24
    score = max(0, min(100, score))
    return score, f"focus {hierarchy['focusSlot']} is {focus_share:.0%} against contract target {desired:.0%}"


def _role_expectation_for_slot(hierarchy: dict[str, Any], slot: str) -> str:
    node = _node_by_slot(hierarchy).get(slot, {})
    role = node.get("role", "support")
    if role in {"navigation", "collection"}:
        return "selector-adjacent"
    if role in {"detail", "inspector"}:
        return "reflection-adjacent"
    if role == "status":
        return "persistent-visible"
    if role == "evidence":
        return "proof-visible"
    if role == "command":
        return "control-adjacent"
    return "consumer-visible"


def _score_presentation_set_contract(
    hierarchy: dict[str, Any],
    pressure: dict[str, Any],
    records: dict[str, dict[str, Any]],
    root_rect: dict[str, float] | None,
    focus_record: dict[str, Any] | None,
) -> tuple[int, str, dict[str, Any]]:
    required_slots = list(pressure.get("requiredSlots") or [])
    if not required_slots:
        return 100, f"presentation set {pressure['source']} has no required slots", {"missingSlots": []}
    missing_slots = [slot for slot in required_slots if slot not in records]
    if missing_slots or not root_rect or not focus_record:
        score = 0 if missing_slots else 28
        return (
            score,
            f"presentation set {pressure['source']} missing required co-present slot(s): {', '.join(missing_slots) or 'root/focus geometry'}",
            {"missingSlots": missing_slots},
        )

    relation_scores: list[int] = []
    relation_details: list[dict[str, Any]] = []
    for slot in required_slots:
        if slot == hierarchy["focusSlot"]:
            relation_scores.append(100)
            relation_details.append({"slot": slot, "score": 100, "relation": {"self": True}})
            continue
        record = records.get(slot)
        if not record:
            relation_scores.append(0)
            relation_details.append({"slot": slot, "score": 0, "relation": {"visible": False}})
            continue
        relation = _spatial_relation(record, focus_record, root_rect)
        expectation = _role_expectation_for_slot(hierarchy, slot)
        affordances = _as_list((_node_by_slot(hierarchy).get(slot, {}).get("semantics") or {}).get("layoutAffordance"))
        relation_score = _score_spatial_expectation(expectation, relation, affordances=affordances)
        relation_scores.append(relation_score)
        relation_details.append({"slot": slot, "score": relation_score, "expectation": expectation, "relation": relation})

    score = round(sum(relation_scores) / max(1, len(relation_scores)))
    weak = [item["slot"] for item in relation_details if item["score"] < 68]
    if weak:
        reason = f"presentation set {pressure['source']} has weak co-presence for {', '.join(weak)}"
    else:
        reason = f"presentation set {pressure['source']} keeps required regions co-present"
    return int(score), reason, {"missingSlots": missing_slots, "relations": relation_details}


def _record_area_share(record: dict[str, Any] | None, root_rect: dict[str, float] | None) -> float:
    rect = _record_rect(record)
    if not rect or not root_rect or root_rect.get("area", 0) <= 0:
        return 0.0
    return max(0.0, min(1.0, rect["area"] / max(1.0, root_rect["area"])))


def _affordance_expectation_for_node(node: dict[str, Any]) -> str:
    semantics = node.get("semantics") or {}
    affordances = set(_as_list(semantics.get("layoutAffordance")))
    growth = set(_as_list(semantics.get("growth")))
    role = node.get("role", "support")
    if "dominant-surface" in affordances or role == "focus":
        if "large-output-stream" in growth:
            return "dominant-output"
        if "large-dense-grid" in growth:
            return "dominant-dense-grid"
        if "large-spatial-surface" in growth:
            return "dominant-spatial-surface"
        return "dominant-surface"
    if "command-rail" in affordances:
        return "command-rail"
    if "rail" in affordances:
        return "selection-rail"
    if "support-collection" in affordances:
        return "support-collection"
    if "inspector" in affordances:
        return "inspector-dock"
    if "status-rail" in affordances:
        return "persistent-status-strip"
    if "proof-region" in affordances:
        return "secondary-proof-dock"
    return "visible-support"


def semantic_affordance_expectations(hierarchy: dict[str, Any]) -> list[dict[str, Any]]:
    focus_slot = hierarchy["focusSlot"]
    expectations: list[dict[str, Any]] = []
    for node in hierarchy.get("nodes", []):
        semantics = node.get("semantics") or {}
        affordances = _as_list(semantics.get("layoutAffordance"))
        growth = _as_list(semantics.get("growth"))
        if not affordances and not growth:
            continue
        expectation = _affordance_expectation_for_node(node)
        hard = node.get("slot") == focus_slot or _is_hard_semantic_requirement(node)
        if expectation in {"dominant-output", "dominant-dense-grid", "dominant-spatial-surface"}:
            hard = True
        expectations.append(
            {
                "slot": node["slot"],
                "role": node.get("role", "support"),
                "expectation": expectation,
                "affordances": affordances,
                "growth": growth,
                "hard": hard,
                "weight": 18 if node.get("slot") == focus_slot else (11 if hard else 7),
            }
        )
    return expectations


def _score_affordance_realization(
    hierarchy: dict[str, Any],
    expectation: dict[str, Any],
    records: dict[str, dict[str, Any]],
    root_rect: dict[str, float] | None,
    focus_record: dict[str, Any] | None,
    measurement: dict[str, Any],
) -> tuple[int, str, dict[str, Any]]:
    slot = expectation["slot"]
    record = records.get(slot)
    if not root_rect or not record:
        return 0, f"{slot} declared {expectation['expectation']} but was not measured as visible", {"visible": False}

    area_share = _record_area_share(record, root_rect)
    target = expectation["expectation"]
    relation: dict[str, Any] = {}
    if slot == hierarchy["focusSlot"]:
        facts = measurement.get("geometryFacts") or {}
        focus_share = float(facts.get("focusShare", area_share) or area_share)
        desired = float(facts.get("desiredFocusShare", hierarchy.get("desiredFocusShare", 0.5)) or 0.5)
        min_share = float(facts.get("minFocusShare", hierarchy.get("minFocusShare", max(0.0, desired - 0.14))) or 0.0)
        miss = max(0.0, desired - focus_share)
        if target in {"dominant-output", "dominant-dense-grid"}:
            tolerance = 0.045
        elif target == "dominant-spatial-surface":
            tolerance = 0.055
        else:
            tolerance = 0.075
        score = 100 - round(min(1.0, miss / max(0.001, tolerance * 2.6)) * 50)
        if focus_share < min_share:
            score -= 28
        score = max(0, min(100, score))
        return (
            int(score),
            f"{slot} realizes {target} at {focus_share:.0%} against target {desired:.0%}",
            {"areaShare": round(focus_share, 4), "desiredShare": round(desired, 4), "minShare": round(min_share, 4)},
        )

    if not focus_record:
        return 24, f"{slot} declared {target} but focus geometry was missing", {"areaShare": round(area_share, 4)}

    relation = _spatial_relation(record, focus_record, root_rect)
    side = bool(relation.get("sideDocked"))
    band = bool(relation.get("bandDocked"))
    near = bool(relation.get("near"))

    if target == "command-rail":
        base = 100 if band else 94 if side else 74 if near else 40
        if area_share > 0.18:
            base -= 12
    elif target == "selection-rail":
        base = 100 if side else 78 if band else 60 if near else 32
        if area_share > 0.24:
            base -= 10
    elif target == "support-collection":
        base = 94 if side or band else 72 if near else 38
        if area_share > 0.30:
            base -= 8
    elif target == "inspector-dock":
        base = 100 if side else 82 if band else 62 if near else 34
        if area_share > 0.26:
            base -= 10
    elif target == "persistent-status-strip":
        base = 100 if band else 88 if side else 66 if near else 38
        if area_share > 0.16:
            base -= 16
    elif target == "secondary-proof-dock":
        base = 92 if side or band else 70 if near else 42
        if area_share > 0.22:
            base -= 12
    else:
        base = 80 if near or side or band else 48

    score = max(0, min(100, int(base)))
    relation_words: list[str] = []
    if side:
        relation_words.append("side-docked")
    if band:
        relation_words.append("band-docked")
    if near:
        relation_words.append("near")
    if not relation_words:
        relation_words.append("visible but weakly placed")
    return (
        score,
        f"{slot} realizes {target} as {', '.join(relation_words)}",
        {"areaShare": round(area_share, 4), "relation": relation},
    )


def semantic_affordance_realization_fit(hierarchy: dict[str, Any], measurement: dict[str, Any]) -> dict[str, Any]:
    expectations = semantic_affordance_expectations(hierarchy)
    root_rect = _root_rect_from_measurement(measurement)
    records = _slot_records_by_slot(measurement)
    focus_record = records.get(hierarchy["focusSlot"])
    evaluated: list[dict[str, Any]] = []
    weighted_total = 0.0
    weight_total = 0.0

    for expectation in expectations:
        score, reason, details = _score_affordance_realization(
            hierarchy,
            expectation,
            records,
            root_rect,
            focus_record,
            measurement,
        )
        weight = float(expectation.get("weight", 1) or 1)
        weighted_total += score * weight
        weight_total += weight
        evaluated.append({**expectation, "score": int(score), "met": score >= 74, "reason": reason, "details": details})

    raw_score = round(weighted_total / weight_total) if weight_total else 0
    hard_misses = [item for item in evaluated if item.get("hard") and item["score"] < 74]
    missed = [item for item in evaluated if item["score"] < 68]
    score = raw_score
    limits: list[str] = []
    if any(item["score"] < 56 for item in hard_misses):
        score = min(score, 58)
        limits.append("severe affordance miss capped fit")
    elif hard_misses:
        score = min(score, 76)
        limits.append("hard affordance miss capped fit")

    if score >= 86 and not hard_misses:
        state = "strongAffordanceFit"
    elif score >= 72 and not any(item["score"] < 56 for item in hard_misses):
        state = "usableAffordanceFit"
    elif score >= 56:
        state = "weakAffordanceFit"
    else:
        state = "affordanceRisk"

    positives = [item["reason"] for item in evaluated if item["score"] >= 84]
    risk_reasons = [item["reason"] for item in sorted(missed, key=lambda item: item["score"])[:4]]
    hard_risk_reasons = [item["reason"] for item in sorted(hard_misses, key=lambda item: item["score"])[:4]]
    return {
        "score": int(score),
        "rawScore": int(raw_score),
        "state": state,
        "expectationCount": len(evaluated),
        "expectations": evaluated,
        "missedAffordanceCount": len(missed),
        "hardMissCount": len(hard_misses),
        "positiveReasons": positives[:4],
        "riskReasons": risk_reasons,
        "hardRiskReasons": hard_risk_reasons,
        "limits": limits,
        "note": "Affordance realization is inferred from generic MCEL tags such as rail, inspector, status strip, proof region, and dominant owned surface; it guides FLOG ranking without declaring a perfect layout.",
    }


def semantic_contract_fit(hierarchy: dict[str, Any], measurement: dict[str, Any]) -> dict[str, Any]:
    pressures = semantic_layout_pressures(hierarchy)
    root_rect = _root_rect_from_measurement(measurement)
    records = _slot_records_by_slot(measurement)
    focus_slot = hierarchy["focusSlot"]
    focus_record = records.get(focus_slot)

    evaluated: list[dict[str, Any]] = []
    weighted_total = 0.0
    weight_total = 0.0

    for pressure in pressures:
        weight = float(pressure.get("weight", 1) or 1)
        kind = pressure["kind"]
        if kind == "focus-growth":
            score, reason = _score_focus_growth_contract(hierarchy, measurement)
            relation = {}
        elif kind == "presentation-set-co-presence":
            score, reason, relation = _score_presentation_set_contract(
                hierarchy,
                pressure,
                records,
                root_rect,
                focus_record,
            )
        else:
            if not root_rect or not focus_record:
                score = 0
                relation = {}
                reason = f"{pressure['source']} cannot be spatially checked because root/focus geometry is missing"
            else:
                source_record = records.get(pressure["source"])
                target_record = records.get(pressure.get("target") or focus_slot) or focus_record
                if not source_record or not target_record:
                    score = 0
                    relation = {}
                    reason = f"{pressure['source']} → {pressure.get('target', focus_slot)} is missing from measured nodes"
                else:
                    relation = _spatial_relation(source_record, target_record, root_rect)
                    score = _score_spatial_expectation(
                        pressure["expectation"],
                        relation,
                        affordances=list(pressure.get("affordances") or []),
                    )
                    relation_words: list[str] = []
                    if relation.get("sideDocked"):
                        relation_words.append("side-docked")
                    if relation.get("bandDocked"):
                        relation_words.append("band-docked")
                    if relation.get("near"):
                        relation_words.append("near")
                    if not relation_words:
                        relation_words.append("visible but weakly related" if relation.get("visible") else "not visible")
                    requirement = "hard" if pressure.get("hard") else "soft"
                    reason = f"{requirement} {pressure['source']} {pressure['primitive']} {pressure['target']} is {', '.join(relation_words)}"

        weighted_total += score * weight
        weight_total += weight
        evaluated.append(
            {
                **pressure,
                "score": int(score),
                "met": score >= 72,
                "reason": reason,
                "relation": relation,
            }
        )

    raw_score = round(weighted_total / weight_total) if weight_total else 0
    hard_risks = [item for item in evaluated if item.get("hard") and item["score"] < 72]
    severe_hard_risks = [item for item in hard_risks if item["score"] < 56]
    soft_risks = [item for item in evaluated if not item.get("hard") and item["score"] < 68]
    contract_limits: list[str] = []
    score = raw_score
    if severe_hard_risks:
        score = min(score, 62)
        contract_limits.append("severe hard contract risk capped contract fit")
    elif hard_risks:
        score = min(score, 78)
        contract_limits.append("hard contract risk capped contract fit")

    confidence = min(1.0, ((len(pressures) + len(semantic_presentation_sets(hierarchy))) / 12.0) if pressures else 0.0)

    risks = [item for item in evaluated if item["score"] < 68]
    positives = [item for item in evaluated if item["score"] >= 82]
    presentation_reasons = [
        item["reason"]
        for item in evaluated
        if item["kind"] == "presentation-set-co-presence" and item["score"] >= 72
    ]

    if confidence < 0.45:
        state = "geometryOnly"
    elif score >= 86 and len(hard_risks) == 0 and len(risks) <= 1:
        state = "strongContractFit"
    elif score >= 72 and len(severe_hard_risks) == 0:
        state = "usableContractFit"
    elif score >= 56:
        state = "weakContractFit"
    else:
        state = "contractRisk"

    return {
        "score": int(score),
        "rawScore": int(raw_score),
        "state": state,
        "confidence": round(confidence, 3),
        "pressureCount": len(evaluated),
        "pressures": evaluated,
        "hardRiskCount": len(hard_risks),
        "softRiskCount": len(soft_risks),
        "hardRiskReasons": [item["reason"] for item in sorted(hard_risks, key=lambda item: item["score"])[:4]],
        "softRiskReasons": [item["reason"] for item in sorted(soft_risks, key=lambda item: item["score"])[:4]],
        "presentationSetReasons": presentation_reasons[:4],
        "contractLimits": contract_limits,
        "positiveReasons": [item["reason"] for item in positives[:4]],
        "riskReasons": [item["reason"] for item in sorted(risks, key=lambda item: item["score"])[:4]],
        "note": "Contract fit is inferred from generic MCEL primitives, phase co-presence, and hard/soft constraints; it is evidence for FLOG ranking, not a claim of perfect inference.",
    }



def apply_semantic_contract_fit(hierarchy: dict[str, Any], measurement: dict[str, Any]) -> dict[str, Any]:
    fit = semantic_contract_fit(hierarchy, measurement)
    affordance_fit = semantic_affordance_realization_fit(hierarchy, measurement)
    classification = measurement.setdefault("classification", {})
    geometry_score = int(classification.get("score", 0) or 0)
    selection_score = round((geometry_score * 0.54) + (fit["score"] * 0.26) + (affordance_fit["score"] * 0.20))

    original_status = str(classification.get("status") or "fail")
    warnings = list(classification.get("warnings") or [])
    hard_risk_count = int(fit.get("hardRiskCount", 0) or 0)
    hard_affordance_miss_count = int(affordance_fit.get("hardMissCount", 0) or 0)
    if original_status == "fail":
        status = "fail"
    elif (hard_risk_count or hard_affordance_miss_count) and selection_score >= 82:
        status = "watch"
    elif selection_score >= 82 and len(warnings) <= 1 and fit["score"] >= 72 and affordance_fit["score"] >= 72:
        status = "pass"
    elif selection_score >= 64:
        status = "watch"
    else:
        status = "fail"

    classification["geometryScore"] = geometry_score
    classification["contractFitScore"] = fit["score"]
    classification["contractFitRawScore"] = fit.get("rawScore", fit["score"])
    classification["contractFitState"] = fit["state"]
    classification["hardContractRiskCount"] = hard_risk_count
    classification["softContractRiskCount"] = int(fit.get("softRiskCount", 0) or 0)
    classification["affordanceFitScore"] = affordance_fit["score"]
    classification["affordanceFitRawScore"] = affordance_fit.get("rawScore", affordance_fit["score"])
    classification["affordanceFitState"] = affordance_fit["state"]
    classification["hardAffordanceMissCount"] = hard_affordance_miss_count
    classification["missedAffordanceCount"] = int(affordance_fit.get("missedAffordanceCount", 0) or 0)
    classification["selectionScore"] = selection_score
    classification["score"] = selection_score
    classification["status"] = status

    positive_reasons = classification.setdefault("positiveReasons", [])
    failure_reasons = classification.setdefault("failureReasons", [])
    review_notes = classification.setdefault("reviewNotes", [])

    if affordance_fit["positiveReasons"]:
        positive_reasons.insert(0, f"generic affordance fit is {affordance_fit['score']}%: {affordance_fit['positiveReasons'][0]}")
    if fit["positiveReasons"]:
        positive_reasons.insert(0, f"generic contract fit is {fit['score']}%: {fit['positiveReasons'][0]}")
    if fit.get("presentationSetReasons"):
        positive_reasons.insert(0, f"phase co-presence: {fit['presentationSetReasons'][0]}")
    if affordance_fit.get("hardRiskReasons"):
        review_notes.insert(0, f"hard affordance review: {affordance_fit['hardRiskReasons'][0]}")
    if fit.get("hardRiskReasons"):
        review_notes.insert(0, f"hard contract review: {fit['hardRiskReasons'][0]}")
    elif fit["riskReasons"]:
        review_notes.insert(0, f"contract-fit review: {fit['riskReasons'][0]}")
    if affordance_fit.get("riskReasons") and not affordance_fit.get("hardRiskReasons"):
        review_notes.insert(0, f"affordance-fit review: {affordance_fit['riskReasons'][0]}")
    if fit.get("contractLimits"):
        review_notes.insert(0, "; ".join(fit["contractLimits"]))
    if affordance_fit.get("limits"):
        review_notes.insert(0, "; ".join(affordance_fit["limits"]))
    if fit["state"] in {"weakContractFit", "contractRisk"} and original_status != "fail":
        failure_reasons.append(f"generic contract fit is only {fit['score']}%; layout preserves rectangles better than behavior relationships")
    if affordance_fit["state"] in {"weakAffordanceFit", "affordanceRisk"} and original_status != "fail":
        failure_reasons.append(f"generic affordance fit is only {affordance_fit['score']}%; tagged spatial affordances are not clearly realized")
    if hard_risk_count and original_status != "fail":
        failure_reasons.append("hard semantic contract pressure needs review before promotion")
    if hard_affordance_miss_count and original_status != "fail":
        failure_reasons.append("hard affordance realization needs review before promotion")
    review_notes.append("Contract and affordance fit are inferred from generic semantic tags, phase sets, hard/soft constraints, and realized geometry; FLOG still keeps multiple candidates for human review.")

    measurement["contractFit"] = fit
    measurement["affordanceFit"] = affordance_fit
    return measurement




def synthetic_hierarchies() -> list[dict[str, Any]]:
    """Return seeded MCEL-good hierarchies with explicit layout roles.

    These fixtures intentionally model the *target* MCEL source quality rather
    than today's under-described live app DOM.  Each hierarchy declares:

    * a focus slot and desired space share;
    * required companions that must remain visible with the focus;
    * nearby companions that should remain spatially close to the focus;
    * deferable slots that may collapse or move later;
    * layout families that are preferred or dangerous for the role contract;
    * generic semantic primitives such as controls, selects, reflects, confirms,
      proves, growth, owned scroll, and persistence.

    That lets FLOG test layout inference against composable app semantics instead
    of pretending that area alone is enough or importing high-level app archetypes.
    """

    def item(kind: str, label: str, *, role: str = "content") -> dict[str, str]:
        return {"kind": kind, "label": label, "role": role}

    def role_min_visible_share(role: str, priority: str) -> float:
        # This is a visibility floor, not a desired space share.  The previous
        # seed used one 6% floor for every companion, which made status strips
        # and compact command bars look "missing" even when the browser proved
        # they were visible.  Keep focus/primary context meaningful while
        # allowing status/evidence to be compact default companions.
        if role == "focus":
            return 0.06
        if role == "status":
            return 0.012
        if role == "evidence":
            return 0.016
        if role == "command":
            return 0.026 if priority == "primary" else 0.022
        if role in {"navigation", "collection"}:
            return 0.03 if priority == "primary" else 0.024
        if role in {"detail", "inspector"}:
            return 0.026
        return 0.022

    def node(
        slot: str,
        kind: str,
        title: str,
        *,
        role: str,
        priority: str,
        weight: int,
        connects: list[str],
        items: list[dict[str, str]],
        scroll: str = "allowed",
        visibility: str = "required",
        proximity: str = "loose",
        min_visible_share: float | None = None,
    ) -> dict[str, Any]:
        return {
            "slot": slot,
            "kind": kind,
            "title": title,
            "role": role,
            "priority": priority,
            "visibility": visibility,
            "proximity": proximity,
            "connects": connects,
            "weight": weight,
            "scroll": scroll,
            "minVisibleShare": role_min_visible_share(role, priority) if min_visible_share is None else min_visible_share,
            "items": items,
        }

    def hierarchy(
        *,
        id: str,
        title: str,
        description: str,
        source_app: str,
        root_concern: str,
        focus_slot: str,
        desired_focus_share: float,
        min_focus_share: float,
        max_focus_share: float,
        required_companions: list[str],
        nearby_companions: list[str],
        deferable_slots: list[str],
        forbidden_default_hidden: list[str],
        preferred_families: list[str],
        dangerous_families: dict[str, str],
        nodes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        nodes = enrich_nodes_with_semantic_primitives(nodes, focus_slot)
        slots = {entry["slot"] for entry in nodes}
        missing = [slot for slot in [focus_slot, *required_companions, *nearby_companions, *deferable_slots] if slot not in slots]
        if missing:
            raise ValueError(f"Hierarchy {id} references missing slot(s): {missing}")
        return {
            "id": id,
            "title": title,
            "description": description,
            "sourceApp": source_app,
            "rootConcern": root_concern,
            "focusSlot": focus_slot,
            "desiredFocusShare": desired_focus_share,
            "minFocusShare": min_focus_share,
            "maxFocusShare": max_focus_share,
            "roleContract": {
                "focus": {
                    "slot": focus_slot,
                    "desiredShare": desired_focus_share,
                    "minShare": min_focus_share,
                    "maxShare": max_focus_share,
                },
                "requiredCompanions": required_companions,
                "nearbyCompanions": nearby_companions,
                "deferableSlots": deferable_slots,
                "forbiddenDefaultHidden": forbidden_default_hidden,
                "preferredFamilies": preferred_families,
                "dangerousFamilies": dangerous_families,
            },
            "nodes": nodes,
        }

    return [
        hierarchy(
            id="operator-control-surface",
            title="Operator Control Surface",
            description="Command-heavy app with records, validation, jobs, and evidence.",
            source_app="conductor",
            root_concern="operations.control",
            focus_slot="workspace",
            desired_focus_share=0.42,
            min_focus_share=0.34,
            max_focus_share=0.62,
            required_companions=["command", "status"],
            nearby_companions=["evidence", "jobs"],
            deferable_slots=["detail"],
            forbidden_default_hidden=["command", "status", "workspace"],
            preferred_families=["split-pane", "source-order-stacked", "sectioned-sidebar"],
            dangerous_families={
                "bounded-drawer": "primary commands and status cannot become drawer-only",
                "dashboard-grid": "operator authority should not become a flat peer dashboard",
            },
            nodes=[
                node("command", "authority-command", "Command", role="command", priority="primary", visibility="required", proximity="near", weight=3, scroll="no", connects=["workspace", "status", "evidence", "jobs"], items=[
                    item("button", "Plan"), item("button", "Apply"), item("button", "Validate"), item("control", "Target"), item("control", "Mode"),
                ]),
                node("workspace", "primary-workspace", "Record Map", role="focus", priority="focus", visibility="required", proximity="self", weight=7, connects=["detail", "status"], items=[
                    item("surface", "Authority map: pending, valid, and blocked records grouped by action state", role="workspace-canvas"),
                    item("collection", "Record row A · target=prod · safe"), item("collection", "Record row B · target=staging · warning"), item("collection", "Record row C · mutation preview ready"),
                    item("collection", "Record row D · dependency blocked"), item("collection", "Record row E · audit required"), item("collection", "Record row F · complete"),
                    item("status", "Selection summary: 3 records selected, 1 requires evidence"), item("text", "Focus occupancy is intentional: rows, selection state, and mutation preview all live inside the primary work surface."),
                ]),
                node("detail", "inspector-detail", "Selected Record", role="detail", priority="secondary", visibility="deferable", proximity="loose", weight=3, connects=["workspace", "evidence"], items=[
                    item("text", "Selected record summary"), item("control", "TTL"), item("control", "Value"), item("button", "Preview change"),
                ]),
                node("status", "status", "Status", role="status", priority="primary", visibility="required", proximity="near", weight=2, scroll="no", connects=["command", "workspace"], items=[
                    item("status", "Ready"), item("status", "2 warnings"),
                ]),
                node("jobs", "collection-jobs", "Jobs", role="collection", priority="secondary", visibility="companion", proximity="near", weight=2, connects=["status", "evidence"], items=[
                    item("collection", "Queued apply"), item("collection", "Last validation"), item("collection", "Audit receipt"),
                ]),
                node("evidence", "evidence", "Evidence", role="evidence", priority="secondary", visibility="companion", proximity="near", weight=2, connects=["command", "status"], items=[
                    item("evidence", "Receipt: planned mutation would touch 3 records."), item("evidence", "Geometry proof should remain visible when failures exist."),
                ]),
            ],
        ),
        hierarchy(
            id="document-workbench",
            title="Document Workbench",
            description="Editor-first app with outline, formatting, AI notes, and source evidence.",
            source_app="document",
            root_concern="document.authoring",
            focus_slot="editor",
            desired_focus_share=0.58,
            min_focus_share=0.48,
            max_focus_share=0.76,
            required_companions=["toolbar", "status"],
            nearby_companions=["outline", "inspector"],
            deferable_slots=["evidence"],
            forbidden_default_hidden=["toolbar", "editor", "status"],
            preferred_families=["sectioned-sidebar", "split-pane", "focus-priority"],
            dangerous_families={
                "bounded-drawer": "document controls may be drawer-like later, but save/status cannot be hidden by default",
                "dashboard-grid": "the editor must not be flattened into a peer card grid",
            },
            nodes=[
                node("toolbar", "authority-command", "Toolbar", role="command", priority="primary", visibility="required", proximity="near", weight=2, scroll="no", connects=["editor", "status"], items=[
                    item("button", "Save"), item("button", "Export"), item("button", "Ask"), item("control", "Block"), item("control", "Style"),
                ]),
                node("outline", "navigation-outline", "Outline", role="navigation", priority="secondary", visibility="companion", proximity="near", weight=2, connects=["editor"], items=[
                    item("collection", "Introduction"), item("collection", "Model"), item("collection", "Results"), item("collection", "Appendix"),
                ]),
                node("editor", "primary-editor", "Page Editor", role="focus", priority="focus", visibility="required", proximity="self", weight=9, connects=["status", "evidence", "outline", "inspector"], items=[
                    item("surface", "Page surface with margins, active cursor, and selected paragraph", role="page-surface"),
                    item("text", "Block 1 · Heading and anchor metadata"), item("text", "Block 2 · Body paragraph with inline comment marker"), item("text", "Block 3 · Callout region tied to source evidence"),
                    item("status", "Selection strip: paragraph 2, words 44-78"), item("collection", "Comment affordance · unresolved style note"), item("collection", "Format context · H2 / body / quote"),
                    item("text", "The editor focus is no longer a blank canvas; useful interior structure must occupy enough of the focus region."),
                ]),
                node("inspector", "inspector-detail", "Inspector", role="detail", priority="secondary", visibility="companion", proximity="near", weight=3, connects=["editor", "evidence"], items=[
                    item("control", "Margins"), item("control", "Page size"), item("button", "Apply preset"), item("text", "AI notes and formatting metadata."),
                ]),
                node("status", "status", "Status", role="status", priority="primary", visibility="required", proximity="near", weight=1, scroll="no", connects=["toolbar", "editor"], items=[
                    item("status", "Draft saved"), item("status", "No validation errors"),
                ]),
                node("evidence", "evidence", "Source Evidence", role="evidence", priority="secondary", visibility="deferable", proximity="loose", weight=2, connects=["editor", "inspector"], items=[
                    item("evidence", "Serialization proof: generated wrappers are runtime-only."), item("evidence", "Layout proof: editor remains the focus."),
                ]),
            ],
        ),
        hierarchy(
            id="system-map-console",
            title="System Map Console",
            description="Graph-like app with a central map, subsystem tentacles, actions, alerts, and evidence.",
            source_app="ai-control",
            root_concern="systems.connected-map",
            focus_slot="map",
            desired_focus_share=0.50,
            min_focus_share=0.40,
            max_focus_share=0.70,
            required_companions=["command", "alerts"],
            nearby_companions=["subsystems", "detail"],
            deferable_slots=["evidence"],
            forbidden_default_hidden=["command", "map", "alerts"],
            preferred_families=["focus-priority", "sectioned-sidebar", "split-pane"],
            dangerous_families={
                "source-order-stacked": "system map loses relationship context when support regions are merely stacked",
                "dashboard-grid": "grid only works if dependency relationships remain visually encoded",
            },
            nodes=[
                node("command", "authority-command", "Global Actions", role="command", priority="primary", visibility="required", proximity="near", weight=2, scroll="no", connects=["map", "alerts", "evidence"], items=[
                    item("button", "Scan"), item("button", "Route"), item("button", "Repair"), item("control", "Scope"),
                ]),
                node("map", "primary-graph", "System Map", role="focus", priority="focus", visibility="required", proximity="self", weight=8, scroll="no", connects=["subsystems", "detail", "alerts"], items=[
                    item("surface", "Central orchestration node with live route labels", role="graph-center"),
                    item("surface", "Dependency edge: gateway → scheduler → worker pool", role="graph-edge"),
                    item("surface", "Connected subsystem tentacle A · healthy"), item("surface", "Connected subsystem tentacle B · degraded"), item("surface", "Connected subsystem tentacle C · pending repair"),
                    item("status", "Fault boundary: no red-zone overlap"), item("collection", "Selected edge: scheduler owns 2 downstream jobs"), item("text", "Map occupancy models real graph content instead of an empty focus card."),
                ]),
                node("subsystems", "collection-subsystems", "Subsystems", role="collection", priority="secondary", visibility="companion", proximity="near", weight=3, connects=["map", "detail"], items=[
                    item("collection", "Gateway"), item("collection", "Scheduler"), item("collection", "Worker pool"), item("collection", "Storage"),
                ]),
                node("detail", "inspector-detail", "Node Detail", role="detail", priority="secondary", visibility="companion", proximity="near", weight=3, connects=["map", "evidence"], items=[
                    item("status", "Latency 18 ms"), item("status", "2 dependent jobs"), item("button", "Open node"),
                ]),
                node("alerts", "status-alerts", "Alerts", role="status", priority="primary", visibility="required", proximity="near", weight=2, connects=["command", "map"], items=[
                    item("status", "Fault zone clear"), item("status", "One dependency degraded"),
                ]),
                node("evidence", "evidence", "Evidence", role="evidence", priority="secondary", visibility="deferable", proximity="loose", weight=2, connects=["command", "map", "detail"], items=[
                    item("evidence", "Graph relationship proof uses data-mc-connects."), item("evidence", "Central/spiral layout only allowed when relationships are explicit."),
                ]),
            ],
        ),
        hierarchy(
            id="calculator-workbench",
            title="Calculator Workbench",
            description="Calculator app with expression entry, keypad, graphing, history, and model explanation.",
            source_app="calculator",
            root_concern="calculator.solve",
            focus_slot="workspace",
            desired_focus_share=0.46,
            min_focus_share=0.34,
            max_focus_share=0.62,
            required_companions=["command", "detail", "status"],
            nearby_companions=["records"],
            deferable_slots=["evidence"],
            forbidden_default_hidden=["command", "workspace", "detail", "status"],
            preferred_families=["split-pane", "inspector", "sectioned-sidebar"],
            dangerous_families={
                "bounded-drawer": "keypad/parameters cannot become deferred drawer-only controls",
                "focus-priority": "the solve surface should not bury keypad and status",
            },
            nodes=[
                node("command", "authority-command", "Mode and Solve", role="command", priority="primary", visibility="required", proximity="near", weight=3, scroll="no", connects=["workspace", "status", "evidence"], items=[
                    item("button", "Basic"), item("button", "Scientific"), item("button", "Graph"), item("control", "Expression"), item("button", "Ask model"),
                ]),
                node("workspace", "primary-calculator", "Solve Surface", role="focus", priority="focus", visibility="required", proximity="self", weight=7, connects=["detail", "records", "status"], items=[
                    item("surface", "Expression display: sin(x) + 2x", role="expression-display"), item("surface", "Result display: roots, extrema, and units", role="result-display"),
                    item("surface", "Graphing canvas with axes and selected point", role="graph-surface"), item("collection", "History relation: previous expression feeds current graph"),
                    item("status", "Domain warning: x ∈ [-10, 10]"), item("text", "The focus is the live solving surface, while keypad and parameters remain required companions."),
                ]),
                node("detail", "inspector-detail", "Keypad and Parameters", role="detail", priority="primary", visibility="required", proximity="near", weight=4, connects=["workspace"], items=[
                    item("button", "7"), item("button", "8"), item("button", "9"), item("control", "X min"), item("control", "X max"),
                ]),
                node("records", "collection-history", "History", role="collection", priority="secondary", visibility="companion", proximity="near", weight=2, connects=["workspace", "evidence"], items=[
                    item("collection", "2 + 2 = 4"), item("collection", "sin(x) plotted"), item("collection", "unit conversion"),
                ]),
                node("status", "status", "Solve Status", role="status", priority="primary", visibility="required", proximity="near", weight=1, scroll="no", connects=["command", "workspace"], items=[
                    item("status", "Ready"), item("status", "No graph errors"),
                ]),
                node("evidence", "evidence", "Explanation Evidence", role="evidence", priority="secondary", visibility="deferable", proximity="loose", weight=2, connects=["workspace"], items=[
                    item("evidence", "Model explanation remains tied to the expression that produced it."), item("evidence", "History and graph controls are support surfaces."),
                ]),
            ],
        ),
        hierarchy(
            id="spreadsheet-workbook",
            title="Spreadsheet Workbook",
            description="Spreadsheet app with grid focus, formula bar, sheet tabs, inspector, charts, and save evidence.",
            source_app="spreadsheet",
            root_concern="spreadsheet.grid",
            focus_slot="grid",
            desired_focus_share=0.60,
            min_focus_share=0.48,
            max_focus_share=0.78,
            required_companions=["formula", "status"],
            nearby_companions=["tabs", "inspector"],
            deferable_slots=["evidence"],
            forbidden_default_hidden=["formula", "grid", "status"],
            preferred_families=["focus-priority", "split-pane", "sectioned-sidebar"],
            dangerous_families={
                "dashboard-grid": "a spreadsheet grid is not a peer dashboard",
                "bounded-drawer": "formula/status cannot become drawer-only",
            },
            nodes=[
                node("formula", "authority-command", "Formula Bar", role="command", priority="primary", visibility="required", proximity="near", weight=2, scroll="no", connects=["grid", "status"], items=[
                    item("control", "Cell"), item("control", "Formula"), item("button", "Commit"), item("button", "Recalculate"),
                ]),
                node("grid", "primary-grid", "Sheet Grid", role="focus", priority="focus", visibility="required", proximity="self", weight=10, connects=["formula", "tabs", "inspector", "status"], items=[
                    item("surface", "A1:H24 editable grid with frozen header row", role="grid-surface"), item("surface", "Selected cell range B4:D9 with fill handle", role="selection-region"),
                    item("collection", "Row group: revenue model"), item("collection", "Row group: expense model"), item("collection", "Inline chart preview tied to selected range"),
                    item("status", "Formula dependency: Sheet1!B4 references Models!C2"), item("text", "The grid owns most space, but useful grid internals must fill the focus area."),
                ]),
                node("tabs", "navigation-tabs", "Sheets", role="navigation", priority="secondary", visibility="companion", proximity="near", weight=2, connects=["grid"], items=[
                    item("collection", "Sheet1"), item("collection", "Models"), item("collection", "Charts"),
                ]),
                node("inspector", "inspector-detail", "Cell Inspector", role="detail", priority="secondary", visibility="companion", proximity="near", weight=3, connects=["grid", "evidence"], items=[
                    item("control", "Format"), item("control", "Validation"), item("button", "Create chart"),
                ]),
                node("status", "status", "Workbook Status", role="status", priority="primary", visibility="required", proximity="near", weight=1, scroll="no", connects=["formula", "grid"], items=[
                    item("status", "Saved"), item("status", "No formula errors"),
                ]),
                node("evidence", "evidence", "Recalc Evidence", role="evidence", priority="secondary", visibility="deferable", proximity="loose", weight=2, connects=["formula", "grid"], items=[
                    item("evidence", "Recalculation proof and file persistence trail."), item("evidence", "Import/export state remains runtime evidence."),
                ]),
            ],
        ),
        hierarchy(
            id="code-studio-workbench",
            title="Code Studio Workbench",
            description="Code editor app with source tree, editor, terminal/test output, SCM evidence, and AI inspector.",
            source_app="code-editor",
            root_concern="code.authoring",
            focus_slot="editor",
            desired_focus_share=0.56,
            min_focus_share=0.44,
            max_focus_share=0.74,
            required_companions=["command", "status"],
            nearby_companions=["records", "detail"],
            deferable_slots=["evidence"],
            forbidden_default_hidden=["command", "editor", "status"],
            preferred_families=["sectioned-sidebar", "split-pane", "focus-priority"],
            dangerous_families={
                "dashboard-grid": "source editor authority should not become a peer tile",
                "bounded-drawer": "test/status context cannot be drawer-only during edits",
            },
            nodes=[
                node("command", "authority-command", "Command Center", role="command", priority="primary", visibility="required", proximity="near", weight=2, scroll="no", connects=["editor", "status", "evidence"], items=[
                    item("button", "Validate"), item("button", "Serialize"), item("button", "Run tests"), item("control", "Branch"),
                ]),
                node("records", "navigation-outline", "File Map", role="navigation", priority="secondary", visibility="companion", proximity="near", weight=3, connects=["editor"], items=[
                    item("collection", "main_computer/"), item("collection", "tests/"), item("collection", "pretty_docs/"), item("collection", "runtime/"),
                ]),
                node("editor", "primary-editor", "Source Editor", role="focus", priority="focus", visibility="required", proximity="self", weight=9, connects=["records", "detail", "status", "evidence"], items=[
                    item("surface", "Tab strip: flog_layout_snapshot_smoke.py · tests/test_flog_layout_snapshot_smoke.py", role="tab-strip"),
                    item("surface", "Source editor: line numbers, active function, selection, and diagnostics gutter", role="source-editor"),
                    item("collection", "Diagnostic: companion proximity scoring needs explanation"), item("collection", "Test marker: report includes reasons"),
                    item("status", "Cursor: scoring block, column 18"), item("text", "The editor should get stable area while verification, SCM evidence, and AI inspection remain connected."),
                ]),
                node("detail", "inspector-detail", "AI and Test Inspector", role="detail", priority="secondary", visibility="companion", proximity="near", weight=3, connects=["editor", "evidence"], items=[
                    item("control", "Evidence filter"), item("button", "Ask AI"), item("surface", "Terminal/test output"),
                ]),
                node("status", "status", "Editor Status", role="status", priority="primary", visibility="required", proximity="near", weight=1, scroll="no", connects=["command", "editor"], items=[
                    item("status", "Route valid"), item("status", "Serialization clean"),
                ]),
                node("evidence", "evidence", "SCM Evidence", role="evidence", priority="secondary", visibility="deferable", proximity="loose", weight=2, connects=["command", "editor"], items=[
                    item("evidence", "SCM receipt proves source/runtime separation."), item("evidence", "Repair evidence stays runtime-owned."),
                ]),
            ],
        ),
        hierarchy(
            id="terminal-console-workbench",
            title="Terminal Console Workbench",
            description="Terminal app with command entry, live output focus, history, process status, and safety evidence.",
            source_app="terminal",
            root_concern="terminal.execution",
            focus_slot="terminal",
            desired_focus_share=0.62,
            min_focus_share=0.50,
            max_focus_share=0.82,
            required_companions=["command", "status"],
            nearby_companions=["records"],
            deferable_slots=["evidence", "detail"],
            forbidden_default_hidden=["command", "terminal", "status"],
            preferred_families=["focus-priority", "sectioned-sidebar", "split-pane"],
            dangerous_families={
                "dashboard-grid": "terminal output should not become one peer card among many",
                "bounded-drawer": "command entry and status cannot hide by default",
            },
            nodes=[
                node("command", "authority-command", "Command Entry", role="command", priority="primary", visibility="required", proximity="near", weight=2, scroll="no", connects=["terminal", "status"], items=[
                    item("control", "Working directory"), item("control", "Command"), item("button", "Run"), item("button", "Stop"),
                ]),
                node("terminal", "primary-terminal", "Terminal Output", role="focus", priority="focus", visibility="required", proximity="self", weight=10, connects=["command", "status", "evidence"], items=[
                    item("surface", "$ python -m pytest tests/test_flog_layout_snapshot_smoke.py", role="command-line"),
                    item("surface", "live stdout/stderr stream with last 24 lines", role="terminal-stream"),
                    item("collection", "PASSED test_render_trial_html_contains_generated_hierarchy_and_candidate"), item("collection", "PASSED test_write_reports_lists_best_candidate_and_pngs"),
                    item("status", "Exit code 0 · duration 1.42s"), item("text", "The terminal focus is a live proof surface with command, output, and result context."),
                ]),
                node("records", "collection-history", "Command History", role="collection", priority="secondary", visibility="companion", proximity="near", weight=2, connects=["command", "terminal"], items=[
                    item("collection", "pytest -q"), item("collection", "git status"), item("collection", "python main.py"),
                ]),
                node("detail", "inspector-detail", "Environment", role="detail", priority="secondary", visibility="deferable", proximity="loose", weight=2, connects=["terminal"], items=[
                    item("control", "Timeout"), item("control", "Env preset"), item("button", "Copy output"),
                ]),
                node("status", "status", "Process Status", role="status", priority="primary", visibility="required", proximity="near", weight=1, scroll="no", connects=["command", "terminal"], items=[
                    item("status", "Idle"), item("status", "Exit code 0"),
                ]),
                node("evidence", "evidence", "Safety Evidence", role="evidence", priority="secondary", visibility="deferable", proximity="loose", weight=2, connects=["command", "terminal"], items=[
                    item("evidence", "Command output is runtime evidence, not source."), item("evidence", "Dangerous actions require explicit user confirmation."),
                ]),
            ],
        ),
        hierarchy(
            id="file-explorer-workspace",
            title="File Explorer Workspace",
            description="File explorer app with tree navigation, file list focus, preview, actions, and selection evidence.",
            source_app="file-explorer",
            root_concern="files.navigation",
            focus_slot="workspace",
            desired_focus_share=0.48,
            min_focus_share=0.38,
            max_focus_share=0.65,
            required_companions=["command", "records", "status"],
            nearby_companions=["detail"],
            deferable_slots=["evidence"],
            forbidden_default_hidden=["command", "records", "workspace", "status"],
            preferred_families=["split-pane", "sectioned-sidebar", "inspector"],
            dangerous_families={
                "bounded-drawer": "tree and file list should not be separated by a hidden drawer",
                "source-order-stacked": "large file lists become too vertically fragmented when stacked",
            },
            nodes=[
                node("command", "authority-command", "File Actions", role="command", priority="primary", visibility="required", proximity="near", weight=2, scroll="no", connects=["records", "workspace", "status"], items=[
                    item("button", "Refresh"), item("button", "New folder"), item("button", "Open"), item("control", "Search"),
                ]),
                node("records", "navigation-outline", "Folder Tree", role="navigation", priority="primary", visibility="required", proximity="near", weight=3, connects=["workspace"], items=[
                    item("collection", "main_computer"), item("collection", "tests"), item("collection", "runtime"), item("collection", "pretty_docs"),
                ]),
                node("workspace", "primary-file-list", "File List", role="focus", priority="focus", visibility="required", proximity="self", weight=8, connects=["records", "detail", "status"], items=[
                    item("surface", "Path bar: main_computer/static/apps/", role="path-surface"),
                    item("collection", "conductor.html · selected · 42 KB"), item("collection", "mcel-scm.js · modified · 18 KB"), item("collection", "test_mcel_scm_layout_style.py · test · 9 KB"),
                    item("collection", "layout-snapshot-report.md · report · 14 KB"), item("collection", "runtime/reports/flog/ · generated proofs"),
                    item("status", "Selection summary: 4 files, preview available"), item("text", "Folder tree, file list, preview, and status need visible relationships for safe file actions."),
                ]),
                node("detail", "inspector-detail", "Preview", role="detail", priority="secondary", visibility="companion", proximity="near", weight=3, connects=["workspace", "evidence"], items=[
                    item("surface", "Selected file preview"), item("button", "Open in editor"), item("button", "Copy path"),
                ]),
                node("status", "status", "Selection Status", role="status", priority="primary", visibility="required", proximity="near", weight=1, scroll="no", connects=["workspace"], items=[
                    item("status", "4 files selected"),
                ]),
                node("evidence", "evidence", "Selection Evidence", role="evidence", priority="secondary", visibility="deferable", proximity="loose", weight=2, connects=["workspace", "detail"], items=[
                    item("evidence", "Selection and preview must remain connected."), item("evidence", "File operations need visible target evidence."),
                ]),
            ],
        ),
    ]


def parse_hierarchies(value: str, available: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {item["id"]: item for item in available}
    if value.strip().lower() == "all":
        return list(available)
    selected: list[dict[str, Any]] = []
    for raw in value.split(","):
        key = slugify(raw)
        if not key:
            continue
        if key not in by_id:
            raise ValueError(f"Unknown synthetic hierarchy: {raw!r}")
        selected.append(by_id[key])
    if not selected:
        raise ValueError("At least one hierarchy is required.")
    return selected


def node_markup(node: dict[str, Any], focus_slot: str) -> str:
    slot = node["slot"]
    focus = slot == focus_slot
    attrs = {
        "class": f"flog-node node-{slugify(slot)} kind-{slugify(node['kind'])} role-{slugify(node.get('role', 'support'))}",
        "data-mc": "region",
        "data-mc-slot": slot,
        "data-mc-kind": node["kind"],
        "data-mc-rank": node.get("priority", "secondary"),
        "data-mc-connects": " ".join(node.get("connects", [])),
        "data-mc-role": node.get("role", "support"),
        "data-mc-visibility": node.get("visibility", "required"),
        "data-mc-proximity": node.get("proximity", "loose"),
        "data-flog-slot": slot,
        "data-flog-role": node.get("role", "support"),
        "data-flog-priority": node.get("priority", "secondary"),
        "data-flog-visibility": node.get("visibility", "required"),
        "data-flog-proximity": node.get("proximity", "loose"),
        "data-flog-min-visible-share": str(node.get("minVisibleShare", 0.06)),
        "data-flog-weight": str(node.get("weight", 1)),
        "data-flog-scroll": node.get("scroll", "no"),
        "data-flog-focus": "true" if focus else "false",
    }
    semantics = node.get("semantics") or {}
    for semantic_key in (*SEMANTIC_RELATION_KEYS, *SEMANTIC_QUALITY_KEYS):
        values = _as_list(semantics.get(semantic_key))
        if values:
            attrs[_semantic_attr_name(semantic_key)] = " ".join(values)

    if focus:
        attrs["aria-label"] = f"Focus surface: {node['title']}"
    attr_text = " ".join(f'{name}="{html.escape(value, quote=True)}"' for name, value in attrs.items())
    parts = [f"<section {attr_text}>"]
    parts.append(f"<header class=\"node-header\"><h2>{html.escape(node['title'])}</h2><span>{html.escape(node['kind'])}</span></header>")
    parts.append("<div class=\"node-body\">")
    for index, item in enumerate(node.get("items", []), start=1):
        kind = item.get("kind", "text")
        role = item.get("role", "content")
        label = item.get("label", "")
        escaped = html.escape(label)
        escaped_slot = html.escape(slot)
        escaped_role = html.escape(role, quote=True)
        item_attrs = (
            f'data-flog-item="true" data-flog-item-kind="{html.escape(kind, quote=True)}" '
            f'data-flog-item-role="{escaped_role}" data-flog-item-index="{index}"'
        )
        if kind == "button":
            parts.append(f"<button data-mc=\"action\" data-mc-slot=\"{escaped_slot}.action\" {item_attrs}>{escaped}</button>")
        elif kind == "control":
            control_id = f"{slugify(slot)}-{slugify(label)}"
            parts.append(
                f"<label data-mc=\"control\" data-mc-slot=\"{escaped_slot}.control\" for=\"{control_id}\" {item_attrs}>"
                f"{escaped}<input id=\"{control_id}\" value=\"{escaped}\" /></label>"
            )
        elif kind == "collection":
            parts.append(f"<div class=\"item collection-item\" data-mc=\"collection-item\" data-mc-slot=\"{escaped_slot}.item\" {item_attrs}>{escaped}</div>")
        elif kind == "status":
            parts.append(f"<div class=\"item status-item\" data-mc=\"status\" data-mc-slot=\"{escaped_slot}.status\" {item_attrs}>{escaped}</div>")
        elif kind == "evidence":
            parts.append(f"<pre class=\"evidence-item\" data-mc=\"evidence\" data-mc-slot=\"{escaped_slot}.evidence\" {item_attrs}>{escaped}</pre>")
        elif kind == "surface":
            parts.append(f"<div class=\"surface-item\" data-mc=\"surface\" data-mc-slot=\"{escaped_slot}.surface\" {item_attrs}>{escaped}</div>")
        else:
            parts.append(f"<p class=\"text-item\" data-mc=\"text\" data-mc-slot=\"{escaped_slot}.text\" {item_attrs}>{escaped}</p>")
    parts.append("</div>")
    parts.append("</section>")
    return "\n".join(parts)


def render_trial_html(hierarchy: dict[str, Any], candidate: str, chrome: str) -> str:
    focus_slot = hierarchy["focusSlot"]
    nodes = "\n".join(node_markup(node, focus_slot) for node in hierarchy["nodes"])
    contract = hierarchy.get("roleContract", {})
    root_attrs = {
        "id": hierarchy["id"],
        "class": f"flog-root trial-{candidate}",
        "data-mc": "component",
        "data-mc-component": "SyntheticFlogHierarchy",
        "data-mc-kind": "control-surface",
        "data-mc-flow": "linear-hierarchical",
        "data-mc-source-app": hierarchy.get("sourceApp", ""),
        "data-mc-root-concern": hierarchy["rootConcern"],
        "data-flog-candidate": candidate,
        "data-flog-source-app": hierarchy.get("sourceApp", ""),
        "data-flog-focus-slot": focus_slot,
        "data-flog-desired-focus-share": str(hierarchy["desiredFocusShare"]),
        "data-flog-min-focus-share": str(hierarchy["minFocusShare"]),
        "data-flog-max-focus-share": str(hierarchy["maxFocusShare"]),
        "data-flog-min-useful-focus-occupancy": str(hierarchy.get("minUsefulFocusOccupancy", 0.30)),
        "data-flog-target-useful-focus-occupancy": str(hierarchy.get("targetUsefulFocusOccupancy", 0.56)),
        "data-flog-required-companions": " ".join(contract.get("requiredCompanions", [])),
        "data-flog-nearby-companions": " ".join(contract.get("nearbyCompanions", [])),
        "data-flog-deferable-slots": " ".join(contract.get("deferableSlots", [])),
        "data-flog-forbidden-default-hidden": " ".join(contract.get("forbiddenDefaultHidden", [])),
        "data-flog-preferred-families": " ".join(contract.get("preferredFamilies", [])),
        "data-flog-dangerous-families": " ".join((contract.get("dangerousFamilies") or {}).keys()),
    }
    root_attr_text = " ".join(f'{name}="{html.escape(str(value), quote=True)}"' for name, value in root_attrs.items())

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>FLOG synthetic trial - {html.escape(hierarchy['title'])} - {candidate}</title>
<style>
{TRIAL_CSS}
</style>
</head>
<body data-flog-chrome="{html.escape(chrome, quote=True)}" data-flog-trial="{html.escape(candidate, quote=True)}">
  <main class="flog-stage">
    <article {root_attr_text}>
      <div class="root-title" data-mc="heading" data-mc-slot="title">
        <h1>{html.escape(hierarchy['title'])}</h1>
        <p>{html.escape(hierarchy['description'])}</p>
      </div>
      {nodes}
    </article>
  </main>
</body>
</html>
"""


TRIAL_CSS = r"""
:root {
  color-scheme: light dark;
  --surface: #f7f8fb;
  --panel: #ffffff;
  --panel-2: #edf1f7;
  --ink: #152033;
  --muted: #586579;
  --border: #bac5d7;
  --focus: #d9edff;
  --authority: #fff2c4;
  --evidence: #eefce9;
  --gap: 14px;
}
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; font-family: system-ui, -apple-system, Segoe UI, sans-serif; background: #202838; color: var(--ink); }
body[data-flog-chrome*="glass"] {
  --surface: #071013;
  --panel: #101d24;
  --panel-2: #172b33;
  --ink: #edf8f8;
  --muted: #b8c9cd;
  --border: #3c5962;
  --focus: #102d41;
  --authority: #2f2610;
  --evidence: #12261c;
}
.flog-stage {
  min-height: 100vh;
  padding: 20px;
  display: grid;
  place-items: center;
}
.flog-root {
  width: min(1320px, calc(100vw - 40px));
  height: calc(100vh - 40px);
  min-height: 520px;
  overflow: hidden;
  border: 2px solid var(--border);
  border-radius: 18px;
  padding: var(--gap);
  background: var(--surface);
  box-shadow: 0 22px 70px rgba(0,0,0,.25);
}
.root-title {
  min-height: 0;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--panel-2);
  overflow: hidden;
}
.root-title h1 { margin: 0 0 3px 0; font-size: 18px; line-height: 1.1; }
.root-title p { margin: 0; color: var(--muted); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.flog-node {
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 10px;
  background: var(--panel);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.flog-node[data-flog-scroll="allowed"] .node-body { overflow: auto; }
.flog-node[data-flog-focus="true"] {
  background: var(--focus);
  border-width: 2px;
}
.kind-authority-command { background: var(--authority); }
.kind-evidence, .node-evidence { background: var(--evidence); }
.node-header { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.node-header h2 { margin: 0; font-size: 15px; line-height: 1.1; }
.node-header span { color: var(--muted); font-size: 11px; white-space: nowrap; }
.node-body {
  min-height: 0;
  display: grid;
  align-content: start;
  gap: 8px;
}
button, input, select, textarea, summary {
  min-height: 34px;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 7px 10px;
  background: rgba(255,255,255,.68);
  color: var(--ink);
}
label { display: grid; gap: 4px; font-size: 12px; color: var(--muted); }
[data-flog-item="true"] { min-width: 0; }
.item, .surface-item, .text-item, .evidence-item {
  min-height: 32px;
  margin: 0;
  padding: 8px 10px;
  border: 1px solid color-mix(in srgb, var(--border), transparent 35%);
  border-radius: 10px;
  background: rgba(255,255,255,.48);
}
.surface-item {
  min-height: 82px;
  display: grid;
  place-items: center;
  border-style: dashed;
  font-weight: 650;
  text-align: center;
}
.flog-node[data-flog-focus="true"] .node-body {
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
}
.flog-node[data-flog-focus="true"] .surface-item {
  min-height: 96px;
}
.flog-node[data-flog-focus="true"] .surface-item[data-flog-item-index="1"] {
  grid-column: 1 / -1;
  min-height: 128px;
}
.flog-node[data-flog-focus="true"] .text-item {
  background: color-mix(in srgb, var(--focus), white 50%);
}
.evidence-item { white-space: pre-wrap; font-size: 12px; }

.trial-source-order-stacked {
  display: flex;
  flex-direction: column;
  gap: var(--gap);
  overflow: auto;
}
.trial-source-order-stacked .flog-node[data-flog-focus="true"] {
  min-height: clamp(300px, 42vh, 430px);
}
.trial-source-order-stacked .flog-node[data-flog-priority="primary"]:not([data-flog-focus="true"]) {
  min-height: 68px;
}


.trial-split-pane {
  display: grid;
  gap: var(--gap);
  grid-template-columns: minmax(190px, 0.18fr) minmax(0, 1fr) minmax(220px, 0.22fr);
  grid-template-rows: minmax(48px, auto) minmax(62px, 0.075fr) minmax(0, 1fr) minmax(86px, 0.105fr);
  grid-template-areas:
    "title title title"
    "command command detail"
    "collection focus detail"
    "status focus evidence";
}
.trial-split-pane .root-title { grid-area: title; }
.trial-split-pane .node-command, .trial-split-pane .node-toolbar { grid-area: command; }
.trial-split-pane .node-workspace, .trial-split-pane .node-editor, .trial-split-pane .node-map { grid-area: focus; }
.trial-split-pane .node-records, .trial-split-pane .node-outline, .trial-split-pane .node-subsystems, .trial-split-pane .node-jobs { grid-area: collection; }
.trial-split-pane .node-detail, .trial-split-pane .node-inspector { grid-area: detail; }
.trial-split-pane .node-status, .trial-split-pane .node-alerts { grid-area: status; }
.trial-split-pane .node-evidence { grid-area: evidence; }

/* Rich seeded roles: allow future app slots to map by role rather than by today's slot names. */
.trial-split-pane .flog-node[data-flog-role="focus"],
.trial-split-pane .flog-node[data-flog-focus="true"] { grid-area: focus; }
.trial-split-pane .flog-node[data-flog-role="command"] { grid-area: command; }
.trial-split-pane .flog-node[data-flog-role="navigation"],
.trial-split-pane .flog-node[data-flog-role="collection"] { grid-area: collection; }
.trial-split-pane .flog-node[data-flog-role="detail"],
.trial-split-pane .flog-node[data-flog-role="inspector"] { grid-area: detail; }
.trial-split-pane .flog-node[data-flog-role="status"] { grid-area: status; }
.trial-split-pane .flog-node[data-flog-role="evidence"] { grid-area: evidence; }

.trial-sectioned-sidebar {
  display: grid;
  gap: var(--gap);
  grid-template-columns: minmax(270px, 0.28fr) minmax(0, 1fr);
  grid-template-rows: minmax(48px, auto) minmax(78px, 0.10fr) minmax(120px, 0.18fr) minmax(120px, 0.18fr) minmax(58px, 0.07fr) minmax(72px, 0.09fr);
  grid-template-areas:
    "title title"
    "command focus"
    "collection focus"
    "detail focus"
    "status focus"
    "evidence focus";
}
.trial-sectioned-sidebar .root-title { grid-area: title; }
.trial-sectioned-sidebar .node-command, .trial-sectioned-sidebar .node-toolbar { grid-area: command; }
.trial-sectioned-sidebar .node-workspace, .trial-sectioned-sidebar .node-editor, .trial-sectioned-sidebar .node-map { grid-area: focus; }
.trial-sectioned-sidebar .node-records, .trial-sectioned-sidebar .node-outline, .trial-sectioned-sidebar .node-subsystems, .trial-sectioned-sidebar .node-jobs { grid-area: collection; }
.trial-sectioned-sidebar .node-detail, .trial-sectioned-sidebar .node-inspector { grid-area: detail; }
.trial-sectioned-sidebar .node-status, .trial-sectioned-sidebar .node-alerts { grid-area: status; }
.trial-sectioned-sidebar .node-evidence { grid-area: evidence; }

.trial-sectioned-sidebar .flog-node[data-flog-role="focus"],
.trial-sectioned-sidebar .flog-node[data-flog-focus="true"] { grid-area: focus; }
.trial-sectioned-sidebar .flog-node[data-flog-role="command"] { grid-area: command; }
.trial-sectioned-sidebar .flog-node[data-flog-role="navigation"],
.trial-sectioned-sidebar .flog-node[data-flog-role="collection"] { grid-area: collection; }
.trial-sectioned-sidebar .flog-node[data-flog-role="detail"],
.trial-sectioned-sidebar .flog-node[data-flog-role="inspector"] { grid-area: detail; }
.trial-sectioned-sidebar .flog-node[data-flog-role="status"] { grid-area: status; }
.trial-sectioned-sidebar .flog-node[data-flog-role="evidence"] { grid-area: evidence; }

.trial-inspector {
  display: grid;
  gap: var(--gap);
  grid-template-columns: minmax(190px, 0.18fr) minmax(0, 1fr) minmax(250px, 0.24fr);
  grid-template-rows: minmax(48px, auto) minmax(62px, 0.075fr) minmax(0, 1fr) minmax(86px, 0.105fr);
  grid-template-areas:
    "title title title"
    "command command detail"
    "collection focus detail"
    "status focus evidence";
}
.trial-inspector .root-title { grid-area: title; }
.trial-inspector .node-command, .trial-inspector .node-toolbar { grid-area: command; }
.trial-inspector .node-workspace, .trial-inspector .node-editor, .trial-inspector .node-map { grid-area: focus; }
.trial-inspector .node-records, .trial-inspector .node-outline, .trial-inspector .node-subsystems, .trial-inspector .node-jobs { grid-area: collection; }
.trial-inspector .node-detail, .trial-inspector .node-inspector { grid-area: detail; }
.trial-inspector .node-status, .trial-inspector .node-alerts { grid-area: status; }
.trial-inspector .node-evidence { grid-area: evidence; }

.trial-inspector .flog-node[data-flog-role="focus"],
.trial-inspector .flog-node[data-flog-focus="true"] { grid-area: focus; }
.trial-inspector .flog-node[data-flog-role="command"] { grid-area: command; }
.trial-inspector .flog-node[data-flog-role="navigation"],
.trial-inspector .flog-node[data-flog-role="collection"] { grid-area: collection; }
.trial-inspector .flog-node[data-flog-role="detail"],
.trial-inspector .flog-node[data-flog-role="inspector"] { grid-area: detail; }
.trial-inspector .flog-node[data-flog-role="status"] { grid-area: status; }
.trial-inspector .flog-node[data-flog-role="evidence"] { grid-area: evidence; }

.trial-dashboard-grid {
  display: grid;
  gap: var(--gap);
  grid-template-columns: repeat(3, minmax(0, 1fr));
  grid-template-rows: auto repeat(2, minmax(0, 1fr));
}
.trial-dashboard-grid .root-title { grid-column: 1 / -1; }
.trial-dashboard-grid .flog-node[data-flog-focus="true"] { grid-column: span 2; }

.trial-focus-priority {
  display: grid;
  gap: var(--gap);
  grid-template-columns: minmax(160px, 0.14fr) minmax(0, 1fr) minmax(235px, 0.21fr);
  grid-template-rows: minmax(48px, auto) minmax(78px, 0.085fr) minmax(0, 1fr) minmax(66px, 0.075fr);
  grid-template-areas:
    "title title command"
    "collection focus command"
    "collection focus detail"
    "status status evidence";
}
.trial-focus-priority .root-title { grid-area: title; }
.trial-focus-priority .node-command, .trial-focus-priority .node-toolbar { grid-area: command; }
.trial-focus-priority .node-workspace, .trial-focus-priority .node-editor, .trial-focus-priority .node-map { grid-area: focus; }
.trial-focus-priority .node-records, .trial-focus-priority .node-outline, .trial-focus-priority .node-subsystems, .trial-focus-priority .node-jobs { grid-area: collection; }
.trial-focus-priority .node-detail, .trial-focus-priority .node-inspector { grid-area: detail; }
.trial-focus-priority .node-status, .trial-focus-priority .node-alerts { grid-area: status; }
.trial-focus-priority .node-evidence { grid-area: evidence; }

.trial-focus-priority .flog-node[data-flog-role="focus"],
.trial-focus-priority .flog-node[data-flog-focus="true"] { grid-area: focus; }
.trial-focus-priority .flog-node[data-flog-role="command"] { grid-area: command; }
.trial-focus-priority .flog-node[data-flog-role="navigation"],
.trial-focus-priority .flog-node[data-flog-role="collection"] { grid-area: collection; }
.trial-focus-priority .flog-node[data-flog-role="detail"],
.trial-focus-priority .flog-node[data-flog-role="inspector"] { grid-area: detail; }
.trial-focus-priority .flog-node[data-flog-role="status"] { grid-area: status; }
.trial-focus-priority .flog-node[data-flog-role="evidence"] { grid-area: evidence; }
.trial-focus-priority .flog-node[data-flog-role="navigation"],
.trial-focus-priority .flog-node[data-flog-role="collection"] {
  /* Nearby can be satisfied by an integrated side rail, not only by center distance. */
  align-self: stretch;
}

.trial-bounded-drawer {
  display: flex;
  flex-direction: column;
  gap: var(--gap);
}
.trial-bounded-drawer .root-title,
.trial-bounded-drawer .node-command,
.trial-bounded-drawer .node-toolbar {
  flex: 0 0 auto;
}
.trial-bounded-drawer .node-workspace,
.trial-bounded-drawer .node-editor,
.trial-bounded-drawer .node-map,
.trial-bounded-drawer .flog-node[data-flog-role="focus"],
.trial-bounded-drawer .flog-node[data-flog-focus="true"] {
  flex: 1 1 auto;
  min-height: 300px;
}
.trial-bounded-drawer .flog-node[data-flog-role="command"],
.trial-bounded-drawer .node-command,
.trial-bounded-drawer .node-toolbar {
  flex: 0 0 auto;
}
.trial-bounded-drawer .flog-node:not([data-flog-focus="true"]):not(.node-command):not(.node-toolbar) {
  flex: 0 0 92px;
}

@media (max-width: 720px) {
  .flog-stage { padding: 10px; }
  .flog-root { width: calc(100vw - 20px); height: calc(100vh - 20px); min-height: 0; }
  .trial-split-pane,
  .trial-sectioned-sidebar,
  .trial-inspector,
  .trial-dashboard-grid,
  .trial-focus-priority,
  .trial-bounded-drawer {
    display: flex;
    flex-direction: column;
    overflow: auto;
  }
  .trial-split-pane .node-detail,
  .trial-split-pane .node-inspector,
  .trial-split-pane .node-jobs,
  .trial-sectioned-sidebar .node-detail,
  .trial-sectioned-sidebar .node-inspector {
    display: flex;
  }
  .flog-node[data-flog-focus="true"] { min-height: 360px; }
}
"""


MEASURE_AND_OVERLAY_JS = r"""
({hierarchyId, candidate, chrome, viewportProfile}) => {
  const doc = document;
  const win = window;
  const root = doc.querySelector(".flog-root");
  const viewport = {
    width: win.innerWidth,
    height: win.innerHeight,
    left: 0,
    top: 0,
    right: win.innerWidth,
    bottom: win.innerHeight,
  };

  function rectObj(rect) {
    const left = Number(rect.left);
    const top = Number(rect.top);
    const width = Math.max(0, Number(rect.width));
    const height = Math.max(0, Number(rect.height));
    return {x: left, y: top, left, top, right: left + width, bottom: top + height, width, height, area: width * height};
  }

  function intersect(a, b) {
    const left = Math.max(a.left, b.left);
    const top = Math.max(a.top, b.top);
    const right = Math.min(a.right, b.right);
    const bottom = Math.min(a.bottom, b.bottom);
    const width = Math.max(0, right - left);
    const height = Math.max(0, bottom - top);
    return {x: left, y: top, left, top, right, bottom, width, height, area: width * height};
  }

  function containsMostly(container, child, tolerance = 2) {
    if (!child || child.area <= 0) return false;
    return child.left >= container.left - tolerance &&
      child.right <= container.right + tolerance &&
      child.top >= container.top - tolerance &&
      child.bottom <= container.bottom + tolerance;
  }

  function cssPath(el) {
    if (!el || !el.tagName) return "";
    if (el.id) return `#${CSS.escape(el.id)}`;
    const slot = el.getAttribute("data-flog-slot") || el.getAttribute("data-mc-slot");
    if (slot) return `[data-mc-slot='${slot}']`;
    const className = String(el.className || "").trim().split(/\s+/).filter(Boolean).slice(0, 2);
    if (className.length) return `${el.tagName.toLowerCase()}.${className.map((name) => CSS.escape(name)).join(".")}`;
    return el.tagName.toLowerCase();
  }

  function labelFor(el) {
    if (!el) return "";
    const aria = el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("placeholder") || "";
    const text = (aria || el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
    return text.slice(0, 140);
  }

  function isVisibleByStyle(el) {
    const style = win.getComputedStyle(el);
    return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || "1") !== 0;
  }

  function recordFor(el) {
    const raw = rectObj(el.getBoundingClientRect());
    return {
      selector: cssPath(el),
      slot: el.getAttribute("data-flog-slot") || el.getAttribute("data-mc-slot") || "",
      role: el.getAttribute("data-flog-role") || el.getAttribute("data-mc-role") || "",
      priority: el.getAttribute("data-flog-priority") || el.getAttribute("data-mc-rank") || "",
      visibility: el.getAttribute("data-flog-visibility") || el.getAttribute("data-mc-visibility") || "",
      proximity: el.getAttribute("data-flog-proximity") || el.getAttribute("data-mc-proximity") || "",
      minVisibleShare: Number(el.getAttribute("data-flog-min-visible-share") || "0"),
      itemKind: el.getAttribute("data-flog-item-kind") || "",
      itemRole: el.getAttribute("data-flog-item-role") || "",
      tag: el.tagName.toLowerCase(),
      label: labelFor(el),
      rect: raw,
      documentRect: {
        x: raw.left + win.scrollX,
        y: raw.top + win.scrollY,
        left: raw.left + win.scrollX,
        top: raw.top + win.scrollY,
        right: raw.right + win.scrollX,
        bottom: raw.bottom + win.scrollY,
        width: raw.width,
        height: raw.height,
        area: raw.area,
      },
    };
  }

  function unionArea(rects, bounds) {
    const clipped = rects
      .map((rect) => intersect(rect, bounds))
      .filter((rect) => rect.width > 0.5 && rect.height > 0.5);
    if (!clipped.length || bounds.area <= 0) return 0;
    const xs = Array.from(new Set([bounds.left, bounds.right, ...clipped.flatMap((rect) => [rect.left, rect.right])])).sort((a, b) => a - b);
    const ys = Array.from(new Set([bounds.top, bounds.bottom, ...clipped.flatMap((rect) => [rect.top, rect.bottom])])).sort((a, b) => a - b);
    let area = 0;
    for (let xi = 0; xi < xs.length - 1; xi += 1) {
      const left = xs[xi];
      const right = xs[xi + 1];
      if (right <= left) continue;
      const midX = (left + right) / 2;
      for (let yi = 0; yi < ys.length - 1; yi += 1) {
        const top = ys[yi];
        const bottom = ys[yi + 1];
        if (bottom <= top) continue;
        const midY = (top + bottom) / 2;
        if (clipped.some((rect) => midX >= rect.left && midX <= rect.right && midY >= rect.top && midY <= rect.bottom)) {
          area += (right - left) * (bottom - top);
        }
      }
    }
    return Math.min(area, bounds.area);
  }

  function isCriticalControl(el) {
    const tag = el.tagName.toLowerCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (tag === "input" && type === "hidden") return false;
    return tag === "button" || role === "button" || ["input", "select", "textarea"].includes(tag);
  }

  if (!root) {
    return {
      hierarchyId,
      candidate,
      chrome,
      viewportProfile,
      rootFound: false,
      geometryFacts: {provedBy: "playwright-chromium", viewport},
      classification: {score: 0, status: "fail", warnings: ["synthetic root was not found"]},
      humanLoop: {
        proved: ["Chromium loaded the page."],
        inferred: [],
        unknowns: ["No root exists, so no layout inference is valid."],
      },
    };
  }

  const rootRaw = rectObj(root.getBoundingClientRect());
  const rootClipped = intersect(rootRaw, viewport);
  const rootDocumentRect = {
    x: rootRaw.left + win.scrollX,
    y: rootRaw.top + win.scrollY,
    left: rootRaw.left + win.scrollX,
    top: rootRaw.top + win.scrollY,
    right: rootRaw.right + win.scrollX,
    bottom: rootRaw.bottom + win.scrollY,
    width: rootRaw.width,
    height: rootRaw.height,
    area: rootRaw.area,
  };

  const nodes = Array.from(root.querySelectorAll(".flog-node")).filter((el) => isVisibleByStyle(el));
  const focus = root.querySelector("[data-flog-focus='true']");
  const requiredNodes = nodes.filter((el) => (el.getAttribute("data-flog-priority") || "") === "primary" || el.getAttribute("data-flog-focus") === "true");
  const nodeRecords = nodes.map(recordFor).filter((item) => item.rect.area > 4);
  const focusRecord = focus ? recordFor(focus) : null;
  const visibleRequired = requiredNodes.map(recordFor).filter((item) => item.rect.area > 4 && intersect(item.rect, rootClipped).area > 4);

  const nodeArea = unionArea(nodeRecords.map((item) => item.rect), rootClipped);
  const nodeCoverageRatio = rootClipped.area > 0 ? nodeArea / rootClipped.area : 0;
  const unclaimedAreaRatio = rootClipped.area > 0 ? Math.max(0, 1 - nodeCoverageRatio) : 1;
  const focusClipped = focusRecord ? intersect(focusRecord.rect, rootClipped) : {left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0, area: 0};
  const focusVisibleArea = focusClipped.area;
  const focusShare = rootClipped.area > 0 ? focusVisibleArea / rootClipped.area : 0;
  const desiredFocusShare = Number(root.getAttribute("data-flog-desired-focus-share") || "0.45");
  const minFocusShare = Number(root.getAttribute("data-flog-min-focus-share") || "0.3");
  const maxFocusShare = Number(root.getAttribute("data-flog-max-focus-share") || "0.75");
  const focusDeviation = Math.abs(focusShare - desiredFocusShare);

  const focusContentRecords = focus
    ? Array.from(focus.querySelectorAll("[data-flog-item='true']"))
        .filter((el) => isVisibleByStyle(el))
        .map(recordFor)
        .filter((item) => item.rect.area > 4 && intersect(item.rect, focusClipped).area > 4)
    : [];
  const focusContentArea = unionArea(focusContentRecords.map((item) => item.rect), focusClipped);
  const usefulFocusOccupancy = focusClipped.area > 0 ? Math.min(1, focusContentArea / focusClipped.area) : 0;
  const minUsefulFocusOccupancy = Number(root.getAttribute("data-flog-min-useful-focus-occupancy") || "0.30");
  const targetUsefulFocusOccupancy = Number(root.getAttribute("data-flog-target-useful-focus-occupancy") || "0.56");
  const usefulFocusOccupancyDeviation = Math.abs(usefulFocusOccupancy - targetUsefulFocusOccupancy);

  function splitAttr(name) {
    return String(root.getAttribute(name) || "").split(/\s+/).map((part) => part.trim()).filter(Boolean);
  }

  const requiredCompanionSlots = splitAttr("data-flog-required-companions");
  const nearbyCompanionSlots = splitAttr("data-flog-nearby-companions");
  const deferableSlots = splitAttr("data-flog-deferable-slots");
  const forbiddenDefaultHiddenSlots = splitAttr("data-flog-forbidden-default-hidden");
  const preferredFamilies = splitAttr("data-flog-preferred-families");
  const dangerousFamilies = splitAttr("data-flog-dangerous-families");

  const nodeBySlot = new Map();
  const elementBySlot = new Map();
  for (const el of nodes) {
    const slot = el.getAttribute("data-flog-slot") || el.getAttribute("data-mc-slot") || "";
    if (slot && !elementBySlot.has(slot)) elementBySlot.set(slot, el);
  }
  for (const record of nodeRecords) {
    if (record.slot && !nodeBySlot.has(record.slot)) nodeBySlot.set(record.slot, record);
  }

  function visibleShareForSlot(slot) {
    const record = nodeBySlot.get(slot);
    if (!record || rootClipped.area <= 0) return 0;
    return intersect(record.rect, rootClipped).area / rootClipped.area;
  }

  function minVisibleShareForSlot(slot) {
    const el = elementBySlot.get(slot);
    const declared = Number(el?.getAttribute("data-flog-min-visible-share") || "0");
    return Math.max(0.012, declared || 0);
  }

  function proximityForSlot(slot) {
    const el = elementBySlot.get(slot);
    return el?.getAttribute("data-flog-proximity") || "loose";
  }

  function proximityThreshold(proximity) {
    if (proximity === "attached") return 0.38;
    if (proximity === "near") return 0.58;
    if (proximity === "self") return 0.20;
    return 0.72;
  }

  function centerOf(rect) {
    return {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2};
  }

  function overlapLength(aStart, aEnd, bStart, bEnd) {
    return Math.max(0, Math.min(aEnd, bEnd) - Math.max(aStart, bStart));
  }

  function nearbyIntegration(record, focus, rootBounds) {
    if (!record || !focus || !rootBounds || rootBounds.area <= 0) {
      return {mode: "missing", score: 0, gap: 1, crossAxisOverlap: 0};
    }
    const rect = record.rect;
    const maxGap = Math.max(18, Math.min(rootBounds.width, rootBounds.height) * 0.045);
    const horizontalGap = Math.max(0, Math.max(focus.left - rect.right, rect.left - focus.right));
    const verticalGap = Math.max(0, Math.max(focus.top - rect.bottom, rect.top - focus.bottom));
    const sideOverlap = overlapLength(rect.top, rect.bottom, focus.top, focus.bottom) / Math.max(1, Math.min(rect.height, focus.height));
    const bandOverlap = overlapLength(rect.left, rect.right, focus.left, focus.right) / Math.max(1, Math.min(rect.width, focus.width));
    const sideRail = horizontalGap <= maxGap && sideOverlap >= 0.22;
    const band = verticalGap <= maxGap && bandOverlap >= 0.30;
    if (!sideRail && !band) {
      return {mode: "distance-only", score: 0, gap: Math.min(horizontalGap, verticalGap), crossAxisOverlap: Math.max(sideOverlap, bandOverlap)};
    }
    const gap = sideRail ? horizontalGap : verticalGap;
    const crossAxisOverlap = sideRail ? sideOverlap : bandOverlap;
    const score = Math.max(0, Math.min(1, (1 - gap / Math.max(1, maxGap)) * Math.min(1, crossAxisOverlap)));
    return {
      mode: sideRail ? "integrated-side-rail" : "integrated-cross-band",
      score,
      gap,
      crossAxisOverlap,
    };
  }

  const focusCenter = focusRecord ? centerOf(focusRecord.rect) : null;
  const rootDiagonal = Math.max(1, Math.hypot(rootClipped.width, rootClipped.height));

  const requiredCompanionResults = requiredCompanionSlots.map((slot) => {
    const visibleShare = visibleShareForSlot(slot);
    const minVisibleShare = minVisibleShareForSlot(slot);
    return {
      slot,
      visibleShare,
      minVisibleShare,
      visible: visibleShare >= minVisibleShare,
    };
  });
  const missingRequiredCompanions = requiredCompanionResults.filter((item) => !item.visible);

  const forbiddenHiddenResults = forbiddenDefaultHiddenSlots.map((slot) => {
    const visibleShare = visibleShareForSlot(slot);
    const minVisibleShare = minVisibleShareForSlot(slot);
    return {
      slot,
      visibleShare,
      minVisibleShare,
      visible: visibleShare >= minVisibleShare,
    };
  });
  const hiddenForbiddenSlots = forbiddenHiddenResults.filter((item) => !item.visible);

  const nearbyCompanionResults = nearbyCompanionSlots.map((slot) => {
    const record = nodeBySlot.get(slot);
    const proximity = proximityForSlot(slot);
    const threshold = proximityThreshold(proximity);
    const minVisibleShare = minVisibleShareForSlot(slot);
    if (!record || !focusCenter || !focusRecord) {
      return {
        slot,
        visibleShare: 0,
        minVisibleShare,
        effectiveMinVisibleShare: minVisibleShare,
        normalizedDistance: 1,
        proximity,
        threshold,
        distanceScore: 0,
        integration: {mode: "missing", score: 0, gap: 1, crossAxisOverlap: 0},
        near: false,
      };
    }
    const center = centerOf(record.rect);
    const normalizedDistance = Math.min(1, Math.hypot(center.x - focusCenter.x, center.y - focusCenter.y) / rootDiagonal);
    const visibleShare = visibleShareForSlot(slot);
    const integration = nearbyIntegration(record, focusRecord.rect, rootClipped);
    const centerDistanceScore = Math.max(0, 1 - (normalizedDistance / Math.max(0.001, threshold)));
    const relationshipScore = Math.max(centerDistanceScore, integration.score);
    const effectiveMinVisibleShare = integration.score >= 0.55 ? minVisibleShare * 0.72 : minVisibleShare;
    return {
      slot,
      visibleShare,
      minVisibleShare,
      effectiveMinVisibleShare,
      normalizedDistance,
      proximity,
      threshold,
      distanceScore: relationshipScore,
      centerDistanceScore,
      integration,
      near: visibleShare >= effectiveMinVisibleShare && (normalizedDistance <= threshold || integration.score >= 0.55),
    };
  });
  const distantNearbyCompanions = nearbyCompanionResults.filter((item) => !item.near);
  const companionVisibilityRatio = requiredCompanionResults.length
    ? requiredCompanionResults.filter((item) => item.visible).length / requiredCompanionResults.length
    : 1;
  const nearbyCompanionRatio = nearbyCompanionResults.length
    ? nearbyCompanionResults.filter((item) => item.near).length / nearbyCompanionResults.length
    : 1;
  const companionProximityScore = nearbyCompanionResults.length
    ? nearbyCompanionResults.reduce((total, item) => total + (item.visibleShare >= item.effectiveMinVisibleShare ? item.distanceScore : 0), 0) / nearbyCompanionResults.length
    : 1;
  const averageNearbyCompanionDistance = nearbyCompanionResults.length
    ? nearbyCompanionResults.reduce((total, item) => total + item.normalizedDistance, 0) / nearbyCompanionResults.length
    : 0;
  const preferredFamilyMatch = preferredFamilies.includes(candidate);
  const dangerousFamilyMatch = dangerousFamilies.includes(candidate);

  const controls = Array.from(root.querySelectorAll("button, input, select, textarea")).filter(isCriticalControl);
  const clippedCriticalControls = [];
  const hiddenCriticalControls = [];
  for (const el of controls) {
    const record = recordFor(el);
    const visible = isVisibleByStyle(el) && record.rect.area > 4;
    if (!visible) {
      hiddenCriticalControls.push({...record, reason: "hidden-or-zero-sized"});
      continue;
    }
    const inViewport = containsMostly(viewport, record.rect);
    const inRoot = containsMostly(rootRaw, record.rect);
    if (!inViewport || !inRoot) {
      clippedCriticalControls.push({...record, reason: !inViewport ? "outside-viewport" : "outside-root"});
    }
  }

  const scrollOwners = [];
  for (const el of [root, ...nodes, ...Array.from(root.querySelectorAll(".node-body"))]) {
    if (!isVisibleByStyle(el)) continue;
    const rect = rectObj(el.getBoundingClientRect());
    if (rect.area <= 4) continue;
    const style = win.getComputedStyle(el);
    const overflowText = `${style.overflow} ${style.overflowX} ${style.overflowY}`;
    const canScroll = /(auto|scroll|overlay)/.test(overflowText);
    const scrolls = el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2;
    if (scrolls && canScroll) {
      scrollOwners.push({
        selector: cssPath(el),
        slot: el.getAttribute("data-flog-slot") || el.getAttribute("data-mc-slot") || "",
        label: labelFor(el),
        rect,
        clientWidth: el.clientWidth,
        clientHeight: el.clientHeight,
        scrollWidth: el.scrollWidth,
        scrollHeight: el.scrollHeight,
      });
    }
  }

  const warnings = [];
  if (rootClipped.area <= 1) warnings.push("root is not visible");
  if (unclaimedAreaRatio > 0.34) warnings.push(`high measured unclaimed layout area (${unclaimedAreaRatio.toFixed(2)})`);
  if (focusShare < minFocusShare) warnings.push(`focus slot is starved (${focusShare.toFixed(2)} < ${minFocusShare.toFixed(2)})`);
  if (focusShare > maxFocusShare) warnings.push(`focus slot dominates beyond target (${focusShare.toFixed(2)} > ${maxFocusShare.toFixed(2)})`);
  if (usefulFocusOccupancy < minUsefulFocusOccupancy) warnings.push(`focus interior is too sparse (${usefulFocusOccupancy.toFixed(2)} < ${minUsefulFocusOccupancy.toFixed(2)})`);
  if (focusContentRecords.length < 4) warnings.push(`focus slot has too few useful interior elements (${focusContentRecords.length})`);
  if (clippedCriticalControls.length) warnings.push(`${clippedCriticalControls.length} critical control(s) extend outside viewport/root`);
  if (hiddenCriticalControls.length) warnings.push(`${hiddenCriticalControls.length} critical control(s) are hidden or zero-sized`);
  if (visibleRequired.length < requiredNodes.length) warnings.push("some required primary/focus nodes are not visible");
  if (missingRequiredCompanions.length) warnings.push(`required companion slot(s) not visible enough: ${missingRequiredCompanions.map((item) => item.slot).join(", ")}`);
  if (hiddenForbiddenSlots.length) warnings.push(`forbidden default-hidden slot(s) are missing from the default view: ${hiddenForbiddenSlots.map((item) => item.slot).join(", ")}`);
  if (distantNearbyCompanions.length) warnings.push(`nearby companion slot(s) are too far from focus: ${distantNearbyCompanions.map((item) => item.slot).join(", ")}`);
  const sourceOrderStarvesHighFocus = candidate === "source-order-stacked" && minFocusShare >= 0.40 && focusShare < minFocusShare;
  if (dangerousFamilyMatch) warnings.push(`candidate is marked dangerous for this role contract: ${candidate}`);
  if (sourceOrderStarvesHighFocus) warnings.push("source-order-stacked preserves visibility but starves a high-focus hierarchy");
  if (!preferredFamilyMatch && preferredFamilies.length) warnings.push(`candidate is outside preferred families for this hierarchy`);
  if (doc.body.scrollWidth > viewport.width + 2) warnings.push("document overflows horizontally");
  if (doc.body.scrollHeight > viewport.height + 2 && candidate !== "source-order-stacked") warnings.push("document overflows vertically outside the root");

  const positiveReasons = [];
  const failureReasons = [];
  const reviewNotes = [];
  function pct(value) {
    return `${(value * 100).toFixed(0)}%`;
  }
  if (focusShare >= minFocusShare && focusShare <= maxFocusShare) {
    positiveReasons.push(`focus ${root.getAttribute("data-flog-focus-slot") || "slot"} got ${pct(focusShare)} of the root against target ${pct(desiredFocusShare)}`);
  } else if (focusShare < minFocusShare) {
    failureReasons.push(`focus share ${pct(focusShare)} is below required minimum ${pct(minFocusShare)}`);
  } else {
    failureReasons.push(`focus share ${pct(focusShare)} exceeds maximum ${pct(maxFocusShare)}, suggesting companion context may be starved`);
  }
  if (usefulFocusOccupancy >= minUsefulFocusOccupancy) {
    positiveReasons.push(`focus interior occupancy is ${pct(usefulFocusOccupancy)} from ${focusContentRecords.length} useful seeded elements`);
  } else {
    failureReasons.push(`focus interior occupancy is only ${pct(usefulFocusOccupancy)}; this looks like a big empty focus panel`);
  }
  if (companionVisibilityRatio === 1) {
    positiveReasons.push("all required companions remained visible enough");
  } else {
    failureReasons.push(`required companion visibility is ${pct(companionVisibilityRatio)}`);
  }
  if (nearbyCompanionRatio === 1) {
    const integratedCount = nearbyCompanionResults.filter((item) => item.integration && item.integration.score >= 0.55).length;
    const integrationText = integratedCount ? `; ${integratedCount} nearby companion(s) are integrated as side/band regions` : "";
    positiveReasons.push(`nearby companions stayed near the focus or were integrated into support regions (avg distance ${averageNearbyCompanionDistance.toFixed(2)}${integrationText})`);
  } else {
    failureReasons.push(`nearby companion fit is ${pct(nearbyCompanionRatio)}; ${distantNearbyCompanions.map((item) => item.slot).join(", ")} need docking or integration review`);
  }
  if (unclaimedAreaRatio <= 0.24) {
    positiveReasons.push(`unclaimed area is controlled at ${pct(unclaimedAreaRatio)}`);
  } else {
    failureReasons.push(`unclaimed area is high at ${pct(unclaimedAreaRatio)}`);
  }
  if (clippedCriticalControls.length === 0 && hiddenCriticalControls.length === 0) {
    positiveReasons.push("no critical controls were clipped or hidden");
  } else {
    failureReasons.push(`${clippedCriticalControls.length + hiddenCriticalControls.length} critical control(s) were clipped or hidden`);
  }
  if (preferredFamilyMatch) {
    positiveReasons.push(`candidate is preferred for this declared role contract`);
  } else if (preferredFamilies.length) {
    reviewNotes.push(`candidate is outside preferred families: ${preferredFamilies.join(", ")}`);
  }
  if (dangerousFamilyMatch) {
    failureReasons.push(`candidate is marked dangerous: ${candidate}`);
  }
  if (sourceOrderStarvesHighFocus) {
    failureReasons.push("source-order-stacked is only a visibility fallback here; it does not satisfy the declared focus minimum");
  }
  if (scrollOwners.length > 2) {
    reviewNotes.push(`${scrollOwners.length} scroll owners may create review burden`);
  }
  if (nearbyCompanionResults.some((item) => item.integration && item.integration.score >= 0.55)) {
    reviewNotes.push("Nearby was satisfied by semantic docking/integration, not just center-point distance.");
  }
  reviewNotes.push("Human review still must compare the PNG proof against the intended app meaning.");

  let score = 100;
  score -= Math.round(unclaimedAreaRatio * 54);
  score -= Math.round(focusDeviation * 72);
  if (focusShare < minFocusShare) score -= 16;
  if (focusShare > maxFocusShare) score -= 8;
  score -= Math.round(Math.max(0, minUsefulFocusOccupancy - usefulFocusOccupancy) * 72);
  score -= Math.min(14, Math.max(0, 4 - focusContentRecords.length) * 4);
  score -= clippedCriticalControls.length * 8;
  score -= hiddenCriticalControls.length * 10;
  score -= Math.max(0, visibleRequired.length < requiredNodes.length ? 18 : 0);
  score -= missingRequiredCompanions.length * 14;
  score -= hiddenForbiddenSlots.length * 16;
  score -= distantNearbyCompanions.length * 6;
  score -= Math.round((1 - companionVisibilityRatio) * 18);
  score -= Math.round((1 - nearbyCompanionRatio) * 10);
  score -= Math.round((1 - companionProximityScore) * 12);
  if (dangerousFamilyMatch) score -= 14;
  if (sourceOrderStarvesHighFocus) score -= 12;
  if (preferredFamilyMatch) score += 6;
  score -= Math.max(0, scrollOwners.length - 2) * 5;
  if (doc.body.scrollWidth > viewport.width + 2) score -= 16;
  if (doc.body.scrollHeight > viewport.height + 2 && candidate !== "source-order-stacked") score -= 8;
  score = Math.max(0, Math.min(100, Math.round(score)));
  const status = score >= 82 && warnings.length <= 1 ? "pass" : score >= 64 ? "watch" : "fail";

  doc.querySelectorAll("[data-flog-snapshot-overlay]").forEach((node) => node.remove());
  const overlay = doc.createElement("div");
  overlay.setAttribute("data-flog-snapshot-overlay", "root");
  overlay.style.position = "absolute";
  overlay.style.left = "0";
  overlay.style.top = "0";
  overlay.style.width = `${Math.max(doc.documentElement.scrollWidth, doc.body.scrollWidth, viewport.width)}px`;
  overlay.style.height = `${Math.max(doc.documentElement.scrollHeight, doc.body.scrollHeight, viewport.height)}px`;
  overlay.style.pointerEvents = "none";
  overlay.style.zIndex = "2147483647";
  overlay.style.fontFamily = "monospace";
  overlay.style.fontSize = "11px";
  overlay.style.lineHeight = "1.2";
  doc.body.appendChild(overlay);

  function addBox(record, color, label, fill = "transparent") {
    const rect = record.documentRect || {
      left: record.left + win.scrollX,
      top: record.top + win.scrollY,
      width: record.width,
      height: record.height,
    };
    if (!rect || rect.width <= 1 || rect.height <= 1) return;
    const box = doc.createElement("div");
    box.setAttribute("data-flog-snapshot-overlay", "box");
    box.style.position = "absolute";
    box.style.left = `${rect.left}px`;
    box.style.top = `${rect.top}px`;
    box.style.width = `${rect.width}px`;
    box.style.height = `${rect.height}px`;
    box.style.border = `2px solid ${color}`;
    box.style.background = fill;
    box.style.boxSizing = "border-box";
    box.style.borderRadius = "4px";
    const tag = doc.createElement("div");
    tag.textContent = label;
    tag.style.position = "absolute";
    tag.style.left = "0";
    tag.style.top = "0";
    tag.style.maxWidth = "360px";
    tag.style.padding = "2px 5px";
    tag.style.color = "#111";
    tag.style.background = color;
    tag.style.opacity = "0.92";
    tag.style.whiteSpace = "nowrap";
    tag.style.overflow = "hidden";
    tag.style.textOverflow = "ellipsis";
    box.appendChild(tag);
    overlay.appendChild(box);
  }

  addBox({documentRect: rootDocumentRect}, "#43a7ff", `root ${hierarchyId}`, "rgba(67, 167, 255, 0.04)");
  nodeRecords.forEach((item) => {
    const isFocus = item.slot === (root.getAttribute("data-flog-focus-slot") || "");
    addBox(item, isFocus ? "#b46cff" : "#38d66b", `${isFocus ? "FOCUS " : ""}${item.slot}`, isFocus ? "rgba(180,108,255,0.07)" : "rgba(56,214,107,0.04)");
  });
  scrollOwners.slice(0, 14).forEach((item) => {
    const rect = item.rect;
    addBox({documentRect: {left: rect.left + win.scrollX, top: rect.top + win.scrollY, width: rect.width, height: rect.height}}, "#ffbf3f", `scroll ${item.slot || item.selector}`, "rgba(255,191,63,0.06)");
  });
  clippedCriticalControls.slice(0, 18).forEach((item) => addBox(item, "#ff4d4d", `clipped ${item.selector}`, "rgba(255,77,77,0.06)"));
  hiddenCriticalControls.slice(0, 12).forEach((item, index) => {
    addBox({documentRect: {left: rootDocumentRect.left + 8, top: rootDocumentRect.top + 28 + index * 18, width: 220, height: 16}}, "#ff4d4d", `hidden ${item.selector}`, "rgba(255,77,77,0.16)");
  });

  const legend = doc.createElement("div");
  legend.setAttribute("data-flog-snapshot-overlay", "legend");
  legend.style.position = "fixed";
  legend.style.left = "12px";
  legend.style.bottom = "12px";
  legend.style.maxWidth = "640px";
  legend.style.padding = "10px 12px";
  legend.style.background = "rgba(0,0,0,.80)";
  legend.style.color = "white";
  legend.style.border = "1px solid rgba(255,255,255,.35)";
  legend.style.borderRadius = "8px";
  legend.style.boxShadow = "0 8px 28px rgba(0,0,0,.35)";
  legend.innerHTML = [
    `<strong>FLOG trial proof</strong>`,
    `hierarchy=${hierarchyId} layout=${candidate} viewport=${viewportProfile} chrome=${chrome}`,
    `score=${score} status=${status} wasted/unclaimed=${unclaimedAreaRatio.toFixed(3)} focus=${focusShare.toFixed(3)} target=${desiredFocusShare.toFixed(2)} focus-occupancy=${usefulFocusOccupancy.toFixed(3)} companion-proximity=${companionProximityScore.toFixed(3)}`,
    `<span style="color:#43a7ff">blue=root</span> <span style="color:#38d66b">green=semantic node</span> <span style="color:#b46cff">purple=focus target</span> <span style="color:#ffbf3f">orange=scroll</span> <span style="color:#ff4d4d">red=clipped/hidden</span>`,
  ].join("<br>");
  overlay.appendChild(legend);

  return {
    hierarchyId,
    candidate,
    chrome,
    viewportProfile,
    rootFound: true,
    title: root.querySelector("h1")?.innerText || hierarchyId,
    geometryFacts: {
      provedBy: "playwright-chromium",
      viewport,
      root: {raw: rootRaw, clipped: rootClipped, documentRect: rootDocumentRect},
      bodyScrollWidth: doc.body.scrollWidth,
      bodyScrollHeight: doc.body.scrollHeight,
      documentOverflowX: doc.body.scrollWidth > viewport.width + 2,
      documentOverflowY: doc.body.scrollHeight > viewport.height + 2,
      nodeCount: nodeRecords.length,
      nodeCoverageRatio,
      unclaimedAreaRatio,
      focusShare,
      desiredFocusShare,
      minFocusShare,
      maxFocusShare,
      focusDeviation,
      focusContentCount: focusContentRecords.length,
      focusContentArea,
      usefulFocusOccupancy,
      minUsefulFocusOccupancy,
      targetUsefulFocusOccupancy,
      usefulFocusOccupancyDeviation,
      requiredNodeCount: requiredNodes.length,
      visibleRequiredNodeCount: visibleRequired.length,
      clippedCriticalControlCount: clippedCriticalControls.length,
      hiddenCriticalControlCount: hiddenCriticalControls.length,
      scrollOwnerCount: scrollOwners.length,
      requiredCompanionSlots,
      nearbyCompanionSlots,
      deferableSlots,
      forbiddenDefaultHiddenSlots,
      preferredFamilies,
      dangerousFamilies,
      companionVisibilityRatio,
      nearbyCompanionRatio,
      companionProximityScore,
      averageNearbyCompanionDistance,
      requiredCompanions: requiredCompanionResults,
      nearbyCompanions: nearbyCompanionResults,
      missingRequiredCompanions,
      hiddenForbiddenSlots,
      distantNearbyCompanions,
      preferredFamilyMatch,
      dangerousFamilyMatch,
    },
    examples: {
      nodes: nodeRecords,
      focus: focusRecord,
      focusContent: focusContentRecords.slice(0, 30),
      clippedCriticalControls: clippedCriticalControls.slice(0, 20),
      hiddenCriticalControls: hiddenCriticalControls.slice(0, 20),
      scrollOwners: scrollOwners.slice(0, 20),
    },
    classification: {score, status, warnings, positiveReasons, failureReasons, reviewNotes},
    humanLoop: {
      required: true,
      proved: [
        "Chromium rendered the candidate layout.",
        "The PNG shows the root, semantic nodes, focus target, scroll owners, and clipped/hidden controls.",
        "The report measured approximate node coverage and unclaimed area inside the root.",
        "The report measured how much of the root was given to the declared focus slot.",
      ],
      inferred: [
        "Whether the unclaimed area is desirable calm spacing or waste.",
        "Whether the declared focus slot is the correct thing to maximize for the real app.",
        "Whether required companions are truly required, nearby, or safely deferable.",
        "Whether this candidate should become a default for this hierarchy/chrome.",
      ],
      unknowns: [
        "Real app data volume may change scroll pressure.",
        "Theme-specific readability is not fully scored yet.",
        "The seeded role contract is a stand-in until live MCEL hierarchies are strong enough.",
      ],
    },
  };
}
"""


def playwright_missing_message(exc: BaseException) -> str:
    return (
        "Playwright/Chromium is required for FLOG layout trial PNGs. "
        "Install the browser with: python -m playwright install chromium. "
        f"Original error: {exc}"
    )


def verify_png_written(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"Expected PNG snapshot was not created: {path}")
    if path.stat().st_size <= 0:
        raise RuntimeError(f"Expected PNG snapshot is empty: {path}")


def capture_png(page: Any, path: Path, *, full_page: bool) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path), full_page=full_page)
    verify_png_written(path)
    return path.name


def measurement_score(item: dict[str, Any]) -> int:
    classification = item.get("classification") or {}
    return int(classification.get("selectionScore", classification.get("score", 0)) or 0)


def measurement_status(item: dict[str, Any]) -> str:
    return str((item.get("classification") or {}).get("status") or "fail")


def measurement_selection_row(
    item: dict[str, Any],
    *,
    selection_state: str,
    highest_scoring: dict[str, Any],
) -> dict[str, Any]:
    classification = item.get("classification") or {}
    facts = item.get("geometryFacts") or {}
    highest_classification = highest_scoring.get("classification") or {}
    row = {
        "hierarchyId": item["hierarchyId"],
        "viewportProfile": item["viewportProfile"],
        "candidate": item["candidate"],
        "score": classification.get("score", 0),
        "status": classification.get("status", "fail"),
        "selectionState": selection_state,
        "noPassingCandidate": selection_state == "noPassingCandidate",
        "unclaimedAreaRatio": facts.get("unclaimedAreaRatio", 0),
        "focusShare": facts.get("focusShare", 0),
        "desiredFocusShare": facts.get("desiredFocusShare", 0),
        "usefulFocusOccupancy": facts.get("usefulFocusOccupancy", 0),
        "companionProximityScore": facts.get("companionProximityScore", 1),
        "geometryScore": classification.get("geometryScore", classification.get("score", 0)),
        "selectionScore": classification.get("selectionScore", classification.get("score", 0)),
        "contractFitScore": classification.get("contractFitScore", 0),
        "contractFitState": classification.get("contractFitState", "notEvaluated"),
        "affordanceFitScore": classification.get("affordanceFitScore", 0),
        "affordanceFitState": classification.get("affordanceFitState", "notEvaluated"),
        "contractFitReasons": (item.get("contractFit") or {}).get("positiveReasons", []),
        "contractFitRisks": (item.get("contractFit") or {}).get("riskReasons", []),
        "hardContractRisks": (item.get("contractFit") or {}).get("hardRiskReasons", []),
        "softContractRisks": (item.get("contractFit") or {}).get("softRiskReasons", []),
        "presentationSetReasons": (item.get("contractFit") or {}).get("presentationSetReasons", []),
        "affordanceFitReasons": (item.get("affordanceFit") or {}).get("positiveReasons", []),
        "affordanceFitRisks": (item.get("affordanceFit") or {}).get("riskReasons", []),
        "hardAffordanceRisks": (item.get("affordanceFit") or {}).get("hardRiskReasons", []),
        "reasons": classification.get("positiveReasons", []),
        "failureReasons": classification.get("failureReasons", []),
        "reviewNotes": classification.get("reviewNotes", []),
        "snapshots": item.get("snapshots", {}),
        "highestScoringCandidate": {
            "candidate": highest_scoring.get("candidate"),
            "score": highest_classification.get("score", 0),
            "status": highest_classification.get("status", "fail"),
        },
    }
    if selection_state == "noPassingCandidate":
        row["highestScoringFailure"] = {
            "candidate": highest_scoring.get("candidate"),
            "score": highest_classification.get("score", 0),
            "status": highest_classification.get("status", "fail"),
            "failureReasons": highest_classification.get("failureReasons", []),
            "snapshots": highest_scoring.get("snapshots", {}),
        }
    return row


def best_by_hierarchy_viewport_rows(measurements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for measurement in measurements:
        key = f"{measurement['hierarchyId']}::{measurement['viewportProfile']}"
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(measurement)

    rows: list[dict[str, Any]] = []
    for key in order:
        ranked = sorted(
            grouped[key],
            key=lambda item: (
                measurement_score(item),
                1 if measurement_status(item) == "pass" else 0,
                1 if measurement_status(item) == "watch" else 0,
            ),
            reverse=True,
        )
        highest_scoring = ranked[0]
        acceptable = [item for item in ranked if measurement_status(item) in ACCEPTABLE_LAYOUT_STATUSES]
        if acceptable:
            rows.append(
                measurement_selection_row(
                    acceptable[0],
                    selection_state="bestPassingCandidate",
                    highest_scoring=highest_scoring,
                )
            )
        else:
            rows.append(
                measurement_selection_row(
                    highest_scoring,
                    selection_state="noPassingCandidate",
                    highest_scoring=highest_scoring,
                )
            )
    return rows


def run_synthetic_trials(
    *,
    hierarchies: list[dict[str, Any]],
    candidates: list[str],
    viewports: list[ViewportProfile],
    chrome: str,
    output_dir: Path,
    screenshot_mode: str,
    keep_html: bool,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - exercised only without dependency
        raise SystemExit(playwright_missing_message(exc)) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    measurements: list[dict[str, Any]] = []
    generated_at = datetime.now(timezone.utc).isoformat()

    with tempfile.TemporaryDirectory(prefix="flog-layout-trials-") as temp_name:
        temp_dir = Path(temp_name)
        try:
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Exception as exc:  # pragma: no cover - depends on local browser install
                    raise SystemExit(playwright_missing_message(exc)) from exc

                try:
                    for viewport in viewports:
                        context = browser.new_context(
                            viewport={"width": viewport.width, "height": viewport.height},
                            device_scale_factor=1,
                        )
                        page = context.new_page()
                        page.on("console", lambda msg: None)
                        for hierarchy in hierarchies:
                            for candidate in candidates:
                                html_text = render_trial_html(hierarchy, candidate, chrome)
                                html_path = temp_dir / f"{slugify(hierarchy['id'])}--{slugify(candidate)}.html"
                                html_path.write_text(html_text, encoding="utf-8")
                                if keep_html:
                                    (output_dir / html_path.name).write_text(html_text, encoding="utf-8")
                                page.goto(html_path.as_uri(), wait_until="domcontentloaded")
                                page.wait_for_timeout(100)
                                measurement = page.evaluate(
                                    MEASURE_AND_OVERLAY_JS,
                                    {
                                        "hierarchyId": hierarchy["id"],
                                        "candidate": candidate,
                                        "chrome": chrome,
                                        "viewportProfile": viewport.name,
                                    },
                                )
                                measurement["hierarchyTitle"] = hierarchy["title"]
                                measurement["hierarchyDescription"] = hierarchy["description"]
                                measurement["sourceApp"] = hierarchy.get("sourceApp", "")
                                measurement["focusSlot"] = hierarchy["focusSlot"]
                                measurement["rootConcern"] = hierarchy["rootConcern"]
                                measurement["roleContract"] = hierarchy.get("roleContract", {})
                                measurement["snapshots"] = {}

                                base_name = f"{slugify(hierarchy['id'])}--{slugify(viewport.name)}--{slugify(candidate)}"
                                if screenshot_mode in {"viewport", "both"}:
                                    png = output_dir / f"{base_name}--viewport.png"
                                    measurement["snapshots"]["viewport"] = capture_png(page, png, full_page=False)
                                if screenshot_mode in {"full-page", "both"}:
                                    png = output_dir / f"{base_name}--full-page.png"
                                    measurement["snapshots"]["fullPage"] = capture_png(page, png, full_page=True)
                                if not measurement["snapshots"]:
                                    raise RuntimeError(
                                        f"No PNG snapshots were written for {hierarchy['id']} / {candidate} / {viewport.name}."
                                    )
                                apply_semantic_contract_fit(hierarchy, measurement)
                                measurements.append(measurement)
                        context.close()
                finally:
                    browser.close()
        finally:
            pass

    best_by_hierarchy = best_by_hierarchy_viewport_rows(measurements)

    return {
        "kind": "mcel.flog.synthetic.layout.trial.report",
        "generatedAt": generated_at,
        "smokeLevel": "synthetic-hierarchy-layout-trials",
        "geometryEngine": "playwright-chromium",
        "hierarchySource": "generated-mcel-like-html",
        "chrome": chrome,
        "candidates": candidates,
        "hierarchies": [
            {
                "id": item["id"],
                "title": item["title"],
                "sourceApp": item.get("sourceApp", ""),
                "rootConcern": item["rootConcern"],
                "focusSlot": item["focusSlot"],
                "desiredFocusShare": item["desiredFocusShare"],
                "roleContract": item.get("roleContract", {}),
                "nodeSlots": [node["slot"] for node in item["nodes"]],
                "nodeRoles": {node["slot"]: node.get("role", "support") for node in item["nodes"]},
            }
            for item in hierarchies
        ],
        "viewports": [{"name": vp.name, "width": vp.width, "height": vp.height} for vp in viewports],
        "screenshotMode": screenshot_mode,
        "snapshotDirectory": ".",
        "snapshotFiles": [
            rel
            for measurement in measurements
            for rel in (measurement.get("snapshots") or {}).values()
        ],
        "semanticContracts": [semantic_contract_audit(item) for item in hierarchies],
        "bestByHierarchyViewport": best_by_hierarchy,
        "humanLoop": {
            "required": True,
            "reason": "The script can measure which synthetic layout trials use space best, but a human must decide whether the synthetic hierarchy matches the intended real app.",
        },
        "measurements": measurements,
    }




def measurement_quality_rank(status: str) -> int:
    return {"pass": 0, "watch": 1, "fail": 2}.get((status or "fail").lower(), 3)


def rollup_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    classification = item.get("classification") or {}
    return (
        measurement_quality_rank(classification.get("status", "fail")),
        -int(classification.get("score", 0)),
        item.get("candidate", ""),
    )


def preferred_snapshot_rel(item: dict[str, Any]) -> str:
    snapshots = item.get("snapshots") or {}
    return snapshots.get("viewport") or snapshots.get("fullPage") or next(iter(snapshots.values()), "")


def _rollup_short_reason(item: dict[str, Any], limit: int = 72) -> str:
    classification = item.get("classification") or {}
    fit = item.get("contractFit") or {}
    affordance_fit = item.get("affordanceFit") or {}
    reasons: list[str] = []
    if affordance_fit.get("hardRiskReasons"):
        reasons = [f"hard affordance risk: {text}" for text in (affordance_fit.get("hardRiskReasons") or [])]
    if not reasons and fit.get("hardRiskReasons"):
        reasons = [f"hard contract risk: {text}" for text in (fit.get("hardRiskReasons") or [])]
    if not reasons and affordance_fit.get("state") in {"weakAffordanceFit", "affordanceRisk"}:
        reasons = [f"affordance risk: {text}" for text in (affordance_fit.get("riskReasons") or [])]
    if not reasons and fit.get("state") in {"weakContractFit", "contractRisk"}:
        reasons = [f"contract risk: {text}" for text in (fit.get("riskReasons") or [])]
    if not reasons and fit.get("presentationSetReasons"):
        reasons = [f"phase: {text}" for text in fit.get("presentationSetReasons", [])]
    if not reasons and affordance_fit.get("positiveReasons"):
        reasons = [f"affordance: {text}" for text in affordance_fit.get("positiveReasons", [])]
    if not reasons and fit.get("positiveReasons"):
        reasons = [f"contract: {text}" for text in fit.get("positiveReasons", [])]
    if not reasons:
        reasons = list(classification.get("failureReasons") or [])
    if not reasons:
        reasons = list(classification.get("positiveReasons") or [])
    if not reasons:
        reasons = list(classification.get("reviewNotes") or [])
    if not reasons:
        return ""
    text = str(reasons[0]).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _draw_wrapped_text(draw: Any, xy: tuple[int, int], text: str, *, width: int, line_height: int = 12) -> None:
    import textwrap

    lines: list[str] = []
    for raw_line in text.splitlines() or [""]:
        wrapped = textwrap.wrap(raw_line, width=width) or [""]
        lines.extend(wrapped)
    draw.multiline_text(xy, "\n".join(lines), fill=(24, 24, 24), spacing=max(2, line_height - 10))


def generate_rollup_pngs(
    report: dict[str, Any],
    output_dir: Path,
    *,
    top_n: int = ROLLUP_TOP_N,
    columns: int = ROLLUP_COLUMNS,
) -> list[dict[str, Any]]:
    if Image is None or ImageDraw is None or ImageOps is None:
        raise RuntimeError(
            "Rollup PNG generation requires Pillow. Install it with 'python -m pip install pillow'."
        )

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for measurement in report.get("measurements", []):
        groups[(measurement["hierarchyId"], measurement["viewportProfile"])].append(measurement)

    rollups: list[dict[str, Any]] = []
    resampling = getattr(Image, "Resampling", Image)
    thumbnail_resample = getattr(resampling, "LANCZOS", getattr(Image, "LANCZOS", 1))

    for (hierarchy_id, viewport_profile), items in sorted(groups.items()):
        ordered = sorted(items, key=rollup_sort_key)[:top_n]
        if not ordered:
            continue

        used_columns = min(max(1, columns), max(1, len(ordered)))
        used_rows = (len(ordered) + used_columns - 1) // used_columns
        header_height = 32
        title_height = 20
        meta_height = 26
        reason_height = 44
        tile_height = ROLLUP_IMAGE_HEIGHT + title_height + meta_height + reason_height + 22
        canvas_width = (ROLLUP_CANVAS_MARGIN * 2) + (used_columns * ROLLUP_TILE_WIDTH) + ((used_columns - 1) * ROLLUP_TILE_GAP)
        canvas_height = (
            ROLLUP_CANVAS_MARGIN * 2
            + header_height
            + (used_rows * tile_height)
            + ((used_rows - 1) * ROLLUP_TILE_GAP)
        )

        canvas = Image.new("RGB", (canvas_width, canvas_height), (246, 246, 246))
        draw = ImageDraw.Draw(canvas)
        header_text = f"{hierarchy_id} / {viewport_profile} - ranked layouts 1-{len(ordered)} (best to worst)"
        draw.text((ROLLUP_CANVAS_MARGIN, ROLLUP_CANVAS_MARGIN), header_text, fill=(0, 0, 0))

        for index, item in enumerate(ordered):
            row, col = divmod(index, used_columns)
            x = ROLLUP_CANVAS_MARGIN + col * (ROLLUP_TILE_WIDTH + ROLLUP_TILE_GAP)
            y = ROLLUP_CANVAS_MARGIN + header_height + row * (tile_height + ROLLUP_TILE_GAP)

            draw.rectangle(
                [x, y, x + ROLLUP_TILE_WIDTH, y + tile_height],
                fill=(255, 255, 255),
                outline=(180, 180, 180),
                width=1,
            )

            image_left = x + 8
            image_top = y + 8
            image_box = (ROLLUP_TILE_WIDTH - 16, ROLLUP_IMAGE_HEIGHT)
            draw.rectangle(
                [image_left, image_top, image_left + image_box[0], image_top + image_box[1]],
                fill=(252, 252, 252),
                outline=(208, 208, 208),
                width=1,
            )

            rel = preferred_snapshot_rel(item)
            image_path = output_dir / rel
            with Image.open(image_path) as raw:
                child = raw.convert("RGB")
                thumb = ImageOps.contain(child, image_box, method=thumbnail_resample)
            paste_x = image_left + (image_box[0] - thumb.width) // 2
            paste_y = image_top + (image_box[1] - thumb.height) // 2
            canvas.paste(thumb, (paste_x, paste_y))

            classification = item.get("classification") or {}
            facts = item.get("geometryFacts") or {}
            title = f"#{index + 1} {item.get('candidate', 'unknown')}"
            draw.text((x + 8, y + 8 + ROLLUP_IMAGE_HEIGHT + 4), title, fill=(0, 0, 0))

            focus_share = float(facts.get("focusShare", 0.0) or 0.0)
            target_share = float(facts.get("desiredFocusShare", 0.0) or 0.0)
            status = classification.get("status", "fail")
            contract_score = classification.get("contractFitScore", 0)
            affordance_score = classification.get("affordanceFitScore", 0)
            meta = f"score {classification.get('score', 0)} · {status} · contract {contract_score}% · afford {affordance_score}% · focus {focus_share:.0%}/{target_share:.0%}"
            draw.text((x + 8, y + 8 + ROLLUP_IMAGE_HEIGHT + 20), meta, fill=(32, 32, 32))

            reason = _rollup_short_reason(item)
            if reason:
                _draw_wrapped_text(
                    draw,
                    (x + 8, y + 8 + ROLLUP_IMAGE_HEIGHT + 38),
                    reason,
                    width=44,
                    line_height=12,
                )

        file_name = f"{slugify(hierarchy_id)}--{slugify(viewport_profile)}--rollup.png"
        rel_path = Path(file_name)
        canvas.save(output_dir / rel_path)
        rollups.append(
            {
                "hierarchyId": hierarchy_id,
                "viewportProfile": viewport_profile,
                "file": rel_path.as_posix(),
                "candidates": [item.get("candidate", "") for item in ordered],
                "columns": used_columns,
                "rows": used_rows,
                "topCount": len(ordered),
            }
        )

    return rollups


def write_reports(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "layout-snapshot-report.json"
    md_path = output_dir / "layout-snapshot-report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    lines: list[str] = []
    lines.append("# FLOG Synthetic Layout Trial Report")
    lines.append("")
    lines.append(f"- Generated: `{report['generatedAt']}`")
    lines.append(f"- Smoke level: `{report['smokeLevel']}`")
    lines.append(f"- Geometry engine: `{report['geometryEngine']}`")
    lines.append(f"- Hierarchy source: `{report['hierarchySource']}`")
    lines.append(f"- Chrome/theme input: `{report['chrome']}`")
    lines.append(f"- Layout candidates: `{', '.join(report['candidates'])}`")
    lines.append(f"- Viewports: `{', '.join(v['name'] for v in report['viewports'])}`")
    lines.append(f"- PNG directory: `{report['snapshotDirectory']}`")
    files = report.get("snapshotFiles") or []
    lines.append(f"- PNG files written: `{len(files)}`")
    if files:
        lines.append(f"- First PNG: `{files[0]}`")
    lines.append("")

    lines.append("## What this proves vs what it does not")
    lines.append("")
    lines.append("This smoke generates MCEL-like hierarchies, applies multiple candidate layouts, measures the rendered geometry in Chromium, and writes one PNG proof per trial.")
    lines.append("It proves rectangles, focus share, unclaimed node area, clipping, hidden controls, and scroll pressure for the synthetic cases.")
    lines.append("It does **not** prove that the live app hierarchy is good enough yet; the synthetic hierarchies are training/evidence fixtures for the FLOG method.")
    lines.append("")

    lines.append("## Synthetic hierarchies")
    lines.append("")
    for item in report["hierarchies"]:
        lines.append(f"### `{item['id']}`")
        lines.append("")
        lines.append(f"- Title: {item['title']}")
        if item.get("sourceApp"):
            lines.append(f"- Source app seed: `{item['sourceApp']}`")
        lines.append(f"- Root concern: `{item['rootConcern']}`")
        lines.append(f"- Focus slot: `{item['focusSlot']}`")
        lines.append(f"- Desired focus share: `{item['desiredFocusShare']}`")
        contract = item.get("roleContract") or {}
        lines.append(f"- Required companions: `{', '.join(contract.get('requiredCompanions', []))}`")
        lines.append(f"- Nearby companions: `{', '.join(contract.get('nearbyCompanions', []))}`")
        lines.append(f"- Deferable slots: `{', '.join(contract.get('deferableSlots', []))}`")
        lines.append(f"- Forbidden default-hidden: `{', '.join(contract.get('forbiddenDefaultHidden', []))}`")
        lines.append(f"- Preferred families: `{', '.join(contract.get('preferredFamilies', []))}`")
        lines.append(f"- Slots: `{', '.join(item['nodeSlots'])}`")
        if item.get("nodeRoles"):
            role_text = ", ".join(f"{slot}:{role}" for slot, role in item["nodeRoles"].items())
            lines.append(f"- Slot roles: `{role_text}`")
        lines.append("")

    if report.get("semanticContracts"):
        lines.append("## Generic semantic contract audit")
        lines.append("")
        lines.append(
            "These audits use composable MCEL primitives such as `controls`, `selects`, `reflects`, `confirms`, and `proves`. They do not rely on high-level app archetype labels."
        )
        lines.append("")
        for audit in report.get("semanticContracts", []):
            grammar = ", ".join(audit.get("inferredLayoutGrammar", [])) or "none"
            lines.append(
                f"- `{audit['hierarchyId']}`: state=`{audit['state']}` "
                f"focus=`{audit['focusSlot']}` primitiveEdges=`{audit.get('primitiveEdgeCount', 0)}` "
                f"layoutPressures=`{audit.get('layoutPressureCount', 0)}` affordances=`{audit.get('affordanceExpectationCount', 0)}` confidence=`{audit.get('contractConfidence', 0):.3f}` grammar=`{grammar}`"
            )
            if audit.get("missingPrimitives"):
                lines.append("  - Missing primitives:")
                for missing in audit.get("missingPrimitives", [])[:8]:
                    lines.append(f"    - {missing}")
            else:
                lines.append("  - Missing primitives: `none`")
            sample = audit.get("relationships", [])[:6]
            if sample:
                lines.append(f"  - Relationship sample: `{'; '.join(sample)}`")
            phase_sets = audit.get("presentationSets", [])[:4]
            if phase_sets:
                phase_text = "; ".join(
                    f"{item['phase']}=[{', '.join(item.get('requiredSlots', []))}]"
                    for item in phase_sets
                )
                lines.append(f"  - Presentation sets: `{phase_text}`")
            quality_counts = audit.get("qualityCounts", {})
            if quality_counts:
                keys = ["phase", "availability", "presentationSet", "relationshipStrength", "hardConstraints", "softPreferences"]
                quality_text = ", ".join(f"{key}={quality_counts.get(key, 0)}" for key in keys)
                lines.append(f"  - Generic quality tags: `{quality_text}`")
            affordances = audit.get("affordanceExpectations", [])[:8]
            if affordances:
                lines.append(f"  - Affordance expectations: `{', '.join(affordances)}`")
        lines.append("")

    lines.append("## Best candidate by hierarchy and viewport")
    lines.append("")
    lines.append("`selectionState=bestPassingCandidate` means the row is the best `pass`/`watch` trial. `selectionState=noPassingCandidate` means every trial failed and the row is only the highest-scoring failure.")
    lines.append("")
    for item in report["bestByHierarchyViewport"]:
        selection_state = item.get("selectionState", "bestPassingCandidate")
        no_passing = bool(item.get("noPassingCandidate"))
        lines.append(f"- `{item['hierarchyId']}` / `{item['viewportProfile']}`: `{item['candidate']}` score=`{item['score']}` status=`{item['status']}` selectionState=`{selection_state}` unclaimed=`{item['unclaimedAreaRatio']:.4f}` focus=`{item['focusShare']:.4f}` target=`{item['desiredFocusShare']:.2f}` occupancy=`{item.get('usefulFocusOccupancy', 0):.4f}` proximity=`{item.get('companionProximityScore', 1):.4f}` contractFit=`{item.get('contractFitScore', 0)}` affordanceFit=`{item.get('affordanceFitScore', 0)}` contractState=`{item.get('contractFitState', 'notEvaluated')}` affordanceState=`{item.get('affordanceFitState', 'notEvaluated')}`")
        if no_passing:
            highest = item.get("highestScoringFailure") or item.get("highestScoringCandidate") or {}
            lines.append(f"  - No passing candidate: showing highest-scoring failure `{highest.get('candidate', item['candidate'])}` score=`{highest.get('score', item['score'])}` status=`{highest.get('status', item['status'])}`.")
        else:
            highest = item.get("highestScoringCandidate") or {}
            if highest and highest.get("candidate") != item.get("candidate"):
                lines.append(f"  - Selected best passing/watch candidate; highest raw score was `{highest.get('candidate')}` score=`{highest.get('score')}` status=`{highest.get('status')}`.")
        snaps = item.get("snapshots") or {}
        if snaps:
            lines.append(f"  - PNG: `{next(iter(snaps.values()))}`")
        if item.get("contractFitReasons"):
            lines.append("  - Contract fit evidence:")
            for reason in item.get("contractFitReasons", [])[:4]:
                lines.append(f"    - {reason}")
        if item.get("affordanceFitReasons"):
            lines.append("  - Affordance realization evidence:")
            for reason in item.get("affordanceFitReasons", [])[:4]:
                lines.append(f"    - {reason}")
        if item.get("hardAffordanceRisks"):
            lines.append("  - Hard affordance risks:")
            for reason in item.get("hardAffordanceRisks", [])[:4]:
                lines.append(f"    - {reason}")
        if item.get("affordanceFitRisks"):
            lines.append("  - Affordance fit risks:")
            for reason in item.get("affordanceFitRisks", [])[:4]:
                lines.append(f"    - {reason}")
        if item.get("hardContractRisks"):
            lines.append("  - Hard contract risks:")
            for reason in item.get("hardContractRisks", [])[:4]:
                lines.append(f"    - {reason}")
        if item.get("contractFitRisks"):
            lines.append("  - Contract fit risks:")
            for reason in item.get("contractFitRisks", [])[:4]:
                lines.append(f"    - {reason}")
        if item.get("presentationSetReasons"):
            lines.append("  - Phase co-presence evidence:")
            for reason in item.get("presentationSetReasons", [])[:4]:
                lines.append(f"    - {reason}")
        if item.get("reasons"):
            lines.append("  - Why it ranked well:")
            for reason in item.get("reasons", [])[:6]:
                lines.append(f"    - {reason}")
        if item.get("failureReasons"):
            lines.append("  - Remaining failures / risks:")
            for reason in item.get("failureReasons", [])[:6]:
                lines.append(f"    - {reason}")
        if item.get("reviewNotes"):
            lines.append("  - Human review notes:")
            for note in item.get("reviewNotes", [])[:4]:
                lines.append(f"    - {note}")
    lines.append("")

    if report.get("rollups"):
        lines.append("## Rollup PNGs")
        lines.append("")
        lines.append(
            "Each hierarchy/viewport gets one compact rollup PNG. The rollup orders the top layouts from best to worst and packs up to 8 tiles into a 4×2 grid."
        )
        lines.append("")
        for item in report.get("rollups", []):
            lines.append(
                f"- `{item['hierarchyId']}` / `{item['viewportProfile']}`: `{item['file']}` "
                f"(tiles=`{item.get('topCount', 0)}`, grid=`{item.get('columns', 0)}x{item.get('rows', 0)}`)"
            )
            if item.get("candidates"):
                lines.append(f"  - Included layouts: `{', '.join(item['candidates'])}`")
        lines.append("")

    lines.append("## All trial measurements")
    lines.append("")
    for item in report["measurements"]:
        facts = item.get("geometryFacts", {})
        classification = item.get("classification", {})
        lines.append(f"### `{item['hierarchyId']}` / `{item['viewportProfile']}` / `{item['candidate']}`")
        lines.append("")
        lines.append(f"- Status: `{classification.get('status')}`")
        lines.append(f"- Score: `{classification.get('score')}`")
        lines.append(f"- Geometry score: `{classification.get('geometryScore', classification.get('score'))}`")
        lines.append(f"- Contract fit score: `{classification.get('contractFitScore', 0)}` state=`{classification.get('contractFitState', 'notEvaluated')}`")
        lines.append(f"- Affordance fit score: `{classification.get('affordanceFitScore', 0)}` state=`{classification.get('affordanceFitState', 'notEvaluated')}`")
        lines.append(f"- Focus slot: `{item.get('focusSlot')}`")
        lines.append(f"- Unclaimed layout area ratio: `{facts.get('unclaimedAreaRatio', 0):.4f}`")
        lines.append(f"- Node coverage ratio: `{facts.get('nodeCoverageRatio', 0):.4f}`")
        lines.append(f"- Focus share: `{facts.get('focusShare', 0):.4f}`")
        lines.append(f"- Desired focus share: `{facts.get('desiredFocusShare', 0):.4f}`")
        lines.append(f"- Focus deviation: `{facts.get('focusDeviation', 0):.4f}`")
        lines.append(f"- Useful focus occupancy: `{facts.get('usefulFocusOccupancy', 0):.4f}`")
        lines.append(f"- Focus content count: `{facts.get('focusContentCount', 0)}`")
        lines.append(f"- Required companion visibility: `{facts.get('companionVisibilityRatio', 1):.4f}`")
        lines.append(f"- Nearby companion fit: `{facts.get('nearbyCompanionRatio', 1):.4f}`")
        lines.append(f"- Companion proximity score: `{facts.get('companionProximityScore', 1):.4f}`")
        if facts.get("missingRequiredCompanions"):
            lines.append("- Missing required companions:")
            for companion in facts.get("missingRequiredCompanions", []):
                lines.append(f"  - `{companion.get('slot')}` visibleShare=`{companion.get('visibleShare', 0):.4f}`")
        if facts.get("distantNearbyCompanions"):
            lines.append("- Nearby companion problems:")
            for companion in facts.get("distantNearbyCompanions", []):
                integration = companion.get("integration") or {}
                lines.append(f"  - `{companion.get('slot')}` distance=`{companion.get('normalizedDistance', 0):.4f}` visibleShare=`{companion.get('visibleShare', 0):.4f}` effectiveMin=`{companion.get('effectiveMinVisibleShare', companion.get('minVisibleShare', 0)):.4f}` integration=`{integration.get('mode', 'distance-only')}` score=`{integration.get('score', 0):.4f}`")
        lines.append(f"- Clipped critical controls: `{facts.get('clippedCriticalControlCount', 0)}`")
        lines.append(f"- Hidden critical controls: `{facts.get('hiddenCriticalControlCount', 0)}`")
        lines.append(f"- Scroll owners: `{facts.get('scrollOwnerCount', 0)}`")
        snaps = item.get("snapshots", {})
        if snaps:
            lines.append("- PNG snapshots:")
            for key, rel in snaps.items():
                lines.append(f"  - `{key}`: `{rel}`")
        contract_fit = item.get("contractFit") or {}
        if contract_fit:
            lines.append("- Generic contract fit:")
            lines.append(f"  - Score: `{contract_fit.get('score', 0)}` raw=`{contract_fit.get('rawScore', contract_fit.get('score', 0))}` state=`{contract_fit.get('state', 'unknown')}` confidence=`{contract_fit.get('confidence', 0):.3f}`")
            if contract_fit.get("contractLimits"):
                for limit in contract_fit.get("contractLimits", [])[:4]:
                    lines.append(f"  - Contract limit: {limit}")
            for reason in contract_fit.get("presentationSetReasons", [])[:4]:
                lines.append(f"  - Phase co-presence: {reason}")
            for reason in contract_fit.get("positiveReasons", [])[:4]:
                lines.append(f"  - Preserved: {reason}")
            for reason in contract_fit.get("hardRiskReasons", [])[:4]:
                lines.append(f"  - Hard risk: {reason}")
            for reason in contract_fit.get("riskReasons", [])[:4]:
                lines.append(f"  - Risk: {reason}")
        affordance_fit = item.get("affordanceFit") or {}
        if affordance_fit:
            lines.append("- Generic affordance realization:")
            lines.append(f"  - Score: `{affordance_fit.get('score', 0)}` raw=`{affordance_fit.get('rawScore', affordance_fit.get('score', 0))}` state=`{affordance_fit.get('state', 'unknown')}`")
            if affordance_fit.get("limits"):
                for limit in affordance_fit.get("limits", [])[:4]:
                    lines.append(f"  - Affordance limit: {limit}")
            for reason in affordance_fit.get("positiveReasons", [])[:4]:
                lines.append(f"  - Realized: {reason}")
            for reason in affordance_fit.get("hardRiskReasons", [])[:4]:
                lines.append(f"  - Hard risk: {reason}")
            for reason in affordance_fit.get("riskReasons", [])[:4]:
                lines.append(f"  - Risk: {reason}")
        reasons = classification.get("positiveReasons") or []
        if reasons:
            lines.append("- Why this candidate worked:")
            for reason in reasons:
                lines.append(f"  - {reason}")
        failures = classification.get("failureReasons") or []
        if failures:
            lines.append("- Why this candidate failed or needs caution:")
            for reason in failures:
                lines.append(f"  - {reason}")
        review_notes = classification.get("reviewNotes") or []
        if review_notes:
            lines.append("- Human review notes:")
            for note in review_notes:
                lines.append(f"  - {note}")
        warnings = classification.get("warnings") or []
        if warnings:
            lines.append("- Warnings:")
            for warning in warnings:
                lines.append(f"  - {warning}")
        human = item.get("humanLoop", {})
        lines.append("- Proved by Chromium:")
        for entry in human.get("proved", []):
            lines.append(f"  - {entry}")
        lines.append("- Inferred, not proved:")
        for entry in human.get("inferred", []):
            lines.append(f"  - {entry}")
        lines.append("- Unknown / needs human review:")
        for entry in human.get("unknowns", []):
            lines.append(f"  - {entry}")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FLOG synthetic MCEL hierarchy layout trials with PNG proof.")
    parser.add_argument("--repo", default=".", help="Repository root. Default: current directory.")
    parser.add_argument("--hierarchies", default=DEFAULT_HIERARCHIES, help="Comma-separated synthetic hierarchy IDs or 'all'.")
    parser.add_argument("--candidates", default=DEFAULT_CANDIDATES, help="Comma-separated layout candidates or 'all'.")
    parser.add_argument("--viewports", default=DEFAULT_VIEWPORTS, help="Comma-separated profiles like desktop=1440x900,narrow=390x844.")
    parser.add_argument("--chrome", default=DEFAULT_CHROME, help="Chrome/theme label to record as layout input.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output report directory. PNGs are written directly here.")
    parser.add_argument(
        "--screenshot-mode",
        choices=["viewport", "full-page", "both"],
        default="viewport",
        help="Which PNG snapshots to write per trial. Default: viewport.",
    )
    parser.add_argument("--keep-html", action="store_true", help="Write each generated synthetic trial HTML into the output directory.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    output_dir = (repo / args.output_dir).resolve() if not Path(args.output_dir).is_absolute() else Path(args.output_dir).resolve()

    available = synthetic_hierarchies()
    hierarchies = parse_hierarchies(args.hierarchies, available)
    candidates = parse_candidates(args.candidates)
    viewports = parse_viewports(args.viewports)

    report = run_synthetic_trials(
        hierarchies=hierarchies,
        candidates=candidates,
        viewports=viewports,
        chrome=args.chrome,
        output_dir=output_dir,
        screenshot_mode=args.screenshot_mode,
        keep_html=args.keep_html,
    )
    report['rollups'] = generate_rollup_pngs(report, output_dir)
    report['rollupFiles'] = [item['file'] for item in report['rollups']]
    json_path, md_path = write_reports(report, output_dir)
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    png_count = 0
    for measurement in report["measurements"]:
        for rel in (measurement.get("snapshots") or {}).values():
            png_count += 1
            print(f"Wrote PNG {output_dir / rel}")
    for rollup in report.get('rollups', []):
        print(f"Wrote rollup PNG {output_dir / rollup['file']}")
    if png_count == 0:
        raise SystemExit("No PNG snapshots were written; refusing to call this FLOG trial successful.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
