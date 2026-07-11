#!/usr/bin/env python3
"""FLOG synthetic layout trial smoke with PNG proof.

This script is intentionally not a live-app layout fixer.  It creates eleven
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
import copy
import html
import itertools
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
ROLLUP_TILE_WIDTH = 176
ROLLUP_IMAGE_HEIGHT = 92
ROLLUP_TILE_GAP = 8
ROLLUP_CANVAS_MARGIN = 14
ROLLUP_ROW_LABEL_WIDTH = 210
ROLLUP_FILE_NAME = "layout-snapshot-final-rollup.png"

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
    "phasePersistence",
    "defaultRealization",
)

LAYOUT_CANDIDATES = [
    "source-order-stacked",
    "split-pane",
    "sectioned-sidebar",
    "inspector",
    "dashboard-grid",
    "focus-priority",
    "bounded-drawer",
    "top-band-dominant-surface",
    "top-band-focus-overlay",
    "selected-context-workflow",
    "progressive-workflow",
    "workflow-with-proof-drawer",
]

ACCEPTABLE_LAYOUT_STATUSES = {"pass", "watch"}

PHASE_AWARE_CANDIDATES = {
    "bounded-drawer",
    "top-band-dominant-surface",
    "top-band-focus-overlay",
    "selected-context-workflow",
    "progressive-workflow",
    "workflow-with-proof-drawer",
}


@dataclass(frozen=True)
class ViewportProfile:
    name: str
    width: int
    height: int


def slugify(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value).strip()).strip("-")
    return clean.lower() or "item"



def candidate_identity(candidate: str | dict[str, Any]) -> str:
    """Return the stable report/file identity for a legacy or composed candidate."""

    if isinstance(candidate, dict):
        return str(candidate.get("id") or candidate.get("candidate") or "composition")
    return str(candidate)


def candidate_render_family(candidate: str | dict[str, Any]) -> str:
    """Return the CSS family used to realize a candidate.

    Recursive compositions deliberately use a neutral render family.  Their local
    policy tuple, rather than a legacy whole-page family name, drives the layout.
    """

    if isinstance(candidate, dict):
        return str(candidate.get("renderFamily") or "recursive-composition")
    return str(candidate)


def candidate_mode(candidate: str | dict[str, Any]) -> str:
    if isinstance(candidate, dict):
        return str(candidate.get("mode") or "recursive-composition")
    return "legacy-family"


def candidate_composition_override(
    candidate: str | dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(candidate, dict):
        return None
    value = candidate.get("composition")
    return copy.deepcopy(value) if isinstance(value, dict) else None


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


def _phase_persistence_for_node(node: dict[str, Any]) -> str:
    """Return the generic persistence mode for phase-aware layout.

    This is deliberately not an app type.  It distinguishes continuously-active
    UI regions from regions that are only required while a task phase is active.
    """

    semantics = node.get("semantics") or {}
    declared = _as_list(semantics.get("phasePersistence"))
    if declared:
        return declared[0]
    if node.get("visibility") == "deferable":
        return "deferable"
    if node.get("visibility") in {"phase", "phase-specific"}:
        return "phase-specific"
    if node.get("role") == "status" or _is_hard_semantic_requirement(node):
        return "persistent"
    return "default"


def _default_realization_for_node(node: dict[str, Any]) -> str:
    semantics = node.get("semantics") or {}
    declared = _as_list(semantics.get("defaultRealization"))
    if declared:
        return declared[0]
    persistence = _phase_persistence_for_node(node)
    if persistence in {"deferable", "phase-specific", "phase-specific-selector", "phase-specific-support"}:
        return "collapsed-trigger"
    return "visible-region"


def _is_phase_specific_node(node: dict[str, Any]) -> bool:
    persistence = _phase_persistence_for_node(node)
    return persistence in {"phase-specific", "phase-specific-selector", "phase-specific-support", "deferable"}


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

        if not _as_list(semantics.get("phasePersistence")):
            if node.get("visibility") == "deferable":
                _add_semantic(semantics, "phasePersistence", "deferable")
            elif node.get("visibility") in {"phase", "phase-specific"}:
                _add_semantic(semantics, "phasePersistence", "phase-specific")
            elif role in {"focus", "command", "status"} or node.get("priority") == "primary":
                _add_semantic(semantics, "phasePersistence", "persistent")
            else:
                _add_semantic(semantics, "phasePersistence", "default")
        if not _as_list(semantics.get("defaultRealization")):
            if _phase_persistence_for_node({"visibility": node.get("visibility"), "role": role, "semantics": semantics}) in {"deferable", "phase-specific", "phase-specific-selector", "phase-specific-support"}:
                _add_semantic(semantics, "defaultRealization", "collapsed-trigger")
            else:
                _add_semantic(semantics, "defaultRealization", "visible-region")

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
            if _is_phase_specific_node(source_node):
                kind = f"{primitive}-phase-selector-access"
                expectation = "phase-selector-access"
                weight = 8 if primitive in {"navigates", "scopes"} else 7
                hard = False
            else:
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

    if expectation == "phase-selector-access":
        # A phase selector only needs a default access affordance.  A full side rail
        # may be fine, but it should not be required just because the selector is
        # semantically meaningful during the selection phase.
        if side or band:
            return 96
        if near:
            return max(72, round(distance_score * 84))
        return 64

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
    realization = _default_realization_for_node(node)
    if realization == "collapsed-trigger" and role in {"navigation", "collection"}:
        return "phase-selector-trigger"
    if realization == "collapsed-trigger" and role == "evidence":
        return "proof-trigger-or-drawer"
    if realization == "collapsed-trigger" and role in {"detail", "inspector"}:
        return "deferable-inspector-trigger"
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

    if target in {"phase-selector-trigger", "proof-trigger-or-drawer", "deferable-inspector-trigger"}:
        if area_share <= 0.075:
            base = 96
        elif area_share <= 0.13:
            base = 88
        elif side or band:
            base = 78
        elif near:
            base = 72
        else:
            base = 58
    elif target == "command-rail":
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



def semantic_phase_scenarios(hierarchy: dict[str, Any]) -> list[dict[str, Any]]:
    """Return explicit phase scenarios, falling back to presentation sets.

    Explicit scenarios let Git Tools be tested as a changing workflow: project
    selection, selected-project default, planning, execution, proof review, and
    recovery can each declare different dominant, persistent, active-support, and
    collapsed-support obligations.  This keeps the model generic while avoiding a
    single static "show every panel" interpretation.
    """

    scenarios = list(hierarchy.get("phaseScenarios") or [])
    if scenarios:
        return scenarios

    fallback: list[dict[str, Any]] = []
    focus_slot = hierarchy["focusSlot"]
    for phase_set in semantic_presentation_sets(hierarchy):
        required = list(phase_set.get("requiredSlots") or [])
        optional = list(phase_set.get("optionalSlots") or [])
        fallback.append(
            {
                "phase": phase_set["phase"],
                "dominantSlot": focus_slot,
                "requiredSlots": required,
                "activeSupportSlots": [],
                "collapsedSlots": optional,
                "minDominantShare": hierarchy.get("minFocusShare", 0.34),
                "targetDominantShare": hierarchy.get("desiredFocusShare", 0.5),
                "maxInactiveTax": 0.18,
                "weight": 1.0,
                "reason": phase_set.get("reason", ""),
            }
        )
    return fallback


def candidate_phase_policy(candidate: str | dict[str, Any]) -> dict[str, Any]:
    """Return realization capabilities for a legacy family or composed policy tuple."""

    identity = candidate_identity(candidate)
    render_family = candidate_render_family(candidate)
    composition = candidate_composition_override(candidate) or {}
    support_policy = str(
        (composition.get("unitPolicies") or {}).get("phase-support") or ""
    )
    phase_aware = (
        candidate_mode(candidate) == "recursive-composition"
        or identity.startswith("compose--")
        or render_family in PHASE_AWARE_CANDIDATES
    )
    if support_policy in {"bounded-bottom-drawer", "inline-phase-stage"}:
        placement = "bottom-drawer" if support_policy == "bounded-bottom-drawer" else "inline-stage"
    elif support_policy in {"bounded-side-drawer", "one-active-plus-triggers"}:
        placement = "side-drawer"
    elif render_family == "workflow-with-proof-drawer":
        placement = "proof-drawer"
    elif render_family == "bounded-drawer":
        placement = "bottom-drawer"
    elif phase_aware:
        placement = "side-drawer"
    else:
        placement = "static-panel"
    return {
        "candidate": identity,
        "renderFamily": render_family,
        "candidateMode": candidate_mode(candidate),
        "phaseAware": phase_aware,
        "supportsCompactTriggers": phase_aware,
        "activeSupportPlacement": placement,
    }



def layout_unit_specs(hierarchy: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate and flatten the recursive layout-unit tree.

    Layout units mirror render responsibilities rather than repository folders.  A
    leaf owns semantic slots; parent units compose child responsibilities.  Every
    slot is owned exactly once so local failures can be attributed without double
    counting.
    """

    tree = hierarchy.get("layoutUnitTree")
    if not tree:
        return []

    known_slots = {node["slot"] for node in hierarchy.get("nodes", [])}
    seen_ids: set[str] = set()
    slot_owners: dict[str, str] = {}
    flattened: list[dict[str, Any]] = []

    def walk(unit: dict[str, Any], path: tuple[str, ...], parent: str | None) -> set[str]:
        unit_id = str(unit.get("id") or "").strip()
        if not unit_id:
            raise ValueError(
                f"Hierarchy {hierarchy.get('id', '<unknown>')} has a layout unit without an id."
            )
        if unit_id in seen_ids:
            raise ValueError(
                f"Hierarchy {hierarchy.get('id', '<unknown>')} repeats layout unit {unit_id!r}."
            )
        seen_ids.add(unit_id)
        current_path = (*path, unit_id)
        own_slots = [str(slot) for slot in (unit.get("slots") or [])]
        unknown = sorted(set(own_slots) - known_slots)
        if unknown:
            raise ValueError(
                f"Layout unit {unit_id!r} references unknown slot(s): {', '.join(unknown)}"
            )
        for slot in own_slots:
            previous = slot_owners.get(slot)
            if previous:
                raise ValueError(
                    f"Slot {slot!r} is owned by both layout units {previous!r} and {unit_id!r}."
                )
            slot_owners[slot] = unit_id

        descendant_slots = set(own_slots)
        children = list(unit.get("children") or [])
        record = {
            **copy.deepcopy(unit),
            "id": unit_id,
            "parentId": parent,
            "path": list(current_path),
            "depth": len(current_path) - 1,
            "slots": own_slots,
            "childIds": [str(child.get("id") or "") for child in children],
        }
        flattened.append(record)
        for child in children:
            descendant_slots.update(walk(child, current_path, unit_id))
        record["descendantSlots"] = sorted(descendant_slots)
        record["leaf"] = not children
        return descendant_slots

    all_owned = walk(tree, (), None)
    missing = sorted(known_slots - all_owned)
    if missing:
        raise ValueError(
            f"Hierarchy {hierarchy.get('id', '<unknown>')} layout units do not own slot(s): "
            f"{', '.join(missing)}"
        )
    return flattened



def layout_unit_dataflow_audit(hierarchy: dict[str, Any]) -> dict[str, Any]:
    """Audit the responsibility graph that feeds the recursive layout units."""

    specs = layout_unit_specs(hierarchy)
    if not specs:
        return {
            "state": "notDeclared",
            "unitCount": 0,
            "edges": [],
            "missingInputs": [],
            "cycles": [],
        }

    root = next(unit for unit in specs if unit.get("parentId") is None)
    external = set(str(value) for value in (root.get("externalInputs") or []))
    leaves = [unit for unit in specs if unit.get("leaf")]
    producer_by_signal: dict[str, str] = {}
    duplicate_outputs: list[str] = []
    for unit in leaves:
        for signal in unit.get("outputs") or []:
            signal = str(signal)
            if signal in producer_by_signal:
                duplicate_outputs.append(signal)
            else:
                producer_by_signal[signal] = unit["id"]

    edges: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []
    adjacency: dict[str, set[str]] = defaultdict(set)
    for unit in leaves:
        for signal in unit.get("inputs") or []:
            signal = str(signal)
            producer = producer_by_signal.get(signal)
            if producer:
                edges.append(
                    {
                        "sourceUnit": producer,
                        "targetUnit": unit["id"],
                        "signal": signal,
                    }
                )
                if producer != unit["id"]:
                    adjacency[producer].add(unit["id"])
            elif signal not in external:
                missing.append({"unitId": unit["id"], "signal": signal})

    cycles: list[list[str]] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(unit_id: str, stack: list[str]) -> None:
        if unit_id in visiting:
            start = stack.index(unit_id) if unit_id in stack else 0
            cycles.append([*stack[start:], unit_id])
            return
        if unit_id in visited:
            return
        visiting.add(unit_id)
        for target in adjacency.get(unit_id, set()):
            visit(target, [*stack, unit_id])
        visiting.remove(unit_id)
        visited.add(unit_id)

    for unit in leaves:
        visit(unit["id"], [])

    state = (
        "complete"
        if not missing and not cycles and not duplicate_outputs
        else "incomplete"
    )
    return {
        "state": state,
        "unitCount": len(leaves),
        "externalInputs": sorted(external),
        "signals": sorted(producer_by_signal),
        "edges": edges,
        "missingInputs": missing,
        "duplicateOutputs": sorted(set(duplicate_outputs)),
        "cycles": cycles,
        "parallelRoots": sorted(
            unit["id"]
            for unit in leaves
            if not any(edge["targetUnit"] == unit["id"] for edge in edges)
        ),
        "note": (
            "Signals describe responsibility ownership and render feeds, not Python "
            "module imports or repository directory boundaries."
        ),
    }


def layout_unit_slot_map(hierarchy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the leaf unit record responsible for each semantic slot."""

    mapping: dict[str, dict[str, Any]] = {}
    for unit in layout_unit_specs(hierarchy):
        for slot in unit.get("slots", []):
            mapping[slot] = unit
    return mapping


OVERLAY_LAYOUT_UNIT_POLICIES: dict[str, dict[str, Any]] = {
    "selector-overlay": {
        "overlayTarget": "workflow",
        "maxOcclusionShare": 0.10,
    },
    "workflow-footer-overlay": {
        "overlayTarget": "workflow",
        "maxOcclusionShare": 0.06,
    },
}


def layout_unit_ownership_spec(
    policy: str,
    realization: str = "active",
) -> dict[str, Any]:
    """Declare intended painted-space ownership for shadow geometry diagnostics."""

    normalized_policy = str(policy or "source-order")
    normalized_realization = str(realization or "active")
    if normalized_realization == "absent":
        return {
            "mode": "absent",
            "overlayTarget": "",
            "maxOcclusionShare": 0.0,
        }
    if normalized_realization == "trigger-only":
        return {
            "mode": "trigger",
            "overlayTarget": "",
            "maxOcclusionShare": 0.0,
        }
    overlay = OVERLAY_LAYOUT_UNIT_POLICIES.get(normalized_policy)
    if overlay:
        return {
            "mode": "overlay",
            "overlayTarget": str(overlay["overlayTarget"]),
            "maxOcclusionShare": float(overlay["maxOcclusionShare"]),
        }
    return {
        "mode": "partition",
        "overlayTarget": "",
        "maxOcclusionShare": 0.0,
    }


def layout_unit_policy_catalog(
    hierarchy: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Return normalized local policy alternatives for every recursive unit."""

    catalog: dict[str, list[dict[str, Any]]] = {}
    for unit in layout_unit_specs(hierarchy):
        declared = list(unit.get("policyCandidates") or [])
        if not declared:
            declared = [
                {
                    "policy": unit.get("defaultPolicy") or "source-order",
                    "alias": slugify(unit.get("defaultPolicy") or "source-order"),
                    "preflightScore": 80,
                }
            ]
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in declared:
            if isinstance(entry, str):
                item = {"policy": entry}
            else:
                item = copy.deepcopy(entry)
            policy = str(item.get("policy") or "").strip()
            if not policy or policy in seen:
                continue
            seen.add(policy)
            normalized.append(
                {
                    **item,
                    "policy": policy,
                    "alias": slugify(item.get("alias") or policy),
                    "preflightScore": int(item.get("preflightScore", 80) or 80),
                }
            )
        if not normalized:
            raise ValueError(f"Layout unit {unit['id']!r} declares no usable policies.")
        catalog[unit["id"]] = normalized
    return catalog


def _composition_compatibility_penalty(unit_policies: dict[str, str]) -> int:
    """Reject obvious resource collisions before expensive browser rendering."""

    penalty = 0
    command = unit_policies.get("command-workflow", "")
    identity = unit_policies.get("project-identity", "")
    feedback = unit_policies.get("persistent-feedback", "")
    support = unit_policies.get("phase-support", "")
    if command == "side-command-rail" and support == "bounded-side-drawer":
        penalty += 12
    if identity == "compact-project-rail" and support == "bounded-side-drawer":
        penalty += 8
    if feedback == "workflow-footer-overlay" and support == "bounded-bottom-drawer":
        penalty += 10
    if feedback == "stacked-feedback" and support == "inline-phase-stage":
        penalty += 6
    return penalty


def recursive_composition_candidate_specs(
    hierarchy: dict[str, Any],
    *,
    max_candidates: int = 12,
) -> list[dict[str, Any]]:
    """Generate a bounded, diverse beam of policy tuples.

    Local alternatives are scored before whole-application rendering.  The browser
    then scores each retained tuple at both local-unit and application levels.
    """

    specs = layout_unit_specs(hierarchy)
    if not specs:
        return []
    catalog = layout_unit_policy_catalog(hierarchy)
    root = next(unit for unit in specs if unit.get("parentId") is None)
    leaves = [unit for unit in specs if unit.get("leaf")]
    ordered_units = [root, *leaves]
    option_lists = [catalog[unit["id"]] for unit in ordered_units]

    generated: list[dict[str, Any]] = []
    for selected in itertools.product(*option_lists):
        root_option = selected[0]
        leaf_options = selected[1:]
        unit_policies = {
            unit["id"]: option["policy"]
            for unit, option in zip(leaves, leaf_options)
        }
        unit_policies[root["id"]] = root_option["policy"]
        local_scores = {
            unit["id"]: option["preflightScore"]
            for unit, option in zip(ordered_units, selected)
        }
        compatibility_penalty = _composition_compatibility_penalty(unit_policies)
        preflight = round(sum(local_scores.values()) / len(local_scores)) - compatibility_penalty
        aliases = [option["alias"] for option in leaf_options]
        identity = "compose--" + "--".join(aliases)
        label = " + ".join(
            f"{unit['id']}={option['policy']}"
            for unit, option in zip(leaves, leaf_options)
        )
        generated.append(
            {
                "id": identity,
                "mode": "recursive-composition",
                "renderFamily": "recursive-composition",
                "compositionLabel": label,
                "preflightScore": preflight,
                "compatibilityPenalty": compatibility_penalty,
                "localPolicyPreflight": local_scores,
                "composition": {
                    "rootPolicy": root_option["policy"],
                    "unitPolicies": unit_policies,
                },
            }
        )

    generated.sort(
        key=lambda item: (
            int(item["preflightScore"]),
            -int(item["compatibilityPenalty"]),
            item["id"],
        ),
        reverse=True,
    )
    limit = max(1, int(max_candidates))
    selected_specs: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    def keep(item: dict[str, Any]) -> None:
        if len(selected_specs) >= limit or item["id"] in selected_ids:
            return
        selected_ids.add(item["id"])
        selected_specs.append(item)

    if generated:
        keep(generated[0])

    # Preserve local-policy diversity before filling the remaining beam by score.
    for unit in leaves:
        for option in catalog[unit["id"]]:
            match = next(
                (
                    item
                    for item in generated
                    if (item["composition"]["unitPolicies"] or {}).get(unit["id"])
                    == option["policy"]
                ),
                None,
            )
            if match:
                keep(match)

    for item in generated:
        keep(item)
        if len(selected_specs) >= limit:
            break

    return selected_specs


def candidate_specs_for_hierarchy(
    hierarchy: dict[str, Any],
    legacy_candidates: list[str],
) -> list[str | dict[str, Any]]:
    """Return actual trial identities for one hierarchy."""

    if hierarchy.get("layoutUnitTree"):
        default_full_set = (
            len(legacy_candidates) == len(LAYOUT_CANDIDATES)
            and set(legacy_candidates) == set(LAYOUT_CANDIDATES)
        )
        if default_full_set:
            return recursive_composition_candidate_specs(
                hierarchy,
                max_candidates=len(LAYOUT_CANDIDATES),
            )
        # An explicit --candidates subset remains an exact legacy-family request.
        return list(legacy_candidates)
    return list(legacy_candidates)


def candidate_unit_composition(
    hierarchy: dict[str, Any],
    candidate: str | dict[str, Any],
) -> dict[str, Any]:
    """Resolve a trial identity into independently scored local policies."""

    identity = candidate_identity(candidate)
    specs = layout_unit_specs(hierarchy)
    if not specs:
        return {
            "candidate": identity,
            "enabled": False,
            "rootPolicy": "legacy-flat",
            "unitPolicies": {},
            "parallelBranches": [],
            "dataflowState": "notDeclared",
            "origin": "legacy-family",
        }

    override = candidate_composition_override(candidate)
    declared = hierarchy.get("unitCompositions") or {}
    selected = copy.deepcopy(
        override
        or declared.get(identity)
        or declared.get(
            "phase-aware" if candidate_phase_policy(candidate)["phaseAware"] else "static"
        )
        or declared.get("*")
        or {}
    )
    explicit_policies = dict(selected.get("unitPolicies") or {})
    unit_policies = {
        unit["id"]: str(
            explicit_policies.get(unit["id"])
            or unit.get("defaultPolicy")
            or "source-order"
        )
        for unit in specs
    }
    root = next(unit for unit in specs if unit.get("parentId") is None)
    root_policy = str(
        selected.get("rootPolicy")
        or unit_policies.get(root["id"])
        or root.get("defaultPolicy")
        or "source-order"
    )
    unit_policies[root["id"]] = root_policy
    dataflow = layout_unit_dataflow_audit(hierarchy)
    generated = isinstance(candidate, dict) and candidate_mode(candidate) == "recursive-composition"
    return {
        "candidate": identity,
        "enabled": True,
        "rootUnitId": root["id"],
        "rootPolicy": root_policy,
        "unitPolicies": unit_policies,
        "parallelBranches": [unit["id"] for unit in specs if unit.get("leaf")],
        "searchMode": (
            "generated-bounded-recursive-composition"
            if generated
            else "bounded-recursive-composition"
        ),
        "origin": "generated-policy-tuple" if generated else "legacy-family-mapping",
        "compositionLabel": (
            str(candidate.get("compositionLabel") or identity)
            if isinstance(candidate, dict)
            else identity
        ),
        "preflightScore": (
            int(candidate.get("preflightScore", 0) or 0)
            if isinstance(candidate, dict)
            else None
        ),
        "compatibilityPenalty": (
            int(candidate.get("compatibilityPenalty", 0) or 0)
            if isinstance(candidate, dict)
            else None
        ),
        "localPolicyPreflight": (
            copy.deepcopy(candidate.get("localPolicyPreflight") or {})
            if isinstance(candidate, dict)
            else {}
        ),
        "dataflowState": dataflow["state"],
        "dataflowEdges": dataflow["edges"],
    }


def realize_layout_unit_tree(
    hierarchy: dict[str, Any],
    candidate: str | dict[str, Any],
    scenario: dict[str, Any],
    realization_states: dict[str, str],
) -> dict[str, Any] | None:
    """Attach phase state and local policy to every recursive layout unit."""

    source = hierarchy.get("layoutUnitTree")
    if not source:
        return None
    composition = candidate_unit_composition(hierarchy, candidate)
    active_support = set(str(slot) for slot in (scenario.get("activeSupportSlots") or []))

    def realize(unit: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
        unit_id = str(unit["id"])
        children = [realize(child, (*path, unit_id)) for child in (unit.get("children") or [])]
        own_slots = [str(slot) for slot in (unit.get("slots") or [])]
        descendant_slots = set(own_slots)
        for child in children:
            descendant_slots.update(child.get("descendantSlots") or [])

        state_counts = defaultdict(int)
        for slot in descendant_slots:
            state_counts[realization_states.get(slot, "absent")] += 1
        active_slots = sorted(
            slot
            for slot in descendant_slots
            if realization_states.get(slot) in {"full-active", "persistent", "inactive-panel"}
        )
        trigger_slots = sorted(
            slot
            for slot in descendant_slots
            if realization_states.get(slot) == "compact-trigger"
        )
        if active_slots and trigger_slots:
            state = "mixed"
        elif active_slots:
            state = "active"
        elif trigger_slots:
            state = "trigger-only"
        else:
            state = "absent"

        policy = composition["unitPolicies"].get(unit_id, "source-order")
        ownership = layout_unit_ownership_spec(policy, state)
        return {
            **copy.deepcopy(unit),
            "id": unit_id,
            "path": [*path, unit_id],
            "policy": policy,
            "ownershipMode": ownership["mode"],
            "overlayTarget": ownership["overlayTarget"],
            "maxOcclusionShare": ownership["maxOcclusionShare"],
            "children": children,
            "leaf": not children,
            "descendantSlots": sorted(descendant_slots),
            "activeSlots": active_slots,
            "triggerSlots": trigger_slots,
            "activeSupportSlots": sorted(descendant_slots & active_support),
            "realization": state,
            "stateCounts": dict(state_counts),
        }

    return realize(copy.deepcopy(source), ())


def _flatten_realized_layout_units(tree: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not tree:
        return []
    flattened: list[dict[str, Any]] = []

    def walk(unit: dict[str, Any], parent: str | None) -> None:
        flattened.append({**unit, "parentId": parent})
        for child in unit.get("children") or []:
            walk(child, unit["id"])

    walk(tree, None)
    return flattened


def canonical_phase_scenario(hierarchy: dict[str, Any]) -> dict[str, Any]:
    scenarios = semantic_phase_scenarios(hierarchy)
    for preferred in ("selected-project-default", "default"):
        match = next((item for item in scenarios if item.get("phase") == preferred), None)
        if match:
            return match
    if not scenarios:
        raise ValueError(f"Hierarchy {hierarchy.get('id', '<unknown>')} has no phase scenarios.")
    return scenarios[0]


def phase_trial_scenarios(hierarchy: dict[str, Any]) -> list[dict[str, Any]]:
    """Return browser trials for the hierarchy.

    Explicit phase batteries are rendered in full.  Generic fallback hierarchies
    retain one canonical default trial so the smoke remains bounded.
    """

    if hierarchy.get("phaseScenarios"):
        return [copy.deepcopy(item) for item in semantic_phase_scenarios(hierarchy)]
    return [copy.deepcopy(canonical_phase_scenario(hierarchy))]


def realize_phase(
    hierarchy: dict[str, Any],
    candidate: str | dict[str, Any],
    scenario: dict[str, Any],
) -> dict[str, Any]:
    """Create the single canonical hierarchy that Chromium and every scorer use."""

    realized = copy.deepcopy(hierarchy)
    policy = candidate_phase_policy(candidate)
    unit_slot_map = layout_unit_slot_map(hierarchy)
    unit_composition = candidate_unit_composition(hierarchy, candidate)
    nodes_by_slot = _node_by_slot(hierarchy)
    known_slots = set(nodes_by_slot)
    dominant_slot = str(scenario.get("dominantSlot") or hierarchy["focusSlot"])
    required_slots = list(dict.fromkeys(str(slot) for slot in (scenario.get("requiredSlots") or [])))
    active_support_slots = list(dict.fromkeys(str(slot) for slot in (scenario.get("activeSupportSlots") or [])))
    collapsed_slots = list(dict.fromkeys(str(slot) for slot in (scenario.get("collapsedSlots") or [])))
    referenced = {dominant_slot, *required_slots, *active_support_slots, *collapsed_slots}
    unknown = sorted(referenced - known_slots)
    if unknown:
        raise ValueError(
            f"Phase {scenario.get('phase', '<unknown>')} for {hierarchy.get('id', '<unknown>')} "
            f"references unknown slot(s): {', '.join(unknown)}"
        )

    active_set = {dominant_slot, *required_slots, *active_support_slots}
    collapsed_set = set(collapsed_slots) - active_set
    if policy["phaseAware"]:
        declared_deferable = set(
            (hierarchy.get("roleContract") or {}).get("deferableSlots", [])
        )
        declared_deferable.update(
            node["slot"]
            for node in hierarchy.get("nodes", [])
            if _is_phase_specific_node(node)
        )
        collapsed_set.update(declared_deferable - active_set)
    realized_nodes: list[dict[str, Any]] = []
    realization_states: dict[str, str] = {}

    for original in hierarchy.get("nodes", []):
        node = copy.deepcopy(original)
        slot = node["slot"]
        if policy["phaseAware"]:
            if slot == dominant_slot or slot in active_support_slots:
                state = "full-active"
            elif slot in required_slots:
                state = "persistent"
            elif slot in collapsed_set:
                state = "compact-trigger"
            else:
                state = "absent"
        else:
            if slot == dominant_slot or slot in active_support_slots:
                state = "full-active"
            elif slot in required_slots:
                state = "persistent"
            else:
                state = "inactive-panel"

        realization_states[slot] = state
        if state == "absent":
            continue

        node["realization"] = state
        node["phaseDominant"] = slot == dominant_slot
        node["phaseSupport"] = slot in active_support_slots
        unit = unit_slot_map.get(slot)
        if unit:
            node["layoutUnitId"] = unit["id"]
            node["layoutUnitPath"] = list(unit["path"])
            node["layoutUnitRole"] = unit.get("role", "support")
            node["layoutUnitPolicy"] = unit_composition["unitPolicies"].get(
                unit["id"], unit.get("defaultPolicy", "source-order")
            )
            node_unit_realization = (
                "trigger-only" if state == "compact-trigger" else "active"
            )
            node_ownership = layout_unit_ownership_spec(
                node["layoutUnitPolicy"], node_unit_realization
            )
            node["layoutUnitOwnershipMode"] = node_ownership["mode"]
            node["layoutUnitOverlayTarget"] = node_ownership["overlayTarget"]
            node["layoutUnitMaxOcclusionShare"] = node_ownership[
                "maxOcclusionShare"
            ]
        if slot == dominant_slot:
            semantics = copy.deepcopy(node.get("semantics") or {})
            _add_semantic(semantics, "authority", "primary-work")
            _add_semantic(semantics, "layoutAffordance", "dominant-surface")
            _add_semantic(semantics, "hardConstraints", "must-own-readable-space")
            if not _as_list(semantics.get("growth")):
                _add_semantic(semantics, "growth", _focus_growth_for_kind(node.get("kind", "")))
            node["semantics"] = semantics
        realized_nodes.append(node)

    target_share = float(scenario.get("targetDominantShare", hierarchy.get("desiredFocusShare", 0.5)) or 0.5)
    minimum_share = float(scenario.get("minDominantShare", hierarchy.get("minFocusShare", 0.32)) or 0.32)
    maximum_share = min(0.94, max(float(hierarchy.get("maxFocusShare", 0.82) or 0.82), target_share + 0.16))
    base_contract = copy.deepcopy(hierarchy.get("roleContract") or {})
    required_companions = [slot for slot in required_slots if slot != dominant_slot]
    visible_full_slots = {
        slot
        for slot, state in realization_states.items()
        if state in {"full-active", "persistent", "inactive-panel"}
    }
    nearby = [
        slot
        for slot in base_contract.get("nearbyCompanions", [])
        if slot in visible_full_slots and slot != dominant_slot
    ]
    base_contract["focus"] = {
        **(base_contract.get("focus") or {}),
        "slot": dominant_slot,
        "desiredShare": target_share,
        "minShare": minimum_share,
        "maxShare": maximum_share,
    }
    base_contract["requiredCompanions"] = required_companions
    base_contract["nearbyCompanions"] = nearby
    deferable_order = [
        slot
        for slot in (hierarchy.get("roleContract") or {}).get("deferableSlots", [])
        if slot in collapsed_set
    ]
    deferable_order.extend(
        node["slot"]
        for node in hierarchy.get("nodes", [])
        if node["slot"] in collapsed_set and node["slot"] not in deferable_order
    )
    base_contract["deferableSlots"] = deferable_order
    base_contract["forbiddenDefaultHidden"] = list(dict.fromkeys([dominant_slot, *required_slots]))

    realized["baseFocusSlot"] = hierarchy["focusSlot"]
    realized["focusSlot"] = dominant_slot
    realized["desiredFocusShare"] = target_share
    realized["minFocusShare"] = minimum_share
    realized["maxFocusShare"] = maximum_share
    realized["roleContract"] = base_contract
    realized["nodes"] = realized_nodes
    realized["phaseScenario"] = copy.deepcopy(scenario)
    realized["phase"] = str(scenario.get("phase") or "default")
    realized["candidatePolicy"] = policy
    realized["realizationStates"] = realization_states
    realized["phaseActiveSlots"] = sorted(active_set)
    realized["phaseCollapsedSlots"] = sorted(collapsed_set)
    realized["unitComposition"] = unit_composition
    realized["layoutUnitTree"] = realize_layout_unit_tree(
        hierarchy,
        candidate,
        scenario,
        realization_states,
    )
    realized["layoutUnits"] = _flatten_realized_layout_units(
        realized.get("layoutUnitTree")
    )
    return realized


def _visible_share_for_record(record: dict[str, Any] | None, root_rect: dict[str, float] | None) -> float:
    if not record or not root_rect or root_rect.get("area", 0) <= 0:
        return 0.0
    return _record_area_share(record, root_rect)


def _phase_score_from_parts(parts: list[tuple[float, float]]) -> int:
    total = 0.0
    weight = 0.0
    for score, score_weight in parts:
        total += max(0.0, min(100.0, score)) * score_weight
        weight += score_weight
    return round(total / weight) if weight else 0


def _score_share_floor(actual: float, floor: float, *, full_at: float | None = None) -> int:
    if floor <= 0:
        return 100
    if actual >= (full_at or floor):
        return 100
    return round(max(0.0, min(1.0, actual / floor)) * 100)


def _score_inactive_tax(tax: float, budget: float) -> int:
    if tax <= budget:
        return 100
    # A static layout with a phase selector permanently consuming space should
    # fall quickly once it exceeds the budget, but not become impossible if the
    # excess is small.
    excess = tax - budget
    return round(max(0.0, 100.0 - (excess / max(0.01, budget * 2.4)) * 100.0))

def _phase_measurement_map(
    hierarchy: dict[str, Any],
    phase_measurements: dict[str, Any] | list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], bool]:
    if isinstance(phase_measurements, dict) and phase_measurements.get("phaseMeasurements"):
        raw_measurements = list(phase_measurements.get("phaseMeasurements") or [])
    elif isinstance(phase_measurements, list):
        raw_measurements = list(phase_measurements)
    else:
        raw_measurements = [phase_measurements]

    by_phase: dict[str, dict[str, Any]] = {}
    legacy_single = len(raw_measurements) == 1 and not (
        raw_measurements[0].get("phase")
        or (raw_measurements[0].get("phaseScenario") or {}).get("phase")
    )
    for measurement in raw_measurements:
        phase = str(
            measurement.get("phase")
            or (measurement.get("phaseScenario") or {}).get("phase")
            or ""
        )
        if not phase and legacy_single:
            phase = str(canonical_phase_scenario(hierarchy).get("phase") or "default")
        if phase:
            by_phase[phase] = measurement
    return by_phase, legacy_single


def _record_realization(
    record: dict[str, Any] | None,
    node: dict[str, Any],
    root_rect: dict[str, float] | None,
) -> str:
    if not record:
        return "absent"
    declared = str(record.get("realization") or "")
    if declared:
        return declared
    share = _visible_share_for_record(record, root_rect)
    if _default_realization_for_node(node) == "collapsed-trigger" and share <= 0.035:
        return "compact-trigger"
    return "full-active"


def semantic_phase_realization_fit(
    hierarchy: dict[str, Any],
    phase_measurements: dict[str, Any] | list[dict[str, Any]],
) -> dict[str, Any]:
    """Score independently rendered phase states and aggregate the policy.

    Each phase must have its own Chromium measurement.  A compact trigger can
    satisfy only an inactive collapsed slot; it can never stand in for an active
    support surface or a phase-dominant surface.
    """

    measurements_by_phase, legacy_single = _phase_measurement_map(hierarchy, phase_measurements)
    nodes_by_slot = _node_by_slot(hierarchy)
    scenarios = semantic_phase_scenarios(hierarchy)
    requires_complete_battery = bool(hierarchy.get("phaseScenarios"))
    if legacy_single:
        canonical_name = str(
            canonical_phase_scenario(hierarchy).get("phase") or "default"
        )
        scenarios = [
            item
            for item in scenarios
            if str(item.get("phase") or "") == canonical_name
        ]
    elif not requires_complete_battery:
        rendered_phases = set(measurements_by_phase)
        scenarios = [
            item
            for item in scenarios
            if str(item.get("phase") or "") in rendered_phases
        ]

    evaluated: list[dict[str, Any]] = []
    hard_failures: list[str] = []
    weighted_total = 0.0
    weight_total = 0.0

    for scenario in scenarios:
        phase = str(scenario["phase"])
        measurement = measurements_by_phase.get(phase)
        phase_weight = float(scenario.get("weight", 1.0) or 1.0)
        dominant_slot = str(scenario.get("dominantSlot") or hierarchy["focusSlot"])
        required_slots = list(scenario.get("requiredSlots") or [])
        active_support_slots = list(scenario.get("activeSupportSlots") or [])
        collapsed_slots = list(scenario.get("collapsedSlots") or [])
        min_dominant = float(
            scenario.get("minDominantShare", hierarchy.get("minFocusShare", 0.32)) or 0.0
        )
        target_dominant = float(
            scenario.get("targetDominantShare", hierarchy.get("desiredFocusShare", min_dominant))
            or min_dominant
        )
        max_inactive_tax = float(scenario.get("maxInactiveTax", 0.12) or 0.12)

        if not measurement:
            reason = f"{phase} phase has no independent browser measurement"
            hard_failures.append(reason)
            evaluated.append(
                {
                    "phase": phase,
                    "score": 0,
                    "dominantSlot": dominant_slot,
                    "dominantShare": 0.0,
                    "minDominantShare": min_dominant,
                    "targetDominantShare": target_dominant,
                    "requiredSlots": required_slots,
                    "activeSupportSlots": active_support_slots,
                    "collapsedSlots": collapsed_slots,
                    "inactiveSlots": collapsed_slots,
                    "inactiveSupportTax": 0.0,
                    "inactiveSupportBudget": max_inactive_tax,
                    "hardFailure": True,
                    "hardFailureReasons": [reason],
                    "reason": reason,
                    "risks": [reason],
                    "snapshots": {},
                }
            )
            weight_total += phase_weight
            continue

        records = _slot_records_by_slot(measurement)
        root_rect = _root_rect_from_measurement(measurement)
        dominant_record = records.get(dominant_slot)
        dominant_share = _visible_share_for_record(dominant_record, root_rect)
        dominant_state = _record_realization(
            dominant_record, nodes_by_slot.get(dominant_slot, {}), root_rect
        )
        dominant_score = _score_share_floor(
            dominant_share, min_dominant, full_at=target_dominant
        )
        if dominant_state == "compact-trigger":
            dominant_score = 0

        missing_required: list[str] = []
        weak_required: list[str] = []
        required_parts: list[tuple[float, float]] = []
        for slot in required_slots:
            record = records.get(slot)
            node = nodes_by_slot.get(slot, {})
            share = _visible_share_for_record(record, root_rect)
            floor = float(node.get("minVisibleShare", 0.012) or 0.012)
            floor = max(0.010, min(floor, 0.055))
            realization = _record_realization(record, node, root_rect)
            if not record or realization == "compact-trigger":
                missing_required.append(slot)
                required_parts.append((0, 1.0))
            else:
                slot_score = _score_share_floor(
                    share, floor, full_at=max(floor, floor * 1.45)
                )
                required_parts.append((slot_score, 1.0))
                if slot_score < 72:
                    weak_required.append(slot)

        active_support_parts: list[tuple[float, float]] = []
        weak_active_support: list[str] = []
        active_support_shares: dict[str, float] = {}
        for slot in active_support_slots:
            record = records.get(slot)
            node = nodes_by_slot.get(slot, {})
            share = _visible_share_for_record(record, root_rect)
            active_support_shares[slot] = share
            role = node.get("role", "support")
            if role == "evidence":
                floor, full_at = 0.14, 0.20
            elif role in {"detail", "inspector"}:
                floor, full_at = 0.12, 0.18
            elif role in {"navigation", "collection"}:
                floor, full_at = 0.20, 0.28
            else:
                floor, full_at = 0.08, 0.14
            realization = _record_realization(record, node, root_rect)
            support_score = _score_share_floor(share, floor, full_at=full_at)
            if realization not in {"full-active", "persistent", "inactive-panel"}:
                support_score = 0
            active_support_parts.append((support_score, 0.85))
            if support_score < 72:
                weak_active_support.append(slot)

        active_set = set(active_support_slots) | {dominant_slot} | set(required_slots)
        inactive_slots = [slot for slot in collapsed_slots if slot not in active_set]
        inactive_tax = sum(
            _visible_share_for_record(records.get(slot), root_rect) for slot in inactive_slots
        )
        inactive_tax_score = _score_inactive_tax(inactive_tax, max_inactive_tax)

        trigger_scores: list[tuple[float, float]] = []
        missing_triggers: list[str] = []
        panel_like_triggers: list[str] = []
        candidate_policy = measurement.get("candidatePolicy") or {}
        supports_compact_triggers = candidate_policy.get("supportsCompactTriggers")
        if supports_compact_triggers is None:
            candidate_name = str(measurement.get("candidate") or "")
            if candidate_name:
                supports_compact_triggers = candidate_phase_policy(candidate_name)[
                    "supportsCompactTriggers"
                ]
            else:
                supports_compact_triggers = any(
                    _record_realization(
                        records.get(slot),
                        nodes_by_slot.get(slot, {}),
                        root_rect,
                    )
                    == "compact-trigger"
                    for slot in inactive_slots
                    if records.get(slot)
                )

        if supports_compact_triggers:
            for slot in inactive_slots:
                record = records.get(slot)
                node = nodes_by_slot.get(slot, {})
                share = _visible_share_for_record(record, root_rect)
                realization = _record_realization(record, node, root_rect)
                if not record:
                    missing_triggers.append(slot)
                    trigger_scores.append((0, 0.35))
                elif realization != "compact-trigger":
                    panel_like_triggers.append(slot)
                    trigger_scores.append((40, 0.35))
                elif share >= 0.0025 and share <= max(
                    0.035, max_inactive_tax * 0.75
                ):
                    trigger_scores.append((100, 0.35))
                elif share > max(0.035, max_inactive_tax * 0.75):
                    panel_like_triggers.append(slot)
                    trigger_scores.append((58, 0.35))
                else:
                    trigger_scores.append((64, 0.35))


        required_score = _phase_score_from_parts(required_parts) if required_parts else 100
        active_support_score = (
            _phase_score_from_parts(active_support_parts) if active_support_parts else 100
        )
        trigger_score = _phase_score_from_parts(trigger_scores) if trigger_scores else 100
        classification = measurement.get("classification") or {}
        geometry_score = int(
            classification.get("geometryScore", classification.get("score", 100)) or 0
        )
        phase_score = _phase_score_from_parts(
            [
                (dominant_score, 1.45),
                (required_score, 1.05),
                (inactive_tax_score, 1.20),
                (active_support_score, 0.90),
                (trigger_score, 0.45),
                (geometry_score, 1.00),
            ]
        )

        facts = measurement.get("geometryFacts") or {}
        clipped_count = int(facts.get("clippedCriticalControlCount", 0) or 0)
        hidden_count = int(facts.get("hiddenCriticalControlCount", 0) or 0)
        phase_hard_failures: list[str] = []
        if dominant_score < 72:
            phase_hard_failures.append(
                f"{phase} phase gives {dominant_slot} {dominant_share:.0%} "
                f"against phase floor {min_dominant:.0%}"
            )
        if missing_required:
            phase_hard_failures.append(
                f"{phase} phase is missing required slot(s): {', '.join(missing_required)}"
            )
        elif weak_required:
            phase_hard_failures.append(
                f"{phase} phase has weak required context: {', '.join(weak_required)}"
            )
        if weak_active_support:
            phase_hard_failures.append(
                f"{phase} phase active support is too small: {', '.join(weak_active_support)}"
            )
        if inactive_tax > max_inactive_tax:
            phase_hard_failures.append(
                f"{phase} phase spends {inactive_tax:.0%} on inactive phase-specific support "
                f"against hard ceiling {max_inactive_tax:.0%}"
            )
        if missing_triggers:
            phase_hard_failures.append(
                f"{phase} phase is missing collapsed trigger(s): {', '.join(missing_triggers)}"
            )
        if panel_like_triggers:
            phase_hard_failures.append(
                f"{phase} phase collapsed slot(s) remain full panels: {', '.join(panel_like_triggers)}"
            )
        if clipped_count or hidden_count:
            phase_hard_failures.append(
                f"{phase} phase has {clipped_count + hidden_count} clipped or hidden active critical control(s)"
            )

        if phase_hard_failures:
            phase_score = min(phase_score, 68)
        hard_failures.extend(phase_hard_failures)
        risks = list(phase_hard_failures)
        if risks:
            reason = risks[0]
        elif active_support_slots:
            reason = (
                f"{phase} phase keeps {dominant_slot} at {dominant_share:.0%}, opens "
                f"{', '.join(active_support_slots)}, and limits inactive support tax to "
                f"{inactive_tax:.0%}"
            )
        elif inactive_slots:
            reason = (
                f"{phase} phase keeps {dominant_slot} at {dominant_share:.0%} and replaces "
                f"{len(inactive_slots)} inactive support surface(s) with compact trigger(s)"
            )
        else:
            reason = (
                f"{phase} phase keeps {dominant_slot} at {dominant_share:.0%} "
                "with required context visible"
            )

        weighted_total += phase_score * phase_weight
        weight_total += phase_weight
        evaluated.append(
            {
                "phase": phase,
                "score": int(phase_score),
                "geometryScore": geometry_score,
                "dominantSlot": dominant_slot,
                "dominantShare": dominant_share,
                "dominantRealization": dominant_state,
                "minDominantShare": min_dominant,
                "targetDominantShare": target_dominant,
                "requiredSlots": required_slots,
                "activeSupportSlots": active_support_slots,
                "activeSupportShares": active_support_shares,
                "collapsedSlots": collapsed_slots,
                "inactiveSlots": inactive_slots,
                "supportsCompactTriggers": bool(supports_compact_triggers),
                "inactiveSupportTax": inactive_tax,
                "inactiveSupportBudget": max_inactive_tax,
                "missingRequiredSlots": missing_required,
                "weakRequiredSlots": weak_required,
                "missingTriggerSlots": missing_triggers,
                "panelLikeTriggerSlots": panel_like_triggers,
                "weakActiveSupportSlots": weak_active_support,
                "clippedCriticalControlCount": clipped_count,
                "hiddenCriticalControlCount": hidden_count,
                "hardFailure": bool(phase_hard_failures),
                "hardFailureReasons": phase_hard_failures,
                "reason": reason,
                "risks": risks,
                "snapshots": measurement.get("snapshots", {}),
            }
        )

    mean_score = round(weighted_total / weight_total) if weight_total else 0
    worst_score = min((item["score"] for item in evaluated), default=0)
    selected_phase = next(
        (
            name
            for name in ("selected-project-default", "default")
            if any(item["phase"] == name for item in evaluated)
        ),
        evaluated[0]["phase"] if evaluated else "",
    )
    selected_default_score = next(
        (item["score"] for item in evaluated if item["phase"] == selected_phase),
        0,
    )
    policy_score = (
        round(
            (0.50 * worst_score)
            + (0.30 * selected_default_score)
            + (0.20 * mean_score)
        )
        if evaluated
        else 0
    )
    unique_hard_failures = list(dict.fromkeys(hard_failures))

    if policy_score >= 86 and not unique_hard_failures:
        state = "strongPhaseFit"
    elif policy_score >= 72 and not unique_hard_failures:
        state = "usablePhaseFit"
    elif policy_score >= 56:
        state = "weakPhaseFit"
    else:
        state = "phaseRisk"

    return {
        "score": int(policy_score),
        "rawScore": int(mean_score),
        "meanScore": int(mean_score),
        "worstScore": int(worst_score),
        "selectedDefaultPhase": selected_phase,
        "selectedDefaultScore": int(selected_default_score),
        "state": state,
        "phaseCount": len(evaluated),
        "phases": evaluated,
        "hardFailureCount": len(unique_hard_failures),
        "hardFailureReasons": unique_hard_failures,
        "positiveReasons": [
            item["reason"]
            for item in evaluated
            if item["score"] >= 82 and not item.get("hardFailure")
        ][:4],
        "riskReasons": unique_hard_failures[:4],
        "note": (
            "Phase fit uses independently rendered browser states. Candidate policy score is "
            "0.50 × worst phase + 0.30 × selected-project/default phase + 0.20 × mean phase; "
            "hard phase failures remain absolute."
        ),
    }



def _unit_records_by_id(measurement: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(record.get("unitId")): record
        for record in ((measurement.get("examples") or {}).get("units") or [])
        if record.get("unitId")
    }


def _overlap_length(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _horizontal_band_policy_score(
    slots: list[str],
    records: dict[str, dict[str, Any]],
    focus_record: dict[str, Any] | None,
    root_rect: dict[str, float] | None,
) -> tuple[int, str, bool]:
    visible = [records[slot] for slot in slots if slot in records]
    if len(visible) <= 1:
        return 100, "single visible feedback member fills the shared band", False

    rects = [_record_rect(record) for record in visible]
    rects = [rect for rect in rects if rect]
    if len(rects) <= 1:
        return 0, "feedback members are missing measurable rectangles", True

    first, second = rects[0], rects[1]
    vertical_overlap = _overlap_length(
        first["top"], first["bottom"], second["top"], second["bottom"]
    ) / max(1.0, min(first["height"], second["height"]))
    horizontal_overlap = _overlap_length(
        first["left"], first["right"], second["left"], second["right"]
    ) / max(1.0, min(first["width"], second["width"]))
    horizontal_gap = max(
        0.0,
        max(first["left"] - second["right"], second["left"] - first["right"]),
    )
    max_gap = (
        max(12.0, min(root_rect["width"], root_rect["height"]) * 0.03)
        if root_rect
        else 24.0
    )
    side_by_side = (
        vertical_overlap >= 0.68
        and horizontal_overlap <= 0.16
        and horizontal_gap <= max_gap
    )
    docked_members = 0
    if focus_record and root_rect:
        for record in visible:
            relation = _spatial_relation(record, focus_record, root_rect)
            if relation.get("bandDocked"):
                docked_members += 1
    dock_ratio = docked_members / len(visible) if visible else 0.0
    score = round(
        (70 if side_by_side else max(0.0, vertical_overlap * 45))
        + (dock_ratio * 30)
    )
    reason = (
        f"feedback members share one horizontal band and "
        f"{docked_members}/{len(visible)} dock directly to the dominant workflow"
        if side_by_side
        else "feedback members are stacked or weakly aligned instead of sharing one band"
    )
    return min(100, score), reason, not side_by_side


def _command_over_dominant_policy_score(
    unit: dict[str, Any],
    records: dict[str, dict[str, Any]],
    focus_slot: str,
    root_rect: dict[str, float] | None,
    unit_record: dict[str, Any] | None,
) -> tuple[int, str, bool]:
    command_slots = [
        slot
        for slot in unit.get("activeSlots") or []
        if (records.get(slot) or {}).get("role") == "command"
    ]
    focus_record = records.get(focus_slot)
    if not focus_record:
        return 0, f"dominant slot {focus_slot} is missing from the primary-work unit", True
    if not command_slots:
        return 100, "phase intentionally omits the command rail while preserving the dominant workflow", False
    command_record = records.get(command_slots[0])
    if not command_record or not root_rect:
        return 0, "command/workflow rectangles are not independently measurable", True
    relation = _spatial_relation(command_record, focus_record, root_rect)
    command_rect = _record_rect(command_record)
    focus_rect = _record_rect(focus_record)
    ordered = bool(
        command_rect
        and focus_rect
        and command_rect["top"] <= focus_rect["top"]
        and relation.get("bandDocked")
    )
    local_focus_share = 0.0
    if unit_record:
        unit_rect = _record_rect(unit_record)
        if unit_rect and unit_rect.get("area", 0) > 0:
            local_focus_share = min(
                1.0,
                (focus_rect or {}).get("area", 0) / unit_rect["area"],
            )
    score = round(
        (70 if ordered else 32)
        + min(30.0, max(0.0, local_focus_share - 0.50) * 100)
    )
    reason = (
        f"command is band-docked above workflow; workflow owns "
        f"{local_focus_share:.0%} of the primary-work unit"
        if ordered
        else "command is not realized as a band above the dominant workflow"
    )
    return min(100, score), reason, not ordered


def _target_closeness_score(value: float, target: float, tolerance: float) -> int:
    if tolerance <= 0:
        return 100 if value == target else 0
    return round(max(0.0, 100.0 - (abs(value - target) / tolerance) * 45.0))


def _selector_unit_policy_score(
    policy: str,
    unit: dict[str, Any],
    records: dict[str, dict[str, Any]],
    root_rect: dict[str, float] | None,
) -> tuple[int, str, bool]:
    full = [
        records[slot]
        for slot in unit.get("activeSlots") or []
        if slot in records and records[slot].get("realization") == "full-active"
    ]
    triggers = [
        records[slot]
        for slot in unit.get("triggerSlots") or []
        if slot in records
    ]
    if not full:
        trigger_tax = sum(
            _record_area_share(record, root_rect)
            for record in triggers
            if root_rect
        )
        score = round(max(72.0, 100.0 - max(0.0, trigger_tax - 0.025) * 500))
        return (
            score,
            f"selector is deferred to {len(triggers)} compact trigger(s) using {trigger_tax:.0%} of the root",
            False,
        )
    if len(full) != 1 or not root_rect:
        return 0, "selector policy does not expose exactly one measurable selector", True

    share = _record_area_share(full[0], root_rect)
    targets = {
        "phase-selector-unit": (0.30, 0.20, 0.16),
        "compact-project-rail": (0.23, 0.16, 0.12),
        "selector-overlay": (0.36, 0.26, 0.16),
    }
    target, minimum, tolerance = targets.get(policy, (0.28, 0.18, 0.18))
    score = _target_closeness_score(share, target, tolerance)
    hard = share < minimum
    if hard:
        score = min(score, 45)
    return (
        score,
        f"{policy} gives the active selector {share:.0%} of the root against local target {target:.0%}",
        hard,
    )


def _command_workflow_policy_score(
    policy: str,
    unit: dict[str, Any],
    records: dict[str, dict[str, Any]],
    focus_slot: str,
    root_rect: dict[str, float] | None,
    unit_record: dict[str, Any] | None,
) -> tuple[int, str, bool]:
    if policy == "command-over-dominant":
        score, reason, hard = _command_over_dominant_policy_score(
            unit, records, focus_slot, root_rect, unit_record
        )
        return min(98, score), reason, hard

    command_slots = [
        slot
        for slot in unit.get("activeSlots") or []
        if (records.get(slot) or {}).get("role") == "command"
    ]
    focus_record = records.get(focus_slot)
    if not command_slots:
        return 96, "phase intentionally omits command controls while preserving workflow", False
    command_record = records.get(command_slots[0])
    if not command_record or not focus_record or not root_rect:
        return 0, "command/workflow rectangles are not independently measurable", True
    relation = _spatial_relation(command_record, focus_record, root_rect)
    command_rect = _record_rect(command_record) or {}
    unit_rect = _record_rect(unit_record) if unit_record else None
    focus_rect = _record_rect(focus_record) or {}
    local_focus_share = (
        min(1.0, focus_rect.get("area", 0) / unit_rect["area"])
        if unit_rect and unit_rect.get("area", 0) > 0
        else 0.0
    )

    if policy == "side-command-rail":
        realized = bool(relation.get("sideDocked"))
        rail_share = command_rect.get("area", 0) / max(1.0, unit_rect["area"] if unit_rect else root_rect["area"])
        score = round(
            (68 if realized else 28)
            + (_target_closeness_score(rail_share, 0.18, 0.12) * 0.16)
            + (min(1.0, local_focus_share / 0.72) * 16)
        )
        return (
            min(100, score),
            f"side command rail uses {rail_share:.0%} of the primary-work unit while workflow keeps {local_focus_share:.0%}",
            not realized or local_focus_share < 0.60,
        )

    # command-inline-header
    realized = bool(relation.get("bandDocked")) and command_rect.get("top", 0) <= focus_rect.get("top", 0)
    height_share = command_rect.get("height", 0) / max(1.0, root_rect["height"])
    score = round(
        (66 if realized else 28)
        + (_target_closeness_score(height_share, 0.055, 0.045) * 0.18)
        + (min(1.0, local_focus_share / 0.78) * 16)
    )
    return (
        min(100, score),
        f"inline command header uses {height_share:.0%} of root height while workflow keeps {local_focus_share:.0%} of its unit",
        not realized or local_focus_share < 0.62,
    )


def _feedback_unit_policy_score(
    policy: str,
    active_slots: list[str],
    records: dict[str, dict[str, Any]],
    focus_record: dict[str, Any] | None,
    root_rect: dict[str, float] | None,
    unit_record: dict[str, Any] | None,
) -> tuple[int, str, bool]:
    visible = [records[slot] for slot in active_slots if slot in records]
    if len(visible) <= 1:
        return 94, "single visible feedback member satisfies the local feedback unit", False
    if not root_rect:
        return 0, "feedback unit has no measurable root", True

    if policy == "shared-horizontal-band":
        base, reason, hard = _horizontal_band_policy_score(
            active_slots, records, focus_record, root_rect
        )
        unit_share = _record_area_share(unit_record, root_rect) if unit_record else 0.0
        efficiency = _target_closeness_score(unit_share, 0.055, 0.055)
        return round(base * 0.84 + efficiency * 0.16), reason + f"; band uses {unit_share:.0%} of root", hard

    rects = [_record_rect(record) for record in visible]
    if any(rect is None for rect in rects):
        return 0, "feedback members lack measurable rectangles", True
    first, second = rects[0], rects[1]
    vertical_overlap = _overlap_length(first["top"], first["bottom"], second["top"], second["bottom"]) / max(1.0, min(first["height"], second["height"]))
    horizontal_overlap = _overlap_length(first["left"], first["right"], second["left"], second["right"]) / max(1.0, min(first["width"], second["width"]))
    unit_share = _record_area_share(unit_record, root_rect) if unit_record else 0.0

    if policy == "stacked-feedback":
        stacked = horizontal_overlap >= 0.70 and vertical_overlap <= 0.20
        score = round(
            (62 if stacked else 26)
            + _target_closeness_score(unit_share, 0.09, 0.07) * 0.28
        )
        return (
            min(100, score),
            f"stacked feedback uses {unit_share:.0%} of root with horizontal overlap {horizontal_overlap:.0%}",
            not stacked,
        )

    # workflow-footer-overlay
    aligned = vertical_overlap >= 0.65 and horizontal_overlap <= 0.20
    unit_rect = _record_rect(unit_record) if unit_record else None
    overlaps_focus = False
    if unit_rect and focus_record:
        focus_rect = _record_rect(focus_record)
        if focus_rect:
            overlap = _overlap_length(unit_rect["top"], unit_rect["bottom"], focus_rect["top"], focus_rect["bottom"])
            overlaps_focus = overlap > 2
    score = round(
        (64 if aligned else 28)
        + _target_closeness_score(unit_share, 0.05, 0.05) * 0.24
        + (8 if overlaps_focus else 3)
    )
    return (
        min(100, score),
        f"footer overlay keeps feedback aligned in {unit_share:.0%} of root and overlaps workflow={overlaps_focus}",
        not aligned,
    )


def _phase_support_policy_score(
    policy: str,
    unit: dict[str, Any],
    records: dict[str, dict[str, Any]],
    root_rect: dict[str, float] | None,
    unit_record: dict[str, Any] | None,
) -> tuple[int, str, bool]:
    expected = set(unit.get("activeSupportSlots") or [])
    realized_full = {
        slot
        for slot in unit.get("activeSlots") or []
        if (records.get(slot) or {}).get("realization") == "full-active"
    }
    mismatch = expected.symmetric_difference(realized_full)
    if mismatch:
        return 0, f"support mismatch for {sorted(mismatch)}", True

    if not expected:
        trigger_tax = sum(
            _record_area_share(records.get(slot), root_rect)
            for slot in unit.get("triggerSlots") or []
            if records.get(slot) and root_rect
        )
        score = round(max(70.0, 98.0 - max(0.0, trigger_tax - 0.04) * 450))
        return (
            score,
            f"inactive support is represented by triggers using {trigger_tax:.0%} of root",
            False,
        )

    if not root_rect or not unit_record:
        return 0, "active support unit has no measurable rectangle", True
    rect = _record_rect(unit_record)
    if not rect:
        return 0, "active support unit has no measurable rectangle", True
    share = _record_area_share(unit_record, root_rect)
    width_ratio = rect["width"] / max(1.0, root_rect["width"])
    height_ratio = rect["height"] / max(1.0, root_rect["height"])

    if policy in {"bounded-side-drawer", "one-active-plus-triggers"}:
        target = 0.22 if policy == "one-active-plus-triggers" else 0.26
        orientation = width_ratio <= 0.36 and height_ratio >= 0.45
        score = round(
            (58 if orientation else 24)
            + _target_closeness_score(share, target, 0.16) * 0.38
        )
        return (
            min(100, score),
            f"{policy} gives active support {share:.0%} of root as a side surface",
            not orientation or share < 0.12,
        )

    target = 0.20 if policy == "bounded-bottom-drawer" else 0.24
    orientation = width_ratio >= 0.72 and height_ratio <= 0.38
    score = round(
        (58 if orientation else 24)
        + _target_closeness_score(share, target, 0.15) * 0.38
    )
    return (
        min(100, score),
        f"{policy} gives active support {share:.0%} of root as a horizontal stage",
        not orientation or share < 0.12,
    )


def semantic_layout_unit_state_fit(
    realized_hierarchy: dict[str, Any],
    measurement: dict[str, Any],
) -> dict[str, Any]:
    """Score one browser state at the lowest responsible layout-unit level."""

    units = list(realized_hierarchy.get("layoutUnits") or [])
    if not units:
        return {
            "evaluated": False,
            "score": 100,
            "state": "notDeclared",
            "hardFailureCount": 0,
            "hardFailureReasons": [],
            "units": [],
            "positiveReasons": [],
            "riskReasons": [],
            "note": "No recursive layout units were declared for this hierarchy.",
        }

    records = _slot_records_by_slot(measurement)
    unit_records = _unit_records_by_id(measurement)
    root_rect = _root_rect_from_measurement(measurement)
    focus_slot = realized_hierarchy["focusSlot"]
    focus_record = records.get(focus_slot)
    examples = measurement.get("examples") or {}
    clipped = list(examples.get("clippedCriticalControls") or [])
    hidden = list(examples.get("hiddenCriticalControls") or [])
    critical_by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for issue in [*clipped, *hidden]:
        if issue.get("unitId"):
            critical_by_unit[str(issue["unitId"])].append(issue)

    results: list[dict[str, Any]] = []
    hard_failures: list[str] = []
    for unit in units:
        if not unit.get("leaf"):
            continue
        unit_id = unit["id"]
        policy = str(unit.get("policy") or "source-order")
        active_slots = list(unit.get("activeSlots") or [])
        trigger_slots = list(unit.get("triggerSlots") or [])
        parts: list[tuple[float, float]] = []
        reasons: list[str] = []
        local_hard: list[str] = []

        missing_active = [slot for slot in active_slots if slot not in records]
        wrong_triggers = [
            slot
            for slot in trigger_slots
            if (records.get(slot) or {}).get("realization") != "compact-trigger"
        ]
        if missing_active:
            local_hard.append(
                f"{unit_id} is missing active slot(s): {', '.join(missing_active)}"
            )
            parts.append((0, 0.25))
        else:
            parts.append((100, 0.25))
            if active_slots:
                reasons.append(
                    f"{unit_id} exposes its active slot(s): {', '.join(active_slots)}"
                )
        if wrong_triggers:
            local_hard.append(
                f"{unit_id} keeps collapsed slot(s) as panels: {', '.join(wrong_triggers)}"
            )
            parts.append((0, 0.15))
        else:
            parts.append((100, 0.15))

        critical_issues = critical_by_unit.get(unit_id, [])
        if critical_issues:
            local_hard.append(
                f"{unit_id} has {len(critical_issues)} clipped or hidden active critical control(s)"
            )
            parts.append((0, 0.25))
        else:
            parts.append((100, 0.25))

        policy_score = 100
        policy_reason = ""
        policy_hard = False
        if not active_slots and not trigger_slots:
            policy_reason = f"{unit_id} is intentionally absent in this phase"
            policy_score = 96
        elif policy in {
            "shared-horizontal-band",
            "stacked-feedback",
            "workflow-footer-overlay",
        }:
            policy_score, policy_reason, policy_hard = _feedback_unit_policy_score(
                policy,
                active_slots,
                records,
                focus_record,
                root_rect,
                unit_records.get(unit_id),
            )
        elif policy in {
            "command-over-dominant",
            "command-inline-header",
            "side-command-rail",
        }:
            if focus_slot not in set(unit.get("descendantSlots") or []):
                policy_reason = f"{unit_id} is not the dominant responsibility in this phase"
                policy_score = 94
            else:
                policy_score, policy_reason, policy_hard = _command_workflow_policy_score(
                    policy,
                    unit,
                    records,
                    focus_slot,
                    root_rect,
                    unit_records.get(unit_id),
                )
        elif policy in {
            "one-active-plus-triggers",
            "bounded-bottom-drawer",
            "bounded-side-drawer",
            "inline-phase-stage",
            "side-active-support",
            "narrow-side-active-support",
            "proof-drawer-or-side-support",
        }:
            normalized_policy = {
                "side-active-support": "bounded-side-drawer",
                "narrow-side-active-support": "one-active-plus-triggers",
                "proof-drawer-or-side-support": "bounded-bottom-drawer",
            }.get(policy, policy)
            policy_score, policy_reason, policy_hard = _phase_support_policy_score(
                normalized_policy,
                unit,
                records,
                root_rect,
                unit_records.get(unit_id),
            )
        elif policy in {
            "phase-selector-unit",
            "compact-project-rail",
            "selector-overlay",
            "selected-context-selector",
            "progressive-selector",
        }:
            normalized_policy = {
                "selected-context-selector": "compact-project-rail",
                "progressive-selector": "phase-selector-unit",
            }.get(policy, policy)
            policy_score, policy_reason, policy_hard = _selector_unit_policy_score(
                normalized_policy,
                unit,
                records,
                root_rect,
            )

        if policy_reason:
            reasons.append(policy_reason)
            parts.append((policy_score, 0.35))
        if policy_hard:
            local_hard.append(f"{unit_id} violates local policy {policy}: {policy_reason}")

        score = _phase_score_from_parts(parts)
        hard_failures.extend(local_hard)
        results.append(
            {
                "unitId": unit_id,
                "role": unit.get("role", "support"),
                "policy": policy,
                "path": unit.get("path", []),
                "score": score,
                "hardFailureCount": len(local_hard),
                "hardFailureReasons": local_hard,
                "activeSlots": active_slots,
                "triggerSlots": trigger_slots,
                "reasons": reasons,
            }
        )

    scores = [item["score"] for item in results]
    worst = min(scores) if scores else 100
    mean = round(sum(scores) / len(scores)) if scores else 100
    score = round((worst * 0.55) + (mean * 0.45))
    if hard_failures:
        state = "unitRisk"
    elif score >= 88:
        state = "strongUnitFit"
    elif score >= 72:
        state = "usableUnitFit"
    elif score >= 56:
        state = "weakUnitFit"
    else:
        state = "unitRisk"
    worst_unit = min(results, key=lambda item: item["score"]) if results else None
    positive = [
        f"{item['unitId']} ({item['policy']}) scored {item['score']}%: "
        f"{item['reasons'][0] if item['reasons'] else 'local obligations satisfied'}"
        for item in sorted(results, key=lambda item: item["score"], reverse=True)
        if item["score"] >= 82
    ]
    risks = [
        f"{item['unitId']} ({item['policy']}) scored {item['score']}%: "
        f"{(item['hardFailureReasons'] or item['reasons'] or ['local review needed'])[0]}"
        for item in sorted(results, key=lambda item: item["score"])
        if item["score"] < 72 or item["hardFailureCount"]
    ]
    return {
        "evaluated": True,
        "score": score,
        "state": state,
        "worstScore": worst,
        "meanScore": mean,
        "worstUnitId": (worst_unit or {}).get("unitId", ""),
        "hardFailureCount": len(hard_failures),
        "hardFailureReasons": list(dict.fromkeys(hard_failures)),
        "units": results,
        "positiveReasons": positive[:4],
        "riskReasons": risks[:4],
        "parallelBranches": [
            {
                "unitId": item["unitId"],
                "score": item["score"],
                "hardFailureCount": item["hardFailureCount"],
            }
            for item in results
        ],
        "note": (
            "Each leaf layout responsibility is scored independently; the state score "
            "retains the worst child so a strong parent cannot average away a broken unit."
        ),
    }


def aggregate_layout_unit_fit(
    hierarchy: dict[str, Any],
    measurements: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate independently measured unit branches across browser phases."""

    if not layout_unit_specs(hierarchy):
        return {
            "evaluated": False,
            "score": 100,
            "state": "notDeclared",
            "hardFailureCount": 0,
            "hardFailureReasons": [],
            "parallelBranches": [],
            "phaseFits": [],
            "positiveReasons": [],
            "riskReasons": [],
        }

    phase_fits: list[dict[str, Any]] = []
    for measurement in measurements:
        fit = measurement.get("layoutUnitFit")
        if not fit:
            fit = semantic_layout_unit_state_fit(hierarchy, measurement)
        phase_fits.append(
            {
                "phase": str(measurement.get("phase") or "default"),
                **copy.deepcopy(fit),
            }
        )
    scores = [int(item.get("score", 0) or 0) for item in phase_fits]
    worst = min(scores) if scores else 0
    mean = round(sum(scores) / len(scores)) if scores else 0
    selected_phase = str(
        canonical_phase_scenario(hierarchy).get("phase") or "default"
    )
    selected = next(
        (item for item in phase_fits if item["phase"] == selected_phase),
        phase_fits[0] if phase_fits else {"score": 0},
    )
    selected_score = int(selected.get("score", 0) or 0)
    score = round((worst * 0.50) + (selected_score * 0.30) + (mean * 0.20))
    hard_failures = list(
        dict.fromkeys(
            reason
            for fit in phase_fits
            for reason in (fit.get("hardFailureReasons") or [])
        )
    )

    branch_scores: dict[str, list[int]] = defaultdict(list)
    branch_hard: dict[str, int] = defaultdict(int)
    for fit in phase_fits:
        for unit in fit.get("units") or []:
            branch_scores[unit["unitId"]].append(int(unit.get("score", 0) or 0))
            branch_hard[unit["unitId"]] += int(unit.get("hardFailureCount", 0) or 0)
    parallel = [
        {
            "unitId": unit_id,
            "worstScore": min(values),
            "meanScore": round(sum(values) / len(values)),
            "hardFailureCount": branch_hard[unit_id],
        }
        for unit_id, values in sorted(branch_scores.items())
    ]

    if hard_failures:
        state = "unitRisk"
    elif score >= 88:
        state = "strongUnitFit"
    elif score >= 72:
        state = "usableUnitFit"
    elif score >= 56:
        state = "weakUnitFit"
    else:
        state = "unitRisk"
    worst_branch = min(
        parallel,
        key=lambda item: (item["worstScore"], item["meanScore"]),
        default=None,
    )
    positive = []
    if worst_branch:
        positive.append(
            f"worst local branch is {worst_branch['unitId']} at "
            f"{worst_branch['worstScore']}% across independently rendered phases"
        )
    risks = hard_failures[:4]
    return {
        "evaluated": True,
        "score": score,
        "state": state,
        "worstScore": worst,
        "meanScore": mean,
        "selectedDefaultScore": selected_score,
        "selectedDefaultPhase": selected_phase,
        "hardFailureCount": len(hard_failures),
        "hardFailureReasons": hard_failures,
        "parallelBranches": parallel,
        "phaseFits": phase_fits,
        "positiveReasons": positive,
        "riskReasons": risks,
        "note": (
            "Recursive unit policy score is 0.50 × worst phase + 0.30 × selected "
            "default + 0.20 × mean. Local hard failures remain absolute."
        ),
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



def apply_realized_state_fit(
    realized_hierarchy: dict[str, Any],
    measurement: dict[str, Any],
) -> dict[str, Any]:
    """Attach contract and affordance evidence for one realized browser state."""

    fit = semantic_contract_fit(realized_hierarchy, measurement)
    affordance_fit = semantic_affordance_realization_fit(realized_hierarchy, measurement)
    unit_fit = semantic_layout_unit_state_fit(realized_hierarchy, measurement)
    classification = measurement.setdefault("classification", {})
    geometry_score = int(classification.get("score", 0) or 0)
    if unit_fit.get("evaluated"):
        state_score = round(
            (geometry_score * 0.45)
            + (fit["score"] * 0.20)
            + (affordance_fit["score"] * 0.15)
            + (unit_fit["score"] * 0.20)
        )
    else:
        state_score = round(
            (geometry_score * 0.55)
            + (fit["score"] * 0.25)
            + (affordance_fit["score"] * 0.20)
        )
    classification["geometryScore"] = geometry_score
    classification["contractFitScore"] = fit["score"]
    classification["contractFitRawScore"] = fit.get("rawScore", fit["score"])
    classification["contractFitState"] = fit["state"]
    classification["hardContractRiskCount"] = int(fit.get("hardRiskCount", 0) or 0)
    classification["softContractRiskCount"] = int(fit.get("softRiskCount", 0) or 0)
    classification["affordanceFitScore"] = affordance_fit["score"]
    classification["affordanceFitRawScore"] = affordance_fit.get("rawScore", affordance_fit["score"])
    classification["affordanceFitState"] = affordance_fit["state"]
    classification["hardAffordanceMissCount"] = int(affordance_fit.get("hardMissCount", 0) or 0)
    classification["missedAffordanceCount"] = int(
        affordance_fit.get("missedAffordanceCount", 0) or 0
    )
    classification["layoutUnitFitScore"] = unit_fit["score"]
    classification["layoutUnitFitState"] = unit_fit["state"]
    classification["layoutUnitHardFailureCount"] = int(
        unit_fit.get("hardFailureCount", 0) or 0
    )
    classification["realizedStateScore"] = state_score
    measurement["contractFit"] = fit
    measurement["affordanceFit"] = affordance_fit
    measurement["layoutUnitFit"] = unit_fit
    return measurement


def apply_semantic_contract_fit(
    hierarchy: dict[str, Any],
    measurement: dict[str, Any],
    *,
    realized_hierarchy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scoring_hierarchy = realized_hierarchy or hierarchy
    if not measurement.get("contractFit") or not measurement.get("affordanceFit"):
        apply_realized_state_fit(scoring_hierarchy, measurement)

    fit = measurement["contractFit"]
    affordance_fit = measurement["affordanceFit"]
    phase_input: dict[str, Any] | list[dict[str, Any]]
    phase_input = measurement.get("phaseMeasurements") or measurement
    phase_fit = semantic_phase_realization_fit(hierarchy, phase_input)
    phase_measurements = (
        list(phase_input)
        if isinstance(phase_input, list)
        else [phase_input]
    )
    unit_fit = aggregate_layout_unit_fit(hierarchy, phase_measurements)
    classification = measurement.setdefault("classification", {})
    geometry_score = int(classification.get("geometryScore", classification.get("score", 0)) or 0)
    if unit_fit.get("evaluated"):
        selection_score = round(
            (geometry_score * 0.34)
            + (fit["score"] * 0.16)
            + (affordance_fit["score"] * 0.13)
            + (phase_fit["score"] * 0.20)
            + (unit_fit["score"] * 0.17)
        )
    else:
        selection_score = round(
            (geometry_score * 0.45)
            + (fit["score"] * 0.22)
            + (affordance_fit["score"] * 0.18)
            + (phase_fit["score"] * 0.15)
        )

    original_status = str(classification.get("status") or "fail")
    warnings = list(classification.get("warnings") or [])
    hard_risk_count = int(fit.get("hardRiskCount", 0) or 0)
    hard_affordance_miss_count = int(affordance_fit.get("hardMissCount", 0) or 0)
    phase_hard_failure_count = int(phase_fit.get("hardFailureCount", 0) or 0)
    unit_hard_failure_count = int(unit_fit.get("hardFailureCount", 0) or 0)
    if original_status == "fail" or phase_hard_failure_count or unit_hard_failure_count:
        status = "fail"
    elif (hard_risk_count or hard_affordance_miss_count) and selection_score >= 82:
        status = "watch"
    elif (
        selection_score >= 82
        and len(warnings) <= 1
        and fit["score"] >= 72
        and affordance_fit["score"] >= 72
        and (not unit_fit.get("evaluated") or unit_fit["score"] >= 72)
    ):
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
    classification["missedAffordanceCount"] = int(
        affordance_fit.get("missedAffordanceCount", 0) or 0
    )
    classification["phaseFitScore"] = phase_fit["score"]
    classification["phaseFitRawScore"] = phase_fit.get("rawScore", phase_fit["score"])
    classification["phaseFitState"] = phase_fit["state"]
    classification["phaseFitRiskCount"] = len(phase_fit.get("riskReasons", []) or [])
    classification["phaseHardFailureCount"] = phase_hard_failure_count
    classification["layoutUnitFitScore"] = unit_fit["score"]
    classification["layoutUnitFitState"] = unit_fit["state"]
    classification["layoutUnitWorstScore"] = unit_fit.get("worstScore", 100)
    classification["layoutUnitHardFailureCount"] = unit_hard_failure_count
    classification["selectionScore"] = selection_score
    classification["score"] = selection_score
    classification["status"] = status

    positive_reasons = classification.setdefault("positiveReasons", [])
    failure_reasons = classification.setdefault("failureReasons", [])
    review_notes = classification.setdefault("reviewNotes", [])

    if unit_fit.get("positiveReasons"):
        positive_reasons.insert(
            0,
            f"recursive unit fit is {unit_fit['score']}%: "
            f"{unit_fit['positiveReasons'][0]}",
        )
    if unit_fit.get("riskReasons"):
        review_notes.insert(
            0, f"layout-unit review: {unit_fit['riskReasons'][0]}"
        )
    if phase_fit["positiveReasons"]:
        positive_reasons.insert(
            0,
            f"phase-aware fit is {phase_fit['score']}%: {phase_fit['positiveReasons'][0]}",
        )
    if phase_fit["riskReasons"]:
        review_notes.insert(0, f"phase-fit review: {phase_fit['riskReasons'][0]}")
    if affordance_fit["positiveReasons"]:
        positive_reasons.insert(
            0,
            f"generic affordance fit is {affordance_fit['score']}%: "
            f"{affordance_fit['positiveReasons'][0]}",
        )
    if fit["positiveReasons"]:
        positive_reasons.insert(
            0,
            f"generic contract fit is {fit['score']}%: {fit['positiveReasons'][0]}",
        )
    if fit.get("presentationSetReasons"):
        positive_reasons.insert(
            0, f"phase co-presence: {fit['presentationSetReasons'][0]}"
        )
    if affordance_fit.get("hardRiskReasons"):
        review_notes.insert(
            0, f"hard affordance review: {affordance_fit['hardRiskReasons'][0]}"
        )
    if fit.get("hardRiskReasons"):
        review_notes.insert(0, f"hard contract review: {fit['hardRiskReasons'][0]}")
    elif fit["riskReasons"]:
        review_notes.insert(0, f"contract-fit review: {fit['riskReasons'][0]}")
    if affordance_fit.get("riskReasons") and not affordance_fit.get("hardRiskReasons"):
        review_notes.insert(
            0, f"affordance-fit review: {affordance_fit['riskReasons'][0]}"
        )
    if fit.get("contractLimits"):
        review_notes.insert(0, "; ".join(fit["contractLimits"]))
    if affordance_fit.get("limits"):
        review_notes.insert(0, "; ".join(affordance_fit["limits"]))
    if fit["state"] in {"weakContractFit", "contractRisk"} and original_status != "fail":
        failure_reasons.append(
            f"generic contract fit is only {fit['score']}%; "
            "layout preserves rectangles better than behavior relationships"
        )
    if (
        affordance_fit["state"] in {"weakAffordanceFit", "affordanceRisk"}
        and original_status != "fail"
    ):
        failure_reasons.append(
            f"generic affordance fit is only {affordance_fit['score']}%; "
            "tagged spatial affordances are not clearly realized"
        )
    if hard_risk_count and original_status != "fail":
        failure_reasons.append(
            "hard semantic contract pressure needs review before promotion"
        )
    if hard_affordance_miss_count and original_status != "fail":
        failure_reasons.append(
            "hard affordance realization needs review before promotion"
        )
    for reason in unit_fit.get("hardFailureReasons", []):
        if reason not in failure_reasons:
            failure_reasons.append(reason)
    for reason in phase_fit.get("hardFailureReasons", []):
        if reason not in failure_reasons:
            failure_reasons.append(reason)
    review_notes.append(
        "Contract, affordance, clipping, phase, and recursive unit scores consume "
        "the same realized browser hierarchy; local hard failures cannot be averaged away."
    )

    measurement["phaseFit"] = phase_fit
    measurement["layoutUnitFit"] = unit_fit
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
        semantics: dict[str, Any] | None = None,
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
            "semantics": semantics or {},
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
        phase_scenarios: list[dict[str, Any]] | None = None,
        layout_unit_tree: dict[str, Any] | None = None,
        unit_compositions: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        nodes = enrich_nodes_with_semantic_primitives(nodes, focus_slot)
        slots = {entry["slot"] for entry in nodes}
        missing = [slot for slot in [focus_slot, *required_companions, *nearby_companions, *deferable_slots] if slot not in slots]
        if missing:
            raise ValueError(f"Hierarchy {id} references missing slot(s): {missing}")
        result = {
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
            "phaseScenarios": phase_scenarios or [],
            "layoutUnitTree": copy.deepcopy(layout_unit_tree),
            "unitCompositions": copy.deepcopy(unit_compositions or {}),
        }
        # Fail at fixture construction time rather than during browser rendering.
        layout_unit_specs(result)
        return result

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
            id="document-page-overlay-workbench",
            title="Document Page Overlay Workbench",
            description="Page authoring app with top document controls, a dominant page canvas, and invoked library/AI overlays.",
            source_app="document",
            root_concern="document.page-authoring",
            focus_slot="page",
            desired_focus_share=0.66,
            min_focus_share=0.55,
            max_focus_share=0.82,
            required_companions=["toolbar", "status"],
            nearby_companions=[],
            deferable_slots=["library", "ai"],
            forbidden_default_hidden=["toolbar", "page", "status"],
            preferred_families=["top-band-focus-overlay", "top-band-dominant-surface", "bounded-drawer", "focus-priority", "source-order-stacked"],
            dangerous_families={
                "sectioned-sidebar": "library and AI are invoked overlays, not default persistent sidebars",
                "split-pane": "the document page should not be split with optional overlays by default",
                "dashboard-grid": "page authoring must not become a peer dashboard of document utilities",
            },
            nodes=[
                node("toolbar", "authority-command", "Document Header + Toolbar", role="command", priority="primary", visibility="required", proximity="near", weight=2, scroll="no", connects=["page", "status", "library", "ai"], items=[
                    item("button", "Pretty Docs"), item("button", "Document AI"), item("select", "Block format"), item("button", "Bold"), item("button", "Italic"),
                    item("button", "Export PDF"), item("button", "Page Layout"), item("control", "Auto-save draft"),
                ]),
                node("page", "primary-page-editor-canvas", "Paged Document Canvas", role="focus", priority="focus", visibility="required", proximity="self", weight=12, connects=["toolbar", "status", "library", "ai"], items=[
                    item("surface", "Outer document object stage owns the plugin rail and the page canvas as internal editor structure", role="document-stage"),
                    item("surface", "Page sheet with fixed margins, active caret, overlay layer, and editable content", role="page-surface"),
                    item("text", "Heading block · title and document metadata"),
                    item("text", "Paragraph block · user-authored content with inline math marker"),
                    item("collection", "Editor-only hidden plugin marker rail is inside the page work surface, not a global sidebar"),
                    item("status", "Selection/caret context: paragraph 2, range 44-78"),
                    item("text", "Page layout popover changes page geometry while the canvas remains the dominant owned surface."),
                    item("collection", "Embedded scene/object handle belongs to the document stage."),
                ]),
                node("status", "status", "Document State", role="status", priority="primary", visibility="required", proximity="near", weight=1, scroll="no", connects=["toolbar", "page"], items=[
                    item("status", "document ready"), item("status", "backend draft saved"), item("status", "local draft path visible"),
                ]),
                node("library", "deferable-document-library", "Pretty Docs Library Overlay", role="collection", priority="secondary", visibility="deferable", proximity="loose", weight=2, min_visible_share=0.012, connects=["page", "toolbar"], items=[
                    item("collection", "Pretty doc: MCEL system guide"), item("collection", "Pretty doc: user-space contract"), item("button", "Refresh library"), item("button", "Close overlay"),
                    item("text", "Fixed left overlay appears when invoked; it should not force a permanent sidebar in the default layout."),
                ]),
                node("ai", "deferable-ai-proof-pane", "Document AI Overlay", role="evidence", priority="secondary", visibility="deferable", proximity="loose", weight=3, min_visible_share=0.012, connects=["page", "toolbar", "status"], items=[
                    item("button", "Lock anchor"), item("button", "Improve"), item("button", "Summarize"), item("evidence", "Preview: proposed edit remains staged before apply."),
                    item("evidence", "AI proof/critique is claim-dependent and may slide in as an overlay."),
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
            id="git-tools-workflow-workbench",
            title="Git Tools Workflow Workbench",
            description="Repository workflow app that tests project selection, selected-project context, guided action planning, execution evidence, and recovery controls as separate phases.",
            source_app="git-tools",
            root_concern="repository.workflow",
            focus_slot="workflow",
            desired_focus_share=0.64,
            min_focus_share=0.50,
            max_focus_share=0.80,
            required_companions=["project-context", "command", "status"],
            nearby_companions=[],
            deferable_slots=["project-selector", "server", "evidence", "advanced"],
            forbidden_default_hidden=["project-context", "workflow", "command", "status"],
            preferred_families=["selected-context-workflow", "progressive-workflow", "workflow-with-proof-drawer", "split-pane", "inspector"],
            dangerous_families={
                "dashboard-grid": "repository action planning should not become unrelated peer cards",
                "source-order-stacked": "repository workflow loses selected-project context when every Git surface is merely stacked",
            },
            phase_scenarios=[
                {
                    "phase": "project-selection",
                    "dominantSlot": "project-selector",
                    "requiredSlots": ["project-selector", "status"],
                    "activeSupportSlots": ["project-selector"],
                    "collapsedSlots": ["server", "evidence", "advanced"],
                    "minDominantShare": 0.20,
                    "targetDominantShare": 0.30,
                    "maxInactiveTax": 0.08,
                    "weight": 0.90,
                    "reason": "Project roster may open during selection, while proof and recovery remain collapsed.",
                },
                {
                    "phase": "selected-project-default",
                    "dominantSlot": "workflow",
                    "requiredSlots": ["project-context", "command", "workflow", "status"],
                    "activeSupportSlots": [],
                    "collapsedSlots": ["project-selector", "server", "evidence", "advanced"],
                    "minDominantShare": 0.58,
                    "targetDominantShare": 0.68,
                    "maxInactiveTax": 0.075,
                    "weight": 1.45,
                    "reason": "After selection, the compact project context persists and the full roster collapses.",
                },
                {
                    "phase": "planning",
                    "dominantSlot": "workflow",
                    "requiredSlots": ["project-context", "command", "workflow", "status"],
                    "activeSupportSlots": ["server"],
                    "collapsedSlots": ["project-selector", "evidence", "advanced"],
                    "minDominantShare": 0.56,
                    "targetDominantShare": 0.66,
                    "maxInactiveTax": 0.075,
                    "weight": 1.20,
                    "reason": "Remote/server context may open only when planning needs it.",
                },
                {
                    "phase": "execution",
                    "dominantSlot": "workflow",
                    "requiredSlots": ["project-context", "command", "workflow", "status"],
                    "activeSupportSlots": ["evidence"],
                    "collapsedSlots": ["project-selector", "server", "advanced"],
                    "minDominantShare": 0.50,
                    "targetDominantShare": 0.60,
                    "maxInactiveTax": 0.075,
                    "weight": 1.10,
                    "reason": "Execution keeps workflow readable while operation progress/evidence becomes active.",
                },
                {
                    "phase": "proof-review",
                    "dominantSlot": "workflow",
                    "requiredSlots": ["project-context", "workflow", "status"],
                    "activeSupportSlots": ["evidence"],
                    "collapsedSlots": ["project-selector", "server", "advanced"],
                    "minDominantShare": 0.46,
                    "targetDominantShare": 0.56,
                    "maxInactiveTax": 0.075,
                    "weight": 1.00,
                    "reason": "Proof review opens receipts without making the project roster permanent.",
                },
                {
                    "phase": "recovery",
                    "dominantSlot": "workflow",
                    "requiredSlots": ["project-context", "workflow", "status"],
                    "activeSupportSlots": ["advanced"],
                    "collapsedSlots": ["project-selector", "server", "evidence"],
                    "minDominantShare": 0.46,
                    "targetDominantShare": 0.56,
                    "maxInactiveTax": 0.075,
                    "weight": 0.95,
                    "reason": "Advanced/manual Git controls are usable only in recovery and should not be default-exposed.",
                },
            ],
            layout_unit_tree={
                "id": "git-tools-application",
                "role": "application-composition",
                "defaultPolicy": "dominant-workflow-stack",
                "policyCandidates": [
                    {
                        "policy": "dominant-workflow-stack",
                        "alias": "workflow-stack",
                        "preflightScore": 100,
                    },
                ],
                "externalInputs": ["repository-catalog", "command-intent"],
                "hardConstraints": [
                    "worst-child-cannot-be-averaged-away",
                    "active-critical-controls-remain-visible",
                ],
                "children": [
                    {
                        "id": "project-identity",
                        "role": "project-identity",
                        "slots": ["project-selector"],
                        "inputs": ["repository-catalog"],
                        "outputs": ["selected-project-scope"],
                        "defaultPolicy": "phase-selector-unit",
                        "policyCandidates": [
                            {
                                "policy": "phase-selector-unit",
                                "alias": "selector-trigger",
                                "preflightScore": 100,
                            },
                            {
                                "policy": "compact-project-rail",
                                "alias": "project-rail",
                                "preflightScore": 92,
                            },
                            {
                                "policy": "selector-overlay",
                                "alias": "selector-overlay",
                                "preflightScore": 88,
                            },
                        ],
                        "hardConstraints": [
                            "selector-full-only-during-project-selection",
                        ],
                    },
                    {
                        "id": "command-workflow",
                        "role": "primary-work",
                        "slots": ["command", "workflow"],
                        "inputs": ["selected-project-scope", "command-intent"],
                        "outputs": ["workflow-state", "workflow-claim"],
                        "defaultPolicy": "command-over-dominant",
                        "policyCandidates": [
                            {
                                "policy": "command-over-dominant",
                                "alias": "command-top",
                                "preflightScore": 100,
                            },
                            {
                                "policy": "command-inline-header",
                                "alias": "command-inline",
                                "preflightScore": 95,
                            },
                            {
                                "policy": "side-command-rail",
                                "alias": "command-side",
                                "preflightScore": 89,
                            },
                        ],
                        "hardConstraints": [
                            "command-controls-workflow",
                            "workflow-owns-readable-space",
                        ],
                    },
                    {
                        "id": "persistent-feedback",
                        "role": "persistent-feedback",
                        "slots": ["project-context", "status"],
                        "inputs": ["selected-project-scope", "workflow-state"],
                        "outputs": ["persistent-context", "operation-status"],
                        "defaultPolicy": "shared-horizontal-band",
                        "policyCandidates": [
                            {
                                "policy": "shared-horizontal-band",
                                "alias": "feedback-band",
                                "preflightScore": 100,
                            },
                            {
                                "policy": "stacked-feedback",
                                "alias": "feedback-stack",
                                "preflightScore": 84,
                            },
                            {
                                "policy": "workflow-footer-overlay",
                                "alias": "feedback-overlay",
                                "preflightScore": 88,
                            },
                        ],
                        "hardConstraints": [
                            "members-share-band",
                            "members-dock-to-workflow",
                        ],
                    },
                    {
                        "id": "phase-support",
                        "role": "phase-support",
                        "slots": ["server", "evidence", "advanced"],
                        "inputs": [
                            "selected-project-scope",
                            "workflow-state",
                            "workflow-claim",
                        ],
                        "outputs": [
                            "server-context",
                            "evidence-proof",
                            "recovery-controls",
                        ],
                        "defaultPolicy": "one-active-plus-triggers",
                        "policyCandidates": [
                            {
                                "policy": "one-active-plus-triggers",
                                "alias": "support-triggers",
                                "preflightScore": 94,
                            },
                            {
                                "policy": "bounded-bottom-drawer",
                                "alias": "support-bottom",
                                "preflightScore": 98,
                            },
                            {
                                "policy": "bounded-side-drawer",
                                "alias": "support-side",
                                "preflightScore": 96,
                            },
                            {
                                "policy": "inline-phase-stage",
                                "alias": "support-inline",
                                "preflightScore": 90,
                            },
                        ],
                        "hardConstraints": [
                            "one-phase-support-surface-active",
                            "inactive-support-becomes-trigger",
                        ],
                    },
                ],
            },
            unit_compositions={
                "phase-aware": {
                    "rootPolicy": "dominant-workflow-stack",
                    "unitPolicies": {
                        "project-identity": "phase-selector-unit",
                        "command-workflow": "command-over-dominant",
                        "persistent-feedback": "shared-horizontal-band",
                        "phase-support": "one-active-plus-triggers",
                    },
                },
                "selected-context-workflow": {
                    "rootPolicy": "dominant-workflow-stack",
                    "unitPolicies": {
                        "project-identity": "selected-context-selector",
                        "command-workflow": "command-over-dominant",
                        "persistent-feedback": "shared-horizontal-band",
                        "phase-support": "side-active-support",
                    },
                },
                "progressive-workflow": {
                    "rootPolicy": "dominant-workflow-stack",
                    "unitPolicies": {
                        "project-identity": "progressive-selector",
                        "command-workflow": "command-over-dominant",
                        "persistent-feedback": "shared-horizontal-band",
                        "phase-support": "narrow-side-active-support",
                    },
                },
                "workflow-with-proof-drawer": {
                    "rootPolicy": "dominant-workflow-stack",
                    "unitPolicies": {
                        "project-identity": "progressive-selector",
                        "command-workflow": "command-over-dominant",
                        "persistent-feedback": "shared-horizontal-band",
                        "phase-support": "proof-drawer-or-side-support",
                    },
                },
                "static": {
                    "rootPolicy": "legacy-flat",
                    "unitPolicies": {
                        "project-identity": "legacy-flat",
                        "command-workflow": "legacy-flat",
                        "persistent-feedback": "legacy-flat",
                        "phase-support": "legacy-flat",
                    },
                },
            },
            nodes=[
                node("project-selector", "navigation-outline", "Project Roster / Switcher", role="navigation", priority="secondary", visibility="deferable", proximity="near", weight=2, scroll="allowed", min_visible_share=0.012, connects=["project-context", "workflow", "status"], semantics={
                    "phasePersistence": ["phase-specific-selector"],
                    "defaultRealization": ["collapsed-trigger"],
                    "presentationSet": ["project-selection"],
                    "phase": ["project-selection"],
                    "availability": ["selection-phase"],
                    "softPreferences": ["selected-context-after-selection", "collapsible-selector"],
                }, items=[
                    item("collection", "Selected project: main_computer_test"), item("collection", "Recent project: hub-site"), item("collection", "Recent project: worker-lab"),
                    item("control", "Add local path"), item("status", "Repository inspected · branch available"),
                ]),
                node("project-context", "repository-context", "Selected Project Context", role="status", priority="primary", visibility="required", proximity="attached", weight=1, scroll="no", min_visible_share=0.018, connects=["project-selector", "workflow", "command", "status"], semantics={
                    "phasePersistence": ["persistent-after-selection"],
                    "defaultRealization": ["compact-band"],
                    "presentationSet": ["selected-project-default", "planning", "execution", "proof-review", "recovery"],
                    "phase": ["selected-project-default", "planning", "execution", "proof-review", "recovery"],
                    "persistence": ["always-visible"],
                    "hardConstraints": ["must-remain-visible"],
                    "softPreferences": ["compact-context-band"],
                }, items=[
                    item("status", "main_computer_test · branch feature/flog-git-workflow"),
                    item("status", "dirty: 2 files · upstream: origin/main · remote: local Gitea"),
                ]),
                node("command", "authority-command", "Workflow Commands", role="command", priority="primary", visibility="required", proximity="near", weight=2, scroll="no", connects=["workflow", "status", "evidence"], items=[
                    item("button", "Inspect"), item("button", "Plan commit"), item("button", "Publish"), item("button", "Open remote"), item("control", "Remote target"),
                ]),
                node("workflow", "primary-workspace", "Guided Repository Workflow", role="focus", priority="focus", visibility="required", proximity="self", weight=12, connects=["project-context", "command", "server", "status", "evidence"], semantics={
                    "phase": ["selected-project-default", "planning", "execution", "proof-review", "recovery"],
                    "presentationSet": ["selected-project-default", "planning", "execution", "proof-review", "recovery"],
                    "softPreferences": ["dominant-progressive-workflow", "selected-context-remains-visible"],
                }, items=[
                    item("surface", "Guided action plan: inspect → review changes → stage → commit → publish", role="workflow-plan"),
                    item("collection", "Changed file: main_computer/web/applications/apps/git-tools.html"),
                    item("collection", "Changed file: tests/test_git_page_wizard_workflow.py"),
                    item("collection", "Planned action: create branch and commit semantic remock"),
                    item("collection", "Preflight: dry-run patch, inspect diff, create receipt"),
                    item("status", "Workflow state: project selected, publish target unresolved"),
                    item("text", "The selected project workflow is the readable work surface; roster, remote context, proof, and advanced controls change by phase."),
                ]),
                node("server", "inspector-detail", "Remote and Server Context", role="detail", priority="secondary", visibility="deferable", proximity="near", weight=2, connects=["workflow", "command", "evidence"], semantics={
                    "phasePersistence": ["phase-specific-support"],
                    "defaultRealization": ["collapsed-trigger"],
                    "presentationSet": ["planning", "execution"],
                    "phase": ["planning", "execution"],
                    "availability": ["context-dependent"],
                }, items=[
                    item("status", "Local Gitea reachable"), item("control", "Remote owner"), item("control", "Repository name"), item("button", "Refresh server"),
                ]),
                node("status", "status", "Operation Status", role="status", priority="primary", visibility="required", proximity="near", weight=1, scroll="no", connects=["project-context", "command", "workflow"], items=[
                    item("status", "No active operation"), item("status", "Selected project is clean enough to plan"),
                ]),
                node("evidence", "evidence", "Operation Evidence", role="evidence", priority="secondary", visibility="deferable", proximity="near", weight=3, min_visible_share=0.012, connects=["command", "workflow", "server"], semantics={
                    "phasePersistence": ["phase-specific-support"],
                    "defaultRealization": ["collapsed-trigger"],
                    "presentationSet": ["execution", "proof-review"],
                    "phase": ["execution", "proof-review"],
                    "availability": ["operation-dependent"],
                }, items=[
                    item("evidence", "Dry-run transcript and Git receipt stay tied to the selected project."),
                    item("evidence", "Publish proof records remote URL, branch, commit hash, and exit state."),
                ]),
                node("advanced", "inspector-detail", "Advanced Git Controls", role="detail", priority="secondary", visibility="deferable", proximity="loose", weight=2, min_visible_share=0.012, connects=["workflow", "evidence"], semantics={
                    "phasePersistence": ["deferable"],
                    "defaultRealization": ["collapsed-trigger"],
                    "presentationSet": ["recovery"],
                    "phase": ["recovery", "advanced"],
                    "availability": ["manual-recovery"],
                }, items=[
                    item("button", "Manual fetch"), item("button", "Force push guard"), item("control", "Refspec"), item("text", "Advanced controls may defer after the main workflow is readable."),
                ]),
            ],
        ),
        hierarchy(
            id="worker-marketplace-policy-workbench",
            title="Worker Marketplace Policy Workbench",
            description="Worker marketplace configuration app with top hub controls, a dominant policy surface, persistent runtime status, and deferable receipts/guardrails.",
            source_app="worker",
            root_concern="worker.marketplace-policy",
            focus_slot="marketplace",
            desired_focus_share=0.64,
            min_focus_share=0.52,
            max_focus_share=0.84,
            required_companions=["hub", "status"],
            nearby_companions=[],
            deferable_slots=["receipts", "guardrails"],
            forbidden_default_hidden=["hub", "marketplace", "status"],
            preferred_families=["top-band-dominant-surface", "top-band-focus-overlay", "progressive-workflow", "focus-priority", "source-order-stacked", "bounded-drawer"],
            dangerous_families={
                "sectioned-sidebar": "worker configuration is a sequential marketplace policy surface, not a permanent navigation-sidebar app",
                "split-pane": "sell/use policy sections should not be split away from the worker marketplace surface by default",
                "dashboard-grid": "worker setup is a staged configuration workflow, not unrelated peer dashboard cards",
            },
            nodes=[
                node("hub", "authority-command", "Hub + Availability Controls", role="command", priority="primary", visibility="required", proximity="near", weight=2, scroll="no", connects=["marketplace", "status", "receipts"], items=[
                    item("button", "Mainnet"), item("button", "Testnet"), item("button", "Test"), item("button", "Dev"),
                    item("control", "Accept paid jobs"), item("control", "Only when totally idle"), item("control", "When AI is idle"),
                    item("button", "Retry Hub"), item("button", "Disconnect"),
                ]),
                node("marketplace", "primary-worker-marketplace-surface", "Worker Marketplace Policy", role="focus", priority="focus", visibility="required", proximity="self", weight=13, connects=["hub", "status", "receipts", "guardrails"], items=[
                    item("surface", "One worker marketplace surface owns both sell-work and use-remote-workers policy sections.", role="worker-policy-surface"),
                    item("collection", "Sell Work: output-token offer, model offer, minimum ETH per estimated token, requested ring"),
                    item("collection", "Worker setup: wallet, credit wallet, assigned ring, worker ID, registration, pool, runtime status"),
                    item("collection", "Use Remote Workers: overflow mode, max ETH per token, max output tokens, daily spend guard"),
                    item("collection", "Wallet readiness: primary wallet, recovery contacts, multi-session key, bridge account funding"),
                    item("status", "Runtime policy: local first, paid jobs disabled until setup and hub registration succeed"),
                    item("text", "The app reads as a top-to-bottom configuration workflow; nothing in the core task wants a permanent sidebar."),
                    item("text", "Buyer and seller controls are internal sections of the marketplace policy surface, not separate navigation panes."),
                ]),
                node("status", "status", "Runtime State", role="status", priority="primary", visibility="required", proximity="near", weight=2, scroll="no", connects=["hub", "marketplace", "receipts"], items=[
                    item("status", "Network: None"), item("status", "Hub session offline"), item("status", "Offer: local only"),
                    item("status", "Worker runtime: not accepting"), item("status", "Requester idle"),
                ]),
                node("receipts", "worker-operation-proof", "Registration + Funding Receipts", role="evidence", priority="secondary", visibility="deferable", proximity="loose", weight=2, min_visible_share=0.012, connects=["hub", "marketplace", "status"], items=[
                    item("evidence", "Hub registration receipt records worker id, pricing policy, ring, and pool."),
                    item("evidence", "Faucet/funding receipt records tx hash, chain id, and bridge balance."),
                    item("evidence", "Runtime sync proof confirms the worker availability state that the status strip summarizes."),
                ]),
                node("guardrails", "deferable-worker-guardrails", "Future Remote-use Guardrails", role="detail", priority="secondary", visibility="deferable", proximity="loose", weight=2, min_visible_share=0.012, connects=["marketplace", "status"], items=[
                    item("control", "Only use remote workers when local AI is busy"), item("control", "Ask before spending"),
                    item("control", "Do not send private files"), item("text", "Future guardrails live in a disclosure/drawer and should not force a default sidebar."),
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
    realization = str(node.get("realization") or "persistent")
    base_class = (
        f"node-{slugify(slot)} kind-{slugify(node['kind'])} "
        f"role-{slugify(node.get('role', 'support'))}"
    )
    attrs = {
        "class": f"flog-node {base_class}",
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
        "data-flog-phase-dominant": "true" if node.get("phaseDominant") else "false",
        "data-flog-phase-support": "true" if node.get("phaseSupport") else "false",
        "data-flog-realization": realization,
        "data-flog-unit-id": node.get("layoutUnitId", ""),
        "data-flog-unit-role": node.get("layoutUnitRole", ""),
        "data-flog-unit-policy": node.get("layoutUnitPolicy", ""),
        "data-flog-ownership-mode": node.get(
            "layoutUnitOwnershipMode", "partition"
        ),
        "data-flog-overlay-target": node.get("layoutUnitOverlayTarget", ""),
        "data-flog-max-occlusion-share": str(
            node.get("layoutUnitMaxOcclusionShare", 0.0)
        ),
        "data-flog-unit-path": "/".join(node.get("layoutUnitPath") or []),
    }
    semantics = node.get("semantics") or {}
    for semantic_key in (*SEMANTIC_RELATION_KEYS, *SEMANTIC_QUALITY_KEYS):
        values = _as_list(semantics.get(semantic_key))
        if values:
            attrs[_semantic_attr_name(semantic_key)] = " ".join(values)

    if realization == "compact-trigger":
        attrs["class"] = f"flog-trigger {base_class}"
        attrs["data-mc"] = "action"
        attrs["data-flog-min-visible-share"] = "0.0025"
        attrs["data-flog-focus"] = "false"
        attrs["data-flog-trigger-for"] = slot
        attrs["aria-expanded"] = "false"
        attrs["type"] = "button"
        attr_text = " ".join(
            f'{name}="{html.escape(str(value), quote=True)}"'
            for name, value in attrs.items()
        )
        return (
            f"<button {attr_text}>"
            f"<span class=\"trigger-label\">{html.escape(node['title'])}</span>"
            "</button>"
        )

    attrs["id"] = f"flog-surface-{slugify(slot)}"
    if focus:
        attrs["aria-label"] = f"Focus surface: {node['title']}"
    attr_text = " ".join(
        f'{name}="{html.escape(str(value), quote=True)}"'
        for name, value in attrs.items()
    )
    parts = [f"<section {attr_text}>"]
    parts.append(
        f"<header class=\"node-header\"><h2>{html.escape(node['title'])}</h2>"
        f"<span>{html.escape(node['kind'])}</span></header>"
    )
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
            parts.append(
                f"<button data-mc=\"action\" data-mc-slot=\"{escaped_slot}.action\" "
                f"{item_attrs}>{escaped}</button>"
            )
        elif kind == "control":
            control_id = f"{slugify(slot)}-{slugify(label)}"
            parts.append(
                f"<label data-mc=\"control\" data-mc-slot=\"{escaped_slot}.control\" "
                f"for=\"{control_id}\" {item_attrs}>"
                f"{escaped}<input id=\"{control_id}\" value=\"{escaped}\" /></label>"
            )
        elif kind == "collection":
            parts.append(
                f"<div class=\"item collection-item\" data-mc=\"collection-item\" "
                f"data-mc-slot=\"{escaped_slot}.item\" {item_attrs}>{escaped}</div>"
            )
        elif kind == "status":
            parts.append(
                f"<div class=\"item status-item\" data-mc=\"status\" "
                f"data-mc-slot=\"{escaped_slot}.status\" {item_attrs}>{escaped}</div>"
            )
        elif kind == "evidence":
            parts.append(
                f"<pre class=\"evidence-item\" data-mc=\"evidence\" "
                f"data-mc-slot=\"{escaped_slot}.evidence\" {item_attrs}>{escaped}</pre>"
            )
        elif kind == "surface":
            parts.append(
                f"<div class=\"surface-item\" data-mc=\"surface\" "
                f"data-mc-slot=\"{escaped_slot}.surface\" {item_attrs}>{escaped}</div>"
            )
        else:
            parts.append(
                f"<p class=\"text-item\" data-mc=\"text\" "
                f"data-mc-slot=\"{escaped_slot}.text\" {item_attrs}>{escaped}</p>"
            )
    parts.append("</div>")
    parts.append("</section>")
    return "\n".join(parts)



def layout_unit_markup(
    unit: dict[str, Any],
    region_nodes_by_slot: dict[str, dict[str, Any]],
    focus_slot: str,
) -> str:
    """Render one recursive unit wrapper around its currently realized regions."""

    own_nodes = [
        region_nodes_by_slot[slot]
        for slot in unit.get("slots") or []
        if slot in region_nodes_by_slot
    ]
    child_markup = [
        layout_unit_markup(child, region_nodes_by_slot, focus_slot)
        for child in unit.get("children") or []
    ]
    child_markup = [part for part in child_markup if part]
    if not own_nodes and not child_markup:
        return ""

    unit_id = str(unit["id"])
    role = str(unit.get("role") or "support")
    policy = str(unit.get("policy") or unit.get("defaultPolicy") or "source-order")
    realization = str(unit.get("realization") or "active")
    attrs = {
        "class": (
            f"flog-layout-unit unit-{slugify(unit_id)} "
            f"unit-role-{slugify(role)} unit-policy-{slugify(policy)}"
        ),
        "data-flog-unit-id": unit_id,
        "data-flog-unit-role": role,
        "data-flog-unit-policy": policy,
        "data-flog-unit-realization": realization,
        "data-flog-ownership-mode": unit.get("ownershipMode", "partition"),
        "data-flog-overlay-target": unit.get("overlayTarget", ""),
        "data-flog-max-occlusion-share": str(
            unit.get("maxOcclusionShare", 0.0)
        ),
        "data-flog-unit-path": "/".join(unit.get("path") or [unit_id]),
        "data-flog-unit-slots": " ".join(unit.get("descendantSlots") or []),
        "data-flog-unit-active-slots": " ".join(unit.get("activeSlots") or []),
        "data-flog-unit-trigger-slots": " ".join(unit.get("triggerSlots") or []),
        "data-flog-unit-active-support-slots": " ".join(
            unit.get("activeSupportSlots") or []
        ),
        "data-flog-unit-inputs": " ".join(unit.get("inputs") or []),
        "data-flog-unit-outputs": " ".join(unit.get("outputs") or []),
        "data-flog-unit-has-active-support": (
            "true" if unit.get("activeSupportSlots") else "false"
        ),
        "aria-label": f"Layout unit: {unit_id}",
    }
    attr_text = " ".join(
        f'{name}="{html.escape(str(value), quote=True)}"'
        for name, value in attrs.items()
    )
    body = "\n".join(
        [*(node_markup(node, focus_slot) for node in own_nodes), *child_markup]
    )
    return f"<section {attr_text}>\n{body}\n</section>"


def render_layout_unit_tree(
    realized_hierarchy: dict[str, Any],
    region_nodes: list[dict[str, Any]],
    focus_slot: str,
) -> str:
    tree = realized_hierarchy.get("layoutUnitTree")
    if not tree:
        return "\n".join(node_markup(node, focus_slot) for node in region_nodes)
    by_slot = {node["slot"]: node for node in region_nodes}
    markup = layout_unit_markup(tree, by_slot, focus_slot)
    composition = realized_hierarchy.get("unitComposition") or {}
    attrs = {
        "class": "flog-unit-tree",
        "data-flog-unit-tree": tree.get("id", ""),
        "data-flog-unit-root-policy": composition.get("rootPolicy", ""),
        "data-flog-unit-search-mode": composition.get("searchMode", ""),
        "data-flog-unit-parallel-branches": " ".join(
            composition.get("parallelBranches") or []
        ),
    }
    attr_text = " ".join(
        f'{name}="{html.escape(str(value), quote=True)}"'
        for name, value in attrs.items()
    )
    return f"<div {attr_text}>\n{markup}\n</div>"


def render_realized_trial_html(
    realized_hierarchy: dict[str, Any],
    candidate: str | dict[str, Any],
    chrome: str,
) -> str:
    candidate_id = candidate_identity(candidate)
    render_family = candidate_render_family(candidate)
    mode = candidate_mode(candidate)
    focus_slot = realized_hierarchy["focusSlot"]
    region_nodes = [
        node for node in realized_hierarchy["nodes"]
        if node.get("realization") != "compact-trigger"
    ]
    trigger_nodes = [
        node for node in realized_hierarchy["nodes"]
        if node.get("realization") == "compact-trigger"
    ]
    nodes = render_layout_unit_tree(realized_hierarchy, region_nodes, focus_slot)
    triggers = "\n".join(node_markup(node, focus_slot) for node in trigger_nodes)
    trigger_strip = (
        f'<div class="flog-trigger-strip" data-flog-trigger-count="{len(trigger_nodes)}">'
        f"{triggers}</div>"
        if trigger_nodes
        else ""
    )
    contract = realized_hierarchy.get("roleContract", {})
    phase_names = [item["phase"] for item in semantic_phase_scenarios(realized_hierarchy)]
    policy = realized_hierarchy.get("candidatePolicy") or candidate_phase_policy(candidate)
    phase = str(realized_hierarchy.get("phase") or "default")
    root_attrs = {
        "id": realized_hierarchy["id"],
        "class": (
            f"flog-root trial-{slugify(render_family)} "
            f"candidate-mode-{slugify(mode)} "
            f"policy-{'phase-aware' if policy.get('phaseAware') else 'static'} "
            f"{'has-layout-units' if realized_hierarchy.get('layoutUnitTree') else 'no-layout-units'}"
        ),
        "data-mc": "component",
        "data-mc-component": "SyntheticFlogHierarchy",
        "data-mc-kind": "control-surface",
        "data-mc-flow": "linear-hierarchical",
        "data-mc-source-app": realized_hierarchy.get("sourceApp", ""),
        "data-mc-root-concern": realized_hierarchy["rootConcern"],
        "data-flog-candidate": candidate_id,
        "data-flog-render-family": render_family,
        "data-flog-candidate-mode": mode,
        "data-flog-composition-label": (
            (realized_hierarchy.get("unitComposition") or {}).get("compositionLabel", "")
        ),
        "data-flog-source-app": realized_hierarchy.get("sourceApp", ""),
        "data-flog-focus-slot": focus_slot,
        "data-flog-base-focus-slot": realized_hierarchy.get("baseFocusSlot", focus_slot),
        "data-flog-phase": phase,
        "data-flog-phase-aware": "true" if policy.get("phaseAware") else "false",
        "data-flog-active-support-placement": policy.get("activeSupportPlacement", ""),
        "data-flog-unit-composition-root-policy": (
            (realized_hierarchy.get("unitComposition") or {}).get("rootPolicy", "")
        ),
        "data-flog-unit-composition-search-mode": (
            (realized_hierarchy.get("unitComposition") or {}).get("searchMode", "")
        ),
        "data-flog-layout-unit-count": str(
            len(realized_hierarchy.get("layoutUnits") or [])
        ),
        "data-flog-desired-focus-share": str(realized_hierarchy["desiredFocusShare"]),
        "data-flog-min-focus-share": str(realized_hierarchy["minFocusShare"]),
        "data-flog-max-focus-share": str(realized_hierarchy["maxFocusShare"]),
        "data-flog-min-useful-focus-occupancy": str(
            realized_hierarchy.get("minUsefulFocusOccupancy", 0.30)
        ),
        "data-flog-target-useful-focus-occupancy": str(
            realized_hierarchy.get("targetUsefulFocusOccupancy", 0.56)
        ),
        "data-flog-required-companions": " ".join(
            contract.get("requiredCompanions", [])
        ),
        "data-flog-nearby-companions": " ".join(
            contract.get("nearbyCompanions", [])
        ),
        "data-flog-deferable-slots": " ".join(contract.get("deferableSlots", [])),
        "data-flog-forbidden-default-hidden": " ".join(
            contract.get("forbiddenDefaultHidden", [])
        ),
        "data-flog-preferred-families": " ".join(
            contract.get("preferredFamilies", [])
        ),
        "data-flog-dangerous-families": " ".join(
            (contract.get("dangerousFamilies") or {}).keys()
        ),
        "data-flog-interaction-phases": " ".join(phase_names),
    }
    root_attr_text = " ".join(
        f'{name}="{html.escape(str(value), quote=True)}"'
        for name, value in root_attrs.items()
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>FLOG synthetic trial - {html.escape(realized_hierarchy['title'])} - {html.escape(candidate_id)} - {html.escape(phase)}</title>
<style>
{TRIAL_CSS}
</style>
</head>
<body data-flog-chrome="{html.escape(chrome, quote=True)}" data-flog-trial="{html.escape(candidate_id, quote=True)}" data-flog-render-family="{html.escape(render_family, quote=True)}" data-flog-phase="{html.escape(phase, quote=True)}">
  <main class="flog-stage">
    <article {root_attr_text}>
      <div class="root-title" data-mc="heading" data-mc-slot="title">
        <h1>{html.escape(realized_hierarchy['title'])}</h1>
        <p>{html.escape(realized_hierarchy['description'])}</p>
      </div>
      {nodes}
      {trigger_strip}
    </article>
  </main>
</body>
</html>
"""


def render_trial_html(
    hierarchy: dict[str, Any],
    candidate: str | dict[str, Any],
    chrome: str,
    scenario: dict[str, Any] | None = None,
) -> str:
    selected_scenario = copy.deepcopy(scenario or canonical_phase_scenario(hierarchy))
    realized = realize_phase(hierarchy, candidate, selected_scenario)
    return render_realized_trial_html(realized, candidate, chrome)


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
.flog-trigger-strip { display: none; }
.flog-trigger { font: inherit; }
/* Layout units are semantic-only until a candidate opts into recursive composition. */
.flog-unit-tree,
.flog-layout-unit { display: contents; }
button, input, select, textarea, summary {
  min-height: 34px;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 7px 10px;
  background: rgba(255,255,255,.68);
  color: var(--ink);
}
.flog-node input,
.flog-node select,
.flog-node textarea {
  width: 100%;
  min-width: 0;
  max-width: 100%;
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

.trial-bounded-drawer,
.trial-top-band-dominant-surface,
.trial-top-band-focus-overlay,
.trial-selected-context-workflow,
.trial-progressive-workflow,
.trial-workflow-with-proof-drawer {
  display: flex;
  flex-direction: column;
  gap: var(--gap);
  overflow: hidden;
  position: relative;
}
.trial-bounded-drawer .root-title,
.trial-top-band-dominant-surface .root-title,
.trial-top-band-focus-overlay .root-title,
.trial-selected-context-workflow .root-title,
.trial-progressive-workflow .root-title,
.trial-workflow-with-proof-drawer .root-title {
  order: 0;
  flex: 0 0 auto;
}
.trial-bounded-drawer .flog-node[data-flog-role="command"],
.trial-top-band-dominant-surface .flog-node[data-flog-role="command"],
.trial-top-band-focus-overlay .flog-node[data-flog-role="command"],
.trial-selected-context-workflow .flog-node[data-flog-role="command"],
.trial-progressive-workflow .flog-node[data-flog-role="command"],
.trial-workflow-with-proof-drawer .flog-node[data-flog-role="command"] {
  order: 10;
  flex: 0 0 58px;
  min-height: 58px;
  padding: 7px 9px;
  flex-direction: row;
  align-items: center;
}
.trial-bounded-drawer .flog-node[data-flog-role="command"] .node-header,
.trial-top-band-dominant-surface .flog-node[data-flog-role="command"] .node-header,
.trial-top-band-focus-overlay .flog-node[data-flog-role="command"] .node-header,
.trial-selected-context-workflow .flog-node[data-flog-role="command"] .node-header,
.trial-progressive-workflow .flog-node[data-flog-role="command"] .node-header,
.trial-workflow-with-proof-drawer .flog-node[data-flog-role="command"] .node-header {
  flex: 0 0 168px;
}
.trial-bounded-drawer .flog-node[data-flog-role="command"] .node-header span,
.trial-top-band-dominant-surface .flog-node[data-flog-role="command"] .node-header span,
.trial-top-band-focus-overlay .flog-node[data-flog-role="command"] .node-header span,
.trial-selected-context-workflow .flog-node[data-flog-role="command"] .node-header span,
.trial-progressive-workflow .flog-node[data-flog-role="command"] .node-header span,
.trial-workflow-with-proof-drawer .flog-node[data-flog-role="command"] .node-header span {
  display: none;
}
.trial-bounded-drawer .flog-node[data-flog-role="command"] .node-body,
.trial-top-band-dominant-surface .flog-node[data-flog-role="command"] .node-body,
.trial-top-band-focus-overlay .flog-node[data-flog-role="command"] .node-body,
.trial-selected-context-workflow .flog-node[data-flog-role="command"] .node-body,
.trial-progressive-workflow .flog-node[data-flog-role="command"] .node-body,
.trial-workflow-with-proof-drawer .flog-node[data-flog-role="command"] .node-body {
  flex: 1 1 auto;
  min-width: 0;
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: minmax(104px, 1fr);
  align-items: center;
  gap: 6px;
}
.trial-bounded-drawer .flog-node[data-flog-role="command"] label,
.trial-top-band-dominant-surface .flog-node[data-flog-role="command"] label,
.trial-top-band-focus-overlay .flog-node[data-flog-role="command"] label,
.trial-selected-context-workflow .flog-node[data-flog-role="command"] label,
.trial-progressive-workflow .flog-node[data-flog-role="command"] label,
.trial-workflow-with-proof-drawer .flog-node[data-flog-role="command"] label {
  display: flex;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
}
.trial-bounded-drawer .flog-node[data-flog-role="command"] label input,
.trial-top-band-dominant-surface .flog-node[data-flog-role="command"] label input,
.trial-top-band-focus-overlay .flog-node[data-flog-role="command"] label input,
.trial-selected-context-workflow .flog-node[data-flog-role="command"] label input,
.trial-progressive-workflow .flog-node[data-flog-role="command"] label input,
.trial-workflow-with-proof-drawer .flog-node[data-flog-role="command"] label input {
  min-width: 72px;
  width: 100%;
}
.trial-bounded-drawer .flog-node[data-flog-phase-dominant="true"],
.trial-top-band-dominant-surface .flog-node[data-flog-phase-dominant="true"],
.trial-top-band-focus-overlay .flog-node[data-flog-phase-dominant="true"],
.trial-selected-context-workflow .flog-node[data-flog-phase-dominant="true"],
.trial-progressive-workflow .flog-node[data-flog-phase-dominant="true"],
.trial-workflow-with-proof-drawer .flog-node[data-flog-phase-dominant="true"] {
  order: 20;
  flex: 1 1 auto;
  min-height: 300px;
}
.trial-bounded-drawer .flog-node[data-flog-role="status"],
.trial-top-band-dominant-surface .flog-node[data-flog-role="status"],
.trial-top-band-focus-overlay .flog-node[data-flog-role="status"],
.trial-selected-context-workflow .flog-node[data-flog-role="status"],
.trial-progressive-workflow .flog-node[data-flog-role="status"],
.trial-workflow-with-proof-drawer .flog-node[data-flog-role="status"] {
  order: 30;
  flex: 0 0 42px;
  min-height: 42px;
  padding: 6px 9px;
  flex-direction: row;
  align-items: center;
}
.trial-bounded-drawer .flog-node[data-flog-role="status"] .node-header,
.trial-top-band-dominant-surface .flog-node[data-flog-role="status"] .node-header,
.trial-top-band-focus-overlay .flog-node[data-flog-role="status"] .node-header,
.trial-selected-context-workflow .flog-node[data-flog-role="status"] .node-header,
.trial-progressive-workflow .flog-node[data-flog-role="status"] .node-header,
.trial-workflow-with-proof-drawer .flog-node[data-flog-role="status"] .node-header {
  flex: 0 0 190px;
}
.trial-bounded-drawer .flog-node[data-flog-role="status"] .node-header span,
.trial-top-band-dominant-surface .flog-node[data-flog-role="status"] .node-header span,
.trial-top-band-focus-overlay .flog-node[data-flog-role="status"] .node-header span,
.trial-selected-context-workflow .flog-node[data-flog-role="status"] .node-header span,
.trial-progressive-workflow .flog-node[data-flog-role="status"] .node-header span,
.trial-workflow-with-proof-drawer .flog-node[data-flog-role="status"] .node-header span {
  display: none;
}
.trial-bounded-drawer .flog-node[data-flog-role="status"] .node-body,
.trial-top-band-dominant-surface .flog-node[data-flog-role="status"] .node-body,
.trial-top-band-focus-overlay .flog-node[data-flog-role="status"] .node-body,
.trial-selected-context-workflow .flog-node[data-flog-role="status"] .node-body,
.trial-progressive-workflow .flog-node[data-flog-role="status"] .node-body,
.trial-workflow-with-proof-drawer .flog-node[data-flog-role="status"] .node-body {
  flex: 1 1 auto;
  min-width: 0;
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: minmax(150px, 1fr);
  gap: 6px;
  overflow: hidden;
}
.trial-bounded-drawer .flog-node[data-flog-role="status"] .item,
.trial-top-band-dominant-surface .flog-node[data-flog-role="status"] .item,
.trial-top-band-focus-overlay .flog-node[data-flog-role="status"] .item,
.trial-selected-context-workflow .flog-node[data-flog-role="status"] .item,
.trial-progressive-workflow .flog-node[data-flog-role="status"] .item,
.trial-workflow-with-proof-drawer .flog-node[data-flog-role="status"] .item {
  min-height: 26px;
  padding: 4px 7px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.trial-selected-context-workflow .node-project-context,
.trial-progressive-workflow .node-project-context,
.trial-workflow-with-proof-drawer .node-project-context {
  order: 11;
  flex: 0 0 46px;
  min-height: 46px;
  padding: 6px 9px;
  flex-direction: row;
  align-items: center;
}
.trial-selected-context-workflow .node-project-context .node-header,
.trial-progressive-workflow .node-project-context .node-header,
.trial-workflow-with-proof-drawer .node-project-context .node-header {
  flex: 0 0 190px;
}
.trial-selected-context-workflow .node-project-context .node-header span,
.trial-progressive-workflow .node-project-context .node-header span,
.trial-workflow-with-proof-drawer .node-project-context .node-header span {
  display: none;
}
.trial-selected-context-workflow .node-project-context .node-body,
.trial-progressive-workflow .node-project-context .node-body,
.trial-workflow-with-proof-drawer .node-project-context .node-body {
  flex: 1 1 auto;
  min-width: 0;
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: minmax(220px, 1fr);
  gap: 6px;
}
.trial-selected-context-workflow .node-project-context .item,
.trial-progressive-workflow .node-project-context .item,
.trial-workflow-with-proof-drawer .node-project-context .item {
  min-height: 28px;
  padding: 5px 7px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.policy-phase-aware .flog-trigger-strip {
  order: 40;
  flex: 0 0 40px;
  min-height: 40px;
  display: flex;
  align-items: stretch;
  gap: 8px;
  padding: 3px 0 0;
  overflow: hidden;
}
.policy-phase-aware .flog-trigger {
  flex: 1 1 0;
  min-width: 0;
  min-height: 34px;
  height: 34px;
  padding: 6px 10px;
  border-style: dashed;
  background: var(--panel-2);
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.policy-phase-aware .flog-trigger .trigger-label {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
}
.policy-phase-aware .flog-node[data-flog-phase-support="true"] {
  position: absolute;
  z-index: 8;
  top: 178px;
  right: 14px;
  bottom: 64px;
  width: 24%;
  min-width: 250px;
  min-height: 180px;
  overflow: auto;
  box-shadow: 0 16px 36px rgba(6, 23, 45, 0.24);
}
.trial-selected-context-workflow .flog-node[data-flog-phase-support="true"] {
  width: 28%;
}
.trial-progressive-workflow .flog-node[data-flog-phase-support="true"] {
  width: 22%;
}
.trial-workflow-with-proof-drawer .flog-node[data-flog-phase-support="true"][data-flog-role="evidence"] {
  top: auto;
  left: 14px;
  right: 14px;
  bottom: 64px;
  width: auto;
  min-width: 0;
  height: 23%;
  min-height: 150px;
}
.trial-bounded-drawer .flog-node[data-flog-phase-support="true"] {
  top: auto;
  left: 14px;
  right: 14px;
  bottom: 64px;
  width: auto;
  min-width: 0;
  height: 25%;
  min-height: 160px;
}
.policy-phase-aware .flog-node[data-flog-phase-support="true"] .node-body {
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 7px;
}
.trial-split-pane .flog-node[data-flog-phase-dominant="true"],
.trial-sectioned-sidebar .flog-node[data-flog-phase-dominant="true"],
.trial-inspector .flog-node[data-flog-phase-dominant="true"],
.trial-focus-priority .flog-node[data-flog-phase-dominant="true"] {
  grid-area: focus !important;
}


.trial-recursive-composition {
  display: flex;
  flex-direction: column;
  gap: var(--gap);
  overflow: hidden;
  position: relative;
}
.trial-recursive-composition .root-title {
  order: 0;
  flex: 0 0 auto;
}
.trial-recursive-composition .flog-node[data-flog-role="command"] {
  padding: 7px 9px;
  display: flex;
  min-width: 0;
}
.trial-recursive-composition .flog-node[data-flog-role="command"] .node-header {
  flex: 0 0 168px;
}
.trial-recursive-composition .flog-node[data-flog-role="command"] .node-header span {
  display: none;
}
.trial-recursive-composition .flog-node[data-flog-role="command"] .node-body {
  flex: 1 1 auto;
  min-width: 0;
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: minmax(104px, 1fr);
  align-items: center;
  gap: 6px;
}
.trial-recursive-composition .flog-node[data-flog-role="command"] label {
  display: flex;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
}
.trial-recursive-composition .flog-node[data-flog-role="command"] label input {
  min-width: 72px;
  width: 100%;
}

/* Recursive unit composition: local policies solve local responsibilities while
   the root policy only composes the units.  Static candidates remain flattened. */
.has-layout-units.policy-phase-aware .flog-unit-tree {
  order: 15;
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="git-tools-application"] {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
  gap: var(--gap);
  position: relative;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="command-workflow"] {
  order: 10;
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
  gap: var(--gap);
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="command-workflow"]
  > .flog-node[data-flog-role="command"] {
  order: 10;
  flex: 0 0 58px;
  min-height: 58px;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="command-workflow"]
  > .flog-node[data-flog-phase-dominant="true"] {
  order: 20;
  flex: 1 1 auto;
  min-height: 280px;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="persistent-feedback"] {
  order: 20;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(0, 1fr));
  gap: 8px;
  flex: 0 0 42px;
  min-width: 0;
  min-height: 42px;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="persistent-feedback"]
  > .flog-node {
  order: initial;
  width: auto;
  min-width: 0;
  min-height: 42px;
  height: 42px;
  padding: 6px 8px;
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 6px;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="persistent-feedback"]
  > .flog-node .node-header {
  flex: 0 0 145px;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="persistent-feedback"]
  > .flog-node .node-header span {
  display: none;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="persistent-feedback"]
  > .flog-node .node-body {
  flex: 1 1 auto;
  min-width: 0;
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: minmax(120px, 1fr);
  gap: 5px;
  overflow: hidden;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="persistent-feedback"]
  > .flog-node .item {
  min-width: 0;
  min-height: 26px;
  padding: 4px 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="project-identity"][data-flog-unit-realization="active"] {
  order: 5;
  display: block;
  position: absolute;
  z-index: 7;
  top: 0;
  left: 0;
  bottom: 56px;
  width: 30%;
  min-width: 270px;
  min-height: 220px;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="project-identity"]
  > .flog-node {
  position: static !important;
  inset: auto !important;
  width: 100% !important;
  height: 100%;
  min-width: 0;
  min-height: 0;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="phase-support"][data-flog-unit-has-active-support="true"] {
  order: 30;
  display: block;
  position: absolute;
  z-index: 8;
  top: 72px;
  right: 0;
  bottom: 56px;
  width: 24%;
  min-width: 250px;
  min-height: 180px;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-side-active-support {
  width: 28%;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-narrow-side-active-support {
  width: 22%;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit[data-flog-unit-id="phase-support"]
  > .flog-node[data-flog-phase-support="true"] {
  position: static !important;
  inset: auto !important;
  width: 100% !important;
  height: 100%;
  min-width: 0;
  min-height: 0;
  overflow: auto;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-proof-drawer-or-side-support[data-flog-unit-active-support-slots~="evidence"] {
  top: auto;
  left: 0;
  right: 0;
  bottom: 56px;
  width: auto;
  min-width: 0;
  height: 23%;
  min-height: 150px;
}
.has-layout-units.policy-phase-aware .flog-trigger-strip {
  order: 40;
  flex: 0 0 40px;
}


/* Generated local-policy alternatives.  These selectors are keyed by unit
   policy, not by the legacy whole-page candidate name. */
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-command-inline-header[data-flog-unit-id="command-workflow"]
  > .flog-node[data-flog-role="command"] {
  flex-basis: 48px;
  min-height: 48px;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-side-command-rail[data-flog-unit-id="command-workflow"] {
  display: grid;
  grid-template-columns: minmax(165px, 0.18fr) minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr);
  gap: var(--gap);
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-side-command-rail[data-flog-unit-id="command-workflow"]
  > .flog-node[data-flog-role="command"] {
  min-height: 0;
  height: auto;
  flex-basis: auto;
  flex-direction: column;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-side-command-rail[data-flog-unit-id="command-workflow"]
  > .flog-node[data-flog-role="command"] .node-header {
  flex: 0 0 auto;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-side-command-rail[data-flog-unit-id="command-workflow"]
  > .flog-node[data-flog-role="command"] .node-body {
  grid-auto-flow: row;
  grid-auto-rows: minmax(34px, auto);
  grid-auto-columns: auto;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-side-command-rail[data-flog-unit-id="command-workflow"]
  > .flog-node[data-flog-phase-dominant="true"] {
  min-height: 280px;
  height: auto;
}

.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-stacked-feedback[data-flog-unit-id="persistent-feedback"] {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: repeat(2, minmax(36px, 1fr));
  flex-basis: 82px;
  min-height: 82px;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-stacked-feedback[data-flog-unit-id="persistent-feedback"]
  > .flog-node {
  height: 38px;
  min-height: 38px;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-workflow-footer-overlay[data-flog-unit-id="persistent-feedback"] {
  position: absolute;
  z-index: 6;
  left: 10px;
  right: 10px;
  bottom: 48px;
  height: 44px;
  min-height: 44px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-workflow-footer-overlay[data-flog-unit-id="persistent-feedback"]
  > .flog-node {
  height: 44px;
  min-height: 44px;
}

.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-compact-project-rail[data-flog-unit-id="project-identity"][data-flog-unit-realization="active"] {
  width: 23%;
  min-width: 230px;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-selector-overlay[data-flog-unit-id="project-identity"][data-flog-unit-realization="active"] {
  left: 50%;
  right: auto;
  width: 38%;
  min-width: 360px;
  transform: translateX(-50%);
  box-shadow: 0 18px 48px rgba(6, 23, 45, 0.30);
}

.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-bounded-side-drawer[data-flog-unit-id="phase-support"][data-flog-unit-has-active-support="true"] {
  width: 28%;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-bounded-bottom-drawer[data-flog-unit-id="phase-support"][data-flog-unit-has-active-support="true"] {
  top: auto;
  left: 0;
  right: 0;
  bottom: 56px;
  width: auto;
  min-width: 0;
  height: 25%;
  min-height: 160px;
}
.has-layout-units.policy-phase-aware
  .flog-layout-unit.unit-policy-inline-phase-stage[data-flog-unit-id="phase-support"][data-flog-unit-has-active-support="true"] {
  position: static;
  inset: auto;
  width: auto;
  min-width: 0;
  min-height: 150px;
  height: auto;
  flex: 0 0 24%;
}

@media (max-width: 720px) {
  .flog-stage { padding: 10px; }
  .flog-root { width: calc(100vw - 20px); height: calc(100vh - 20px); min-height: 0; }
  .trial-split-pane,
  .trial-sectioned-sidebar,
  .trial-inspector,
  .trial-dashboard-grid,
  .trial-focus-priority,
  .trial-bounded-drawer,
  .trial-top-band-dominant-surface,
  .trial-top-band-focus-overlay,
  .trial-selected-context-workflow,
  .trial-progressive-workflow,
  .trial-workflow-with-proof-drawer,
  .trial-recursive-composition {
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
({hierarchyId, candidate, renderFamily, candidateMode, chrome, viewportProfile, phase}) => {
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
    const unit = el.closest("[data-flog-unit-id]");
    return {
      selector: cssPath(el),
      slot: el.getAttribute("data-flog-slot") || el.getAttribute("data-mc-slot") || "",
      unitId: unit?.getAttribute("data-flog-unit-id") || "",
      unitRole: unit?.getAttribute("data-flog-unit-role") || "",
      unitPolicy: unit?.getAttribute("data-flog-unit-policy") || "",
      unitPath: unit?.getAttribute("data-flog-unit-path") || "",
      ownershipMode:
        el.getAttribute("data-flog-ownership-mode") ||
        unit?.getAttribute("data-flog-ownership-mode") ||
        "partition",
      overlayTarget:
        el.getAttribute("data-flog-overlay-target") ||
        unit?.getAttribute("data-flog-overlay-target") ||
        "",
      maxOcclusionShare: Number(
        el.getAttribute("data-flog-max-occlusion-share") ||
        unit?.getAttribute("data-flog-max-occlusion-share") ||
        "0"
      ),
      role: el.getAttribute("data-flog-role") || el.getAttribute("data-mc-role") || "",
      priority: el.getAttribute("data-flog-priority") || el.getAttribute("data-mc-rank") || "",
      visibility: el.getAttribute("data-flog-visibility") || el.getAttribute("data-mc-visibility") || "",
      realization: el.getAttribute("data-flog-realization") || "",
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


  function unionBounds(rects) {
    const visible = rects.filter((rect) => rect && rect.area > 4);
    if (!visible.length) {
      return {x: 0, y: 0, left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0, area: 0};
    }
    const left = Math.min(...visible.map((rect) => rect.left));
    const top = Math.min(...visible.map((rect) => rect.top));
    const right = Math.max(...visible.map((rect) => rect.right));
    const bottom = Math.max(...visible.map((rect) => rect.bottom));
    return {
      x: left,
      y: top,
      left,
      top,
      right,
      bottom,
      width: Math.max(0, right - left),
      height: Math.max(0, bottom - top),
      area: Math.max(0, right - left) * Math.max(0, bottom - top),
    };
  }

  function unitRecordFor(el) {
    const ownRect = rectObj(el.getBoundingClientRect());
    const childRects = Array.from(
      el.querySelectorAll(":scope > .flog-node, :scope > .flog-layout-unit, .flog-node")
    )
      .filter((child) => isVisibleByStyle(child))
      .map((child) => rectObj(child.getBoundingClientRect()))
      .filter((rect) => rect.area > 4);
    const raw = ownRect.area > 4 ? ownRect : unionBounds(childRects);
    return {
      unitId: el.getAttribute("data-flog-unit-id") || "",
      role: el.getAttribute("data-flog-unit-role") || "",
      policy: el.getAttribute("data-flog-unit-policy") || "",
      realization: el.getAttribute("data-flog-unit-realization") || "",
      ownershipMode: el.getAttribute("data-flog-ownership-mode") || "partition",
      overlayTarget: el.getAttribute("data-flog-overlay-target") || "",
      maxOcclusionShare: Number(el.getAttribute("data-flog-max-occlusion-share") || "0"),
      path: el.getAttribute("data-flog-unit-path") || "",
      slots: String(el.getAttribute("data-flog-unit-slots") || "").split(/\s+/).filter(Boolean),
      activeSlots: String(el.getAttribute("data-flog-unit-active-slots") || "").split(/\s+/).filter(Boolean),
      triggerSlots: String(el.getAttribute("data-flog-unit-trigger-slots") || "").split(/\s+/).filter(Boolean),
      activeSupportSlots: String(el.getAttribute("data-flog-unit-active-support-slots") || "").split(/\s+/).filter(Boolean),
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


  function pointInsideRect(x, y, rect) {
    return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
  }

  function semanticOwnerForElement(el) {
    if (!el || !el.closest) return null;
    const owner = el.closest(".flog-node, .flog-trigger");
    return owner && root.contains(owner) ? owner : null;
  }

  function colorAlpha(value) {
    const text = String(value || "").trim().toLowerCase();
    if (!text || text === "transparent") return 0;
    const rgba = text.match(/^rgba?\(([^)]+)\)$/);
    if (!rgba) return 1;
    const parts = rgba[1].split(/[\s,\/]+/).filter(Boolean);
    if (parts.length < 4) return 1;
    const alpha = Number(parts[3]);
    return Number.isFinite(alpha) ? Math.max(0, Math.min(1, alpha)) : 1;
  }

  function semanticOwnerPaintsOpaque(owner) {
    if (!owner) return false;
    const style = win.getComputedStyle(owner);
    const opacity = Number(style.opacity || "1");
    if (!Number.isFinite(opacity) || opacity <= 0.03) return false;
    if (style.backgroundImage && style.backgroundImage !== "none") return true;
    return colorAlpha(style.backgroundColor) * opacity >= 0.03;
  }

  function semanticOwnerKey(record, index) {
    return record.slot || record.selector || `owner-${index}`;
  }

  function unitAncestorsFor(owner) {
    const units = [];
    let unit = owner?.closest?.(".flog-layout-unit[data-flog-unit-id]") || null;
    while (unit && root.contains(unit)) {
      units.push(unit);
      const parent = unit.parentElement;
      unit = parent?.closest?.(".flog-layout-unit[data-flog-unit-id]") || null;
    }
    return units;
  }

  function paintedOwnershipShadowFor(
    nodeElements,
    records,
    unitEls,
    units,
    bounds,
    focusSlot
  ) {
    const entries = nodeElements
      .map((el, index) => {
        const record = records[index];
        if (!record) return null;
        const clipped = intersect(record.rect, bounds);
        if (clipped.area <= 0.5) return null;
        return {
          el,
          record,
          clipped,
          key: semanticOwnerKey(record, index),
        };
      })
      .filter(Boolean);
    const xs = Array.from(new Set([
      bounds.left,
      bounds.right,
      ...entries.flatMap((entry) => [entry.clipped.left, entry.clipped.right]),
    ])).sort((a, b) => a - b);
    const ys = Array.from(new Set([
      bounds.top,
      bounds.bottom,
      ...entries.flatMap((entry) => [entry.clipped.top, entry.clipped.bottom]),
    ])).sort((a, b) => a - b);
    const byElement = new Map(entries.map((entry) => [entry.el, entry]));
    const effectiveByKey = new Map();
    const unitEffectiveById = new Map();
    const occludedByKey = new Map();
    const overlayOcclusionByUnit = new Map();
    const partitionOverlapByUnit = new Map();
    let cellCount = 0;
    let ownedCellCount = 0;
    let exclusiveOwnedArea = 0;
    let declaredOverlayArea = 0;
    let partitionOverlapCellArea = 0;

    function addArea(map, key, area) {
      map.set(key, (map.get(key) || 0) + area);
    }

    function addOcclusion(lowerKey, upperKey, area) {
      if (!occludedByKey.has(lowerKey)) occludedByKey.set(lowerKey, new Map());
      addArea(occludedByKey.get(lowerKey), upperKey, area);
    }

    for (let xi = 0; xi < xs.length - 1; xi += 1) {
      const left = xs[xi];
      const right = xs[xi + 1];
      if (right - left <= 0.25) continue;
      const midX = (left + right) / 2;
      for (let yi = 0; yi < ys.length - 1; yi += 1) {
        const top = ys[yi];
        const bottom = ys[yi + 1];
        if (bottom - top <= 0.25) continue;
        const midY = (top + bottom) / 2;
        const candidates = entries.filter((entry) =>
          pointInsideRect(midX, midY, entry.clipped)
        );
        if (!candidates.length) continue;
        cellCount += 1;
        const hits = doc.elementsFromPoint(midX, midY);
        const stack = [];
        const seen = new Set();
        for (const hit of hits) {
          const owner = semanticOwnerForElement(hit);
          if (!owner || seen.has(owner) || !byElement.has(owner)) continue;
          seen.add(owner);
          stack.push(byElement.get(owner));
        }
        for (const candidateEntry of candidates) {
          if (!seen.has(candidateEntry.el)) stack.push(candidateEntry);
        }
        const topOwner =
          stack.find((entry) => semanticOwnerPaintsOpaque(entry.el)) ||
          stack[0] ||
          candidates[0];
        if (!topOwner) continue;
        const area = (right - left) * (bottom - top);
        ownedCellCount += 1;
        exclusiveOwnedArea += area;
        addArea(effectiveByKey, topOwner.key, area);
        for (const unitEl of unitAncestorsFor(topOwner.el)) {
          const unitId = unitEl.getAttribute("data-flog-unit-id") || "";
          if (unitId) addArea(unitEffectiveById, unitId, area);
        }

        if (!semanticOwnerPaintsOpaque(topOwner.el)) continue;
        const lowerEntries = candidates.filter((entry) => entry.el !== topOwner.el);
        let overlayClaimsCell = false;
        let partitionClaimsForeignCell = false;
        for (const lower of lowerEntries) {
          addOcclusion(lower.key, topOwner.key, area);
          const target = topOwner.record.overlayTarget || "";
          const foreignUnit = Boolean(
            topOwner.record.unitId &&
            lower.record.unitId &&
            topOwner.record.unitId !== lower.record.unitId
          );
          if (
            topOwner.record.ownershipMode === "overlay" &&
            (!target || lower.record.slot === target)
          ) {
            overlayClaimsCell = true;
          }
          if (
            topOwner.record.ownershipMode === "partition" &&
            foreignUnit
          ) {
            partitionClaimsForeignCell = true;
          }
        }
        if (overlayClaimsCell) {
          declaredOverlayArea += area;
          const unitId = topOwner.record.unitId || topOwner.key;
          addArea(overlayOcclusionByUnit, unitId, area);
        }
        if (partitionClaimsForeignCell) {
          partitionOverlapCellArea += area;
          const unitId = topOwner.record.unitId || topOwner.key;
          addArea(partitionOverlapByUnit, unitId, area);
        }
      }
    }

    const rawClaimedArea = entries.reduce(
      (total, entry) => total + entry.clipped.area,
      0
    );
    const unionClaimedArea = unionArea(
      entries.map((entry) => entry.clipped),
      bounds
    );
    const overlapMatrix = [];
    const nodeSummaries = entries.map((entry) => {
      const effectiveArea = effectiveByKey.get(entry.key) || 0;
      const occluders = Array.from(
        (occludedByKey.get(entry.key) || new Map()).entries()
      )
        .map(([upperKey, area]) => {
          const upper = entries.find((candidateEntry) => candidateEntry.key === upperKey);
          const upperMode = upper?.record.ownershipMode || "partition";
          const upperTarget = upper?.record.overlayTarget || "";
          const foreignUnit = Boolean(
            upper?.record.unitId &&
            entry.record.unitId &&
            upper.record.unitId !== entry.record.unitId
          );
          const declaredOverlay = Boolean(
            upperMode === "overlay" &&
            (!upperTarget || entry.record.slot === upperTarget)
          );
          const undeclaredPartitionOverlap = Boolean(
            upperMode === "partition" &&
            foreignUnit
          );
          return {
            slot: upper?.record.slot || "",
            unitId: upper?.record.unitId || "",
            ownerKey: upperKey,
            ownershipMode: upperMode,
            overlayTarget: upperTarget,
            foreignUnit,
            declaredOverlay,
            undeclaredPartitionOverlap,
            area,
            shareOfRoot: bounds.area > 0 ? area / bounds.area : 0,
            shareOfNode: entry.clipped.area > 0 ? area / entry.clipped.area : 0,
          };
        })
        .sort((a, b) => b.area - a.area);
      for (const occluder of occluders) {
        overlapMatrix.push({
          occludedSlot: entry.record.slot,
          occludedUnitId: entry.record.unitId,
          occludingSlot: occluder.slot,
          occludingUnitId: occluder.unitId,
          occludingOwnershipMode: occluder.ownershipMode,
          occludingOverlayTarget: occluder.overlayTarget,
          foreignUnit: occluder.foreignUnit,
          declaredOverlay: occluder.declaredOverlay,
          undeclaredPartitionOverlap: occluder.undeclaredPartitionOverlap,
          area: occluder.area,
          shareOfRoot: occluder.shareOfRoot,
          shareOfOccludedNode: occluder.shareOfNode,
        });
      }
      const occludedArea = Math.max(0, entry.clipped.area - effectiveArea);
      return {
        slot: entry.record.slot,
        unitId: entry.record.unitId,
        ownerKey: entry.key,
        ownershipMode: entry.record.ownershipMode,
        overlayTarget: entry.record.overlayTarget,
        maxOcclusionShare: entry.record.maxOcclusionShare,
        rawVisibleArea: entry.clipped.area,
        rawVisibleShare: bounds.area > 0 ? entry.clipped.area / bounds.area : 0,
        effectiveVisibleArea: effectiveArea,
        effectiveVisibleShare: bounds.area > 0 ? effectiveArea / bounds.area : 0,
        occludedArea,
        occludedShare: bounds.area > 0 ? occludedArea / bounds.area : 0,
        occludedBy: occluders,
      };
    });
    overlapMatrix.sort((a, b) => b.area - a.area);

    const unitSummaries = units.map((unitRecord, index) => {
      const unitEl = unitEls[index];
      const clipped = intersect(unitRecord.rect, bounds);
      const effectiveArea = unitEffectiveById.get(unitRecord.unitId) || 0;
      return {
        unitId: unitRecord.unitId,
        policy: unitRecord.policy,
        realization: unitRecord.realization,
        ownershipMode: unitRecord.ownershipMode,
        overlayTarget: unitRecord.overlayTarget,
        maxOcclusionShare: unitRecord.maxOcclusionShare,
        rawVisibleArea: clipped.area,
        rawVisibleShare: bounds.area > 0 ? clipped.area / bounds.area : 0,
        effectiveOwnedArea: effectiveArea,
        effectiveOwnedShare: bounds.area > 0 ? effectiveArea / bounds.area : 0,
        selector: unitEl ? cssPath(unitEl) : "",
      };
    });

    const overlayBudgets = unitSummaries
      .filter((unit) => unit.ownershipMode === "overlay")
      .map((unit) => {
        const area = overlayOcclusionByUnit.get(unit.unitId) || 0;
        const share = bounds.area > 0 ? area / bounds.area : 0;
        return {
          unitId: unit.unitId,
          policy: unit.policy,
          overlayTarget: unit.overlayTarget,
          occlusionArea: area,
          occlusionShare: share,
          maxOcclusionShare: unit.maxOcclusionShare,
          exceeded:
            unit.maxOcclusionShare > 0 &&
            share > unit.maxOcclusionShare + 0.0005,
        };
      });
    const focus = nodeSummaries.find((item) => item.slot === focusSlot) || null;
    const doubleClaimedArea = Math.max(0, rawClaimedArea - unionClaimedArea);
    const doubleClaimedShare =
      bounds.area > 0 ? doubleClaimedArea / bounds.area : 0;
    const declaredOverlayShare =
      bounds.area > 0 ? declaredOverlayArea / bounds.area : 0;
    const undeclaredPartitionOverlapArea = Math.max(
      0,
      doubleClaimedArea - declaredOverlayArea
    );
    const undeclaredPartitionOverlapShare =
      bounds.area > 0 ? undeclaredPartitionOverlapArea / bounds.area : 0;
    const partitionOverlapCellShare =
      bounds.area > 0 ? partitionOverlapCellArea / bounds.area : 0;
    const partitionOverlapByUnitRows = Array.from(
      partitionOverlapByUnit.entries()
    )
      .map(([unitId, area]) => ({
        unitId,
        area,
        shareOfRoot: bounds.area > 0 ? area / bounds.area : 0,
      }))
      .sort((a, b) => b.area - a.area);
    const undeclaredPartitionOverlapMatrix = overlapMatrix.filter(
      (item) => item.undeclaredPartitionOverlap
    );
    return {
      mode: "shadow-only",
      enforced: false,
      algorithm: "rect-edge-cells+elementsFromPoint",
      rootArea: bounds.area,
      cellCount,
      ownedCellCount,
      rawClaimedArea,
      rawClaimedShare: bounds.area > 0 ? rawClaimedArea / bounds.area : 0,
      unionClaimedArea,
      unionClaimedShare: bounds.area > 0 ? unionClaimedArea / bounds.area : 0,
      doubleClaimedArea,
      doubleClaimedShare,
      exclusiveOwnedArea,
      exclusiveOwnedShare:
        bounds.area > 0 ? exclusiveOwnedArea / bounds.area : 0,
      declaredOverlayArea,
      declaredOverlayShare,
      undeclaredPartitionOverlapArea,
      undeclaredPartitionOverlapShare,
      partitionOverlapCellArea,
      partitionOverlapCellShare,
      partitionOverlapByUnit: partitionOverlapByUnitRows,
      undeclaredPartitionOverlapMatrix,
      effectiveFocusShare: focus?.effectiveVisibleShare || 0,
      rawFocusShare: focus?.rawVisibleShare || 0,
      focusOccludedShare: focus?.occludedShare || 0,
      nodes: nodeSummaries,
      units: unitSummaries,
      overlapMatrix,
      overlayBudgets,
      overlayBudgetExceeded: overlayBudgets.filter((item) => item.exceeded),
    };
  }

  function associatedLabelsForControl(el) {
    const labels = new Set();
    for (const label of Array.from(el.labels || [])) labels.add(label);
    const closestLabel = el.closest?.("label") || null;
    if (closestLabel) labels.add(closestLabel);
    if (el.id) {
      for (const label of Array.from(
        doc.querySelectorAll(`label[for="${CSS.escape(el.id)}"]`)
      )) {
        labels.add(label);
      }
    }
    return Array.from(labels);
  }

  function declaredControlProxyFor(hit, el) {
    if (!hit || !hit.closest || !el.id) return null;
    const proxy = hit.closest("[data-flog-control-proxy-for]");
    if (!proxy) return null;
    const targets = String(
      proxy.getAttribute("data-flog-control-proxy-for") || ""
    )
      .split(/\s+/)
      .map((item) => item.trim())
      .filter(Boolean);
    return targets.includes(el.id) ? proxy : null;
  }

  function isAssociatedControlHit(hit, el, labels) {
    if (!hit) return false;
    if (hit === el || el.contains(hit)) return true;
    if (
      labels.some(
        (label) => hit === label || label.contains(hit)
      )
    ) {
      return true;
    }
    return Boolean(declaredControlProxyFor(hit, el));
  }

  function firstPointerReceivingHit(hits) {
    const passThrough = [];
    for (const hit of hits) {
      if (!hit || !hit.tagName) continue;
      const style = win.getComputedStyle(hit);
      if (
        style.display === "none" ||
        style.visibility === "hidden" ||
        Number(style.opacity || "1") === 0
      ) {
        continue;
      }
      if (style.pointerEvents === "none") {
        passThrough.push(hit);
        continue;
      }
      return {hit, passThrough};
    }
    return {hit: null, passThrough};
  }

  function semanticOwnerLabel(owner) {
    if (!owner) return "";
    return (
      owner.getAttribute("data-flog-slot") ||
      owner.getAttribute("data-mc-slot") ||
      cssPath(owner)
    );
  }

  function controlInterceptionShadowFor(el) {
    const record = recordFor(el);
    const rect = intersect(record.rect, rootClipped);
    const emptyResult = {
      ...record,
      sampleCount: 0,
      actionablePointCount: 0,
      selfOwnedPointCount: 0,
      foreignInterceptedPointCount: 0,
      noPointerTargetPointCount: 0,
      pointerEventsNonePassThroughPointCount: 0,
      unownedPointerTargetPointCount: 0,
      interceptedPointCount: 0,
      actionableShare: 1,
      fullyIntercepted: false,
      partiallyIntercepted: false,
      fullyForeignIntercepted: false,
      partiallyForeignIntercepted: false,
      interceptedBy: [],
      outcomeCounts: {
        "self-owned": 0,
        "foreign-intercepted": 0,
        "no-pointer-target": 0,
        "pointer-events-none-pass-through": 0,
        "unowned-pointer-target": 0,
      },
      sampleOutcomes: [],
    };
    if (rect.area <= 4 || !isVisibleByStyle(el)) {
      return emptyResult;
    }
    const insetX = Math.min(Math.max(1, rect.width * 0.16), rect.width / 2);
    const insetY = Math.min(Math.max(1, rect.height * 0.20), rect.height / 2);
    const points = [
      {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2},
      {x: rect.left + insetX, y: rect.top + insetY},
      {x: rect.right - insetX, y: rect.top + insetY},
      {x: rect.left + insetX, y: rect.bottom - insetY},
      {x: rect.right - insetX, y: rect.bottom - insetY},
    ].filter((point) =>
      point.x >= viewport.left &&
      point.x <= viewport.right &&
      point.y >= viewport.top &&
      point.y <= viewport.bottom
    );
    const labels = associatedLabelsForControl(el);
    const controlOwner = semanticOwnerForElement(el);
    const controlOwnerKey = semanticOwnerLabel(controlOwner);
    let actionablePointCount = 0;
    let selfOwnedPointCount = 0;
    let foreignInterceptedPointCount = 0;
    let noPointerTargetPointCount = 0;
    let pointerEventsNonePassThroughPointCount = 0;
    let unownedPointerTargetPointCount = 0;
    const interceptedBy = new Map();
    const sampleOutcomes = [];
    const outcomeCounts = {
      "self-owned": 0,
      "foreign-intercepted": 0,
      "no-pointer-target": 0,
      "pointer-events-none-pass-through": 0,
      "unowned-pointer-target": 0,
    };

    for (const point of points) {
      const hits = doc.elementsFromPoint(point.x, point.y);
      const pointerResult = firstPointerReceivingHit(hits);
      const pointerHit = pointerResult.hit;
      const passedThrough = pointerResult.passThrough.length > 0;
      if (passedThrough) {
        pointerEventsNonePassThroughPointCount += 1;
        outcomeCounts["pointer-events-none-pass-through"] += 1;
      }

      if (!pointerHit) {
        noPointerTargetPointCount += 1;
        outcomeCounts["no-pointer-target"] += 1;
        sampleOutcomes.push({
          ...point,
          outcome: "no-pointer-target",
          pointerTarget: "",
          owner: "",
          passThrough: passedThrough,
        });
        continue;
      }

      const associated = isAssociatedControlHit(
        pointerHit,
        el,
        labels
      );
      const targetOwner = semanticOwnerForElement(pointerHit);
      const targetOwnerKey = semanticOwnerLabel(targetOwner);
      const sameSemanticOwner = Boolean(
        controlOwner &&
        targetOwner &&
        controlOwner === targetOwner
      );

      if (associated || sameSemanticOwner) {
        selfOwnedPointCount += 1;
        outcomeCounts["self-owned"] += 1;
        if (associated) actionablePointCount += 1;
        sampleOutcomes.push({
          ...point,
          outcome: passedThrough
            ? "pointer-events-none-pass-through"
            : "self-owned",
          pointerTarget: cssPath(pointerHit),
          owner: targetOwnerKey || controlOwnerKey,
          associated,
          passThrough: passedThrough,
        });
        continue;
      }

      if (targetOwner && targetOwner !== controlOwner) {
        foreignInterceptedPointCount += 1;
        outcomeCounts["foreign-intercepted"] += 1;
        const key = targetOwnerKey || cssPath(pointerHit);
        if (key) {
          interceptedBy.set(key, (interceptedBy.get(key) || 0) + 1);
        }
        sampleOutcomes.push({
          ...point,
          outcome: "foreign-intercepted",
          pointerTarget: cssPath(pointerHit),
          owner: key,
          associated: false,
          passThrough: passedThrough,
        });
        continue;
      }

      unownedPointerTargetPointCount += 1;
      outcomeCounts["unowned-pointer-target"] += 1;
      sampleOutcomes.push({
        ...point,
        outcome: "unowned-pointer-target",
        pointerTarget: cssPath(pointerHit),
        owner: "",
        associated: false,
        passThrough: passedThrough,
      });
    }

    const fullyForeignIntercepted =
      points.length > 0 &&
      foreignInterceptedPointCount === points.length;
    const partiallyForeignIntercepted =
      foreignInterceptedPointCount > 0;
    return {
      ...record,
      sampleCount: points.length,
      controlOwner: controlOwnerKey,
      actionablePointCount,
      selfOwnedPointCount,
      foreignInterceptedPointCount,
      noPointerTargetPointCount,
      pointerEventsNonePassThroughPointCount,
      unownedPointerTargetPointCount,
      interceptedPointCount: foreignInterceptedPointCount,
      actionableShare:
        points.length > 0 ? actionablePointCount / points.length : 1,
      fullyIntercepted: fullyForeignIntercepted,
      partiallyIntercepted: partiallyForeignIntercepted,
      fullyForeignIntercepted,
      partiallyForeignIntercepted,
      interceptedBy: Array.from(interceptedBy.entries())
        .map(([owner, count]) => ({owner, count}))
        .sort((a, b) => b.count - a.count),
      outcomeCounts,
      sampleOutcomes,
    };
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

  const nodes = Array.from(root.querySelectorAll(".flog-node, .flog-trigger")).filter((el) => isVisibleByStyle(el));
  const focus = root.querySelector("[data-flog-phase-dominant='true']") || root.querySelector("[data-flog-focus='true']");
  const requiredNodes = nodes.filter((el) => (el.getAttribute("data-flog-priority") || "") === "primary" || el.getAttribute("data-flog-focus") === "true");
  const nodeElementRecords = nodes
    .map((el) => ({el, record: recordFor(el)}))
    .filter((item) => item.record.rect.area > 4);
  const nodeElements = nodeElementRecords.map((item) => item.el);
  const nodeRecords = nodeElementRecords.map((item) => item.record);
  const unitElementRecords = Array.from(
    root.querySelectorAll(".flog-layout-unit[data-flog-unit-id]")
  )
    .map((el) => ({el, record: unitRecordFor(el)}))
    .filter((item) => item.record.unitId && item.record.rect.area > 4);
  const unitElements = unitElementRecords.map((item) => item.el);
  const unitRecords = unitElementRecords.map((item) => item.record);
  const focusRecord = focus ? recordFor(focus) : null;
  const visibleRequired = requiredNodes.map(recordFor).filter((item) => item.rect.area > 4 && intersect(item.rect, rootClipped).area > 4);
  const paintedOwnershipShadow = paintedOwnershipShadowFor(
    nodeElements,
    nodeRecords,
    unitElements,
    unitRecords,
    rootClipped,
    root.getAttribute("data-flog-focus-slot") || ""
  );

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
  const candidateFamily = renderFamily || candidate;
  const legacyFamilyCandidate = candidateMode !== "recursive-composition";
  const preferredFamilyMatch = legacyFamilyCandidate && preferredFamilies.includes(candidateFamily);
  const dangerousFamilyMatch = legacyFamilyCandidate && dangerousFamilies.includes(candidateFamily);

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

  const criticalControlInterceptionShadow = controls.map(
    controlInterceptionShadowFor
  );
  const fullyInterceptedCriticalControlsShadow =
    criticalControlInterceptionShadow.filter(
      (item) => item.fullyForeignIntercepted
    );
  const partiallyInterceptedCriticalControlsShadow =
    criticalControlInterceptionShadow.filter(
      (item) => item.partiallyForeignIntercepted
    );
  const controlInterceptionOutcomeTotalsShadow =
    criticalControlInterceptionShadow.reduce(
      (totals, item) => {
        totals.actionable += item.actionablePointCount || 0;
        totals.selfOwned += item.selfOwnedPointCount || 0;
        totals.foreignIntercepted +=
          item.foreignInterceptedPointCount || 0;
        totals.noPointerTarget +=
          item.noPointerTargetPointCount || 0;
        totals.pointerEventsNonePassThrough +=
          item.pointerEventsNonePassThroughPointCount || 0;
        totals.unownedPointerTarget +=
          item.unownedPointerTargetPointCount || 0;
        return totals;
      },
      {
        actionable: 0,
        selfOwned: 0,
        foreignIntercepted: 0,
        noPointerTarget: 0,
        pointerEventsNonePassThrough: 0,
        unownedPointerTarget: 0,
      }
    );

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
  const sourceOrderStarvesHighFocus = candidateFamily === "source-order-stacked" && minFocusShare >= 0.40 && focusShare < minFocusShare;
  if (dangerousFamilyMatch) warnings.push(`candidate is marked dangerous for this role contract: ${candidate}`);
  if (sourceOrderStarvesHighFocus) warnings.push("source-order-stacked preserves visibility but starves a high-focus hierarchy");
  if (legacyFamilyCandidate && !preferredFamilyMatch && preferredFamilies.length) warnings.push(`candidate is outside preferred families for this hierarchy`);
  if (doc.body.scrollWidth > viewport.width + 2) warnings.push("document overflows horizontally");
  if (doc.body.scrollHeight > viewport.height + 2 && candidateFamily !== "source-order-stacked") warnings.push("document overflows vertically outside the root");

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
  const hardGeometryGatePassed =
    focusShare >= minFocusShare &&
    companionVisibilityRatio === 1 &&
    clippedCriticalControls.length === 0 &&
    hiddenCriticalControls.length === 0 &&
    !dangerousFamilyMatch;
  if (preferredFamilyMatch && hardGeometryGatePassed) {
    reviewNotes.push(`candidate belongs to a preferred family; no score bonus is applied`);
  } else if (preferredFamilyMatch) {
    reviewNotes.push(`candidate belongs to a preferred family but still must pass hard geometry gates`);
  } else if (legacyFamilyCandidate && preferredFamilies.length) {
    reviewNotes.push(`candidate is outside preferred families: ${preferredFamilies.join(", ")}`);
  } else if (!legacyFamilyCandidate) {
    reviewNotes.push("candidate identity is a generated local-policy composition, not a legacy whole-page family");
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
  reviewNotes.push("Painted ownership and control interception are reported in shadow mode only; ranking still uses raw geometry.");
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
  score -= Math.max(0, scrollOwners.length - 2) * 5;
  if (doc.body.scrollWidth > viewport.width + 2) score -= 16;
  if (doc.body.scrollHeight > viewport.height + 2 && candidateFamily !== "source-order-stacked") score -= 8;
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
  unitRecords.forEach((item) => {
    addBox(item, "#00c2c7", `UNIT ${item.unitId} · ${item.policy}`, "rgba(0,194,199,0.025)");
  });
  nodeRecords.forEach((item) => {
    const isFocus = item.slot === (root.getAttribute("data-flog-focus-slot") || "");
    addBox(item, isFocus ? "#b46cff" : "#38d66b", `${isFocus ? "FOCUS " : ""}${item.slot}`, isFocus ? "rgba(180,108,255,0.07)" : "rgba(56,214,107,0.04)");
  });
  nodeRecords
    .filter((item) => item.ownershipMode === "overlay")
    .forEach((item) => {
      addBox(
        item,
        "#ff9f43",
        `SHADOW overlay ${item.slot} → ${item.overlayTarget || "declared target"}`,
        "repeating-linear-gradient(135deg, rgba(255,159,67,.16) 0 6px, rgba(255,159,67,.03) 6px 12px)"
      );
    });
  scrollOwners.slice(0, 14).forEach((item) => {
    const rect = item.rect;
    addBox({documentRect: {left: rect.left + win.scrollX, top: rect.top + win.scrollY, width: rect.width, height: rect.height}}, "#ffbf3f", `scroll ${item.slot || item.selector}`, "rgba(255,191,63,0.06)");
  });
  clippedCriticalControls.slice(0, 18).forEach((item) => addBox(item, "#ff4d4d", `clipped ${item.selector}`, "rgba(255,77,77,0.06)"));
  hiddenCriticalControls.slice(0, 12).forEach((item, index) => {
    addBox({documentRect: {left: rootDocumentRect.left + 8, top: rootDocumentRect.top + 28 + index * 18, width: 220, height: 16}}, "#ff4d4d", `hidden ${item.selector}`, "rgba(255,77,77,0.16)");
  });
  partiallyInterceptedCriticalControlsShadow.slice(0, 18).forEach((item) => {
    const label = item.fullyForeignIntercepted
      ? "foreign intercepted"
      : "partially foreign intercepted";
    addBox(
      item,
      "#ff3df2",
      `SHADOW ${label} ${item.selector}`,
      "rgba(255,61,242,0.10)"
    );
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
    `hierarchy=${hierarchyId} layout=${candidate} phase=${phase || root.getAttribute("data-flog-phase") || "default"} viewport=${viewportProfile} chrome=${chrome}`,
    `score=${score} status=${status} wasted/unclaimed=${unclaimedAreaRatio.toFixed(3)} focus(raw)=${focusShare.toFixed(3)} focus(effective-shadow)=${paintedOwnershipShadow.effectiveFocusShare.toFixed(3)} target=${desiredFocusShare.toFixed(2)} focus-occupancy=${usefulFocusOccupancy.toFixed(3)} companion-proximity=${companionProximityScore.toFixed(3)}`,
    `painted-ownership=shadow-only exclusive=${paintedOwnershipShadow.exclusiveOwnedShare.toFixed(3)} double-claimed=${paintedOwnershipShadow.doubleClaimedShare.toFixed(3)} overlay=${paintedOwnershipShadow.declaredOverlayShare.toFixed(3)} undeclared-partition-overlap=${paintedOwnershipShadow.undeclaredPartitionOverlapShare.toFixed(3)} foreign-intercepted-controls=${partiallyInterceptedCriticalControlsShadow.length}`,
    `<span style="color:#43a7ff">blue=root</span> <span style="color:#00c2c7">cyan=layout unit</span> <span style="color:#38d66b">green=semantic node</span> <span style="color:#b46cff">purple=focus target</span> <span style="color:#ffbf3f">orange=scroll</span> <span style="color:#ff9f43">hatched=declared overlay (shadow)</span> <span style="color:#ff3df2">magenta=foreign interception (shadow)</span> <span style="color:#ff4d4d">red=clipped/hidden</span>`,
  ].join("<br>");
  overlay.appendChild(legend);

  return {
    hierarchyId,
    candidate,
    renderFamily: candidateFamily,
    candidateMode,
    chrome,
    viewportProfile,
    phase: phase || root.getAttribute("data-flog-phase") || "default",
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
      layoutUnitCount: unitRecords.length,
      nodeCoverageRatio,
      unclaimedAreaRatio,
      focusShare,
      paintedOwnershipMode: "shadow-only",
      paintedOwnershipEnforced: false,
      paintedOwnershipShadow,
      effectiveFocusShareShadow: paintedOwnershipShadow.effectiveFocusShare,
      focusOccludedShareShadow: paintedOwnershipShadow.focusOccludedShare,
      exclusiveOwnedShareShadow: paintedOwnershipShadow.exclusiveOwnedShare,
      doubleClaimedShareShadow: paintedOwnershipShadow.doubleClaimedShare,
      declaredOverlayShareShadow: paintedOwnershipShadow.declaredOverlayShare,
      undeclaredPartitionOverlapShareShadow:
        paintedOwnershipShadow.undeclaredPartitionOverlapShare,
      partitionOverlapCellShareShadow:
        paintedOwnershipShadow.partitionOverlapCellShare,
      interceptedCriticalControlCountShadow:
        fullyInterceptedCriticalControlsShadow.length,
      partiallyInterceptedCriticalControlCountShadow:
        partiallyInterceptedCriticalControlsShadow.length,
      foreignInterceptedCriticalControlCountShadow:
        partiallyInterceptedCriticalControlsShadow.length,
      controlInterceptionOutcomeTotalsShadow,
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
      units: unitRecords,
      nodes: nodeRecords,
      focus: focusRecord,
      focusContent: focusContentRecords.slice(0, 30),
      clippedCriticalControls: clippedCriticalControls.slice(0, 20),
      hiddenCriticalControls: hiddenCriticalControls.slice(0, 20),
      criticalControlInterceptionShadow:
        criticalControlInterceptionShadow.slice(0, 40),
      fullyInterceptedCriticalControlsShadow:
        fullyInterceptedCriticalControlsShadow.slice(0, 20),
      partiallyInterceptedCriticalControlsShadow:
        partiallyInterceptedCriticalControlsShadow.slice(0, 20),
      paintedOwnershipOverlapMatrix:
        paintedOwnershipShadow.overlapMatrix.slice(0, 80),
      undeclaredPartitionOverlapMatrixShadow:
        paintedOwnershipShadow.undeclaredPartitionOverlapMatrix.slice(0, 40),
      controlInterceptionOutcomeTotalsShadow,
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
        "Shadow diagnostics sampled exclusive painted ownership, inter-unit occlusion, declared overlay budgets, and critical-control interception without changing ranking.",
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
    phase_fit = item.get("phaseFit") or {}
    unit_fit = item.get("layoutUnitFit") or {}
    painted_shadow = facts.get("paintedOwnershipShadow") or {}
    highest_classification = highest_scoring.get("classification") or {}
    row = {
        "hierarchyId": item["hierarchyId"],
        "viewportProfile": item["viewportProfile"],
        "candidate": item["candidate"],
        "candidateMode": item.get("candidateMode", "legacy-family"),
        "renderFamily": item.get("renderFamily", item.get("candidate", "")),
        "compositionLabel": (item.get("unitComposition") or {}).get(
            "compositionLabel", ""
        ),
        "compositionPreflightScore": (item.get("unitComposition") or {}).get(
            "preflightScore"
        ),
        "score": classification.get("score", 0),
        "status": classification.get("status", "fail"),
        "selectionState": selection_state,
        "noPassingCandidate": selection_state == "noPassingCandidate",
        "unclaimedAreaRatio": facts.get("unclaimedAreaRatio", 0),
        "focusShare": facts.get("focusShare", 0),
        "paintedOwnershipMode": facts.get("paintedOwnershipMode", "unavailable"),
        "paintedOwnershipEnforced": bool(
            facts.get("paintedOwnershipEnforced", False)
        ),
        "effectiveFocusShareShadow": painted_shadow.get(
            "effectiveFocusShare", facts.get("focusShare", 0)
        ),
        "focusOccludedShareShadow": painted_shadow.get(
            "focusOccludedShare", 0
        ),
        "exclusiveOwnedShareShadow": painted_shadow.get(
            "exclusiveOwnedShare", 0
        ),
        "doubleClaimedShareShadow": painted_shadow.get(
            "doubleClaimedShare", 0
        ),
        "declaredOverlayShareShadow": painted_shadow.get(
            "declaredOverlayShare", 0
        ),
        "undeclaredPartitionOverlapShareShadow": painted_shadow.get(
            "undeclaredPartitionOverlapShare", 0
        ),
        "partitionOverlapCellShareShadow": painted_shadow.get(
            "partitionOverlapCellShare", 0
        ),
        "partitionOverlapByUnitShadow": painted_shadow.get(
            "partitionOverlapByUnit", []
        ),
        "undeclaredPartitionOverlapMatrixShadow": painted_shadow.get(
            "undeclaredPartitionOverlapMatrix", []
        ),
        "overlayBudgetExceededShadow": painted_shadow.get(
            "overlayBudgetExceeded", []
        ),
        "interceptedCriticalControlCountShadow": facts.get(
            "interceptedCriticalControlCountShadow", 0
        ),
        "partiallyInterceptedCriticalControlCountShadow": facts.get(
            "partiallyInterceptedCriticalControlCountShadow", 0
        ),
        "foreignInterceptedCriticalControlCountShadow": facts.get(
            "foreignInterceptedCriticalControlCountShadow",
            facts.get("partiallyInterceptedCriticalControlCountShadow", 0),
        ),
        "controlInterceptionOutcomeTotalsShadow": facts.get(
            "controlInterceptionOutcomeTotalsShadow", {}
        ),
        "paintedOwnershipOverlapMatrixShadow": painted_shadow.get(
            "overlapMatrix", []
        ),
        "desiredFocusShare": facts.get("desiredFocusShare", 0),
        "usefulFocusOccupancy": facts.get("usefulFocusOccupancy", 0),
        "companionProximityScore": facts.get("companionProximityScore", 1),
        "geometryScore": classification.get("geometryScore", classification.get("score", 0)),
        "selectionScore": classification.get("selectionScore", classification.get("score", 0)),
        "contractFitScore": classification.get("contractFitScore", 0),
        "contractFitState": classification.get("contractFitState", "notEvaluated"),
        "affordanceFitScore": classification.get("affordanceFitScore", 0),
        "affordanceFitState": classification.get("affordanceFitState", "notEvaluated"),
        "phaseFitScore": classification.get("phaseFitScore", 0),
        "phaseFitState": classification.get("phaseFitState", "notEvaluated"),
        "phaseWorstScore": phase_fit.get("worstScore", 0),
        "phaseMeanScore": phase_fit.get("meanScore", 0),
        "phaseSelectedDefaultScore": phase_fit.get("selectedDefaultScore", 0),
        "phaseSelectedDefaultPhase": phase_fit.get("selectedDefaultPhase", ""),
        "phaseHardFailureCount": phase_fit.get("hardFailureCount", 0),
        "layoutUnitFitScore": classification.get("layoutUnitFitScore", 100),
        "layoutUnitFitState": classification.get("layoutUnitFitState", "notDeclared"),
        "layoutUnitWorstScore": unit_fit.get("worstScore", 100),
        "layoutUnitHardFailureCount": unit_fit.get("hardFailureCount", 0),
        "layoutUnitParallelBranches": unit_fit.get("parallelBranches", []),
        "unitComposition": item.get("unitComposition", {}),
        "canonicalPhase": item.get("canonicalPhase", item.get("phase", "")),
        "phaseSnapshots": item.get("phaseSnapshots", {}),
        "contractFitReasons": (item.get("contractFit") or {}).get("positiveReasons", []),
        "contractFitRisks": (item.get("contractFit") or {}).get("riskReasons", []),
        "hardContractRisks": (item.get("contractFit") or {}).get("hardRiskReasons", []),
        "softContractRisks": (item.get("contractFit") or {}).get("softRiskReasons", []),
        "presentationSetReasons": (item.get("contractFit") or {}).get("presentationSetReasons", []),
        "phaseFitReasons": (item.get("phaseFit") or {}).get("positiveReasons", []),
        "phaseFitRisks": (item.get("phaseFit") or {}).get("riskReasons", []),
        "layoutUnitFitReasons": unit_fit.get("positiveReasons", []),
        "layoutUnitFitRisks": unit_fit.get("riskReasons", []),
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
    snapshot_files: list[str] = []
    generated_at = datetime.now(timezone.utc).isoformat()

    with tempfile.TemporaryDirectory(prefix="flog-layout-trials-") as temp_name:
        temp_dir = Path(temp_name)
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
                        scenarios = phase_trial_scenarios(hierarchy)
                        multi_phase = len(scenarios) > 1
                        canonical_phase = str(
                            canonical_phase_scenario(hierarchy).get("phase") or "default"
                        )
                        trial_candidates = candidate_specs_for_hierarchy(
                            hierarchy, candidates
                        )
                        for candidate in trial_candidates:
                            candidate_id = candidate_identity(candidate)
                            render_family = candidate_render_family(candidate)
                            phase_measurements: list[dict[str, Any]] = []
                            realized_by_phase: dict[str, dict[str, Any]] = {}
                            for scenario in scenarios:
                                phase = str(scenario.get("phase") or "default")
                                realized = realize_phase(hierarchy, candidate, scenario)
                                realized_by_phase[phase] = realized
                                html_text = render_realized_trial_html(
                                    realized, candidate, chrome
                                )
                                phase_suffix = (
                                    f"--{slugify(phase)}" if multi_phase else ""
                                )
                                html_name = (
                                    f"{slugify(hierarchy['id'])}--{slugify(candidate_id)}"
                                    f"{phase_suffix}.html"
                                )
                                html_path = temp_dir / html_name
                                html_path.write_text(html_text, encoding="utf-8")
                                if keep_html:
                                    (output_dir / html_name).write_text(
                                        html_text, encoding="utf-8"
                                    )
                                page.goto(
                                    html_path.as_uri(), wait_until="domcontentloaded"
                                )
                                page.wait_for_timeout(100)
                                measurement = page.evaluate(
                                    MEASURE_AND_OVERLAY_JS,
                                    {
                                        "hierarchyId": hierarchy["id"],
                                        "candidate": candidate_id,
                                        "renderFamily": render_family,
                                        "candidateMode": candidate_mode(candidate),
                                        "chrome": chrome,
                                        "viewportProfile": viewport.name,
                                        "phase": phase,
                                    },
                                )
                                measurement["hierarchyTitle"] = hierarchy["title"]
                                measurement["hierarchyDescription"] = hierarchy[
                                    "description"
                                ]
                                measurement["sourceApp"] = hierarchy.get(
                                    "sourceApp", ""
                                )
                                measurement["focusSlot"] = realized["focusSlot"]
                                measurement["baseFocusSlot"] = hierarchy["focusSlot"]
                                measurement["rootConcern"] = hierarchy["rootConcern"]
                                measurement["roleContract"] = realized.get(
                                    "roleContract", {}
                                )
                                measurement["phase"] = phase
                                measurement["phaseScenario"] = copy.deepcopy(
                                    scenario
                                )
                                measurement["candidatePolicy"] = copy.deepcopy(
                                    realized.get("candidatePolicy") or {}
                                )
                                measurement["candidateSpec"] = (
                                    copy.deepcopy(candidate)
                                    if isinstance(candidate, dict)
                                    else {
                                        "id": candidate_id,
                                        "mode": "legacy-family",
                                        "renderFamily": render_family,
                                    }
                                )
                                measurement["renderFamily"] = render_family
                                measurement["candidateMode"] = candidate_mode(candidate)
                                measurement["unitComposition"] = copy.deepcopy(
                                    realized.get("unitComposition") or {}
                                )
                                measurement["layoutUnitTree"] = copy.deepcopy(
                                    realized.get("layoutUnitTree")
                                )
                                measurement["layoutUnits"] = copy.deepcopy(
                                    realized.get("layoutUnits") or []
                                )
                                measurement["realizationStates"] = copy.deepcopy(
                                    realized.get("realizationStates") or {}
                                )
                                measurement["snapshots"] = {}

                                base_name = (
                                    f"{slugify(hierarchy['id'])}--"
                                    f"{slugify(viewport.name)}--{slugify(candidate_id)}"
                                    f"{phase_suffix}"
                                )
                                if screenshot_mode in {"viewport", "both"}:
                                    png = output_dir / f"{base_name}--viewport.png"
                                    rel = capture_png(page, png, full_page=False)
                                    measurement["snapshots"]["viewport"] = rel
                                    snapshot_files.append(rel)
                                if screenshot_mode in {"full-page", "both"}:
                                    png = output_dir / f"{base_name}--full-page.png"
                                    rel = capture_png(page, png, full_page=True)
                                    measurement["snapshots"]["fullPage"] = rel
                                    snapshot_files.append(rel)
                                if not measurement["snapshots"]:
                                    raise RuntimeError(
                                        "No PNG snapshots were written for "
                                        f"{hierarchy['id']} / {candidate} / "
                                        f"{viewport.name} / {phase}."
                                    )
                                apply_realized_state_fit(realized, measurement)
                                phase_measurements.append(measurement)

                            canonical_state = next(
                                (
                                    item
                                    for item in phase_measurements
                                    if item.get("phase") == canonical_phase
                                ),
                                phase_measurements[0],
                            )
                            canonical_realized = realized_by_phase.get(
                                str(canonical_state.get("phase"))
                            ) or next(iter(realized_by_phase.values()))
                            aggregate = copy.deepcopy(canonical_state)
                            aggregate["focusSlot"] = hierarchy["focusSlot"]
                            aggregate["baseFocusSlot"] = hierarchy["focusSlot"]
                            aggregate["roleContract"] = hierarchy.get(
                                "roleContract", {}
                            )
                            aggregate["phaseMeasurements"] = phase_measurements
                            aggregate["phaseSnapshots"] = {
                                item["phase"]: copy.deepcopy(item.get("snapshots", {}))
                                for item in phase_measurements
                            }
                            aggregate["canonicalPhase"] = str(
                                canonical_state.get("phase") or canonical_phase
                            )
                            apply_semantic_contract_fit(
                                hierarchy,
                                aggregate,
                                realized_hierarchy=canonical_realized,
                            )
                            measurements.append(aggregate)
                    context.close()
            finally:
                browser.close()

    best_by_hierarchy = best_by_hierarchy_viewport_rows(measurements)

    return {
        "kind": "mcel.flog.synthetic.layout.trial.report",
        "generatedAt": generated_at,
        "smokeLevel": "synthetic-hierarchy-layout-trials",
        "geometryEngine": "playwright-chromium",
        "paintedOwnershipMode": "shadow-only",
        "paintedOwnershipEnforced": False,
        "hierarchySource": "generated-mcel-like-html",
        "chrome": chrome,
        "candidates": candidates,
        "candidateCatalogByHierarchy": {
            item["id"]: [
                (
                    copy.deepcopy(candidate)
                    if isinstance(candidate, dict)
                    else {
                        "id": candidate,
                        "mode": "legacy-family",
                        "renderFamily": candidate,
                    }
                )
                for candidate in candidate_specs_for_hierarchy(item, candidates)
            ]
            for item in hierarchies
        },
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
                "nodeRoles": {
                    node["slot"]: node.get("role", "support")
                    for node in item["nodes"]
                },
                "phaseScenarios": semantic_phase_scenarios(item),
                "browserPhaseScenarios": [
                    scenario["phase"] for scenario in phase_trial_scenarios(item)
                ],
                "layoutUnitTree": copy.deepcopy(item.get("layoutUnitTree")),
                "layoutUnits": layout_unit_specs(item),
                "layoutUnitDataflow": layout_unit_dataflow_audit(item),
                "localPolicyCatalog": (
                    layout_unit_policy_catalog(item)
                    if item.get("layoutUnitTree")
                    else {}
                ),
                "unitCompositionCandidates": {
                    candidate_identity(candidate): candidate_unit_composition(
                        item, candidate
                    )
                    for candidate in candidate_specs_for_hierarchy(item, candidates)
                    if item.get("layoutUnitTree")
                },
            }
            for item in hierarchies
        ],
        "viewports": [
            {"name": vp.name, "width": vp.width, "height": vp.height}
            for vp in viewports
        ],
        "screenshotMode": screenshot_mode,
        "snapshotDirectory": ".",
        "snapshotFiles": snapshot_files,
        "semanticContracts": [
            semantic_contract_audit(item) for item in hierarchies
        ],
        "bestByHierarchyViewport": best_by_hierarchy,
        "humanLoop": {
            "required": True,
            "reason": (
                "The script measures independent realized phase states, but a human "
                "must still decide whether the synthetic hierarchy matches the intended "
                "real app."
            ),
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
) -> list[dict[str, Any]]:
    """Write one compact final rollup PNG for the whole smoke run.

    Older runs wrote one rollup PNG per hierarchy, which was convenient locally but
    expensive to upload/review.  The final rollup keeps the same best-to-worst
    ordering but packs every hierarchy/viewport into one row inside a single PNG.
    """

    if Image is None or ImageDraw is None or ImageOps is None:
        raise RuntimeError(
            "Rollup PNG generation requires Pillow. Install it with 'python -m pip install pillow'."
        )

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    order: list[tuple[str, str]] = []
    for measurement in report.get("measurements", []):
        key = (measurement["hierarchyId"], measurement["viewportProfile"])
        if key not in groups:
            order.append(key)
        groups[key].append(measurement)

    if not order:
        return []

    ranked_groups: list[dict[str, Any]] = []
    for hierarchy_id, viewport_profile in order:
        ordered = sorted(groups[(hierarchy_id, viewport_profile)], key=rollup_sort_key)[:top_n]
        if ordered:
            ranked_groups.append(
                {
                    "hierarchyId": hierarchy_id,
                    "viewportProfile": viewport_profile,
                    "items": ordered,
                    "candidates": [item.get("candidate", "") for item in ordered],
                }
            )

    if not ranked_groups:
        return []

    column_count = max(len(group["items"]) for group in ranked_groups)
    header_height = 44
    row_height = ROLLUP_IMAGE_HEIGHT + 54
    canvas_width = (
        ROLLUP_CANVAS_MARGIN * 2
        + ROLLUP_ROW_LABEL_WIDTH
        + (column_count * ROLLUP_TILE_WIDTH)
        + ((column_count - 1) * ROLLUP_TILE_GAP)
    )
    canvas_height = (
        ROLLUP_CANVAS_MARGIN * 2
        + header_height
        + (len(ranked_groups) * row_height)
        + ((len(ranked_groups) - 1) * ROLLUP_TILE_GAP)
    )

    canvas = Image.new("RGB", (canvas_width, canvas_height), (246, 246, 246))
    draw = ImageDraw.Draw(canvas)
    resampling = getattr(Image, "Resampling", Image)
    thumbnail_resample = getattr(resampling, "LANCZOS", getattr(Image, "LANCZOS", 1))

    header = f"FLOG final rollup - {len(ranked_groups)} app/viewport row(s), top {top_n} max each, best to worst left-to-right"
    draw.text((ROLLUP_CANVAS_MARGIN, ROLLUP_CANVAS_MARGIN), header, fill=(0, 0, 0))
    draw.text(
        (ROLLUP_CANVAS_MARGIN, ROLLUP_CANVAS_MARGIN + 18),
        "One PNG replaces per-app rollups to reduce upload/review pressure.",
        fill=(64, 64, 64),
    )

    for row_index, group in enumerate(ranked_groups):
        y = ROLLUP_CANVAS_MARGIN + header_height + row_index * (row_height + ROLLUP_TILE_GAP)
        label_x = ROLLUP_CANVAS_MARGIN
        label_text = f"{group['hierarchyId']}\n{group['viewportProfile']}"
        draw.rectangle(
            [
                label_x,
                y,
                label_x + ROLLUP_ROW_LABEL_WIDTH - ROLLUP_TILE_GAP,
                y + row_height,
            ],
            fill=(255, 255, 255),
            outline=(180, 180, 180),
            width=1,
        )
        _draw_wrapped_text(draw, (label_x + 8, y + 10), label_text, width=28, line_height=13)

        if group["items"]:
            winner = group["items"][0]
            winner_classification = winner.get("classification") or {}
            winner_text = f"winner: {winner.get('candidate', '')} · {winner_classification.get('status', 'fail')}"
            _draw_wrapped_text(draw, (label_x + 8, y + 46), winner_text, width=28, line_height=12)

        for index, item in enumerate(group["items"]):
            x = ROLLUP_CANVAS_MARGIN + ROLLUP_ROW_LABEL_WIDTH + index * (ROLLUP_TILE_WIDTH + ROLLUP_TILE_GAP)
            draw.rectangle(
                [x, y, x + ROLLUP_TILE_WIDTH, y + row_height],
                fill=(255, 255, 255),
                outline=(188, 188, 188),
                width=1,
            )

            image_left = x + 6
            image_top = y + 6
            image_box = (ROLLUP_TILE_WIDTH - 12, ROLLUP_IMAGE_HEIGHT)
            draw.rectangle(
                [image_left, image_top, image_left + image_box[0], image_top + image_box[1]],
                fill=(252, 252, 252),
                outline=(216, 216, 216),
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
            focus_share = float(facts.get("focusShare", 0.0) or 0.0)
            target_share = float(facts.get("desiredFocusShare", 0.0) or 0.0)
            title = f"#{index + 1} {item.get('candidate', 'unknown')}"
            draw.text((x + 6, y + 6 + ROLLUP_IMAGE_HEIGHT + 4), title[:28], fill=(0, 0, 0))
            meta = (
                f"{classification.get('score', 0)} {classification.get('status', 'fail')} "
                f"C{classification.get('contractFitScore', 0)} A{classification.get('affordanceFitScore', 0)} P{classification.get('phaseFitScore', 0)} "
                f"F{focus_share:.0%}/{target_share:.0%}"
            )
            draw.text((x + 6, y + 6 + ROLLUP_IMAGE_HEIGHT + 20), meta[:33], fill=(32, 32, 32))

    rel_path = Path(ROLLUP_FILE_NAME)
    canvas.save(output_dir / rel_path, optimize=True)

    return [
        {
            "kind": "finalRollup",
            "file": rel_path.as_posix(),
            "groupCount": len(ranked_groups),
            "columns": column_count,
            "rows": len(ranked_groups),
            "topCountPerGroup": top_n,
            "groups": [
                {
                    "hierarchyId": group["hierarchyId"],
                    "viewportProfile": group["viewportProfile"],
                    "candidates": group["candidates"],
                    "topCount": len(group["candidates"]),
                }
                for group in ranked_groups
            ],
        }
    ]


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
    lines.append(
        f"- Painted ownership: `{report.get('paintedOwnershipMode', 'unavailable')}` "
        f"(enforced=`{bool(report.get('paintedOwnershipEnforced', False))}`)"
    )
    lines.append(f"- Hierarchy source: `{report['hierarchySource']}`")
    lines.append(f"- Chrome/theme input: `{report['chrome']}`")
    lines.append(
        f"- Legacy layout families (generic trials / explicit subsets): "
        f"`{', '.join(report['candidates'])}`"
    )
    recursive_catalogs = [
        values
        for hierarchy_id, values in (report.get("candidateCatalogByHierarchy") or {}).items()
        if any(item.get("mode") == "recursive-composition" for item in values)
    ]
    if recursive_catalogs:
        lines.append(
            "- Recursive candidate identity: generated local-policy tuples "
            "(legacy whole-page names are not used for those rankings)"
        )
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
    lines.append("It also reports shadow-only exclusive painted ownership, inter-unit occlusion, undeclared partition overlap, overlay-budget use, and foreign-owner critical-control interception from Chromium hit testing.")
    lines.append("Shadow ownership does **not** affect ranking, pass/fail, phase minimums, contract scoring, or promotion in this stage; all enforced scores still use the existing raw geometry.")
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
        if item.get("layoutUnits"):
            unit_text = "; ".join(
                f"{unit['id']}[{', '.join(unit.get('slots') or unit.get('descendantSlots') or [])}]"
                for unit in item["layoutUnits"]
                if unit.get("leaf")
            )
            lines.append(f"- Recursive layout units: `{unit_text}`")
            composition_count = len(item.get("unitCompositionCandidates") or {})
            lines.append(
                f"- Unit composition candidates: `{composition_count}` "
                "(local policies are scored independently before application ranking)"
            )
            dataflow = item.get("layoutUnitDataflow") or {}
            lines.append(
                f"- Unit feed graph: state=`{dataflow.get('state', 'notDeclared')}` "
                f"edges=`{len(dataflow.get('edges') or [])}` "
                f"parallelRoots=`{', '.join(dataflow.get('parallelRoots') or [])}`"
            )
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
                keys = ["phase", "availability", "presentationSet", "relationshipStrength", "hardConstraints", "softPreferences", "phasePersistence", "defaultRealization"]
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
        lines.append(f"- `{item['hierarchyId']}` / `{item['viewportProfile']}`: `{item['candidate']}` score=`{item['score']}` status=`{item['status']}` selectionState=`{selection_state}` unclaimed=`{item['unclaimedAreaRatio']:.4f}` focus=`{item['focusShare']:.4f}` target=`{item['desiredFocusShare']:.2f}` occupancy=`{item.get('usefulFocusOccupancy', 0):.4f}` proximity=`{item.get('companionProximityScore', 1):.4f}` contractFit=`{item.get('contractFitScore', 0)}` affordanceFit=`{item.get('affordanceFitScore', 0)}` phaseFit=`{item.get('phaseFitScore', 0)}` unitFit=`{item.get('layoutUnitFitScore', 100)}` contractState=`{item.get('contractFitState', 'notEvaluated')}` affordanceState=`{item.get('affordanceFitState', 'notEvaluated')}` phaseState=`{item.get('phaseFitState', 'notEvaluated')}` unitState=`{item.get('layoutUnitFitState', 'notDeclared')}`")
        if item.get("paintedOwnershipMode") == "shadow-only":
            lines.append(
                "  - Painted ownership shadow (not enforced): "
                f"rawFocus=`{item.get('focusShare', 0):.4f}` "
                f"effectiveFocus=`{item.get('effectiveFocusShareShadow', 0):.4f}` "
                f"focusOccluded=`{item.get('focusOccludedShareShadow', 0):.4f}` "
                f"exclusiveOwned=`{item.get('exclusiveOwnedShareShadow', 0):.4f}` "
                f"doubleClaimed=`{item.get('doubleClaimedShareShadow', 0):.4f}` "
                f"declaredOverlay=`{item.get('declaredOverlayShareShadow', 0):.4f}` "
                f"undeclaredPartitionOverlap=`{item.get('undeclaredPartitionOverlapShareShadow', 0):.4f}` "
                f"fullyForeignInterceptedControls=`{item.get('interceptedCriticalControlCountShadow', 0)}` "
                f"foreignInterceptedControls=`{item.get('foreignInterceptedCriticalControlCountShadow', 0)}`"
            )
            for overlap in item.get("paintedOwnershipOverlapMatrixShadow", [])[:4]:
                lines.append(
                    "    - "
                    f"`{overlap.get('occludedSlot')}` occluded by "
                    f"`{overlap.get('occludingSlot')}` "
                    f"rootShare=`{overlap.get('shareOfRoot', 0):.4f}` "
                    f"nodeShare=`{overlap.get('shareOfOccludedNode', 0):.4f}`"
                )
            for partition_overlap in item.get(
                "partitionOverlapByUnitShadow", []
            )[:4]:
                lines.append(
                    "    - Undeclared partition overlap (shadow): "
                    f"`{partition_overlap.get('unitId')}` "
                    f"rootShare=`{partition_overlap.get('shareOfRoot', 0):.4f}`"
                )
            for budget in item.get("overlayBudgetExceededShadow", [])[:4]:
                lines.append(
                    "    - Overlay budget exceeded (shadow): "
                    f"`{budget.get('unitId')}` "
                    f"share=`{budget.get('occlusionShare', 0):.4f}` "
                    f"max=`{budget.get('maxOcclusionShare', 0):.4f}`"
                )
        if item.get("compositionLabel"):
            lines.append(
                f"  - Composed policies: `{item['compositionLabel']}` "
                f"preflight=`{item.get('compositionPreflightScore')}`"
            )
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
        if item.get("phaseFitReasons"):
            lines.append("  - Phase-aware realization evidence:")
            for reason in item.get("phaseFitReasons", [])[:4]:
                lines.append(f"    - {reason}")
        if item.get("phaseFitRisks"):
            lines.append("  - Phase-aware realization risks:")
            for reason in item.get("phaseFitRisks", [])[:4]:
                lines.append(f"    - {reason}")
        if item.get("layoutUnitFitReasons"):
            lines.append("  - Recursive layout-unit evidence:")
            for reason in item.get("layoutUnitFitReasons", [])[:4]:
                lines.append(f"    - {reason}")
        if item.get("layoutUnitFitRisks"):
            lines.append("  - Recursive layout-unit risks:")
            for reason in item.get("layoutUnitFitRisks", [])[:4]:
                lines.append(f"    - {reason}")
        if item.get("layoutUnitParallelBranches"):
            branch_text = ", ".join(
                f"{branch['unitId']}:worst={branch.get('worstScore', branch.get('score', 0))}"
                for branch in item.get("layoutUnitParallelBranches", [])
            )
            lines.append(f"  - Parallel unit branches: `{branch_text}`")
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
            "The smoke writes one final compact rollup PNG for the whole run. Each hierarchy/viewport is one row, with ranked layouts ordered best to worst from left to right."
        )
        lines.append("")
        for item in report.get("rollups", []):
            lines.append(
                f"- Final rollup: `{item['file']}` "
                f"(groups=`{item.get('groupCount', 0)}`, grid=`{item.get('columns', 0)}x{item.get('rows', 0)}`, topPerGroup=`{item.get('topCountPerGroup', 0)}`)"
            )
            for group in item.get("groups", []):
                if group.get("candidates"):
                    lines.append(
                        f"  - `{group['hierarchyId']}` / `{group['viewportProfile']}`: `{', '.join(group['candidates'])}`"
                    )
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
        lines.append(f"- Phase fit score: `{classification.get('phaseFitScore', 0)}` state=`{classification.get('phaseFitState', 'notEvaluated')}`")
        lines.append(f"- Recursive unit fit score: `{classification.get('layoutUnitFitScore', 100)}` state=`{classification.get('layoutUnitFitState', 'notDeclared')}`")
        if (item.get("layoutUnitFit") or {}).get("parallelBranches"):
            branch_text = ", ".join(
                f"{branch['unitId']}:worst={branch.get('worstScore', branch.get('score', 0))}"
                for branch in item["layoutUnitFit"]["parallelBranches"]
            )
            lines.append(f"- Parallel unit branches: `{branch_text}`")
        lines.append(f"- Focus slot: `{item.get('focusSlot')}`")
        lines.append(f"- Unclaimed layout area ratio: `{facts.get('unclaimedAreaRatio', 0):.4f}`")
        lines.append(f"- Node coverage ratio: `{facts.get('nodeCoverageRatio', 0):.4f}`")
        lines.append(f"- Focus share: `{facts.get('focusShare', 0):.4f}`")
        painted_shadow = facts.get("paintedOwnershipShadow") or {}
        if facts.get("paintedOwnershipMode") == "shadow-only":
            lines.append("- Painted ownership shadow diagnostics (not enforced):")
            lines.append(
                f"  - Raw focus share: `{painted_shadow.get('rawFocusShare', facts.get('focusShare', 0)):.4f}`"
            )
            lines.append(
                f"  - Effective focus share: `{painted_shadow.get('effectiveFocusShare', 0):.4f}`"
            )
            lines.append(
                f"  - Focus occluded share: `{painted_shadow.get('focusOccludedShare', 0):.4f}`"
            )
            lines.append(
                f"  - Exclusive owned share: `{painted_shadow.get('exclusiveOwnedShare', 0):.4f}`"
            )
            lines.append(
                f"  - Double-claimed share: `{painted_shadow.get('doubleClaimedShare', 0):.4f}`"
            )
            lines.append(
                f"  - Declared overlay share: `{painted_shadow.get('declaredOverlayShare', 0):.4f}`"
            )
            lines.append(
                f"  - Undeclared partition overlap share: `{painted_shadow.get('undeclaredPartitionOverlapShare', 0):.4f}`"
            )
            lines.append(
                f"  - Partition-overlap cell share: `{painted_shadow.get('partitionOverlapCellShare', 0):.4f}`"
            )
            lines.append(
                f"  - Fully foreign-intercepted critical controls: `{facts.get('interceptedCriticalControlCountShadow', 0)}`"
            )
            lines.append(
                f"  - Critical controls with any foreign interception: `{facts.get('foreignInterceptedCriticalControlCountShadow', facts.get('partiallyInterceptedCriticalControlCountShadow', 0))}`"
            )
            outcome_totals = facts.get("controlInterceptionOutcomeTotalsShadow") or {}
            lines.append(
                "  - Control hit-test outcomes: "
                f"selfOwned=`{outcome_totals.get('selfOwned', 0)}` "
                f"foreignIntercepted=`{outcome_totals.get('foreignIntercepted', 0)}` "
                f"noPointerTarget=`{outcome_totals.get('noPointerTarget', 0)}` "
                f"pointerEventsNonePassThrough=`{outcome_totals.get('pointerEventsNonePassThrough', 0)}` "
                f"unownedPointerTarget=`{outcome_totals.get('unownedPointerTarget', 0)}`"
            )
            for overlap in painted_shadow.get("overlapMatrix", [])[:8]:
                lines.append(
                    "  - Overlap: "
                    f"`{overlap.get('occludedSlot')}` occluded by "
                    f"`{overlap.get('occludingSlot')}` "
                    f"rootShare=`{overlap.get('shareOfRoot', 0):.4f}` "
                    f"nodeShare=`{overlap.get('shareOfOccludedNode', 0):.4f}`"
                )
            for partition_overlap in painted_shadow.get(
                "partitionOverlapByUnit", []
            )[:4]:
                lines.append(
                    "  - Undeclared partition overlap: "
                    f"`{partition_overlap.get('unitId')}` "
                    f"rootShare=`{partition_overlap.get('shareOfRoot', 0):.4f}`"
                )
            for budget in painted_shadow.get("overlayBudgetExceeded", [])[:4]:
                lines.append(
                    "  - Overlay budget exceeded: "
                    f"`{budget.get('unitId')}` "
                    f"share=`{budget.get('occlusionShare', 0):.4f}` "
                    f"max=`{budget.get('maxOcclusionShare', 0):.4f}`"
                )
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
        phase_shadow_rows = [
            phase_item
            for phase_item in (item.get("phaseMeasurements") or [])
            if ((phase_item.get("geometryFacts") or {}).get("paintedOwnershipMode"))
            == "shadow-only"
        ]
        if phase_shadow_rows:
            lines.append("- Painted ownership shadow by browser phase (not enforced):")
            for phase_item in phase_shadow_rows:
                phase_facts = phase_item.get("geometryFacts") or {}
                phase_shadow = phase_facts.get("paintedOwnershipShadow") or {}
                lines.append(
                    f"  - `{phase_item.get('phase', 'default')}`: "
                    f"rawFocus=`{phase_shadow.get('rawFocusShare', phase_facts.get('focusShare', 0)):.4f}` "
                    f"effectiveFocus=`{phase_shadow.get('effectiveFocusShare', 0):.4f}` "
                    f"focusOccluded=`{phase_shadow.get('focusOccludedShare', 0):.4f}` "
                    f"exclusiveOwned=`{phase_shadow.get('exclusiveOwnedShare', 0):.4f}` "
                    f"doubleClaimed=`{phase_shadow.get('doubleClaimedShare', 0):.4f}` "
                    f"undeclaredPartitionOverlap=`{phase_shadow.get('undeclaredPartitionOverlapShare', 0):.4f}` "
                    f"foreignInterceptedControls=`{phase_facts.get('foreignInterceptedCriticalControlCountShadow', phase_facts.get('partiallyInterceptedCriticalControlCountShadow', 0))}`"
                )
                for overlap in phase_shadow.get("overlapMatrix", [])[:4]:
                    lines.append(
                        "    - "
                        f"`{overlap.get('occludedSlot')}` occluded by "
                        f"`{overlap.get('occludingSlot')}` "
                        f"rootShare=`{overlap.get('shareOfRoot', 0):.4f}`"
                    )
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
        phase_fit = item.get("phaseFit") or {}
        if phase_fit:
            lines.append("- Generic phase-aware realization:")
            lines.append(f"  - Score: `{phase_fit.get('score', 0)}` raw=`{phase_fit.get('rawScore', phase_fit.get('score', 0))}` state=`{phase_fit.get('state', 'unknown')}`")
            for reason in phase_fit.get("positiveReasons", [])[:4]:
                lines.append(f"  - Preserved: {reason}")
            for reason in phase_fit.get("riskReasons", [])[:4]:
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
